"""
아키텍처 벤치마크 — 6개 실험.

목적은 효용 측정이 아니라 **주장의 반증**이다. 지금까지 내가 단언한 것들:
  E1  앵커 텍스트가 어휘 격차를 메운다
  E2  손익분기 적중률 = 1/n
  E3  RRF가 Wilson 신뢰도를 버려 손실이 발생한다
  E4  L1을 개선하면 L2의 문턱이 올라간다
  E5  비링크 도달 비율이 낮으면 L2는 링크 그래프의 복사본
  E6  churn이 적중률을 잠식한다
"""
from __future__ import annotations
import sys, math
import numpy as np
from corpus import build_corpus, zipf_query_stream
from retrieval import BM25Field, DenseSim, rrf, rrf_with_prob_boost, ShortcutCache, wilson

TOPICS = 60
MAX_READS = 20


def build_indexes(c, dense_noise=0.55):
    body = {p.pid: p.title_terms * 3 + p.body_terms for p in c.pages}
    anchor = {p.pid: [t for a in p.anchors_in for t in a] for p in c.pages}
    return (BM25Field(body), BM25Field(anchor),
            DenseSim(c, TOPICS, noise=dense_noise))


def rank_position(ranked, target):
    for i, (pid, _) in enumerate(ranked):
        if pid == target:
            return i + 1
    return None


def reads_for(ranked, target):
    """에이전트가 정답을 찾을 때까지 읽는 페이지 수. 못 찾으면 MAX_READS."""
    pos = rank_position(ranked[:MAX_READS], target)
    return pos if pos else MAX_READS


def hdr(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


# --------------------------------------------------------------- E1
def e1_anchor_contribution(c, queries):
    hdr("E1  앵커 텍스트 기여도 — 어휘 격차가 있는 페이지에서만 효과가 나는가")
    bm_body, bm_anchor, dense = build_indexes(c)

    buckets = {"격차 있음": [], "격차 없음": []}
    for target, terms in queries:
        b = "격차 있음" if target in c.gap_pages else "격차 없음"
        body_only = bm_body.search(terms, 100)
        fused = rrf([(body_only, 1.0), (bm_anchor.search(terms, 100), 1.0)])
        buckets[b].append((reads_for(body_only, target), reads_for(fused, target)))

    print(f"{'구간':<12}{'질의수':>7}{'본문만 n':>12}{'+앵커 n':>12}{'개선':>10}")
    for b, rows in buckets.items():
        a = np.mean([r[0] for r in rows]); d = np.mean([r[1] for r in rows])
        print(f"{b:<12}{len(rows):>7}{a:>12.2f}{d:>12.2f}{100*(a-d)/a:>9.1f}%")
    return buckets


# --------------------------------------------------------------- E2
def e2_breakeven(c, queries, warm_fracs=(0.0, 0.1, 0.2, 0.3, 0.5, 0.8)):
    hdr("E2  손익분기 검증 — E[L2] < n  ⟺  p > 1/n 인가")
    bm_body, bm_anchor, dense = build_indexes(c)

    ranked_cache, n_base = {}, {}
    for target, terms in queries:
        k = (target, tuple(terms))
        if k not in ranked_cache:
            r = rrf([(bm_body.search(terms, 100), 1.0), (bm_anchor.search(terms, 100), 1.0)])
            ranked_cache[k] = r
            n_base[k] = reads_for(r, target)

    # 버그 수정: 고유 키 평균이 아니라 질의 스트림 가중 평균이어야 한다.
    # Zipf 반복 때문에 두 값이 다르고, 베이스라인이 틀리면 비교가 전부 무의미해진다.
    n_mean = float(np.mean([n_base[(t, tuple(s))] for t, s in queries]))
    print(f"베이스라인 평균 읽기 n = {n_mean:.2f} (질의 가중)"
          f"   ->  예측 손익분기 적중률 = 1/n = {100/n_mean:.1f}%\n")
    print(f"{'워밍업':>8}{'실측 적중률':>12}{'E[L2] 읽기':>12}{'L1 대비':>10}{'예측(1+(1-p)n)':>16}")

    for wf in warm_fracs:
        cache = ShortcutCache(min_reliability=0.0)   # 워밍업 통제를 위해 게이트 해제
        warm = queries[:int(len(queries) * wf)]
        for target, terms in warm:
            for _ in range(5):
                cache.record(terms, target, True)

        hits, reads = 0, []
        for target, terms in queries:
            k = (target, tuple(terms))
            n = n_base[k]
            sc = cache.lookup(terms)
            if sc and sc[0] == target:
                hits += 1; reads.append(1)
            elif sc:
                reads.append(1 + n)
            else:
                reads.append(n)
        p = hits / len(queries)
        e = float(np.mean(reads))
        pred = 1 + (1 - p) * n_mean if p > 0 else n_mean
        print(f"{wf*100:>7.0f}%{p*100:>11.1f}%{e:>12.2f}{100*(e/n_mean-1):>9.1f}%{pred:>16.2f}")
    return n_mean


# --------------------------------------------------------------- E3
def e3_reliability_loss(c, queries, n_probe=1200):
    hdr("E3  RRF의 Wilson 신뢰도 손실 — 확률 부스트 대안과 비교")
    bm_body, bm_anchor, dense = build_indexes(c)
    rng = np.random.default_rng(7)

    # 신뢰도가 제각각인 숏컷 집합을 만든다. 일부는 틀린 목적지를 가리킨다.
    stats = {"RRF": [], "확률 부스트": []}
    for target, terms in queries[:n_probe]:
        lists = [(bm_body.search(terms, 100), 1.0), (bm_anchor.search(terms, 100), 1.0)]

        # 신뢰도를 무작위로 부여하고, 신뢰도가 낮을수록 틀릴 확률을 높인다
        rel = float(rng.uniform(0.45, 0.98))
        correct = rng.random() < rel          # 신뢰도가 곧 정답 확률이 되도록 구성
        dest = target if correct else int(rng.integers(0, c.n))

        sc_list = [(dest, rel)]
        r_rrf = rrf(lists + [(sc_list, 1.6)])
        r_prob = rrf_with_prob_boost(lists, {dest: rel})

        stats["RRF"].append(reads_for(r_rrf, target))
        stats["확률 부스트"].append(reads_for(r_prob, target))

    print(f"{'방식':<14}{'평균 읽기 n':>14}{'개선':>10}")
    a = np.mean(stats["RRF"]); b = np.mean(stats["확률 부스트"])
    print(f"{'RRF (현재)':<14}{a:>14.3f}{'—':>10}")
    print(f"{'확률 부스트':<14}{b:>14.3f}{100*(a-b)/a:>9.1f}%")
    print("\n  신뢰도를 정답 확률과 일치시켜 생성했으므로, 신뢰도를 쓰는 쪽이")
    print("  이기지 못하면 그 정보는 애초에 쓸모가 없다는 뜻이 된다.")
    return a, b


# --------------------------------------------------------------- E4
def e4_tension(c, queries):
    hdr("E4  L1↔L2 긴장 — L1이 좋아질수록 L2 문턱이 오르는가")
    print(f"{'L1 구성':<26}{'평균 n':>10}{'필요 적중률':>14}")
    bm_body, bm_anchor, _ = build_indexes(c)
    configs = [
        ("본문 BM25만", lambda t: bm_body.search(t, 100)),
        ("본문 + 앵커", lambda t: rrf([(bm_body.search(t, 100), 1.0),
                                       (bm_anchor.search(t, 100), 1.0)])),
        ("본문 + 앵커 (앵커 2배)", lambda t: rrf([(bm_body.search(t, 100), 1.0),
                                                  (bm_anchor.search(t, 100), 2.0)])),
    ]
    out = []
    for name, fn in configs:
        ns = [reads_for(fn(terms), target) for target, terms in queries[:1500]]
        n = float(np.mean(ns))
        out.append((name, n, 100 / n))
        print(f"{name:<26}{n:>10.2f}{100/n:>13.1f}%")
    return out


# --------------------------------------------------------------- E5
def e5_nonlink_reach(c, queries):
    hdr("E5  비링크 도달 비율 — L2가 링크 그래프의 복사본인가")
    bm_body, bm_anchor, _ = build_indexes(c)
    outl = {p.pid: set(p.outlinks) for p in c.pages}

    direct = hop1 = hop2 = nonlink = 0
    for target, terms in queries[:2000]:
        r = rrf([(bm_body.search(terms, 100), 1.0), (bm_anchor.search(terms, 100), 1.0)])
        if not r:
            continue
        seed = r[0][0]
        if seed == target:
            direct += 1
        elif target in outl.get(seed, ()):
            hop1 += 1
        elif any(target in outl.get(m, ()) for m in list(outl.get(seed, ()))[:40]):
            hop2 += 1
        else:
            nonlink += 1
    tot = direct + hop1 + hop2 + nonlink
    print(f"  검색으로 직행 (링크 무관) : {direct:>5} ({100*direct/tot:5.1f}%)")
    print(f"  링크 1홉으로 도달         : {hop1:>5} ({100*hop1/tot:5.1f}%)")
    print(f"  링크 2홉으로 도달         : {hop2:>5} ({100*hop2/tot:5.1f}%)")
    print(f"  링크로 도달 불가          : {nonlink:>5} ({100*nonlink/tot:5.1f}%)")
    nl = (direct + nonlink) / tot
    print(f"\n  비링크 도달 비율 = {100*nl:.1f}%")
    print("  낮으면 궤적이 사람 링크를 다시 배우는 것이므로 L2가 퇴화한다.")
    return nl


# --------------------------------------------------------------- E6
def e6_churn(c, queries, rates=(0.0, 0.05, 0.15, 0.35, 0.60)):
    hdr("E6  churn 민감도 — 변경된 페이지 비율이 적중률을 얼마나 잠식하는가")
    print("  설계: 앞 60%로 캐시 워밍업 -> churn 적용 -> 나머지 40%를 기록 없이 측정")
    print("  (측정 중 기록하면 캐시가 즉시 복구되어 churn 효과가 가려진다)\n")
    rng = np.random.default_rng(11)
    split = int(len(queries) * 0.6)
    warm, test = queries[:split], queries[split:]

    # 인기 페이지가 더 자주 바뀐다고 가정 (실제 위키의 churn은 균일하지 않다)
    pop = {}
    for t, _ in queries:
        pop[t] = pop.get(t, 0) + 1
    pids = np.array(list(pop.keys()))
    w = np.array([pop[p] for p in pids], dtype=float)
    w /= w.sum()

    print(f"{'변경 페이지 비율':>16}{'서빙율':>10}{'적중률':>10}{'캐시 크기':>12}")
    for rate in rates:
        cache = ShortcutCache(min_reliability=0.45)
        for t, terms in warm:
            cache.record(terms, t, True)
        n_inv = int(len(pids) * rate)
        if n_inv:
            for p in rng.choice(pids, size=n_inv, replace=False, p=w):
                cache.invalidate(int(p))
        served = hits = 0
        for t, terms in test:
            sc = cache.lookup(terms)
            if sc:
                served += 1
                if sc[0] == t:
                    hits += 1
        sr = served / len(test)
        hr = hits / served if served else 0.0
        print(f"{rate*100:>15.0f}%{sr*100:>9.1f}%{hr*100:>9.1f}%{len(cache.by_key):>12}")
    print("\n  서빙율 = 캐시가 답을 내놓은 비율 (커버리지)")
    print("  적중률 = 내놓은 답이 맞은 비율 (정확도)")


if __name__ == "__main__":
    print("합성 코퍼스 생성 중...")
    c = build_corpus(n_pages=2000, vocab_gap=0.45, avg_outdeg=12, seed=0)
    q = zipf_query_stream(c, n_queries=6000, alpha=1.1, seed=1)
    print(f"  페이지 {c.n}개 · 어휘격차 페이지 {len(c.gap_pages)}개 "
          f"({100*len(c.gap_pages)/c.n:.0f}%) · 질의 {len(q)}개")
    print(f"  고유 정답 페이지 {len(set(t for t,_ in q))}개 (Zipf 핫셋)")

    e1_anchor_contribution(c, q[:2500])
    n_mean = e2_breakeven(c, q[:2500])
    e3_reliability_loss(c, q)
    e4_tension(c, q)
    e5_nonlink_reach(c, q)
    e6_churn(c, q)
