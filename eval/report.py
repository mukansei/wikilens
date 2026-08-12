#!/usr/bin/env python3
"""
`results/*.jsonl` → 표.

**수치를 손으로 옮기지 않는다.** 앞서 결과를 문서에 적었더니 질의를 바꾼 순간 그 문서가
조용히 낡았다(질의 4개가 전부 교체됐는데 문서는 옛 값을 말하고 있었다).

**두 하네스를 나란히 놓지 않는다.** 재는 축이 다르다 — `rank.py` 는 순위,
`agent.py` 는 실제 토큰·비용이다. 같은 표 모양으로 내면 4자리 차이 나는 값을
비교로 읽게 된다(예전에 그렇게 만들었다).
"""
from __future__ import annotations

import argparse
import sys
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harness import RESULTS, load, overlaps, summarize, too_few  # noqa: E402

CASE_ORDER = ["A 원시grep", "B 로컬판", "C 서버판"]


def collect() -> list:
    """
    같은 조합은 **마지막 것만** 센다. `--no-resume` 로 다시 돌리거나 실패 후 재시도하면
    같은 키가 여러 줄이 되는데, 그대로 세면 한 측정이 두 번 들어가 중앙값이 흔들린다.
    로그는 append-only 로 두고(언제 무엇을 쟀는지가 남는다) 집계에서만 접는다.
    """
    rows = []
    for f in sorted(RESULTS.glob("*.jsonl")):
        rows += load(f)
    latest = {}
    for r in rows:
        if not r.warmup and not r.error:
            latest[r.key()] = r
    return list(latest.values())


def rank_table(rows: list) -> None:
    """순위 — 랭커가 있는 둘만. 원시 grep 은 랭커가 없어 대상이 아니다."""
    rs = [r for r in rows if r.harness == "rank"]
    if not rs:
        return
    print("\n=== 순위 (정답이 몇 위에 오나) ===")
    cases = [c for c in CASE_ORDER if any(r.case == c for r in rs)]
    print("  " + "그룹".ljust(24) + "".join(c.rjust(18) for c in cases))
    for g in sorted({r.group for r in rs}):
        cells = []
        for c in cases:
            v = [r for r in rs if r.group == g and r.case == c]
            found = sorted(r.rank for r in v if r.rank > 0)
            cells.append(f"{len(found)}/{len(v)} 중앙 {found[len(found)//2]}위"
                         if found else f"0/{len(v)}  —")
        print("  " + g[:22].ljust(24) + "".join(x.rjust(18) for x in cells))


def cost_table(rows: list, md: bool) -> None:
    """비용 — 실제 세션에서 실제 토큰. 시뮬레이션이 아니다."""
    rs = [r for r in rows if r.harness == "agent"]
    if not rs:
        return
    by = {}
    for r in rs:
        by.setdefault(r.case, []).append(r)
    by = {c: by[c] for c in CASE_ORDER if c in by}

    print(f"\n## 비용 (실제 세션 {len(rs)}건)\n" if md
          else f"\n=== 비용 (실제 세션 {len(rs)}건) ===")
    head = ["케이스", "적중", "토큰 중앙값 [IQR]", "턴", "세션 시간"]
    if md:
        print("| " + " | ".join(head) + " |")
        print("|" + "|".join(["---"] * len(head)) + "|")

    dists = {}
    for case, v in by.items():
        m = summarize([float(r.tokens) for r in v])
        dists[case] = m
        cells = [case, f"{sum(r.hit for r in v)}/{len(v)}",
                 f"{m['median']:,.0f} [{m['q1']:,.0f}~{m['q3']:,.0f}]",
                 f"{summarize([float(r.turns) for r in v])['median']:.0f}",
                 f"{summarize([r.seconds for r in v])['median']:.0f}s"]
        print(("| " + " | ".join(cells) + " |") if md
              else "  " + "  ".join(c.ljust(w) for c, w in zip(cells, [11, 7, 28, 5, 9])))

    # **말할 수 없는 것을 말하지 않는다.** "표본이 모자라 모른다" 와 "겹쳐서 못 가린다"
    # 는 다른 상태다 — 뭉치면 리포트가 늘 같은 문장을 뱉어 정보가 0 이 된다.
    pairs = [(a, b) for i, a in enumerate(dists) for b in list(dists)[i + 1:]]
    for label, items in (
        ("표본 부족(<4)이라 판단 보류",
         [f"{a}↔{b}" for a, b in pairs if too_few(dists[a], dists[b])]),
        ("**IQR 이 겹쳐 우열을 주장할 수 없음**",
         [f"{a}↔{b}" for a, b in pairs
          if not too_few(dists[a], dists[b]) and overlaps(dists[a], dists[b])]),
        ("IQR 이 분리돼 차이를 말할 수 있음",
         [f"{a}↔{b}" for a, b in pairs
          if not too_few(dists[a], dists[b]) and not overlaps(dists[a], dists[b])]),
    ):
        if items:
            msg = f"{label}: " + " · ".join(items)
            print(f"\n{msg}" if md else f"\n  {msg}")
    print(f"\n총 비용 ${sum(r.cost for r in rs):.2f}" if md
          else f"  총 비용 ${sum(r.cost for r in rs):.2f}")


def wrong_answers(rows: list) -> None:
    """
    **틀렸을 때 무엇이라고 답했나.** 랭킹을 고치려면 이 열이 필요하다 — `none` 이 많으면
    검색이 못 찾은 것이고, 다른 ID 가 반복되면 **그것이 더 나은 답일 수도** 있다.
    """
    bad = [r for r in rows if not r.hit and r.answer and r.answer != "none"]
    if not bad:
        return
    seen = {}
    for r in bad:
        k = (r.group, r.case, r.answer)
        seen[k] = seen.get(k, 0) + 1
    print("\n=== 오답 (무엇과 헷갈렸나) ===")
    for (g, c, ans), n in sorted(seen.items(), key=lambda x: -x[1])[:10]:
        print(f"  {g[:20]:22} {c:11} → {ans:12} ×{n}")


def learning_curve(rows: list) -> None:
    """
    **warm 실험에서 학습이 실제로 일하나.** C 만 회차를 거듭할수록 궤적을 더 들고 있다.
    값어치를 한다면 적중이 오르고 토큰이 줄어야 한다 — 안 그러면 이 코퍼스에서 그 층은
    값이 없다는 뜻이고, 그것도 유효한 결과다.
    """
    c = [r for r in rows if r.harness == "agent" and r.case.startswith("C")
         and r.extra.get("trajectories", -1) > 0]
    if not c:
        return
    print("\n=== 학습 곡선 (warm · C 케이스) ===")
    by_rep = {}
    for r in c:
        by_rep.setdefault(r.rep, []).append(r)
    for rep in sorted(by_rep):
        v = by_rep[rep]
        traj = summarize([float(r.extra["trajectories"]) for r in v])
        tok = summarize([float(r.tokens) for r in v])
        print(f"  r{rep}: 궤적 {traj['median']:>6.0f}건 · 적중 {sum(r.hit for r in v)}/{len(v)}"
              f" · 토큰 {tok['median']:>9,.0f}")
    print("  → 회차가 갈수록 적중이 오르고 토큰이 줄면 학습이 일하는 것이다")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true")
    a = ap.parse_args()
    rows = collect()
    if not rows:
        print("  results/ 에 데이터가 없다 — rank.py 나 agent.py 를 먼저 돌린다",
              file=sys.stderr)
        return 2
    if a.md:
        print("# 측정 결과\n\n> **`eval/report.py` 가 생성한다.** 손으로 고치지 말 것.\n"
              "> 질의는 [`queries.py`](queries.py), 방법은 [`README.md`](README.md).")
    rank_table(rows)
    cost_table(rows, a.md)
    if not a.md:
        wrong_answers(rows)
        learning_curve(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
