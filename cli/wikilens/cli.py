"""wikilens CLI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import layout
from .build import build


def _cmd_doctor(args) -> int:
    """연결·인증·권한을 sync 실행 전에 확인한다."""
    from .sync import client_from_env

    d = client_from_env().doctor()
    print(f"대상       : {d.base_url}")
    print(f"배포 형태  : {d.deployment or '판별 실패'}"
          + (f" (경로 접두사 '{d.prefix}')" if d.prefix is not None else ""))
    print(f"인증 방식  : {d.auth_mode}")
    print(f"인증       : {'성공 — ' + str(d.account) if d.authenticated else '실패'}")
    print(f"본문 확장  : {'가능' if d.storage_expandable else '확인 안 됨'}")
    if d.spaces:
        print(f"접근 가능 스페이스 {len(d.spaces)}개:")
        for k, n in d.spaces[:15]:
            print(f"  {k:<12} {n}")
        if len(d.spaces) > 15:
            print(f"  ... 외 {len(d.spaces)-15}개")
    if d.errors:
        print("\n문제:")
        for e in d.errors:
            print("  " + e.replace("\n", "\n  "))
    print()
    if d.ok:
        key = d.spaces[0][0] if d.spaces else "SPACEKEY"
        # 볼트 경로는 **지금 쓰고 있는 값**을 그대로 보여준다. 예전에는 `~/wiki` 가
        # 박혀 있어서, 래퍼를 거쳐 들어온 사용자(볼트가 `~/.wikilens/vault` 인)에게
        # 엉뚱한 경로를 안내했다.
        print(f"준비 완료. 다음: wikilens --root {args.root} sync --space {key}")
        return 0
    print("위 문제를 해결한 뒤 다시 실행하세요.")
    return 1


def _cmd_sync(args) -> int:
    from .sync import client_from_env, sync

    client = client_from_env()
    if not args.skip_check:
        d = client.doctor()
        if not d.ok:
            print("연결 확인 실패. wikilens doctor 로 자세히 보세요.")
            for e in d.errors:
                print("  " + e.replace("\n", "\n  "))
            return 1
    rep = sync(
        Path(args.root),
        client,
        spaces=args.space,
        full=args.full,
        verbose=args.verbose,
        follow_refs=args.follow_refs,
    )
    if rep.resumed_from:
        print(f"이전 중단 지점에서 이어받음: {rep.resumed_from}")
    print(
        f"싱크 완료: 받음 {rep.fetched} · 변경없음 {rep.unchanged} · "
        f"실패 {rep.failed} · 삭제 {len(rep.removed)} · {rep.elapsed_s:.1f}초"
    )
    if args.follow_refs:
        print(f"참조 확장: 지정 스페이스 밖에서 낱개로 받음 {rep.referenced}건")
    if not args.no_build:
        return _cmd_build(args)
    print("\n다음: wikilens build")
    return 0


def _cmd_build(args) -> int:
    rep = build(Path(args.root))
    changed = rep.pages_written + rep.structures_written
    # 안 바뀐 파일은 안 쓴다. 그래서 "변경 없음" 이 정상이고, 그것 자체가 빌드
    # 멱등성이 지켜졌다는 신호다 — 같은 입력으로 두 번 돌리면 두 번째는 0 이어야 한다.
    print(
        f"빌드 완료: 파싱 {rep.parsed} · 기록 {changed}"
        + (" (변경 없음 — 멱등)" if changed == 0 and rep.parsed else "")
    )
    print(
        f"링크 {rep.total_links}개 중 {rep.resolved_links}개 해석 "
        f"({rep.resolution_rate*100:.1f}%)"
    )
    print(f"별칭 보유 {rep.targets_with_anchors} · 고아 후보 {rep.orphans}")
    root = Path(args.root)
    print(f"\n  {layout.aliases_path(root)}")
    print(f"  {layout.anchors_path(root)}")
    print(f"  {layout.tree_path(root)}")
    if rep.resolution_rate < 0.7 and rep.total_links:
        print(
            "\n주의: 링크 해석률이 낮습니다. 링크 대상 스페이스가 싱크 범위 밖일 수 있습니다."
        )
    return 0


def _cmd_acl(args) -> int:
    """
    권한을 수집한다. **콘텐츠 싱크와 분리돼 있다** — 권한 변경은 `lastModified` 를
    건드리지 않아 증분 sync 가 영영 못 잡는다. 더 자주 돌려야 하는 이유다.
    """
    from .acl import collect
    from .sync import client_from_env

    rep = collect(Path(args.root), client_from_env(), verbose=args.verbose, sleep_s=args.sleep)
    print(f"권한 수집 완료: 페이지 {rep.pages} · 제한 있음 {rep.restricted} "
          f"(그중 상속 {rep.inherited}) · 실패 {rep.failed} · 미확정 {rep.unresolved} "
          f"· {rep.elapsed_s:.1f}초")
    print(f"토큰 {len(rep.tokens)}종")
    for t in sorted(rep.tokens)[:20]:
        print(f"  {t}")
    if len(rep.tokens) > 20:
        print(f"  ... 외 {len(rep.tokens)-20}종")
    if rep.failed:
        # **공개로 바뀌지 않는다** — 실패분은 이전 값을 유지한다. 다만 새 페이지는
        # 아예 빠지므로 서버가 그 페이지를 못 보게 된다(fail-closed).
        print(f"\n주의: {rep.failed}건을 조회하지 못했습니다. 그 페이지는 이전 권한을 "
              f"유지하며, 처음 보는 페이지라면 **아무에게도 안 보입니다.**")
    if rep.unresolved:
        # 실패 한 건이 그 아래 가지 전체를 미확정으로 만든다(조상 깊이 중앙값 5).
        # 실패 개수만 보면 영향 범위를 못 읽으므로 따로 말한다.
        print(f"주의: {rep.unresolved}건은 **조상을 못 읽어** 권한을 확정하지 "
              f"못했습니다. 다시 돌리면 대개 해소됩니다.")
    print("\n다음: 서버에서 POST /api/admin/reindex (권한이 색인에 반영됩니다)")
    return 1 if (rep.failed or rep.unresolved) else 0


def _cmd_stats(args) -> int:
    from .tokenizer import tokenize

    root = Path(args.root)
    p = layout.anchors_path(root)
    if not p.exists():
        print("derived/anchors.jsonl 이 없습니다. 먼저 wikilens build 를 실행하세요.")
        return 1

    entries = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l]
    total = len(entries)
    if not total:
        print("derived/anchors.jsonl 이 비어 있습니다. 싱크된 페이지가 없는지 확인하세요.")
        return 1

    with_alias = [e for e in entries if e["indeg"] > 0]
    orphans = total - len(with_alias)
    indegs = sorted((e["indeg"] for e in entries), reverse=True)

    print(f"페이지 {total}개")
    print(f"  별칭 보유 {len(with_alias)} ({100*len(with_alias)/total:.0f}%)")
    print(f"  고아 후보 {orphans} ({100*orphans/total:.0f}%)")
    if indegs:
        print(f"  인링크 최대 {indegs[0]} · 중앙값 {indegs[len(indegs)//2]}")

    # 어휘 격차: 앵커와 제목이 토큰을 하나도 안 공유하는 페이지 비율.
    # 단순 문자열 불일치(대소문자·조사)는 근사 토크나이저가 흡수하므로 격차가 아니다 —
    # 제목만으로는 도저히 못 찾을 경우만 센다.
    #
    # `tokenizer.py` 는 **두 판 어느 쪽의 실제 검색 토크나이저도 아니다**(서버는 Nori,
    # 로컬은 ripgrep). 재려는 것이 "이 코퍼스에 어휘 격차가 있는가"라는 도입 판단이라
    # 판과 무관해야 같은 수가 나온다 — 자세한 근거는 그 파일 독스트링에.
    #
    # 빈 토큰(너무 짧은 앵커 등)은 신호가 없으므로 격차로 세지 않는다.
    def _has_gap(e: dict) -> bool:
        title_tokens = set(tokenize(e["title"]))
        if not title_tokens:
            return False  # 제목 자체가 토큰화 안 되면 "겹침 없음"을 판단할 신호가 없다
        return any(
            (at := set(tokenize(a["text"]))) and title_tokens.isdisjoint(at)
            for a in e["anchors"]
        )

    gap = sum(1 for e in with_alias if _has_gap(e))
    print(f"\n제목과 어휘가 안 겹치는 별칭을 가진 페이지: {gap} ({100*gap/total:.0f}%)")
    print("  이 비율이 낮으면 어휘 격차가 없다는 뜻이고, 이 도구의 효용도 낮습니다.")

    print("\n인링크 상위:")
    for e in sorted(entries, key=lambda x: -x["indeg"])[:10]:
        # 위 gap 판정(토큰 비교)과 다르게 여기는 문자열 완전 비교다. 사람이
        # 눈으로 훑는 목록이라 대소문자·띄어쓰기만 다른 표현도 굳이 다 보여준다
        # — 통계는 "검색 가능한가"를, 이 목록은 "실제로 뭐라고 불리는가"를 답한다.
        terms = sorted({a["text"] for a in e["anchors"] if a["text"] != e["title"]})
        extra = f" — {' · '.join(terms[:4])}" if terms else ""
        print(f"  {e['indeg']:>3}  {e['title']}{extra}")
    return 0


def _add_common(parser) -> None:
    """
    최상위 플래그(`--root`·`-v`)를 서브커맨드 **뒤에도** 허용한다.

    최상위 파서에만 두면 `wikilens sync --root ~/wiki` 가 파싱 에러다. 자연스러운 순서가
    거부되는 것이라 문서 세 곳이 "반드시 서브커맨드 앞에" 를 경고하고 있었는데,
    경고를 복제하는 것보다 양쪽을 받는 편이 싸다.

    `SUPPRESS` 가 필수다 — 기본값을 두면 서브파서가 최상위에서 이미 받은 값을
    **덮어써서** 앞에 준 `--root` 가 조용히 무시된다.
    """
    parser.add_argument("--root", default=argparse.SUPPRESS,
                        help="볼트 루트 (최상위에도 줄 수 있습니다)")
    # `-v` 도 같은 함정이었다 — `--root` 만 고치고 남겨뒀더니 `acl -v` 가 죽었다(실측).
    # 최상위 플래그는 전부 같은 규칙이어야 한다.
    parser.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="자세한 진행 출력 (최상위에도 줄 수 있습니다)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="wikilens",
        description="Confluence 위키를 로컬 마크다운으로 미러링하고 별칭 색인을 만듭니다.",
    )
    p.add_argument("--root", default=".", help="볼트 루트 (기본: 현재 디렉터리). "
                                               "서브커맨드 앞뒤 어디에 와도 됩니다")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="Confluence에서 원본을 받아옵니다")
    s.add_argument("--space", action="append", required=True, help="스페이스 키 (반복 가능)")
    s.add_argument("--full", action="store_true", help="전체 재싱크 + 삭제 감지")
    s.add_argument("--no-build", action="store_true", help="싱크만 하고 빌드는 생략")
    s.add_argument("--skip-check", action="store_true", help="사전 연결 확인 생략")
    s.add_argument(
        "--follow-refs", action="store_true",
        help="지정 스페이스 밖을 가리키는 링크를 낱개로(스페이스 전체 아님) 따라가 받습니다",
    )
    s.set_defaults(func=_cmd_sync)

    dr = sub.add_parser("doctor", help="연결·인증·권한을 확인합니다")
    dr.set_defaults(func=_cmd_doctor)

    b = sub.add_parser("build", help="파싱하고 별칭 색인을 생성합니다")
    b.set_defaults(func=_cmd_build)

    st = sub.add_parser("stats", help="볼트 통계 — 어휘 격차와 고아 문서")
    st.set_defaults(func=_cmd_stats)

    ac = sub.add_parser("acl", help="페이지별 읽기 권한을 수집합니다 (sync 와 별도 주기)")
    ac.add_argument("--sleep", type=float, default=0.0,
                    help="요청 사이 대기(초). Confluence 부하를 낮춥니다")
    ac.set_defaults(func=_cmd_acl)

    for parser in (s, dr, b, st, ac):
        _add_common(parser)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
