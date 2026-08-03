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
        print(f"준비 완료. 다음: wikilens --root ~/wiki sync --space {key}")
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
    rep = build(Path(args.root), verbose=args.verbose)
    print(
        f"빌드 완료: 파싱 {rep.parsed} · 페이지 {rep.pages_written} · "
        f"구조 {rep.structures_written}"
    )
    print(
        f"링크 {rep.total_links}개 중 {rep.resolved_links}개 해석 "
        f"({rep.resolution_rate*100:.1f}%)"
    )
    print(f"별칭 보유 {rep.targets_with_anchors} · 고아 후보 {rep.orphans}")
    root = Path(args.root)
    print(f"\n  {layout.aliases_path(root)}")
    print(f"  {layout.anchors_path(root)}")
    if rep.resolution_rate < 0.7 and rep.total_links:
        print(
            "\n주의: 링크 해석률이 낮습니다. 링크 대상 스페이스가 싱크 범위 밖일 수 있습니다."
        )
    return 0


def _cmd_stats(args) -> int:
    from .tokenizer import tokenize

    root = Path(args.root)
    p = layout.anchors_path(root)
    if not p.exists():
        print("derived/anchors.jsonl 이 없습니다. 먼저 wikilens build 를 실행하세요.")
        return 1

    entries = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l]
    total = len(entries)
    with_alias = [e for e in entries if e["indeg"] > 0]
    orphans = total - len(with_alias)
    indegs = sorted((e["indeg"] for e in entries), reverse=True)

    print(f"페이지 {total}개")
    print(f"  별칭 보유 {len(with_alias)} ({100*len(with_alias)/total:.0f}%)")
    print(f"  고아 후보 {orphans} ({100*orphans/total:.0f}%)")
    if indegs:
        print(f"  인링크 최대 {indegs[0]} · 중앙값 {indegs[len(indegs)//2]}")

    # 어휘 격차: 앵커와 제목이 검색 토큰을 하나도 안 공유하는 페이지 비율.
    # 단순 문자열 불일치(대소문자·띄어쓰기·어순)는 검색 토크나이저가 이미
    # 흡수하므로 격차가 아니다 — 실제 검색이 제목만으로 못 찾는 경우만 센다.
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


def _cmd_serve(args) -> int:
    import os
    os.environ.setdefault("WIKILENS_STATE", args.state)
    import uvicorn
    print(f"WikiLens 서버 · 상태 {args.state} · http://{args.host}:{args.port}")
    print("서버는 페이지 ID와 키워드만 저장합니다. 콘텐츠·제목·경로는 받지 않습니다.")
    uvicorn.run("wikilens.server.app:app", host=args.host, port=args.port, log_level="warning")
    return 0


def _cmd_search(args) -> int:
    from .search import search

    rep = search(Path(args.root), args.query, server=args.server, limit=args.limit)
    if not rep.terms:
        print("질의에서 토큰을 추출하지 못했습니다.")
        return 1

    flag = "Confident" if rep.confident else "Diffuse"
    print(f"[{flag}] 최대 IDF {rep.max_idf:.2f} · 로컬 후보 {rep.local_candidates} "
          f"· 서버 힌트 {rep.server_hints}"
          + (f" · ACL로 버림 {rep.dropped_by_acl}" if rep.dropped_by_acl else ""))
    if not rep.confident:
        print("  질의어가 흔한 토큰뿐입니다. 어휘 경로가 실패했을 수 있습니다.")
    print()
    for i, r in enumerate(rep.results, 1):
        tag = {"local": " ", "server": "S", "both": "*"}[r.source]
        rel = f" rel={r.reliability:.2f}" if r.reliability is not None else ""
        print(f" {tag} {i}. {r.title}{rel}")
        print(f"      {r.path}")
    if any(r.source in ("both", "server") for r in rep.results):
        print("\n  * = 로컬과 서버 양쪽, S = 서버 힌트만")
    return 0


def _cmd_hook(args) -> int:
    """플러그인 훅이 없을 때 수동 관측용. 훅 스크립트와 같은 입력을 받는다."""
    import subprocess
    from pathlib import Path as _P
    script = _P(__file__).parent.parent / "plugin" / "hooks" / "observe.py"
    return subprocess.run([sys.executable, str(script)], stdin=sys.stdin).returncode


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="wikilens",
        description="Confluence 위키를 로컬 마크다운으로 미러링하고 별칭 색인을 만듭니다.",
    )
    p.add_argument("--root", default=".", help="볼트 루트 (기본: 현재 디렉터리)")
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

    sv = sub.add_parser("serve", help="공유 서버를 띄웁니다 (서버판)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)
    sv.add_argument("--state", default="./.wikilens-server")
    sv.set_defaults(func=_cmd_serve)

    sc = sub.add_parser("search", help="로컬 랭킹 + 서버 힌트로 검색합니다")
    sc.add_argument("query")
    sc.add_argument("--server", default=None, help="예: http://127.0.0.1:8787")
    sc.add_argument("--limit", type=int, default=8)
    sc.set_defaults(func=_cmd_search)

    hk = sub.add_parser("hook", help="훅 이벤트를 stdin JSON으로 처리합니다")
    hk.set_defaults(func=_cmd_hook)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
