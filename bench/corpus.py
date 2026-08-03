"""
합성 위키 코퍼스 생성기.

이 벤치마크가 답할 수 있는 것과 없는 것:

  답할 수 있음 — 설계의 내부 논리가 일관적인가. 손익분기 공식이 맞는가.
                 앵커 텍스트가 어휘 격차를 실제로 메우는가. RRF가 신뢰도를
                 버리는 손실이 얼마인가. 파라미터 민감도가 어떤가.

  답할 수 없음 — 실제 위키에서 효용이 있는가. 실사용 적중률이 얼마인가.
                 이건 합성 코퍼스의 파라미터를 내가 정했기 때문이다.
                 여기 나오는 적중률은 내가 심어놓은 가정의 함수일 뿐이다.

핵심 모델링 결정:
  - 문서 제목/본문은 '공식 어휘', 앵커 텍스트는 '사용자 어휘'를 쓴다.
    vocab_gap 비율만큼 둘이 다르다. 이게 앵커 텍스트 가치의 원천이다.
  - 링크 그래프는 선호적 연결(preferential attachment)로 생성해
    실제 위키의 멱법칙 차수 분포를 흉내낸다.
  - 질의는 Zipf 분포로 페이지를 고른다. 핫셋이 존재해야 캐시가 의미를 갖는다.
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field


@dataclass
class Page:
    pid: int
    title_terms: list[str]      # 공식 어휘
    body_terms: list[str]
    outlinks: list[int] = field(default_factory=list)
    anchors_in: list[list[str]] = field(default_factory=list)  # 들어오는 앵커 텍스트


@dataclass
class Corpus:
    pages: list[Page]
    user_vocab: dict[int, list[str]]   # pid -> 사용자가 실제로 부르는 이름
    gap_pages: set[int]                # 공식/사용자 어휘가 다른 페이지

    @property
    def n(self) -> int:
        return len(self.pages)


def build_corpus(
    n_pages: int = 2000,
    vocab_gap: float = 0.45,      # 제목과 사용자 어휘가 다른 페이지 비율
    avg_outdeg: float = 12.0,
    body_len: int = 40,
    topic_count: int = 60,
    topical_link_bias: float = 0.7,   # 0=무작위 링크, 1=강한 주제 클러스터링
    seed: int = 0,
) -> Corpus:
    rng = np.random.default_rng(seed)

    # 어휘: 각 주제마다 공식 용어와 구어 용어를 따로 둔다
    formal = [f"formal{t}_{i}" for t in range(topic_count) for i in range(8)]
    casual = [f"casual{t}_{i}" for t in range(topic_count) for i in range(8)]
    common = [f"common{i}" for i in range(40)]   # 흔한 토큰. IDF를 떨어뜨리는 역할

    pages: list[Page] = []
    user_vocab: dict[int, list[str]] = {}
    gap_pages: set[int] = set()

    for pid in range(n_pages):
        topic = pid % topic_count
        base = topic * 8

        # 제목: 공식 어휘 2개 + 고유 식별 토큰
        title = [formal[base + rng.integers(0, 8)], f"uniq{pid}"]

        # 본문: 제목 어휘 반복 + 같은 주제 어휘 + 흔한 토큰
        body = list(title) * 3
        body += [formal[base + int(rng.integers(0, 8))] for _ in range(body_len // 3)]
        body += [common[int(rng.integers(0, len(common)))] for _ in range(body_len // 3)]

        pages.append(Page(pid=pid, title_terms=title, body_terms=body))

        # 사용자 어휘: gap이면 구어 용어, 아니면 제목과 동일
        if rng.random() < vocab_gap:
            gap_pages.add(pid)
            user_vocab[pid] = [casual[base + int(rng.integers(0, 8))], f"uniq{pid}"]
        else:
            user_vocab[pid] = list(title)

    # ---- 링크 그래프: 선호적 연결 + 주제 상관 ----
    # 실제 위키는 관련 주제끼리 링크된다. 이 상관을 넣지 않으면 링크가
    # 아무 정보도 담지 않게 되어 '비링크 도달 비율'이 자명하게 100%가 된다.
    topic_of = np.array([p % topic_count for p in range(n_pages)])
    indeg = np.ones(n_pages)
    for pid in range(n_pages):
        k = max(1, int(rng.poisson(avg_outdeg)))
        p = indeg.copy()
        p[topic_of == topic_of[pid]] *= (1.0 + 40.0 * topical_link_bias)
        p[pid] = 0.0
        p = p / p.sum()
        targets = rng.choice(n_pages, size=min(k, n_pages - 1), replace=False, p=p)
        for t in targets:
            if t == pid:
                continue
            pages[pid].outlinks.append(int(t))
            indeg[t] += 1
            # 앵커 텍스트는 사용자 어휘로 쓰인다 — 이게 핵심 가정
            pages[int(t)].anchors_in.append(list(user_vocab[int(t)]))

    return Corpus(pages=pages, user_vocab=user_vocab, gap_pages=gap_pages)


def zipf_query_stream(corpus: Corpus, n_queries: int, alpha: float = 1.1,
                      seed: int = 1) -> list[tuple[int, list[str]]]:
    """
    (정답 페이지, 질의 토큰) 스트림. 질의는 사용자 어휘로 표현된다.
    Zipf 분포라 핫셋이 존재한다 — 캐시가 의미를 가지려면 필요한 조건.
    """
    rng = np.random.default_rng(seed)
    ranks = np.arange(1, corpus.n + 1)
    w = 1.0 / ranks**alpha
    w /= w.sum()
    order = rng.permutation(corpus.n)     # 인기 순위를 pid에 무작위 배정

    out = []
    for _ in range(n_queries):
        r = int(rng.choice(corpus.n, p=w))
        pid = int(order[r])
        terms = list(corpus.user_vocab[pid])
        # 질의는 완전하지 않다: 토큰 일부만 쓰고 흔한 토큰이 섞인다
        if len(terms) > 1 and rng.random() < 0.5:
            terms = terms[:1]
        if rng.random() < 0.3:
            terms.append(f"common{int(rng.integers(0, 40))}")
        out.append((pid, terms))
    return out
