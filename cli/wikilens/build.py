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
from dataclasses import dataclass
from pathlib import Path

from . import layout
from .convert import parse, render_page_file
from .models import AnchorEntry, StructureSignature, canonical_json, jsonl_line


@dataclass
class BuildReport:
    parsed: int = 0
    skipped: int = 0
    pages_written: int = 0
    structures_written: int = 0
    total_links: int = 0
    resolved_links: int = 0
    targets_with_anchors: int = 0
    orphans: int = 0

    @property
    def resolution_rate(self) -> float:
        return self.resolved_links / self.total_links if self.total_links else 0.0


def _load_raw_index(root: Path) -> dict[str, dict]:
    """.sync-state.json 의 페이지 메타데이터. sync가 기록한다."""
    p = layout.sync_state_path(root)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("pages", {})


def build(root: Path, verbose: bool = False) -> BuildReport:
    root = Path(root)
    meta = _load_raw_index(root)
    report = BuildReport()

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
    for pid, m in sorted(meta.items()):
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
        signatures[pid] = sig
        report.parsed += 1

        _write_if_changed(layout.page_path(root, pid), render_page_file(sig, md))
        report.pages_written += 1
        _write_if_changed(layout.structure_path(root, pid), canonical_json(sig.to_dict()))
        report.structures_written += 1

    # ---- 3패스: 전치 ----
    entries = transpose(signatures, report)
    _write_anchors(root, entries)
    _write_aliases(root, entries, signatures)
    return report


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
    p.write_text("".join(jsonl_line(e.to_dict()) for e in entries), encoding="utf-8")


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
        "형식: `제목 | 별칭 · 별칭 … | 인링크수 | 경로` — 한 줄이라 grep 결과에 경로가 항상 포함됩니다.",
        "",
        "## 색인",
        "",
    ]
    with_alias = [e for e in entries if _alias_terms(e)]
    without = [e for e in entries if not _alias_terms(e)]

    for e in sorted(with_alias, key=lambda x: -x.indeg):
        terms = " · ".join(_alias_terms(e))
        lines.append(f"{e.title} | {terms} | {e.indeg} | {e.path}")

    if without:
        lines += [
            "",
            "## 별칭 없는 페이지",
            "",
            "어느 문서도 링크하지 않았거나 제목 그대로만 링크된 페이지입니다.",
            "검색으로만 도달 가능하므로 **고아 문서 후보**입니다.",
            "",
        ]
        for e in sorted(without, key=lambda x: x.title):
            lines.append(f"{e.title} | (별칭 없음) | 0 | {e.path}")

    lines.append("")
    layout.aliases_path(root).write_text("\n".join(lines), encoding="utf-8")


def _write_if_changed(path: Path, content: str) -> bool:
    """
    내용이 같으면 쓰지 않는다.

    mtime이 무의미하게 바뀌는 것을 막고, git을 쓰는 경우 diff 노이즈를 없앤다.
    결정적 직렬화와 짝이 되는 장치다.
    """
    layout.ensure_parent(path)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True
