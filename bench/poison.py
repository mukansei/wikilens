#!/usr/bin/env python3
"""
**틀린 답을 학습시키면 얼마나 오래 아픈가** — 오염과 회복을 잰다. $0.

    bench/setup.sh server warm
    python3 bench/poison.py

`learn.py` 와 뼈대가 같다(HTTP 세 번으로 궤적을 쌓는다). 다른 것은 **무엇을
읽느냐**뿐이다 — 저쪽은 늘 정답을 읽어 "학습이 발동하나" 를 재고, 이쪽은 일부러
**오답**을 읽혀 "그 발동이 틀렸을 때 되돌아오나" 를 잰다.

## 왜 재나

`DECISIONS.md` D26 (2026-08-21): 학습 없는 대조군이 `answer` 를 부른 세 번이
**세 번 다 틀린 문서**였다. `dest = declared ?: reads.last()` 라 그 진술이 그대로
간선이 된다. D26 은 "되돌리는 장치는 있다" 고 적었는데 **그 장치의 효과는 한 번도
안 쟀다** — 일화 하나뿐이다. 가드가 있다는 사실과 가드가 작동한다는 사실은 다르다.

## 두 국면

    ① 오염   검색 → **오답**을 읽음 → 종료      오답이 서빙될 때까지
    ② 회복   검색 → **정답**을 읽음 → 종료      오답 서빙이 멈출 때까지
    ③ 정상화 검색 → **정답**을 읽음 → 종료      정답이 서빙될 때까지

`--accept N/D` 를 주면 ②가 **섞인다** — D세션마다 N세션은 추천받은 오답을 그냥 읽고
끝낸다. 그때는 `rejected` 가 비어 미스가 아니라 **히트**가 되므로 오염이 강화된다.
②의 기본(수용 0%)은 "모두가 오답을 알아본다" 는 낙관적 전제였고, 이 옵션이 그것을
푼다.

②와 ③을 나누는 것이 이 측정의 요점이다. 오답이 안 나오는 것과 정답이 나오는 것은
다르고, **그 사이에 학습층이 아무 일도 안 하는 구간이 있다.**

②가 실사용의 회복 모양이다. 추천받은 문서를 열어보고 아니어서 다른 것을 읽으면
`rejected = served - reads` 로 오답이 미스를 먹고, 동시에 정답이 히트를 먹는다.
**둘이 같은 세션에서 일어난다** — 그래서 회복은 오답을 밀어내는 동시에 정답을
끌어올린다.

## 읽는 값

    발동   오염 몇 회차부터 오답이 서빙되나
    회복   그 뒤 몇 **세션** 만에 오답 서빙이 멈추나
    정상화 정답이 서빙되기까지 몇 세션이나        ← 여기까지 가야 원상복구다

**세션이지 "정답 읽기 횟수" 가 아니다** — `--accept` 를 주면 그중 일부는 오답을
읽는다. 사용자가 겪는 것은 세션 수이므로 그쪽으로 센다.
    피해   그 사이 오답을 서빙받은 세션 수

`hints` 만으로는 **무엇이** 서빙됐는지 모른다. `/api/search` 응답에서 오답이
힌트로 왔는지를 직접 본다 — 정답이 서빙되는 것은 회복이지 피해가 아니다.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# 주소·사용자는 `harness` 가 정본이다 — 이 스크립트도 궤적을 *만드는* 물건이라
# 다른 서버를 재는 실수가 되돌려지지 않는다.
from harness import (BENCH_USER, Writer, api_get, api_post,  # noqa: E402
                     record, require_server)

#: **G05 에서 실제로 일어난 오염이다** — 합성이 아니다. 학습 없는 6세션 중 5세션이
#: 정답 대신 이 문서를 골랐고, 그중 둘은 `answer` 로 못박기까지 했다(D26).
#: 제목이 거의 같고 문서 번호가 이웃한다.
QUERY = "ds앱 보안 관련해서 뭐 조치한다는 보고 있었는데"
GOLD = "286067130"
WRONG = "286066505"

#: **오염된 문서의 어휘 순위가 회복 비용을 정한다** — `hitWeight` 가 1위 ×1 ·
#: 2~3위 ×2 · 그 아래 ×3 이고, 히트만 가중하므로 ×3 으로 배운 간선은 미스 3번이라야
#: 상쇄된다. 그 주장을 시험하려고 같은 질의의 5위 문서를 두 번째 사례로 둔다 —
#: 질의·정답·서버가 전부 같고 **오염된 문서의 순위만 다르다.**
DEEP = "196981024"


def probe(sid: str, read: str) -> dict:
    """
    한 세션. 검색하고, 지정한 문서를 읽고, 닫는다.

    **`learnedHints` 는 검색 시점의 값**이다 — 이번 세션이 만드는 궤적은 아직
    확정 전이라 반영되지 않는다. 회차 r 의 힌트는 r 회차 **이전까지**의 학습이다.
    """
    res = api_post("/api/search", {"query": QUERY, "userKey": BENCH_USER,
                                   "sessionId": sid, "limit": 8})
    hits = res.get("hits", [])
    ids = [h["pageId"] for h in hits]
    # **어느 문서가 힌트로 왔는지**를 본다. 개수만 세면 정답이 서빙된 회복 국면과
    # 오답이 서빙된 피해 국면이 구별되지 않는다.
    served = [h["pageId"] for h in hits if h.get("source") in ("learned", "both")]
    api_post("/api/read", {"pageId": read, "userKey": BENCH_USER, "sessionId": sid})
    api_post("/api/session/end", {"sessionId": sid})
    return {
        "read": read,
        "hints": int(res.get("learnedHints", 0)),
        "served": served,
        "wrong_served": WRONG in served,
        "gold_served": GOLD in served,
        "rank_wrong": (ids.index(WRONG) + 1) if WRONG in ids else -1,
        "rank_gold": (ids.index(GOLD) + 1) if GOLD in ids else -1,
    }


def phase(w, name: str, read: str, stop, limit: int, stamp: str, start: int,
          accept: tuple[int, int] = (0, 1)) -> list:
    """
    `stop(r)` 이 참이 될 때까지 돈다. 한 국면의 회차 기록을 돌려준다.

    `accept=(N, D)` 면 D세션마다 앞의 N세션이 **오답을 읽는다**(수용). 무작위가
    아니라 결정적 교대다 — 재현이 안 되면 회차 수를 비교할 수 없다.
    """
    out = []
    n, d = accept
    for i in range(limit):
        took = (i % d) < n
        r = probe(f"poison-{stamp}-{name}-{i}", WRONG if took else read)
        r["accepted"] = took
        out.append(r)
        mark = "오답 서빙" if r["wrong_served"] else ("정답 서빙" if r["gold_served"] else "-")
        act = "수용" if r.get("accepted") else "거부"
        print(f"  {name:6} r{start + i}  힌트 {r['hints']}  {mark:10} {act}"
              f"  오답 {r['rank_wrong']:>3}위 · 정답 {r['rank_gold']:>3}위")
        w.write(record(harness="poison", case=name, group="G05", qi=0, query=QUERY,
                       gold=GOLD, rep=start + i, hit=r["gold_served"], **r))
        if stop(out):
            break
        time.sleep(0.1)
    return out


def main() -> int:
    global WRONG
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=12, help="국면당 최대 회차")
    ap.add_argument("--wrong", default=WRONG,
                    help=f"오염시킬 문서. 기본은 실제 오답 {WRONG}(어휘 1위), "
                         f"{DEEP} 는 어휘 5위라 가중이 ×3 이다")
    ap.add_argument("--accept", default="0/1", metavar="N/D",
                    help="회복 국면에서 D세션마다 N세션이 오답을 그냥 읽는다(수용). "
                         "기본 0/1 = 아무도 안 받아들인다")
    ap.add_argument("--out", default=str(HERE / "results" / "poison.jsonl"))
    a = ap.parse_args()
    WRONG = a.wrong
    n, d = (int(x) for x in a.accept.split("/"))
    acc = (n, d)

    bad = require_server(need_plugins=False)
    if bad:
        print(f"  ✗ {bad}", file=sys.stderr)
        return 2

    t0 = api_get("/api/stats").get("trajectories", 0)
    print(f"  서버 학습량 {t0}건에서 시작 · 오염 대상 {WRONG}"
          f" · 수용률 {n}/{d} = {100 * n // d}%\n")
    stamp = time.strftime("%H%M%S")

    with Writer(pathlib.Path(a.out)) as w:
        print("  ① 오염 — 오답을 읽힌다")
        po = phase(w, "poison", WRONG, lambda o: o[-1]["wrong_served"], a.max, stamp, 0)
        fired = len(po) - 1 if po and po[-1]["wrong_served"] else -1

        print("\n  ② 회복 — 추천을 거부하고 정답을 읽는다")
        rc = phase(w, "recover", GOLD, lambda o: not o[-1]["wrong_served"],
                   a.max, stamp, len(po), accept=acc)
        healed = len(rc) - 1 if rc and not rc[-1]["wrong_served"] else -1

        print("\n  ③ 정상화 — 정답이 서빙되기까지")
        nm = phase(w, "normal", GOLD, lambda o: o[-1]["gold_served"],
                   a.max, stamp, len(po) + len(rc))
        back = len(rc) + len(nm) if nm and nm[-1]["gold_served"] else -1

    allr = po + rc + nm
    harm = sum(1 for r in allr if r["wrong_served"])
    dead = sum(1 for r in rc + nm if not r["wrong_served"] and not r["gold_served"])
    print(f"\n  === 판정 ===")
    print(f"    발동    오염 {fired}회 관측 뒤 서빙" if fired >= 0
          else "    발동    끝까지 서빙 안 됨 — 이 자리는 오염되지 않는다")
    print(f"    회복    {healed}세션 뒤 오답 서빙 멈춤" if healed >= 0
          else f"    회복    {a.max}회 거부해도 계속 서빙")
    print(f"    정상화  {back}세션 뒤 정답 서빙" if back >= 0
          else f"    정상화  {a.max}회로는 정답이 안 올라옴")
    print(f"    피해    오답을 서빙받은 세션 {harm}개")
    print(f"    무력    오답도 정답도 안 나온 세션 {dead}개 — 학습층이 놀고 있다")
    if fired > 0 and back > 0:
        print(f"\n    오염 {fired}세션 : 정상화 {back}세션 = **{back / fired:.0f}배**")
    st = api_get("/api/stats")
    print(f"    서버 자기진단 — 서빙 {st.get('served')} · 거부 {st.get('rejected')}"
          f" · pWrong {st.get('pWrong')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
