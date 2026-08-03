"""L1 검색 레이어 + L2 숏컷 캐시."""
from __future__ import annotations
import math
from collections import defaultdict
import numpy as np
from corpus import Corpus


# ----------------------------------------------------------------- BM25

class BM25Field:
    """단일 필드에 대한 BM25. 본문용과 앵커용을 따로 만든다."""

    def __init__(self, docs: dict[int, list[str]], k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.doclen: dict[int, int] = {}
        tf: dict[int, dict[str, int]] = {}
        for pid, terms in docs.items():
            c: dict[str, int] = defaultdict(int)
            for t in terms:
                c[t] += 1
            tf[pid] = c
            self.doclen[pid] = max(1, len(terms))
        for pid, c in tf.items():
            for t, f in c.items():
                self.postings[t].append((pid, f))
        self.N = max(1, len(docs))
        self.avgdl = float(np.mean(list(self.doclen.values()))) if self.doclen else 1.0
        self.df = {t: len(p) for t, p in self.postings.items()}

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(((self.N - df + 0.5) / (df + 0.5)) + 1.0)

    def search(self, terms: list[str], limit: int = 100) -> list[tuple[int, float]]:
        acc: dict[int, float] = defaultdict(float)
        for t in terms:
            idf = self.idf(t)
            if idf <= 0:
                continue
            for pid, f in self.postings.get(t, ()):
                dl = self.doclen[pid]
                acc[pid] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return sorted(acc.items(), key=lambda x: -x[1])[:limit]

    def max_idf(self, terms: list[str]) -> float:
        vals = [self.idf(t) for t in terms]
        return max(vals) if vals else 0.0


class DenseSim:
    """
    Dense 검색 시뮬레이션.

    모델링: 임베딩은 같은 주제의 문서를 끌어오지만 정밀하지 않다.
    주제 일치 신호에 가우시안 노이즈를 얹어 랭킹한다. noise가 클수록
    나쁜 임베딩 모델. 이건 가정이지 측정이 아니다.
    """

    def __init__(self, corpus: Corpus, topic_count: int, noise: float = 0.55, seed=2):
        self.rng = np.random.default_rng(seed)
        self.topic_of = np.array([p.pid % topic_count for p in corpus.pages])
        self.noise = noise
        self.n = corpus.n

    def search(self, target_topic: int, limit: int = 100) -> list[tuple[int, float]]:
        base = (self.topic_of == target_topic).astype(float)
        scores = base + self.rng.normal(0, self.noise, size=self.n)
        idx = np.argsort(-scores)[:limit]
        return [(int(i), float(scores[i])) for i in idx]


# ----------------------------------------------------------------- 융합

def rrf(lists: list[tuple[list[tuple[int, float]], float]], k: float = 60.0,
        limit: int = 50) -> list[tuple[int, float]]:
    """순위만 사용. 점수 스케일이 다른 랭커를 정규화 없이 합친다."""
    acc: dict[int, float] = defaultdict(float)
    for ranked, w in lists:
        for rank, (pid, _) in enumerate(ranked):
            acc[pid] += w / (k + rank + 1)
    return sorted(acc.items(), key=lambda x: -x[1])[:limit]


def rrf_with_prob_boost(lists, shortcut: dict[int, float], alpha: float = 3.0,
                        k: float = 60.0, limit: int = 50) -> list[tuple[int, float]]:
    """
    제안한 수정안: 비보정 랭커끼리는 RRF, 숏컷은 확률 부스트로 별도 결합.
    Wilson 하한은 이미 확률이므로 순위로 뭉개면 정보를 잃는다.
    """
    base = dict(rrf(lists, k=k, limit=limit * 4))
    for pid, rel in shortcut.items():
        base[pid] = base.get(pid, 0.0) * (1 + alpha * rel) + (0.02 * rel if pid not in base else 0.0)
    return sorted(base.items(), key=lambda x: -x[1])[:limit]


# ----------------------------------------------------------------- L2

def wilson(hits: int, misses: int, z: float = 1.96) -> float:
    n = hits + misses
    if n == 0:
        return 0.0
    p = hits / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    return max(0.0, min(1.0, (centre - margin) / denom))


class ShortcutCache:
    def __init__(self, min_reliability: float = 0.45):
        self.by_key: dict[tuple, dict] = {}
        self.min_rel = min_reliability

    @staticmethod
    def key(terms: list[str]) -> tuple:
        return tuple(sorted(set(terms)))

    def lookup(self, terms: list[str]) -> tuple[int, float] | None:
        e = self.by_key.get(self.key(terms))
        if not e:
            return None
        rel = wilson(e["hits"], e["misses"])
        if rel < self.min_rel:
            return None
        return e["dest"], rel

    def record(self, terms: list[str], dest: int, useful: bool):
        k = self.key(terms)
        e = self.by_key.setdefault(k, {"dest": dest, "hits": 0, "misses": 0})
        if e["dest"] != dest:
            e["misses"] += 1        # 같은 키에 다른 목적지 -> 충돌로 간주
            return
        if useful:
            e["hits"] += 1
        else:
            e["misses"] += 1

    def invalidate(self, dest: int):
        for k, e in list(self.by_key.items()):
            if e["dest"] == dest:
                del self.by_key[k]
