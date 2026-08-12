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
from harness import RESULTS, load, overlaps, summarize, too_few  # noqa: E402

CASE_ORDER = ["A 원시grep", "B 로컬판", "C 서버판"]


def collect() -> list:
    """
    `results/` 전부를 읽되 **같은 조합은 마지막 것만 센다.**

    `--no-resume` 로 다시 돌리거나, 실패한 뒤 성공할 때까지 다시 돌리면 같은
    `(harness, case, group, qi, rep)` 이 여러 줄이 된다. 그대로 세면 한 측정이
    두 번 들어가 중앙값이 흔들린다 — 실측으로 확인했다(같은 키 3줄이 3건으로 셈).
    로그는 append-only 로 두고(무엇을 언제 쟀는지가 남는다) 집계에서만 접는다.
    """
    rows = []
    for f in sorted(RESULTS.glob("*.jsonl")):
        rows += load(f)
    latest = {}
    for r in rows:
        if r.warmup or r.error:
            continue
        latest[r.key()] = r          # 나중 줄이 이긴다
    return list(latest.values())


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

        # **말할 수 없는 것을 말하지 않는다.** 이 블록이 이 리포트의 요점이다.
        # "표본이 모자라 모른다" 와 "겹쳐서 못 가린다" 는 다른 상태다 — 뭉치면
        # 리포트가 늘 같은 문장을 뱉어 정보가 0 이 된다.
        pairs = [(a, b) for i, a in enumerate(dists) for b in list(dists)[i + 1:]]
        thin = [f"{a} ↔ {b}" for a, b in pairs if too_few(dists[a], dists[b])]
        amb = [f"{a} ↔ {b}" for a, b in pairs
               if not too_few(dists[a], dists[b]) and overlaps(dists[a], dists[b])]
        clear = [f"{a} ↔ {b}" for a, b in pairs
                 if not too_few(dists[a], dists[b]) and not overlaps(dists[a], dists[b])]
        for label, items in (("표본 부족(<4)이라 판단 보류", thin),
                             ("**IQR 이 겹쳐 우열을 주장할 수 없음**", amb),
                             ("IQR 이 분리돼 차이를 말할 수 있음", clear)):
            if items:
                msg = f"{label}: " + " · ".join(items)
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


def wrong_answers(rows: list) -> None:
    """
    **틀렸을 때 무엇이라고 답했는지.** 랭킹을 고치려면 이 열이 필요하다 — `none` 이
    많으면 검색이 못 찾은 것이고, 다른 ID 가 반복되면 **그것이 더 나은 답일 수도**
    있다(G05 처럼 정답 설정 자체가 모호한 그룹이 있다).
    """
    bad = [r for r in rows if not r.hit and r.answer]
    if not bad:
        return
    print("\n=== 오답 (무엇과 헷갈렸나) ===")
    seen = {}
    for r in bad:
        seen.setdefault((r.group, r.case, r.answer), 0)
        seen[(r.group, r.case, r.answer)] += 1
    for (g, c, ans), n in sorted(seen.items(), key=lambda x: -x[1])[:12]:
        tag = "못 찾음" if ans == "none" else ans
        print(f"  {g[:20]:22} {c:11} → {tag:12} ×{n}")


def learning_curve(rows: list) -> None:
    """
    **warm 실험에서 학습이 실제로 일하나.**

    C 케이스만 회차를 거듭할수록 서버가 궤적을 더 들고 있다. 학습이 값어치를 한다면
    **순위가 올라가고 토큰이 줄어야** 한다 — 안 그러면 이 코퍼스에서 그 층은 값이
    없다는 뜻이고, 그것도 유효한 결과다(저장소의 "각 층은 스스로 증명한다").

    `trajectories` 가 전부 0 이면 cold 실험이라 이 절을 내지 않는다.
    """
    c = [r for r in rows if r.case.startswith("C")
         and r.extra.get("trajectories", -1) > 0]
    if not c:
        return
    print("\n=== 학습 곡선 (warm · C 케이스) ===")
    print("  회차별로 서버가 들고 있던 궤적 수와 그때의 성적")
    by_rep = {}
    for r in c:
        by_rep.setdefault(r.rep, []).append(r)
    for rep in sorted(by_rep):
        rs = by_rep[rep]
        traj = summarize([float(r.extra["trajectories"]) for r in rs])
        tok = summarize([float(r.tokens or r.chars) for r in rs])
        hit = sum(r.hit for r in rs)
        print(f"  r{rep}: 궤적 {traj['median']:>6.0f}건 · 적중 {hit}/{len(rs)} · "
              f"비용 {tok['median']:>9,.0f}")
    print("  → 회차가 갈수록 적중이 오르고 비용이 줄면 학습이 일하는 것이다")


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
    if not a.md:
        wrong_answers(rows)
        learning_curve(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
