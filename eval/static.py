#!/usr/bin/env python3
"""
프로세스 없이 재는 쪽 — 순위 · 문자 수 · 지연.

`rg` 와 HTTP 를 직접 부른다. 싸고 빠르고 결정적이라 반복 측정에 맞는다. 대신 **에이전트가
실제로 어떻게 행동하는지는 모른다** — 후보를 몇 개 열어볼지, 실패하면 어떻게 재시도할지를
코드로 흉내낸 값이다. 그쪽은 `agent.py` 가 잰다.

    python3 eval/static.py --reps 5

측정 대상은 "찾을 수 있나" 가 아니라 **정답에 닿기까지 컨텍스트에 몇 자가 들어가나** 다.
세 방식 모두 같은 종점에서 끝난다 — 정답 문서의 본문이 컨텍스트에 들어온 상태.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
VAULT = pathlib.Path.home() / ".wikilens" / "vault"
PAGES = VAULT / "mirror" / "pages"
SERVER = "http://127.0.0.1:8787"
USER = "eval"

sys.path.insert(0, str(HERE))
from harness import Writer, make  # noqa: E402
from queries import GROUPS  # noqa: E402

#: 모델이 질의에서 버리는 말. **이게 없으면 로컬판에 불공정하다** — 구어체는 앞 두
#: 낱말이 `화면에서`·`어떻게` 같은 군더더기라, 그대로 패턴에 쓰면 못 찾는다(실측: 4/30
#: → 6/30). 스킬이 "여러 단어로 물으면 그대로 grep 하지 마세요" 라고 가르치는 단계다.
STOP = set("""어떻게 어디 어디서 어디에 어디로 어디지 뭐 무엇 무슨 있어 있나 있지 있는
있었는데 정리된 관련해서 관련 대해 대한 그 이 저 좀 해 봐야 보고싶어 보고싶은데 알고싶어
찾아줘 알 수 거 것 등 및 하는 하기로 했지 한다는 되는지 되는 타는지 쌓이는지 돌아가는지
나눠 쪼개는 들어가야 받지 보려면 언제 왜 방법 문서 자료 내용 목록 같은 이슈 사전 조사한
검토한 처리해 접수 그거 이거 지난 지금 이번 저번 이랑 관해""".split())


def picks(q: str) -> list[str]:
    ws = [w.strip("?,.·") for w in q.replace("?", " ").split()]
    return [w for w in ws if len(w) > 1 and w not in STOP][:2]


def pattern(ts: list[str]) -> str:
    """한 줄 안에서 매칭되므로 순서를 양쪽으로 준다(스킬이 가르치는 형태)."""
    return f"{ts[0]}.*{ts[1]}|{ts[1]}.*{ts[0]}" if len(ts) > 1 else ts[0]


def sh(args: list[str]) -> tuple[str, float]:
    t = time.perf_counter()
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
    return r.stdout, time.perf_counter() - t


def post(path: str, payload: dict) -> tuple[dict, float]:
    req = urllib.request.Request(SERVER + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}"), time.perf_counter() - t


#: 경로·ALIASES 줄에서 페이지 ID 를 뽑는다. **못 찾았을 때 무엇을 대신 골랐는지**를
#: 기록하려는 것이고, 그 값이 랭킹 진단의 단서다(G01 의 길이 정규화가 그렇게 드러났다).
_PID = re.compile(r"/(\d{4,})\.md")


def top_pid(s: str) -> str:
    m = _PID.search(s)
    return m.group(1) if m else ""


def gold_file(pid: str) -> pathlib.Path | None:
    return next(PAGES.rglob(f"{pid}.md"), None)


def read_gold(pid: str) -> tuple[int, float]:
    """정답 읽기. **시간에 넣는다** — 예전에는 로컬판만 안 넣어서 한쪽이 유리했다."""
    f = gold_file(pid)
    if not f:
        return 0, 0.0
    t = time.perf_counter()
    n = len(f.read_text(encoding="utf-8", errors="replace"))
    return n, time.perf_counter() - t


# ---------------------------------------------------------------- 세 방식

def case_a(q: str, gold: str) -> dict:
    """
    안내 없이 본문을 훑고 후보를 순서대로 열어본다. **힌트 파일을 안 쓴다.**

    **정답 위치를 아는 채로 거기까지만 연다 — 오라클이다.** 실제 에이전트는 어디
    있는지 모르므로 더 열거나(못 알아보고 지나침) 덜 열 수 있다(먼저 포기). 그래서
    이 값은 "이 순서로 훑을 때 정답에 닿는 최소 비용" 이지 실사용 예측이 아니다.
    실사용은 `agent.py` 가 잰다 — **둘의 차이가 곧 에이전트의 판단 비용**이다.

    후보 정렬이 파일명 순인 것도 같은 성격이다. 원시 grep 에는 랭킹이 없으므로
    임의 순서가 맞는 모델이지만, 운이 좋으면 앞에 오고 나쁘면 뒤에 온다.
    """
    ts = picks(q) or [q.split()[0]]
    out, dt = sh(["rg", "-l", "-i", "--", ts[0], str(PAGES)])
    chars, calls, sec = len(out), 1, dt
    hits = out.split()
    if len(hits) > 20 and len(ts) > 1:
        out2, dt2 = sh(["rg", "-l", "-i", "--", pattern(ts), str(PAGES)])
        chars += len(out2); calls += 1; sec += dt2
        hits = out2.split() or hits
    ranked = sorted(hits)
    idx = next((i for i, h in enumerate(ranked) if gold in h), None)
    opened = ranked[: (idx + 1 if idx is not None else min(len(ranked), 5))]
    for f in opened:
        t = time.perf_counter()
        chars += len(pathlib.Path(f).read_text(encoding="utf-8", errors="replace"))
        sec += time.perf_counter() - t
        calls += 1
    return {"hit": idx is not None, "rank": (idx + 1) if idx is not None else -1,
            "chars": chars, "calls": calls, "seconds": sec,
            "answer": gold if idx is not None else (top_pid(ranked[0]) if ranked else "none"),
            "extra": {"candidates": len(hits), "opened": len(opened)}}


def case_b(q: str, gold: str) -> dict:
    """로컬판 스킬 절차: ALIASES → TREE → 본문. **어느 단계에서 찾았는지 기록한다.**"""
    ts = picks(q)
    if not ts:
        return {"hit": False, "rank": -1, "chars": 0, "calls": 0, "seconds": 0.0}
    pat = pattern(ts)
    chars = calls = 0
    sec = 0.0
    last_first = ""      # 못 찾았을 때 "대신 무엇이 1순위였나"
    for stage, path, flags in (("ALIASES", VAULT / "ALIASES.md", ()),
                               ("TREE", VAULT / "TREE.md", ()),
                               ("BODY", PAGES, ("-l",))):
        out, dt = sh(["rg", "-i", *flags, "--", pat, str(path)])
        chars += len(out); calls += 1; sec += dt
        lines = [ln for ln in out.splitlines() if ln.strip()]
        if lines and not last_first:
            last_first = lines[0]
        for i, ln in enumerate(lines, 1):
            if gold in ln:
                n, dt2 = read_gold(gold)
                return {"hit": True, "rank": i, "chars": chars + n,
                        "calls": calls + 1, "seconds": sec + dt2, "answer": gold,
                        "extra": {"stage": stage, "candidates": len(lines)}}
    return {"hit": False, "rank": -1, "chars": chars, "calls": calls, "seconds": sec,
            "answer": top_pid(last_first) if last_first else "none",
            "extra": {"stage": "none"}}


def case_c(q: str, gold: str) -> dict:
    """서버판: search 한 번 + read 한 번."""
    res, dt = post("/api/search", {"query": q, "userKey": USER, "limit": 10})
    chars = len(json.dumps(res, ensure_ascii=False))
    ids = [h["pageId"] for h in res.get("hits", [])]
    idx = ids.index(gold) if gold in ids else None
    rd, dt2 = post("/api/read", {"pageId": gold, "userKey": USER})
    return {"hit": idx is not None, "rank": (idx + 1) if idx is not None else -1,
            "chars": chars + len(rd.get("markdown", "")), "calls": 2,
            "seconds": dt + dt2,
            "answer": gold if idx is not None else (ids[0] if ids else "none"),
            "extra": {"candidates": len(ids)}}


CASES = [("A 원시grep", case_a), ("B 로컬판", case_b), ("C 서버판", case_c)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", nargs="*", default=None)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default="static.jsonl")
    a = ap.parse_args()

    groups = [g for g in GROUPS
              if a.groups is None or any(g[0].startswith(p) for p in a.groups)]
    out = HERE / "results" / a.out
    # 정적 측정은 싸므로 이어받기 없이 다시 돈다. **다만 `--groups` 를 줬을 때
    # 파일을 통째로 지우면 안 된다** — 다른 그룹을 앞서 잰 결과가 조용히 사라진다.
    # 이번에 다시 잴 그룹의 줄만 걷어내고 나머지는 남긴다.
    if out.exists():
        names = {g[0] for g in groups}
        kept = [ln for ln in out.read_text(encoding="utf-8").splitlines()
                if ln.strip() and json.loads(ln).get("group") not in names]
        out.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")

    # **워밍이 반복보다 중요하다.** 1회만 워밍했을 때 서버를 3~4배 느리게 쟀다(JIT).
    for _ in range(3):
        for _n, gold, _t, qs in groups:
            for q in qs:
                for _c, fn in CASES:
                    fn(q, gold)

    with Writer(out) as w:
        for name, gold, _title, queries in groups:
            print(f"\n{name}  정답 {gold}")
            for qi, q in enumerate(queries):
                print(f"  q{qi} {q[:52]}")
                for cname, fn in CASES:
                    best = None
                    for rep in range(a.reps):
                        r = fn(q, gold)
                        rec = make(harness="static", case=cname, group=name, qi=qi,
                                     query=q, gold=gold, rep=rep, **r)
                        w.write(rec)
                        if best is None or rec.seconds < best.seconds:
                            best = rec
                    print(f"    {cname:10} {'○' if best.hit else '✗'} "
                          f"{best.chars:>8,}자 · {best.calls}회 · "
                          f"{best.seconds*1000:>6.0f}ms · 순위 {best.rank}"
                          + (f" · {best.extra.get('stage')}" if best.extra.get("stage") else ""))
    print(f"\n  결과 {out} — 표는 `python3 eval/report.py` 로")
    return 0


if __name__ == "__main__":
    sys.exit(main())
