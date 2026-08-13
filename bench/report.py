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
from harness import RESULTS, load, overlaps, summarize, too_few, wilson  # noqa: E402

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
    """
    도달 — **A 와 B·C 는 단위가 다르다.**

    B·C 는 정답이 몇 위인지, A 는 grep 후보 **몇 건 중 하나**인지다(`stage == "GREP"`).
    같은 열에 놓되 단위를 표기한다 — 400건 중 하나와 1위는 전혀 다른 상태이고,
    그것을 뭉개면 A 가 실제보다 잘하는 것처럼 읽힌다.
    """
    rs = [r for r in rows if r.harness == "rank"]
    if not rs:
        return
    print("\n=== 도달 (정답을 몇 위 / 후보 몇 건 중에) ===")
    cases = [c for c in CASE_ORDER if any(r.case == c for r in rs)]
    print("  " + "그룹".ljust(22) + "".join(c.rjust(19) for c in cases))
    for g in sorted({r.group for r in rs}):
        cells = []
        for c in cases:
            v = [r for r in rs if r.group == g and r.case == c]
            found = sorted(r.rank for r in v if r.rank > 0)
            unit = "건중" if any(r.extra.get("stage") == "GREP" for r in v) else "위"
            cells.append(f"{len(found)}/{len(v)} 중앙 {found[len(found)//2]}{unit}"
                         if found else f"0/{len(v)}  —")
        print("  " + g[:20].ljust(22) + "".join(x.rjust(19) for x in cells))
    grep = [r for r in rs if r.extra.get("stage") == "GREP" and r.rank > 0]
    if grep:
        med = sorted(r.rank for r in grep)[len(grep)//2]
        print(f"  ※ 원시 grep 의 숫자는 순위가 아니라 **후보 수**다 — 중앙값 {med}건 중 하나.")


def stage_note(rows: list) -> None:
    """
    로컬판이 **어느 단계에서** 찾았나. `BODY` 는 약한 증거다 — `rank.py` 가 정답을
    찾을 때까지 내려가는데 실제 에이전트는 앞 단계에서 멈출 수 있기 때문이다.
    """
    rs = [r for r in rows if r.harness == "rank" and r.case.startswith("B") and r.hit]
    if not rs:
        return
    by = {}
    for r in rs:
        st = r.extra.get("stage", "?")
        by[st] = by.get(st, 0) + 1
    tail = by.get("TREE", 0) + by.get("BODY", 0)
    print(f"\n  로컬판 적중 단계: {by}")
    if tail:
        print(f"    → TREE·BODY 로 내려가 찾은 {tail}건은 **낙관적**이다 — "
              "실제 에이전트는 ALIASES 에서 멈출 수 있다")


def coverage(rows: list) -> None:
    """
    **어느 그룹이 몇 칸 찼나.** 형상·모델·코퍼스는 검사하면서 **무엇을 쟀는지**는 안
    봤다 — 시험 실행의 잔류 세션 하나가 본 측정에 섞여 있었는데(G04 한 건) 리포트가
    그것을 못 짚었다. 부분 측정은 "어디까지 쟀나" 가 곧 결과의 범위다.
    """
    rs = [r for r in rows if r.harness == "agent"]
    if not rs:
        return
    per = {}
    for r in rs:
        per.setdefault(r.group, set()).add((r.qi, r.case))
    full = 3 * 3   # 질의 3 × 케이스 3
    print("\n=== 커버리지 (에이전트 측정) ===")
    for g in sorted(per):
        n = len(per[g])
        bar = "█" * n + "·" * (full - n)
        flag = "" if n == full else ("  ← 부분" if n > 1 else "  ← 조각, 다른 실행의 잔류일 수 있다")
        print(f"  {g[:22]:24} {bar} {n}/{full}{flag}")


def cost_table(rows: list, md: bool) -> None:
    """비용 — 실제 세션에서 실제 토큰. 시뮬레이션이 아니다."""
    rs = [r for r in rows if r.harness == "agent"]
    # **0 토큰 세션은 측정이 아니다.** `error` 는 없는데 토큰이 0 이면 무언가 잘못
    # 끝난 것이고(모델 미상, 도구 0회), 그것이 중앙값에 섞이면 값이 반토막 난다
    # (실측: 0 과 20만이 섞여 중앙값 10만). 세되 통계에서는 뺀다.
    dud = [r for r in rs if r.tokens == 0]
    rs = [r for r in rs if r.tokens > 0]
    if dud:
        print(f"\n  ! 토큰 0 인 세션 {len(dud)}건은 통계에서 뺐다 "
              "(무언가 잘못 끝난 것이다 — 결과 파일에는 남아 있다)")
    if not rs:
        return
    by = {}
    for r in rs:
        by.setdefault(r.case, []).append(r)
    by = {c: by[c] for c in CASE_ORDER if c in by}

    print(f"\n## 비용 (실제 세션 {len(rs)}건)\n" if md
          else f"\n=== 비용 (실제 세션 {len(rs)}건) ===")
    head = ["케이스", "적중", "토큰 중앙값 [IQR]", "호출", "턴", "세션 시간"]
    if md:
        print("| " + " | ".join(head) + " |")
        print("|" + "|".join(["---"] * len(head)) + "|")

    dists = {}
    for case, v in by.items():
        m = summarize([float(r.tokens) for r in v])
        dists[case] = m
        # **호출 수가 비용의 실제 동인이다** — 왕복 하나하나가 지연과 토큰을 함께
        # 쓴다. 기록만 하고 안 보여주면 잰 뜻이 없다.
        calls = summarize([float(r.calls) for r in v])
        cells = [case, f"{sum(r.hit for r in v)}/{len(v)}",
                 f"{m['median']:,.0f} [{m['q1']:,.0f}~{m['q3']:,.0f}]",
                 f"{calls['median']:.0f}",
                 f"{summarize([float(r.turns) for r in v])['median']:.0f}",
                 f"{summarize([r.seconds for r in v])['median']:.0f}s"]
        print(("| " + " | ".join(cells) + " |") if md
              else "  " + "  ".join(c.ljust(w) for c, w in zip(cells, [11, 7, 28, 5, 5, 9])))

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
    # **적중률도 판정한다.** 토큰은 IQR 로 가리면서 적중률은 맨눈으로 두면, 정작
    # 가장 중요한 축(찾았나 못 찾았나)이 근거 없이 읽힌다. 비율이므로 IQR 이 아니라
    # Wilson 구간이다.
    print("\n적중률 95% 구간:" if md else "\n  적중률 95% 구간:")
    cis = {}
    for case, v in by.items():
        lo, hi = wilson(sum(r.hit for r in v), len(v))
        cis[case] = (lo, hi)
        print(f"    {case:11} {sum(r.hit for r in v)}/{len(v)} "
              f"[{lo*100:.0f}%~{hi*100:.0f}%]")
    apairs = [(a, b) for i, a in enumerate(cis) for b in list(cis)[i + 1:]]
    sep = [f"{a}↔{b}" for a, b in apairs
           if cis[a][1] < cis[b][0] or cis[b][1] < cis[a][0]]
    print(f"    → 구간이 분리된 쌍: {' · '.join(sep) if sep else '없음 — 적중률 차이를 주장할 수 없다'}")

    print(f"\n총 비용 ${sum(r.cost for r in rs):.2f}" if md
          else f"  총 비용 ${sum(r.cost for r in rs):.2f}")


#: 케이스별로 **보여도 되는** 도구. 벗어나면 격리가 깨진 것이다.
EXPECTED = {"A 원시grep": ("Bash", "Read", "Grep", "Glob", "ToolSearch"),
            "B 로컬판": ("Bash", "Read", "Grep", "Glob", "Skill", "ToolSearch"),
            "C 서버판": ("mcp__", "Skill", "ToolSearch", "Read")}

#: 케이스가 **반드시 써야 하는** 도구. 안 쓰면 그 방식을 잰 게 아니다.
#:
#: 허용 목록만으로는 부족하다 — C 가 MCP 를 한 번도 안 쓰고 `Skill` 로만 답해도
#: 통과한다(합성 데이터로 확인). 그러면 서버판이 아니라 클라이언트 스킬의 안내문을
#: 잰 셈이고, 겉으로는 정상이다.
REQUIRED = {"C 서버판": "mcp__"}


def isolation(rows: list) -> None:
    """
    **격리가 실제로 먹혔나.** 도구 이름을 기록하는 이유가 이것이다 — 숫자만 보면
    결함을 못 본다(실측: B 가 서버판 MCP 를 쓰고 있었는데 답도 맞고 토큰도 그럴듯했다).

    A·B 에 `mcp__` 가 나오거나 A 에 `Skill` 이 나오면 설치본이 샌 것이다.
    """
    rs = [r for r in rows if r.harness == "agent" and r.extra.get("tools")]
    if not rs:
        return
    print("\n=== 격리 검증 (실제로 쓴 도구) ===")
    bad = []
    for case in CASE_ORDER:
        v = [r for r in rs if r.case == case]
        if not v:
            continue
        used = {}
        for r in v:
            for t in r.extra["tools"]:
                k = "mcp__…" if t.startswith("mcp__") else t
                used[k] = used.get(k, 0) + 1
        ok = EXPECTED.get(case, ())
        stray = [t for t in used if not any(t.startswith(p) for p in ok)]
        need = REQUIRED.get(case)
        missing = need and not any(t.startswith(need) for t in used)
        mark = "✗ 누출" if stray else ("✗ 미사용" if missing else "○")
        print(f"  {case:11} {mark} {used}")
        if stray:
            bad.append(f"{case}: 누출 {stray}")
        if missing:
            bad.append(f"{case}: {need} 를 한 번도 안 씀 — 그 방식을 잰 게 아니다")
    # **검색 없이 맞혔으면 검색을 잰 게 아니다.** 모델이 사전 지식으로 답할 수 있고,
    # 그러면 그 세션은 볼트가 없어도 같은 결과를 낸다.
    noop = [r for r in rs if r.hit and r.calls == 0]
    if noop:
        print(f"  ★ 도구를 한 번도 안 쓰고 맞힌 세션 {len(noop)}건 — "
              "사전 지식으로 답한 것이라 검색을 잰 게 아니다")
    if bad:
        print("  ★ 격리가 깨졌다 — 이 측정은 무효다: " + " · ".join(bad))


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
        print("# 측정 결과\n\n> **`bench/report.py` 가 생성한다.** 손으로 고치지 말 것.\n"
              "> 질의는 [`queries.py`](queries.py), 방법은 [`README.md`](README.md).")
    # **어느 형상에서 잰 값인가.** 섞여 있으면 그대로 비교하면 안 된다 — 코드가
    # 바뀐 전후를 한 표에 넣는 셈이다.
    shapes = {}
    for r in rows:
        k = (r.commit or "(기록 없음)", r.dirty)
        shapes[k] = shapes.get(k, 0) + 1
    print("\n=== 형상 ===")
    for (c, d), n in sorted(shapes.items(), key=lambda x: -x[1]):
        print(f"  {c:28} {'더러운 트리 ← 재현 불가' if d else '':24} {n:>4}건")

    # **형상 말고도 결과를 바꾸는 축이 셋 있다.** 하나라도 섞이면 그대로 비교하면
    # 안 된다 — 모델이 다르면 토큰·턴이 통째로 달라지고, 코퍼스가 다르면 grep 비용도
    # 랭킹도 달라진다. 시각은 그 둘이 언제 갈렸는지를 짚는 유일한 단서다.
    for label, key, fmt in (("모델이", "model", str),
                            ("코퍼스가", "corpus", lambda v: f"{v:,}건")):
        vals = {}
        for r in rows:
            v = getattr(r, key)
            if v:
                vals[v] = vals.get(v, 0) + 1
        if len(vals) > 1:
            detail = " · ".join(f"{fmt(k)}({n})" for k, n in
                                sorted(vals.items(), key=lambda x: -x[1]))
            print(f"  ★ {label} 섞여 있다: {detail}")
    stamps = sorted(r.ts for r in rows if r.ts)
    if stamps:
        print(f"  측정 기간 {stamps[0][:16]} ~ {stamps[-1][:16]}")

    if len(shapes) > 1:
        print("  ★ 형상이 섞여 있다 — 코드가 바뀐 전후를 한 표에 넣고 있다")

    rank_table(rows)
    stage_note(rows)
    coverage(rows)
    cost_table(rows, a.md)
    if not a.md:
        isolation(rows)
        wrong_answers(rows)
        learning_curve(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
