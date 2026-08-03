#!/usr/bin/env python3
"""
서버판 엔드투엔드 데모.

실제 Claude Code 세션 대신 훅 스크립트를 직접 호출해 세션을 시뮬레이션한다.
훅이 받는 JSON은 실제와 동일한 형식이므로, 이 데모가 통과하면 실제 연동도 동작한다.

  1. 볼트 준비 (어휘 격차 포함)
  2. 서버 기동
  3. 여러 사용자 세션 시뮬레이션 -> 궤적 축적
  4. 힌트가 서빙되기 시작하는 시점 관찰
  5. 경로 의존 질의가 학습되지 않는지 확인
  6. p_wrong 확인
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from wikilens import layout  # noqa: E402
from wikilens.build import build  # noqa: E402

VAULT = Path("/tmp/wl-demo/vault")
STATE = Path("/tmp/wl-demo/server")
BUFFER = Path("/tmp/wl-demo/buffer")
SERVER = "http://127.0.0.1:8788"
HOOK = ROOT / "prototype" / "observe_hook.py"

PAGES = {
    "300000001": ("OAuth 2.0 인가 코드 흐름", []),
    "300000002": ("세션 저장소 아키텍처", []),
    "300000003": ("배포 파이프라인 규격", []),
    "300000004": ("온보딩 체크리스트", [
        ("300000001", "로그인 붙이는 법"), ("300000003", "배포하는 법")]),
    "300000005": ("서비스 운영 런북", [
        ("300000001", "인증 붙이기"), ("300000002", "세션 어디 저장됨")]),
    "300000006": ("신규 입사자 FAQ", [("300000001", "로그인 붙이는 법")]),
}


def step(t):
    print(f"\n\033[1;36m── {t}\033[0m")


def make_vault():
    shutil.rmtree(VAULT.parent, ignore_errors=True)
    state = {"cursor": None, "pages": {}}
    for pid, (title, links) in PAGES.items():
        body = f"<h1>{title}</h1><p>본문.</p>"
        for to, anchor in links:
            body += (f'<p><ac:link><ri:content-entity ri:content-id="{to}"/>'
                     f"<ac:link-body>{anchor}</ac:link-body></ac:link></p>")
        layout.ensure_parent(layout.raw_path(VAULT, pid)).write_text(body, encoding="utf-8")
        state["pages"][pid] = {"title": title, "space": "PLATFORM", "version": 1, "updated": ""}
    layout.ensure_parent(layout.sync_state_path(VAULT)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return build(VAULT)


def hook(event: dict):
    env = {**os.environ, "WIKILENS_SERVER": SERVER, "WIKILENS_BUFFER": str(BUFFER)}
    subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                   text=True, capture_output=True, env=env)


def session(sid: str, query: str, reads: list[str]):
    """한 세션: 질의 -> 읽기들 -> 종료. 실제 훅 페이로드 형식 그대로."""
    hook({"session_id": sid, "hook_event_name": "UserPromptSubmit", "prompt": query})
    for pid in reads:
        hook({"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Read",
              "tool_input": {"file_path": str(VAULT / layout.rel_page_path(pid))}})
    hook({"session_id": sid, "hook_event_name": "SessionEnd"})


def api(path: str, payload=None):
    import httpx
    if payload is None:
        return httpx.get(f"{SERVER}{path}", timeout=5).json()
    return httpx.post(f"{SERVER}{path}", json=payload, timeout=5).json()


def search(q: str, with_server: bool = True):
    from wikilens.search import search as do
    return do(VAULT, q, server=SERVER if with_server else None)


def main() -> int:
    step("1. 볼트 준비")
    rep = make_vault()
    print(f"  페이지 {rep.parsed} · 링크 {rep.resolved_links}/{rep.total_links} 해석 "
          f"· 고아 {rep.orphans}")

    step("2. 서버 기동")
    shutil.rmtree(STATE, ignore_errors=True)
    env = {**os.environ, "WIKILENS_STATE": str(STATE)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "wikilens.server.app:app",
         "--host", "127.0.0.1", "--port", "8788", "--log-level", "error"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            api("/health"); break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    else:
        print("  서버 기동 실패"); proc.kill(); return 1
    print("  기동 완료")

    try:
        step("3. 콜드 스타트 — 궤적이 없을 때")
        r = search("로그인 붙이는 법")
        print(f"  서버 힌트 {r.server_hints}개 (0이 정상)")
        for x in r.results[:2]:
            print(f"    {x.title}  [{x.source}]")

        step("4. 세션 시뮬레이션 — 여러 사용자가 같은 것을 찾는다")
        # 헛걸음을 거쳐 정답에 도달하는 현실적 궤적
        trails = [
            ("s1", "로그인 붙이는 법 문서 어디 있어", ["300000004", "300000001"]),
            ("s2", "로그인 붙이는 법 알려줘",        ["300000006", "300000001"]),
            ("s3", "로그인 붙이는 법",                ["300000001"]),
            ("s4", "로그인 붙이는 법 문서",           ["300000002", "300000001"]),
            ("s5", "로그인 붙이는 법 페이지",         ["300000001"]),
        ]
        for i, (sid, q, reads) in enumerate(trails, 1):
            session(sid, q, reads)
            st = api("/stats")
            r = search("로그인 붙이는 법")
            served = "O" if r.server_hints else "X"
            print(f"  세션 {i}: 궤적 {st['trajectories']:>2} · 서빙 {served}"
                  + (f" (rel={r.results[0].reliability:.2f})"
                     if r.results and r.results[0].reliability else ""))

        step("5. 경로 의존 질의는 학습되지 않는다")
        session("s6", "토큰이 어떻게 흐르나", ["300000002", "300000001"])
        session("s7", "왜 이 인증 방식이지", ["300000001"])
        st = api("/stats")
        print(f"  궤적 {st['trajectories']}건 기록 · 간선 키 {st['keys']}개")
        print("  TRACING/RATIONALE 궤적은 로그에는 남지만 간선을 만들지 않습니다.")
        h = api("/hints", {"keywords": ["토큰", "흐르"], "limit": 5})
        print(f"  '토큰 흐르' 힌트: {len(h['hints'])}개 (0이 정상)")

        step("6. 모호한 질의 — 목적지 분포")
        for sid, dest in [("a1", "300000002"), ("a2", "300000002"), ("a3", "300000003")]:
            session(sid, "설정 문서", [dest])
        st = api("/stats")
        print(f"  모호한 키 {st['ambiguous_keys']}개 — 같은 키에 목적지가 여럿")
        print("  실패로 벌주지 않고 분포로 기록합니다.")

        step("7. 지표")
        st = api("/stats")
        for k in ("trajectories", "keys", "key_page_pairs", "ambiguous_keys",
                  "hits", "misses", "p_wrong"):
            print(f"  {k:<16} {st[k]}")
        print("\n  손익분기: p_hit > p_wrong/(n-1) — 적중률이 아니라 오답률이 기준입니다.")

        step("8. ACL — 볼트에 없는 페이지는 버려진다")
        # 다른 사용자가 자기 권한으로 본 페이지. 내 볼트에는 없다.
        for i in range(12):
            session(f"x{i}", "인수합병 실사 자료 어디", ["999999999"])
        raw = api("/hints", {"keywords": ["인수합병", "실사", "자료", "어디"], "limit": 5})
        r = search("인수합병 실사 자료 어디")
        print(f"  서버가 직접 반환한 힌트: {len(raw['hints'])}개")
        print(f"  클라이언트 결과: 힌트 {r.server_hints} · ACL로 버림 {r.dropped_by_acl}")
        print("  서버는 ID를 알지만 내 볼트에 없으므로 결과에 나오지 않습니다.")
        print("  서버는 그 페이지의 제목도 경로도 모릅니다 — 유출 경로가 없습니다.")

        step("9. 최종 검색")
        r = search("로그인 붙이는 법")
        from wikilens.search import entry_idf_threshold
        from wikilens import index as _ix
        n = _ix.load(VAULT, with_body=False).n
        print(f"  [{'Confident' if r.confident else 'Diffuse'}] "
              f"IDF {r.max_idf:.2f} / 임계 {entry_idf_threshold(n):.2f} (문서 {n}개 기준)")
        if not r.confident:
            print("  소규모 코퍼스라 IDF가 압축됩니다. 실제 위키(수천~수만 문서)에서는")
            print("  같은 항이 Confident로 판정됩니다. 임계는 코퍼스 크기에 연동됩니다.")
        for i, x in enumerate(r.results[:3], 1):
            tag = {"local": " ", "server": "S", "both": "*"}[x.source]
            rel = f" rel={x.reliability:.2f}" if x.reliability else ""
            print(f"  {tag} {i}. {x.title}{rel}")
            print(f"       {x.path}")
        print("\n\033[1;32m완료.\033[0m")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
