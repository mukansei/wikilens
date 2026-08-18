"""
빌드 단계: raw/ 를 파싱해 pages/·structure/ 를 만들고, 앵커를 전치한다.

sync와 build를 나눈 이유는 **제목→ID 해석의 완전성** 때문이다.
Confluence 링크는 대개 제목으로 대상을 가리키는데, 파싱 시점에 전체 제목 색인이
없으면 미해결 링크가 생긴다. raw/를 전부 받은 뒤 한 번에 파싱하면 해석이 완전해진다.

부수 효과로 build가 순수 로컬·멱등이 되어 네트워크 없이 반복 실행·테스트가 가능하다.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import layout
from . import scripts as scripts_mod
from .convert import parse, render_page_file
from .models import AnchorEntry, StructureSignature, canonical_json


@dataclass
class BuildReport:
    parsed: int = 0
    skipped: int = 0
    #: **실제로 디스크에 쓴** 개수. 내용이 같으면 안 쓰므로 `parsed` 보다 작다.
    #: 예전엔 `_write_if_changed` 의 반환값을 버리고 무조건 증가시켜서, 세 숫자가
    #: 항상 같았다 — 아무것도 안 바뀐 재빌드도 "페이지 2383" 이라고 찍었다.
    #: 이 저장소의 핵심 계약이 빌드 멱등성인데, 그걸 보여줄 수 있는 유일한 수를
    #: 버리고 있었던 셈이다.
    pages_written: int = 0
    structures_written: int = 0
    total_links: int = 0
    resolved_links: int = 0
    targets_with_anchors: int = 0
    orphans: int = 0
    #: 문자 집합 밖이라 볼트에서 뺀 페이지. **비어 있지 않으면 사용자에게 말해야 한다** —
    #: 빠진 문서는 검색 결과에 안 나오는 것으로만 드러나고 "문서가 없다" 와 구별되지 않는다.
    excluded: list[str] = field(default_factory=list)
    #: 제외되면서 지운 파생 파일 수(`pages/`·`structure/`). 원본 `raw/` 는 안 지운다.
    pages_removed: int = 0
    #: 제외된 페이지의 비율. **왜 빠졌는지 답할 수 있어야 한다** — ID 목록만 남기면
    #: 운영자가 "이 문서가 왜 없지" 를 물었을 때 볼트를 다시 계산해야 답한다.
    excluded_ratio: dict[str, float] = field(default_factory=dict)

    @property
    def resolution_rate(self) -> float:
        return self.resolved_links / self.total_links if self.total_links else 0.0


def _load_raw_index(root: Path) -> dict[str, dict]:
    """.sync-state.json 의 페이지 메타데이터. sync가 기록한다."""
    p = layout.sync_state_path(root)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("pages", {})


#: 진행 표시 간격(페이지)과 그것을 켜는 하한.
#:
#: 13,947건 코퍼스에서 `build` 는 **2분 28초 동안 한 줄도 안 찍었다**(실측 2026-08-18).
#: 그 침묵이 `sync` 바로 뒤에 오므로 — `sync` 는 페이지마다 찍는다 — 사용자에게는
#: 싱크가 멈춘 것으로 보인다. 첫 설정에서 이 침묵이 마지막 인상이다.
#:
#: 시간의 96%가 `parse()` 다(프로파일: 402초/417초). 그래서 2패스 루프에만 단다.
#: 500건이면 이 코퍼스에서 약 5초 간격이다. 하한을 두는 것은 작은 볼트가 조용하도록.
PROGRESS_EVERY = 500


def build(root: Path, index_scripts: list[str] | None = None,
          script_threshold: float = 0.10,
          progress: Callable[[int, int], None] | None = None) -> BuildReport:
    """
    파생물을 만든다. [index_scripts] 를 주면 **그 문자 집합 밖의 문서를 볼트에서 뺀다.**

    **판정은 본문만 본다** — 제목은 이 코퍼스처럼 이중언어로 다는 관행이 있어
    짧은 목차 페이지를 잘못 뺀다(실측 44건, 자식 255건의 계층이 깨졌다).

    다국어 코퍼스용이고 근거는 `scripts.py` 에 있다. 판정을 여기서 하는 이유는
    **결정이 한 곳이어야 두 판이 같은 답을 내기** 때문이다 — 서버에 두면 로컬판이
    아무것도 못 받는다.

    빠진 문서는 `ALIASES.md`·`TREE.md`·`anchors.jsonl` 에 안 들어가고, 서버가 같은
    결정을 따르도록 `derived/excluded.json` 에 남긴다(서버는 페이지 목록을
    `.sync-state.json` 에서 얻으므로 파생물에서 빼는 것만으로는 안 걸러진다).

    **`mirror/pages/`·`mirror/structure/` 도 지운다** — 안 지우면 로컬판이 본문
    grep 으로 여전히 찾는데 서버판은 못 찾아, 같은 볼트에 두 판이 다른 답을 낸다.
    **원본 `raw/` 는 안 건드리므로** 설정을 바꿔 다시 빌드하면 되살아난다(네트워크 없음).
    """
    root = Path(root)
    meta = _load_raw_index(root)
    report = BuildReport()
    ranges = scripts_mod.resolve(index_scripts or [])
    # **여기서 검사한다 — 호출부가 아니라.** `config.json` 쪽만 범위를 보고 있어서
    # `--script-threshold -1` 이 그대로 통과했고, 그러면 **전 문서가 제외되며 본문
    # 파일이 전부 지워진다**(실측: 2/2). `15` 를 넣는 실수(퍼센트로 생각)는 반대로
    # 아무것도 안 걸러 사용자가 걸린 줄 안다. 둘 다 조용해서 더 나쁘다.
    if ranges and not 0.0 <= script_threshold <= 1.0:
        raise ValueError(
            f"문턱은 0~1 사이여야 합니다 (받은 값: {script_threshold}). "
            f"퍼센트가 아니라 비율입니다 — 15% 는 0.15 입니다."
        )

    # ---- 1패스: 제목 -> ID 색인 ----
    # (space, title) 과 (title) 양쪽으로 색인한다. 링크가 스페이스 키를 생략하는 경우가 흔하다.
    by_space_title: dict[tuple[str, str], str] = {}
    by_title: dict[str, list[str]] = defaultdict(list)
    for pid, m in meta.items():
        t, s = m.get("title", ""), m.get("space", "")
        if t:
            by_space_title[(s, t)] = pid
            by_title[t].append(pid)

    def resolve(title: str, space: str | None) -> str | None:
        if space and (space, title) in by_space_title:
            return by_space_title[(space, title)]
        cands = by_title.get(title, [])
        # 동명이인이 여러 스페이스에 있으면 해석하지 않는다. 틀린 간선보다 없는 편이 낫다.
        return cands[0] if len(cands) == 1 else None

    # ---- 2패스: 파싱 + 기록 ----
    signatures: dict[str, StructureSignature] = {}
    # 여기가 전체 시간의 96% 다. `progress` 가 없으면 아무 일도 안 하므로 테스트와
    # 라이브러리 호출은 그대로 조용하다 — 찍는 것은 CLI 의 몫이다.
    total = len(meta)
    announce = progress if progress and total > PROGRESS_EVERY else None
    for done, (pid, m) in enumerate(sorted(meta.items()), 1):
        if announce and done % PROGRESS_EVERY == 0:
            try:
                announce(done, total)
            except OSError:
                # **진행 표시는 장식이다 — 그것 때문에 빌드가 죽으면 안 된다.**
                # `wikilens build | head` 처럼 파이프를 일찍 닫으면 `print` 가
                # `BrokenPipeError` 를 내고, 그게 여기로 올라와 2패스 한가운데서
                # 빌드를 끝낸다. 그러면 `mirror/pages/` 는 일부만 갱신됐는데
                # `ALIASES`·`TREE`·`anchors` 는 옛 집합을 가리키는 상태로 남는다
                # (파생물은 전부 마지막에 쓴다). 겉으로는 traceback 하나뿐이다.
                # 침묵으로 되돌아가되 빌드는 끝낸다.
                announce = None
        raw = layout.raw_path(root, pid)
        if not raw.exists():
            report.skipped += 1
            continue
        sig, md = parse(
            page_id=pid,
            title=m.get("title", ""),
            space=m.get("space", ""),
            version=int(m.get("version", 0)),
            xhtml=raw.read_text(encoding="utf-8"),
            resolve=resolve,
        )
        # **본문만 본다 — 제목은 안 본다.**
        #
        # 처음엔 제목도 넣었다("본문이 짧고 제목만 다른 언어인 문서" 를 잡으려고).
        # **정확히 반대로 작동했다** — 이 코퍼스는 목차 페이지 제목을 이중언어로 달고
        # (`01-07. Không có xử lý (기타 목차)`) 그런 페이지는 본문이 거의 없어서,
        # 제목의 베트남어가 비율을 지배했다. 실측: 그렇게 잘못 빠진 44건이 **자식
        # 255건을 거느린 계층 노드**였고, 부모가 빠지면 자식이 루트로 승격돼 계층이
        # 평평해진다.
        #
        # 반대로 제목을 빼야만 걸리는 문서가 4건 있었는데 **전부 진짜 베트남어**였다
        # (제목만 한국어이고 본문이 `Giải thích Page`). 제목이 판정을 흐리고 있었다.
        #
        # 읽는 사람이 읽는 것은 본문이다. 제목은 라벨이다.
        #
        # **판정은 파싱 직후다** — 마크다운이 만들어진 뒤라 원본 XHTML 의 태그가
        # 섞이지 않는다(태그명은 전부 ASCII 라 선언 안으로 세어져 비율을 희석한다).
        ratio = scripts_mod.foreign_word_ratio(md, ranges) if ranges else 0.0
        if ranges and ratio > script_threshold:
            report.excluded.append(pid)
            report.excluded_ratio[pid] = round(ratio, 3)
            # **파생물을 지운다 — 두 판이 같은 문서 집합을 봐야 한다.**
            #
            # 안 지우면 로컬판이 본문 grep(스킬 3단계)으로 여전히 찾는데 서버판은
            # 완전히 못 찾는다. 같은 볼트에 두 판이 다른 답을 내는 것은 이 저장소가
            # 반복해서 지워온 실패 모양이다.
            #
            # **원본(`raw/`)은 안 건드린다** — 설정을 바꿔 다시 빌드하면 되살아난다.
            # `sync --full` 이 사라진 페이지를 지우는 것과 같은 규칙이고, 되돌리기가
            # 네트워크 없이 되는 것도 그 덕이다.
            for f in (layout.page_path(root, pid), layout.structure_path(root, pid)):
                if f.exists():
                    f.unlink()
                    report.pages_removed += 1
            continue

        signatures[pid] = sig
        report.parsed += 1

        if _write_if_changed(layout.page_path(root, pid), render_page_file(sig, md)):
            report.pages_written += 1
        if _write_if_changed(layout.structure_path(root, pid), canonical_json(sig.to_dict())):
            report.structures_written += 1

    # ---- 3패스: 전치 ----
    entries = transpose(signatures, report)
    _write_anchors(root, entries)
    _write_aliases(root, entries, signatures)
    # 빠진 페이지는 트리에서도 사라져야 한다 — `_write_tree` 는 `signatures` 에 있는
    # 것만 그리므로(`valid`) 자동으로 걸러진다. 부모가 빠지면 자식이 루트로 승격된다.
    _write_tree(root, meta, signatures)
    _write_excluded(root, report, index_scripts or [], script_threshold)
    return report


def _write_excluded(root: Path, report: BuildReport,
                    scripts: list[str], threshold: float) -> None:
    """
    `build` 가 뺀 페이지와 **그 근거**. 서버가 같은 결정을 따르게 하는 유일한 통로다.

    서버는 페이지 목록을 `.sync-state.json`(= `sync` 가 쓴다)에서 얻으므로, 파생물에서
    빼는 것만으로는 색인에 그대로 들어간다. 그래서 결정을 파일로 남긴다.

    **설정과 비율을 함께 남긴다** — ID 목록만 있으면 운영자가 "이 문서가 왜 없지" 를
    물었을 때 답할 방법이 없고, 서버도 `/api/stats` 에 개수만 낼 뿐 무슨 설정이었는지
    모른다. 볼트가 스스로 말하게 하는 것이 이 저장소의 규칙이다.

    **비어도 쓴다** — 파일이 없는 것과 "뺄 것이 없다" 가 구별되어야, 옛 볼트를 읽는
    서버가 이 기능 이전인지 아닌지 안다.
    """
    p = layout.ensure_parent(root / "derived" / "excluded.json")
    _write_if_changed(p, canonical_json({
        "scripts": list(scripts),
        "threshold": threshold if scripts else None,
        "excluded": sorted(report.excluded),
        "ratio": report.excluded_ratio,
    }))


def transpose(
    signatures: dict[str, StructureSignature], report: BuildReport | None = None
) -> list[AnchorEntry]:
    """
    링크를 **대상 기준으로 뒤집는다.**

    원본에는 "A가 T라는 문구로 B를 링크한다"가 A 안에 적혀 있다.
    전치하면 "B는 A, C, D에게서 T, U, V로 불린다"가 된다.

    새 정보는 0이지만 접근 경로가 새로 생긴다 — 원본 구조로 "이 페이지를 사람들이
    뭐라고 부르나"를 답하려면 전수 스캔이 필요하다. 이게 이 프로젝트의 핵심이다.
    """
    incoming: dict[str, list[dict[str, str]]] = defaultdict(list)

    for src_id, sig in signatures.items():
        for link in sig.links:
            if report is not None:
                report.total_links += 1
            if not link.to:
                continue
            if report is not None:
                report.resolved_links += 1
            incoming[link.to].append({"text": link.anchor, "from": src_id})

    entries: list[AnchorEntry] = []
    for pid, sig in sorted(signatures.items()):
        anchors = incoming.get(pid, [])
        # 같은 표현이 여러 곳에서 오면 빈도가 곧 신뢰도이므로 중복을 유지하되,
        # (표현, 출처) 쌍은 중복 제거한다.
        seen, uniq = set(), []
        for a in anchors:
            k = (a["text"], a["from"])
            if k not in seen:
                seen.add(k)
                uniq.append(a)
        uniq.sort(key=lambda a: (a["text"], a["from"]))

        entries.append(
            AnchorEntry(
                target=pid,
                title=sig.title,
                space=sig.space,
                path=layout.rel_page_path(pid),
                anchors=uniq,
                indeg=len(uniq),
            )
        )
        if report is not None:
            if uniq:
                report.targets_with_anchors += 1
            else:
                report.orphans += 1

    return entries


def _alias_terms(entry: AnchorEntry) -> list[str]:
    """
    별칭 목록. 빈도 내림차순으로 정렬해 흔한 호칭이 앞에 오게 한다.
    제목과 같은 표현은 제외한다 — 제목은 이미 옆에 적혀 있어 중복이다.
    """
    counts: dict[str, int] = defaultdict(int)
    for a in entry.anchors:
        t = a["text"].strip()
        if t and t != entry.title:
            counts[t] += 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _write_anchors(root: Path, entries: list[AnchorEntry]) -> None:
    p = layout.ensure_parent(layout.anchors_path(root))
    # JSONL 한 줄도 같은 결정적 직렬화를 쓴다 — 규칙이 갈리면 멱등성이 깨진다.
    p.write_text("".join(canonical_json(e.to_dict()) for e in entries),
                 encoding="utf-8", newline="\n")


def _write_aliases(
    root: Path, entries: list[AnchorEntry], signatures: dict[str, StructureSignature]
) -> None:
    """
    grep 가능한 앵커 전치 렌더링.

    **한 줄에 전부 담는다.** 블록 형식으로 하면 경로가 별칭 줄보다 위에 놓여
    `grep -A`로는 안 보이고 `-B`를 써야 한다. 검색하는 쪽이 컨텍스트 플래그
    방향을 미리 알아야 하는 형식은 실패한 형식이다.
    한 줄이면 어떤 grep 모드에서도 경로가 함께 나온다.
    """
    lines = [
        "# 별칭 색인",
        "",
        "다른 문서들이 각 페이지를 **실제로 부르는 이름**입니다. 자동 생성 — 직접 수정하지 마세요.",
        "",
        "형식: `스페이스 | 제목 | 별칭 · 별칭 … | 인링크수 | 경로`"
        " — 한 줄이라 grep 결과에 스페이스와 경로가 항상 포함됩니다.",
        "",
        "## 색인",
        "",
    ]
    # 엔트리당 한 번만 계산한다. 예전엔 분류 2회 + 렌더 1회로 3중 재계산이었다.
    scored = [(e, _alias_terms(e)) for e in entries]
    with_alias = [(e, terms) for e, terms in scored if terms]
    without = [e for e, terms in scored if not terms]

    for e, terms in sorted(with_alias, key=lambda x: -x[0].indeg):
        lines.append(f"{e.space} | {e.title} | {' · '.join(terms)} | {e.indeg} | {e.path}")

    # **둘을 갈라 적는다.** 한때 한 절에 묶고 인링크 수를 `0` 으로 **찍어** 넣었는데,
    # 그 둘은 정반대의 것이다 — 아무도 안 링크한 문서와, 다들 제목 그대로 링크하는
    # 문서다. 실측(13,933건): 그 절 13,469건 중 **1,258건이 인링크가 있었고** 가장
    # 심한 것은 115개였다. 이 파일을 grep 하는 쪽은 그 `0` 과 "고아 문서 후보" 를
    # 그대로 읽으므로, 가장 정본에 가까운 페이지를 고아로 보고하고 있었다.
    by_title_only = sorted([e for e in without if e.indeg > 0], key=lambda x: -x.indeg)
    orphans = [e for e in without if e.indeg <= 0]

    if by_title_only:
        lines += [
            "",
            "## 제목 그대로만 링크된 페이지",
            "",
            "링크는 있는데 전부 제목 그대로라 별칭이 안 생긴 페이지입니다.",
            "**고아가 아닙니다** — 인링크가 많을수록 오히려 정본에 가깝습니다.",
            "",
        ]
        for e in by_title_only:
            lines.append(f"{e.space} | {e.title} | (제목으로만) | {e.indeg} | {e.path}")

    if orphans:
        lines += [
            "",
            "## 별칭 없는 페이지",
            "",
            "어느 문서도 링크하지 않은 페이지입니다.",
            "검색으로만 도달 가능하므로 **고아 문서 후보**입니다.",
            "",
        ]
        for e in sorted(orphans, key=lambda x: x.title):
            lines.append(f"{e.space} | {e.title} | (별칭 없음) | 0 | {e.path}")

    lines.append("")
    layout.aliases_path(root).write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _write_tree(
    root: Path, meta: dict[str, dict], signatures: dict[str, StructureSignature]
) -> None:
    """
    부모-자식 계층을 그대로 보여주는 목차.

    앵커 색인과 분리된 별도 신호다 — "이 문서를 뭐라고 부르나"가 아니라
    "이 문서가 어디 분류에 속하나"를 답한다. 정확한 어휘를 몰라도 영역만 알 때
    위에서 아래로 내려가며 찾는 용도. 앵커처럼 부모 제목을 별칭에 섞지 않는다 —
    같은 부모 아래 문서 수십 개가 그 제목을 공유하면 모호성만 커진다
    (실제로 이 문제를 겪었다: 인링크 상위 앵커가 3개 문서에 걸쳐 모호했던 사례).
    """
    valid = {pid: m for pid, m in meta.items() if pid in signatures}
    children: dict[str, list[str]] = defaultdict(list)
    roots: list[str] = []

    for pid, m in valid.items():
        ancestors = m.get("ancestors") or []
        parent_id = str(ancestors[-1]["id"]) if ancestors else None
        if parent_id and parent_id in valid:
            children[parent_id].append(pid)
        else:
            roots.append(pid)

    lines = [
        "# 페이지 트리",
        "",
        "부모-자식 계층입니다. 자동 생성 — 직접 수정하지 마세요.",
        "정확한 이름을 모르고 영역만 알 때는 ALIASES.md 대신 이걸로 위에서부터 내려가며 찾으세요.",
        "",
    ]

    # 순환 방어. 서버판 `TreeRenderer` 가 같은 이유로 같은 방어를 한다 —
    # 한쪽만 있으면 손상된 `.sync-state.json` 하나에 두 판이 **다른 트리**를 낸다.
    seen: set[str] = set()

    def render(pid: str, depth: int) -> None:
        if pid in seen:
            return
        seen.add(pid)
        m = valid[pid]
        lines.append(
            f"{'  ' * depth}- {m.get('title', '')} [{m.get('space', '')}]"
            f" — {layout.rel_page_path(pid)}"
        )
        for child in sorted(children.get(pid, []), key=lambda c: valid[c].get("title", "")):
            render(child, depth + 1)

    for pid in sorted(roots, key=lambda p: valid[p].get("title", "")):
        render(pid, 0)

    # 순환에 갇혀 어느 루트에서도 안 닿는 페이지들. 그냥 두면 **TREE.md 에서 통째로
    # 사라진다** — 고아 문서를 찾는 유일한 경로가 TREE.md 이므로 조용한 유실이다.
    # (실측: A↔B 순환이면 3건 중 1건만 실렸다.) 루트로 승격해 이어 그린다.
    for pid in sorted(valid.keys() - seen, key=lambda p: valid[p].get("title", "")):
        render(pid, 0)

    lines.append("")
    layout.tree_path(root).write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _write_if_changed(path: Path, content: str) -> bool:
    """
    내용이 같으면 쓰지 않는다.

    mtime이 무의미하게 바뀌는 것을 막고, git을 쓰는 경우 diff 노이즈를 없앤다.
    결정적 직렬화와 짝이 되는 장치다.
    """
    layout.ensure_parent(path)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8", newline="\n")
    return True
