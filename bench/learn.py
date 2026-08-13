#!/usr/bin/env python3
"""
**학습이 발동하나** — 궤적을 직접 쌓아 힌트가 서빙되는 회차를 잰다. $0.

    bench/setup.sh server warm
    python3 bench/learn.py --pattern repeat
    python3 bench/learn.py --pattern transfer

`agent.py` 와 재는 것이 다르다. 이쪽은 **서버가 힌트를 내주기 시작하는가**이고
저쪽은 **모델이 그 힌트를 써서 실제로 덜 헤매는가**다. 순서가 있다 — 서빙이 0 인 채로
세션을 태우면 "학습이 행동을 안 바꿨다" 와 "애초에 학습이 없었다" 가 구별되지 않는다.

에이전트가 필요 없는 이유는 학습 루프 전체가 HTTP 세 번이기 때문이다:

    POST /api/search      {query, userKey, sessionId}  → 응답의 learnedHints
    POST /api/read        {pageId, userKey, sessionId} → 궤적의 목적지가 정해진다
    POST /api/session/end {sessionId}                  → 궤적이 확정된다

**정답을 읽는다.** 사람이 그 문서를 찾아 읽은 세션을 흉내내는 것이라 학습에게 가장
유리한 조건이고, 여기서도 안 오르면 그 아래는 볼 것이 없다. 반대로 오른다고 실사용이
그렇다는 뜻은 아니다 — 실사용은 엉뚱한 문서도 읽는다.

## 예측 (2026-08-12 계산, `agent.py` 의 `plan` 독스트링에 유도)

    repeat    같은 질의 반복       c=1.0        → 2회차부터 서빙돼야 한다
    transfer  q0 학습 → q1·q2 시험  c=0.17~0.43  → 문턱 0.73 에 못 미쳐 안 될 것이다

**예측이 틀리면 그게 결과다.** 특히 transfer 가 성공하면 표현 전이가 되는 것이라
D23 의 전제가 바뀐다.

## 첫 실측 (2026-08-13, MINIMAL 3그룹 · reps 4 · cold 시작)

`transfer` 는 예측대로 시험 단계에서 끝까지 0 이었다. `repeat` 은 **갈렸고, 그
갈림이 D23 그 자체다** — 어휘 순위가 사전확률이 되어 필요한 관측 수를 정한다:

    G04  어휘 1위   prior 0.85  →  r1 부터 서빙 (1관측 0.616)
    G09  어휘 19위  prior 중간  →  r3 부터 서빙 — **뜨는 순간 순위가 밖→1위**
    G01  어휘 밖    prior 0.3   →  끝까지 0 (3관측 0.281 < 문턱 0.45, 7관측 필요)

**학습이 가장 필요한 문서에서 가장 늦게, 또는 아예 안 발동한다.** G09 가 그 층이
실제로 일한다는 증거이자 그 대가의 증거다 — 세 번을 헤매야 한 번을 얻는다.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time
import urllib.error

HERE = pathlib.Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))
# **주소·사용자는 `harness` 가 정본이다.** 여기서 따로 들면 `rank.py` 와 다른 서버를
# 재게 되고, 이 스크립트는 궤적을 *만드는* 물건이라 그 실수가 되돌려지지 않는다.
from harness import (BENCH_USER, SERVER, Writer, api_get, api_post,  # noqa: E402
                     record, require_server, select_groups)
from queries import GROUPS, MINIMAL  # noqa: E402


def stats() -> dict:
    try:
        return api_get("/api/stats")
    except Exception:  # noqa: BLE001
        return {}


def probe(query: str, gold: str, sid: str, read_gold: bool) -> dict:
    """
    한 세션. 검색하고, (원하면) 정답을 읽고, 닫는다.

    **`learnedHints` 는 검색 시점의 값**이다 — 이번 세션이 만드는 궤적은 아직 확정
    전이므로 반영되지 않는다. 그래서 회차 r 의 힌트 수는 **r 회차 이전까지의 학습**을
    말한다. 이것이 "2회차부터 서빙" 이라는 예측의 정확한 뜻이다.
    """
    t = time.perf_counter()
    res = api_post("/api/search", {"query": query, "userKey": BENCH_USER,
                                   "sessionId": sid, "limit": 8})
    ids = [h["pageId"] for h in res.get("hits", [])]
    err = ""
    if read_gold:
        # **읽어야 궤적이 목적지를 갖는다.** 안 읽으면 `onEnd` 가 빈 스팬을 버린다.
        try:
            api_post("/api/read", {"pageId": gold, "userKey": BENCH_USER, "sessionId": sid})
        except urllib.error.HTTPError as e:
            err = f"read {gold} → HTTP {e.code}"
    # **실패해도 세션은 닫는다.** 예전에는 read 가 실패하면 여기로 안 와서 서버에
    # 열린 스팬이 남았고, 다음 회차가 그 상태에서 재는 것이 됐다(`SessionSweeper` 가
    # 5분 뒤 거두지만 벤치는 그보다 빨리 끝난다).
    api_post("/api/session/end", {"sessionId": sid})
    if err:
        return {"error": err, "seconds": time.perf_counter() - t}
    return {
        "hints": int(res.get("learnedHints", 0)),
        "lexical": int(res.get("lexicalCandidates", 0)),
        "rank": (ids.index(gold) + 1) if gold in ids else -1,
        "seconds": time.perf_counter() - t,
    }


def steps(pattern: str, reps: int) -> list[tuple[int, int, bool]]:
    """
    한 그룹에서 밟을 `(질의 번호, 회차, 학습인가)` 목록.

    `repeat` 은 같은 표현을 반복한다 — 커버리지 c=1.0 이라 학습에 가장 유리하다.
    `transfer` 는 q0 로 학습시킨 뒤 **다른 표현**으로 시험하고, **시험 회차는 읽지
    않는다** — 읽으면 그 자체가 학습이 되어 다음 시험이 오염된다.

    회차 번호가 음수가 되면 안 된다 — 워밍(`rep=-1`)과 이어받기 키가 충돌한다.
    `main` 이 `transfer` 에 `reps >= 3` 을 요구하는 이유가 그것이다.
    """
    if pattern == "repeat":
        return [(0, r, True) for r in range(reps)]
    out = [(0, r, True) for r in range(reps - 2)]
    return out + [(qi, reps - 2 + i, False) for i, qi in enumerate((1, 2))]


def verdict(fired: list[tuple[str, int]], pattern: str) -> None:
    """
    그룹별로 몇 회차부터 서빙됐나, 그리고 그것이 **예측과 같은가**.

    **예측을 여기 적어 두고 대조한다.** 나중에 결과만 남으면 이 수치가 예상과 같았는지
    달랐는지를 복원할 수 없다 — 그 차이가 D23 을 다시 볼지를 가른다.
    """
    what = "반복 질의" if pattern == "repeat" else "**다른 표현**(시험 단계)"
    print(f"\n  === 판정 ({pattern}) — {what} 에 힌트가 서빙됐나 ===")
    for name, first in fired:
        print(f"    {name:<22} " + ("끝까지 힌트 0" if first < 0 else f"r{first} 부터 서빙"))

    none = [n for n, f in fired if f < 0]
    if pattern == "repeat":
        print("\n    예측: r1 부터 서빙 (c=1.0, ebLower(1,0,0.85)=0.62 > 문턱 0.45)")
        if none:
            print(f"    → **예측과 다르다.** {len(none)}그룹이 끝까지 0 이다. "
                  "가장 유리한 조건에서도 안 오르면 아래는 볼 것이 없다")
    else:
        print("\n    예측: 끝까지 0 (항 겹침 0~3개 → c=0.17~0.43 < 문턱 0.73)")
        if len(none) < len(fired):
            print("    → **예측을 깼다.** 표현 전이가 된다는 뜻이라 D23 의 전제가 바뀐다. "
                  "항이 적은 질의는 겹침 하나가 c 를 크게 올린다")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", choices=["repeat", "transfer"], default="repeat")
    ap.add_argument("--groups", nargs="*", default=list(MINIMAL))
    ap.add_argument("--reps", type=int, default=4,
                    help="repeat: 총 회차 / transfer: 학습 회차 + 시험 2회")
    ap.add_argument("--out", default="learn.jsonl")
    a = ap.parse_args()

    groups = select_groups(GROUPS, a.groups)
    if not groups:
        print("  해당 그룹 없음", file=sys.stderr)
        return 2

    # **`transfer` 는 학습 단계가 있어야 성립한다.** `--reps 2` 면 학습이 0회인데
    # 그대로 돌면 시험이 당연히 0 이고, 아래 판정이 **"예측대로"** 라고 찍는다 —
    # 아무것도 안 재고 예측을 확인한 셈이 된다(`agent.py` 가 같은 이유로 막는다).
    # `--reps 1` 이하면 회차가 음수가 되어 워밍(rep=-1)과 키까지 충돌한다.
    if a.pattern == "transfer" and a.reps < 3:
        print(f"  ✗ --pattern transfer 는 --reps 3 이상이 필요하다 "
              f"(지금 {a.reps} → 학습 {max(0, a.reps - 2)}회)", file=sys.stderr)
        return 2
    if a.reps < 1:
        print(f"  ✗ --reps 는 1 이상이어야 한다 (지금 {a.reps})", file=sys.stderr)
        return 2

    # 플러그인 격리는 안 쓴다 — 순수 HTTP 라 `setup.sh server` 면 된다.
    bad = require_server(need_plugins=False)
    if bad:
        print(f"  ✗ {bad}", file=sys.stderr)
        return 2

    t0 = stats()
    # `agent.py` 와 같은 판정 — 사람이 주는 플래그가 아니라 서버가 든 궤적으로 정한다.
    mode = "warm" if t0.get("trajectories", 0) > 0 else "cold"
    print(f"  {SERVER} · 궤적 {t0.get('trajectories', 0)}건 · 포스팅 항 {t0.get('terms', 0)}개")
    if t0.get("trajectories", 0) == 0 and a.pattern == "transfer":
        print("  (cold 에서 시작한다 — transfer 는 학습 단계가 있으므로 정상)")
    print(f"  {a.pattern} · {len(groups)}그룹 · reps {a.reps}\n")

    out = HERE / "results" / a.out
    stamp = time.strftime("%H%M%S")
    #: (그룹, 판정 대상 단계에서 처음 힌트가 뜬 회차 · 없으면 -1)
    fired: list[tuple[str, int]] = []

    with Writer(out) as w:
        for name, gold, _title, queries in groups:
            print(f"  {name}  정답 {gold}")
            first = -1
            for qi, rep, learn in steps(a.pattern, a.reps):
                q = queries[qi]
                r = probe(q, gold, f"learn-{stamp}-{name[:3]}-{qi}-{rep}", learn)
                w.write(record(harness="learn", case="C 서버판", group=name, qi=qi,
                               query=q, gold=gold, rep=rep, mode=mode,
                               hit=r.get("rank", -1) > 0, rank=r.get("rank", -1),
                               seconds=r.get("seconds", 0.0), error=r.get("error", ""),
                               extra={"hints": r.get("hints", 0), "pattern": a.pattern,
                                      "learned": learn,
                                      "lexical": r.get("lexical", 0)}))
                if r.get("error"):
                    print(f"    r{rep} ✗ {r['error']}")
                    continue
                h = r["hints"]
                # **판정 대상은 `transfer` 에서 시험 단계뿐이다.** 학습 단계는 q0 를
                # 반복하는 것이라 거기 힌트가 뜨는 것은 `repeat` 이 이미 재는 것이고,
                # 전이의 증거가 아니다 — 이것을 안 가르면 transfer 가 **항상**
                # "예측을 깼다" 로 나온다(실측으로 그렇게 나왔다).
                judged = learn if a.pattern == "repeat" else not learn
                if h > 0 and first < 0 and judged:
                    first = rep
                tag = "학습" if learn else "시험"
                rk = f"{r['rank']:>2}위" if r["rank"] > 0 else " 밖 "
                print(f"    r{rep} {tag} q{qi} 힌트 {h:>2} · {rk} · 어휘 {r['lexical']:>3}건"
                      f"  「{q[:34]}」")
            fired.append((name, first))

    t1 = stats()
    print(f"\n  궤적 {t0.get('trajectories', 0)} → {t1.get('trajectories', 0)}건"
          f" · 포스팅 항 {t0.get('terms', 0)} → {t1.get('terms', 0)}개"
          f" · 서빙 {t1.get('served', 0)} · 거부 {t1.get('rejected', 0)}")

    verdict(fired, a.pattern)

    print(f"\n  결과 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
