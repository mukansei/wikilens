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
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent

#: **`setup.sh` 가 띄우는 전용 서버.** 운영(:8787)으로 돌리면 벤치 질의가 그대로
#: 학습으로 쌓인다 — 궤적은 이 저장소의 유일한 복구 불가 자산이라 되돌릴 수 없다.
#: 아래 `guard()` 가 주소를 확인한다.
SERVER = "http://127.0.0.1:8790"
USER = "bench"

sys.path.insert(0, str(HERE))
from harness import Writer, record  # noqa: E402
from queries import GROUPS, MINIMAL  # noqa: E402


def post(path: str, body: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(SERVER + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def stats() -> dict:
    try:
        with urllib.request.urlopen(SERVER + "/api/stats", timeout=10) as r:
            return json.loads(r.read())
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
    res = post("/api/search", {"query": query, "userKey": USER,
                               "sessionId": sid, "limit": 8})
    ids = [h["pageId"] for h in res.get("hits", [])]
    if read_gold:
        # **읽어야 궤적이 목적지를 갖는다.** 안 읽으면 `onEnd` 가 빈 스팬을 버린다.
        try:
            post("/api/read", {"pageId": gold, "userKey": USER, "sessionId": sid})
        except urllib.error.HTTPError as e:
            return {"error": f"read {gold} → HTTP {e.code}", "seconds": time.perf_counter() - t}
    post("/api/session/end", {"sessionId": sid})
    return {
        "hints": int(res.get("learnedHints", 0)),
        "lexical": int(res.get("lexicalCandidates", 0)),
        "rank": (ids.index(gold) + 1) if gold in ids else -1,
        "seconds": time.perf_counter() - t,
    }


def guard() -> str:
    """
    **운영 서버로 돌지 않게 막는다.** 이 스크립트는 궤적을 *만드는* 물건이라
    주소를 잘못 주면 되돌릴 수 없다(`rank.py` 는 `sessionId` 를 안 보내 안전했다).
    """
    if not SERVER.endswith(":8790"):
        return f"SERVER 가 {SERVER} 다 — 이 스크립트는 궤적을 만든다. :8790 이어야 한다"
    s = stats()
    if not s:
        return f"{SERVER} 에 못 닿는다 — `bench/setup.sh server warm` 을 먼저 돌릴 것"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", choices=["repeat", "transfer"], default="repeat")
    ap.add_argument("--groups", nargs="*", default=list(MINIMAL))
    ap.add_argument("--reps", type=int, default=4,
                    help="repeat: 총 회차 / transfer: 학습 회차 + 시험 2회")
    ap.add_argument("--out", default="learn.jsonl")
    a = ap.parse_args()

    bad = guard()
    if bad:
        print(f"  ✗ {bad}", file=sys.stderr)
        return 2

    groups = [g for g in GROUPS if any(g[0].startswith(p) for p in a.groups)]
    if not groups:
        print("  해당 그룹 없음", file=sys.stderr)
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
            if a.pattern == "repeat":
                # 같은 표현을 반복한다 — 커버리지 c=1.0 이라 학습에 가장 유리하다.
                steps = [(0, r, True) for r in range(a.reps)]
            else:
                # q0 로 학습시킨 뒤 **다른 표현**으로 시험한다. 시험 회차는 읽지 않는다 —
                # 읽으면 그 자체가 학습이 되어 다음 시험이 오염된다.
                steps = [(0, r, True) for r in range(a.reps - 2)]
                steps += [(qi, a.reps - 2 + i, False) for i, qi in enumerate((1, 2))]

            for qi, rep, learn in steps:
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

    what = "반복 질의" if a.pattern == "repeat" else "**다른 표현**(시험 단계)"
    print(f"\n  === 판정 ({a.pattern}) — {what} 에 힌트가 서빙됐나 ===")
    for name, first in fired:
        if first < 0:
            print(f"    {name:<22} 끝까지 힌트 0")
        else:
            print(f"    {name:<22} r{first} 부터 서빙")

    # **예측을 여기 적어 두고 대조한다.** 나중에 결과만 남으면 이 수치가 예상과 같았는지
    # 달랐는지를 복원할 수 없다 — 그 차이가 D23 을 다시 볼지를 가른다.
    none = [n for n, f in fired if f < 0]
    if a.pattern == "repeat":
        print("\n    예측: r1 부터 서빙 (c=1.0, ebLower(1,0,0.85)=0.62 > 문턱 0.45)")
        if none:
            print(f"    → **예측과 다르다.** {len(none)}그룹이 끝까지 0 이다. "
                  "가장 유리한 조건에서도 안 오르면 아래는 볼 것이 없다")
    else:
        print("\n    예측: 끝까지 0 (항 겹침 0~3개 → c=0.17~0.43 < 문턱 0.73)")
        if len(none) < len(fired):
            print("    → **예측을 깼다.** 표현 전이가 된다는 뜻이라 D23 의 전제가 바뀐다. "
                  "항이 적은 질의는 겹침 하나가 c 를 크게 올린다")

    print(f"\n  결과 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
