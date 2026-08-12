#!/usr/bin/env python3
"""
`results/*.jsonl` → 표.

**수치를 손으로 옮기지 않는다.** 앞서 결과를 문서에 손으로 적었더니 질의를 바꾼 순간
그 문서가 조용히 낡았다(질의 4개가 전부 교체됐는데 문서는 옛 값을 말하고 있었다).
이제 표는 여기서만 나온다 — 데이터가 정본이다.

    python3 eval/report.py                 # results/ 전부
    python3 eval/report.py --md > eval/RESULTS.md
"""
from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harness import RESULTS, load, overlaps, summarize  # noqa: E402

CASE_ORDER = ["A 원시grep", "B 로컬판", "C 서버판"]


def collect() -> list:
    rows = []
    for f in sorted(RESULTS.glob("*.jsonl")):
        rows += load(f)
    return [r for r in rows if not r.warmup and not r.error]


def by_case(rows: list, harness: str) -> dict:
    out = {}
    for r in rows:
        if r.harness == harness:
            out.setdefault(r.case, []).append(r)
    return {c: out[c] for c in CASE_ORDER if c in out}


def fmt(s: dict, unit: str = "") -> str:
    if not s.get("n"):
        return "—"
    return f"{s['median']:,.0f}{unit} [{s['q1']:,.0f}~{s['q3']:,.0f}]"


def emit(rows: list, md: bool) -> None:
    bar = "|" if md else " "

    for harness, metric, unit in (("static", "chars", "자"), ("agent", "tokens", "tok")):
        groups = by_case(rows, harness)
        if not groups:
            continue
        title = {"static": "정적 측정 (프로세스 없이)",
                 "agent": "에이전트 측정 (독립 세션)"}[harness]
        print(f"\n## {title}\n" if md else f"\n=== {title} ===")
        n = sum(len(v) for v in groups.values())
        print(f"측정 {n}건\n" if md else f"  측정 {n}건\n")

        head = ["케이스", "적중", f"{metric} 중앙값 [IQR]", "지연 ms", "턴/호출"]
        if md:
            print("| " + " | ".join(head) + " |")
            print("|" + "|".join(["---"] * len(head)) + "|")

        dists = {}
        for case, rs in groups.items():
            hit = sum(r.hit for r in rs)
            m = summarize([getattr(r, metric) for r in rs])
            dists[case] = m
            sec = summarize([r.seconds * 1000 for r in rs])
            calls = summarize([float(r.turns or r.calls) for r in rs])
            cells = [case, f"{hit}/{len(rs)}", fmt(m, unit),
                     f"{sec.get('median',0):,.0f}", f"{calls.get('median',0):.0f}"]
            print(("| " + " | ".join(cells) + " |") if md
                  else "  " + "  ".join(c.ljust(w) for c, w in
                                        zip(cells, [11, 7, 30, 9, 7])))

        # **겹치면 우열을 말하지 않는다.** 이 한 줄이 이 리포트의 요점이다.
        pairs = [(a, b) for i, a in enumerate(dists) for b in list(dists)[i + 1:]]
        amb = [f"{a} ↔ {b}" for a, b in pairs if overlaps(dists[a], dists[b])]
        if amb:
            msg = ("**IQR 이 겹쳐 우열을 주장할 수 없는 쌍:** " + " · ".join(amb))
            print(f"\n{msg}" if md else f"\n  {msg}")

        if harness == "agent":
            cost = sum(r.cost for r in rows if r.harness == "agent")
            print(f"\n총 비용 ${cost:.2f}" if md else f"  총 비용 ${cost:.2f}")

    # 그룹별 적중 — 어느 질의가 어려운지가 다음 실행의 대상을 정한다
    print("\n## 그룹별 적중\n" if md else "\n=== 그룹별 적중 ===")
    gs = sorted({r.group for r in rows})
    cases = [c for c in CASE_ORDER if any(r.case == c for r in rows)]
    if md:
        print("| 그룹 | " + " | ".join(cases) + " |")
        print("|" + "|".join(["---"] * (len(cases) + 1)) + "|")
    for g in gs:
        cells = [g]
        for c in cases:
            rs = [r for r in rows if r.group == g and r.case == c]
            cells.append(f"{sum(r.hit for r in rs)}/{len(rs)}" if rs else "—")
        print(("| " + " | ".join(cells) + " |") if md
              else "  " + cells[0].ljust(24) + "  ".join(x.rjust(7) for x in cells[1:]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="마크다운으로")
    a = ap.parse_args()
    rows = collect()
    if not rows:
        print("  results/ 에 데이터가 없다 — static.py 나 agent.py 를 먼저 돌린다",
              file=sys.stderr)
        return 2
    if a.md:
        print("# 측정 결과\n\n> **이 파일은 `eval/report.py` 가 생성한다.** "
              "손으로 고치지 말 것 — 다시 돌리면 덮인다.\n"
              "> 질의는 [`queries.py`](queries.py), 방법은 [`README.md`](README.md).")
    emit(rows, a.md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
