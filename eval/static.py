#!/usr/bin/env python3
"""
세 검색 방식 비교: 원시 grep · 로컬판 스킬 · 서버판 MCP.

**측정 대상은 "찾을 수 있나" 가 아니라 "정답에 닿기까지 컨텍스트에 몇 자가 들어가나" 다.**
네 질의 모두 정답 문서가 확인돼 있고, 질의어가 본문에도 있어 세 방식 다 도달 가능하다.

각 방식은 같은 종점에서 끝난다: **정답 문서의 본문이 컨텍스트에 들어온 상태.**
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time
import urllib.request

VAULT = pathlib.Path.home() / ".wikilens" / "vault"
PAGES = VAULT / "mirror" / "pages"
SERVER = "http://127.0.0.1:8787"
USER = "bench"

#: 질의 정본은 [queries.py] 하나다 — 여기 따로 두면 두 하네스가 다른 시험지를 푼다.
#: 예전에는 이 파일이 자기 질의 4개를 들고 있었다.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from queries import GROUPS  # noqa: E402

#: (질의, 정답 pageId, 성격) 으로 편다. 그룹 안 세 변형이 각각 한 측정이다.
QUERIES = [
    (q, gold, f"{name} {'abc'[i]}")
    for name, gold, _title, qs in GROUPS
    for i, q in enumerate(qs)
]

#: 측정 반복. **워밍이 이보다 중요하다** — 처음 이 값이 3 이고 워밍이 1회였을 때
#: 서버를 3~4배 느리게 쟀다(JVM JIT 미가동). 아래 `main` 이 전 질의로 3회 워밍한다.
RUNS = 7


def sh(args: list[str]) -> tuple[str, float]:
    t = time.perf_counter()
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
    return r.stdout, time.perf_counter() - t


def post(path: str, payload: dict) -> tuple[dict, float]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(SERVER + path, data=body,
                                 headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read() or b"{}")
    return out, time.perf_counter() - t


def page_path(pid: str) -> pathlib.Path | None:
    return next(PAGES.rglob(f"{pid}.md"), None)


def body_chars(pid: str) -> int:
    p = page_path(pid)
    return len(p.read_text(encoding="utf-8", errors="replace")) if p else 0


def terms(q: str) -> list[str]:
    return [t for t in q.replace("/", " ").split() if len(t) > 1]


# ---------------------------------------------------------------- 세 방식

def raw_grep(q: str, gold: str) -> dict:
    """
    에이전트가 스스로 판단해 볼트를 grep. 안내가 없으므로 가장 변별력 있어 보이는
    낱말부터 훑고, 너무 많으면 조인다 — 실제로 하는 행동을 흉내낸다.
    """
    chars, calls, elapsed = 0, 0, 0.0
    ts = terms(q)
    # ① 첫 낱말로 파일 목록
    out, dt = sh(["rg", "-l", "-i", "--", ts[0], str(PAGES)])
    chars += len(out); calls += 1; elapsed += dt
    hits = out.split()
    # ② 너무 많으면 낱말을 AND 로 조인다 (한 줄 안에서 매칭되므로 순서 양쪽)
    if len(hits) > 20 and len(ts) > 1:
        pat = f"{ts[0]}.*{ts[1]}|{ts[1]}.*{ts[0]}"
        out2, dt2 = sh(["rg", "-l", "-i", "--", pat, str(PAGES)])
        chars += len(out2); calls += 1; elapsed += dt2
        hits = out2.split() or hits
    # ③ 상위 후보를 읽어 확인 — 정답이 몇 번째인지 모르므로 순서대로 연다
    ranked = sorted(hits)
    idx = next((i for i, h in enumerate(ranked) if gold in h), None)
    opened = ranked[: (idx + 1 if idx is not None else min(len(ranked), 5))]
    for f in opened:
        chars += len(pathlib.Path(f).read_text(encoding="utf-8", errors="replace"))
        calls += 1
    return {"chars": chars, "calls": calls, "sec": elapsed,
            "found": idx is not None, "candidates": len(hits), "opened": len(opened)}


def local_plugin(q: str, gold: str) -> dict:
    """로컬판 스킬: ALIASES.md 먼저, 없으면 TREE.md, 그래도 없으면 본문."""
    chars, calls, elapsed = 0, 0, 0.0
    ts = terms(q)
    pat = f"{ts[0]}.*{ts[1]}|{ts[1]}.*{ts[0]}" if len(ts) > 1 else ts[0]
    out, dt = sh(["rg", "-i", "--", pat, str(VAULT / "ALIASES.md")])
    chars += len(out); calls += 1; elapsed += dt
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:                                   # 2단계 TREE.md
        out, dt = sh(["rg", "-i", "--", pat, str(VAULT / "TREE.md")])
        chars += len(out); calls += 1; elapsed += dt
        lines = [l for l in out.splitlines() if l.strip()]
    if not lines:                                   # 3단계 본문
        out, dt = sh(["rg", "-l", "-i", "--", pat, str(PAGES)])
        chars += len(out); calls += 1; elapsed += dt
        lines = out.splitlines()
    idx = next((i for i, l in enumerate(lines) if gold in l), None)
    # **정답 읽기도 시간에 넣는다.** 예전에는 문자 수만 세고 시간은 안 쟀는데,
    # 서버판은 `read` 왕복이 시간에 잡혀서 **한쪽만 유리했다.**
    # 서브프로세스가 아니라 직접 읽는다 — Claude Code 의 `Read` 도구에 해당한다.
    t0 = time.perf_counter()
    chars += body_chars(gold); calls += 1
    elapsed += time.perf_counter() - t0
    return {"chars": chars, "calls": calls, "sec": elapsed,
            "found": idx is not None, "candidates": len(lines),
            "rank": (idx + 1) if idx is not None else -1}


def server_plugin(q: str, gold: str) -> dict:
    """서버판: search 한 번 + read 한 번."""
    res, dt = post("/api/search", {"query": q, "userKey": USER, "limit": 10})
    chars = len(json.dumps(res, ensure_ascii=False))
    calls, elapsed = 1, dt
    ids = [h["pageId"] for h in res.get("hits", [])]
    idx = ids.index(gold) if gold in ids else None
    rd, dt2 = post("/api/read", {"pageId": gold, "userKey": USER})
    chars += len(rd.get("markdown", "")); calls += 1; elapsed += dt2
    return {"chars": chars, "calls": calls, "sec": elapsed,
            "found": idx is not None, "candidates": len(ids),
            "rank": (idx + 1) if idx is not None else -1}


# ---------------------------------------------------------------- 실행

def main() -> None:
    methods = [("원시 grep", raw_grep), ("로컬판", local_plugin), ("서버판", server_plugin)]
    for q, gold, kind in QUERIES:
        print(f"\n질의: {q!r}   정답 {gold}")
        print(f"  ({kind})")
        for name, fn in methods:
            for _ in range(3):
                fn(q, gold)                          # 워밍 — 1회로는 JIT 이 안 올라온다
            best = min((fn(q, gold) for _ in range(RUNS)), key=lambda r: r["sec"])
            mark = "○" if best["found"] else "✗"
            rank = best.get("rank", best.get("opened", 0))
            print(f"    {name:9} {mark} {best['chars']:>8,}자 · {best['calls']}회 · "
                  f"{best['sec']*1000:>7.0f}ms · 후보 {best['candidates']:>5,} · 순위 {rank}")


if __name__ == "__main__":
    main()
