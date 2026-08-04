"""
서버측 스코어링.

두 가지만 한다: 무엇을 학습 대상으로 삼을지 거르고(게이트), 얼마나 믿을지 정한다(EB).
콘텐츠는 보지 않는다 — 서버에는 없다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class QueryKind(str, Enum):
    """경로 의존성 분류. LOCALIZATION만 캐싱 가능하다."""

    LOCALIZATION = "LOCALIZATION"
    TRACING = "TRACING"
    RATIONALE = "RATIONALE"
    UNKNOWN = "UNKNOWN"

    @property
    def cacheable(self) -> bool:
        return self is QueryKind.LOCALIZATION


# 경로가 곧 답인 질의 — 목적지만 주면 답을 삭제하는 것과 같다
# 주의: 도메인 명사를 마커로 쓰면 안 된다. '파이프라인', '워크플로' 같은 단어는
# "배포 파이프라인 문서 어디"처럼 순수 localization 질의에도 흔히 등장한다.
# 흐름을 **묻는** 표현만 넣는다.
_TRACING = (
    "흐름", "어떻게 동작", "어떻게 흐르", "어떻게 처리", "호출 경로", "호출 체인",
    "거쳐", "생명주기", "단계별로",
    "how does", "how is", "trace through", "end to end", "walk through",
)
# 근거가 경유 노드에 분산 — 목적지만으로 재구성 불가
_RATIONALE = (
    "왜 ", "왜?", "이유", "근거", "의도", "배경", "설계 결정", "트레이드오프",
    "why ", "why?", "rationale", "reason for", "trade-off", "tradeoff",
)
# 목적지 자체가 답 — 경유 노드를 건너뛰어도 무손실
# 위치 명사뿐 아니라 **조회 동사**도 포함한다. "로그인 붙이는 법 알려줘"는
# 명백한 조회인데 위치 명사가 없어 UNKNOWN으로 빠지던 사례가 있었다.
# UNKNOWN 기본값을 캐싱 가능으로 뒤집는 대신 여기를 넓히는 편이 게이트를 약화시키지 않는다.
_LOCALIZATION = (
    "어디", "어딨", "위치", "정의", "찾아", "찾기", "문서", "가이드", "페이지",
    "알려줘", "보여줘", "알려주", "보여주", "있나", "있어", "뭐야", "어느",
    "where is", "where are", "which page", "find the", "locate", "docs for",
    "show me", "tell me", "look up",
)


def classify(query: str) -> QueryKind:
    """
    LLM 호출 없이 어휘 규칙으로만 판정한다.
    조회 경로에 LLM이 들어가면 아끼려던 비용을 되불러들여 구조가 뒤집힌다.

    오분류 비용이 비대칭이므로(캐싱 가능한 걸 놓치면 원래 비용, 경로 의존을
    캐싱하면 틀린 답) 경로 의존 신호를 우선한다.
    """
    q = query.lower()
    if any(m in q for m in _RATIONALE):
        return QueryKind.RATIONALE
    if any(m in q for m in _TRACING):
        return QueryKind.TRACING
    if any(m in q for m in _LOCALIZATION):
        return QueryKind.LOCALIZATION
    # 마커 어디에도 안 걸리는 짧은 질의는 대개 심볼/제목 조회다. 문턱을 8로 잡은
    # 근거: 실측 실패 사례("컨텐츠 노출 권한 필터링에 대한 3가지 방법", 마커 없음)가
    # 7토큰이었고 자연어 조회 질의 대부분이 마커 없이 3토큰을 넘긴다 — 3은 너무
    # 타이트해서 학습 간선이 거의 안 생겼다. 마커 체크가 이 폴백보다 먼저 실행되므로
    # RATIONALE/TRACING 질의는 마커가 커버하는 한 길이와 무관하게 안전하다. 마커에
    # 안 걸리는 긴 경로의존 질의를 오분류할 잔여 위험은 남는다 — pWrong으로 모니터링.
    if len(query.strip().split()) <= 8:
        return QueryKind.LOCALIZATION
    return QueryKind.UNKNOWN


# ------------------------------------------------------------------ 신뢰도

def wilson_lower(hits: int, misses: int, z: float = 1.96) -> float:
    """참고용. 실제 서빙 판정에는 eb_lower를 쓴다."""
    n = hits + misses
    if n == 0:
        return 0.0
    p = hits / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    return max(0.0, min(1.0, (centre - margin) / (1 + z2 / n)))


# 사전분포는 부드러운 신호이지 확신이 아니다. 0이나 1에 붙으면 Beta의 한쪽 모수가
# 0이 되어 사전분포가 관측을 완전히 압도한다 — 한 번 관측에 신뢰도 1.0이 나온다.
PRIOR_FLOOR, PRIOR_CEIL = 0.05, 0.85


def eb_lower(hits: int, misses: int, prior_mean: float, kappa: float = 5.0,
             q: float = 0.05) -> float:
    """
    검색 점수를 사전분포로 쓰는 Beta-Binomial 사후 하한.

    Wilson은 균등 사전(정보 없음)의 특수한 경우다. 클라이언트가 계산한 로컬 검색
    점수를 사전분포로 주면, 같은 전적이라도 검색 신호가 강한 후보를 더 믿는다.
    표본이 쌓이면 사전분포 영향이 자동으로 사라진다.

    scipy 없이 계산하려고 Beta 분위수를 뉴턴법으로 푼다 — 서버 의존성을 줄인다.
    """
    pm = min(PRIOR_CEIL, max(PRIOR_FLOOR, prior_mean))
    a = pm * kappa + hits
    b = (1.0 - pm) * kappa + misses
    return _beta_ppf(q, a, b)


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _beta_cdf(x: float, a: float, b: float) -> float:
    """정규화 불완전 베타 함수. 연분수 전개."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = _log_beta(a, b)
    front = math.exp(a * math.log(x) + b * math.log(1 - x) - lbeta)
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(x, a, b) / a
    return 1.0 - math.exp(b * math.log(1 - x) + a * math.log(x) - lbeta) * _betacf(1 - x, b, a) / b


def _betacf(x: float, a: float, b: float, itmax: int = 200, eps: float = 3e-12) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _beta_ppf(q: float, a: float, b: float, tol: float = 1e-8) -> float:
    """이분법. 안정성이 속도보다 중요하다."""
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _beta_cdf(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


@dataclass
class Hint:
    """서버가 반환하는 힌트. 페이지 ID와 신뢰도뿐 — 제목도 내용도 없다."""

    page_id: str
    hits: int
    misses: int
    reliability: float

    def to_dict(self) -> dict:
        return {
            "page_id": self.page_id,
            "hits": self.hits,
            "misses": self.misses,
            "reliability": round(self.reliability, 4),
        }
