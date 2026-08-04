"""
서버 스코어링 로직 테스트.

`wikilens.server.scoring`은 Kotlin `Scoring.kt`와 나란히 유지되는 정본이다 —
`check-contracts.sh`가 둘의 임계값·상수 일치를 검사한다. 여기 있는 것 대부분은
실제로 겪은 버그를 잠그는 테스트다("동작한다"가 아니라 "이 방식으로 깨졌었다").

공유 Python 서버(`app.py`/`store.py`, 클라이언트 로컬 색인/검색 `index.py`/`search.py`)는
`docs/DECISIONS.md`가 명시적으로 뒤집은 설계(훅이 서버로 관측을 푸시)의 잔재라
제거되었다 — 실제 서버판은 Kotlin + MCP다. `scoring.py`만 그 설계와 무관하게
계속 쓰인다.
"""
from __future__ import annotations

import pytest

from wikilens.server.scoring import (
    PRIOR_CEIL, QueryKind, classify, eb_lower, wilson_lower,
)
from wikilens.tokenizer import tokenize


def test_korean_bigrams_absorb_particles():
    """교착어 대응: '로그인을'과 '로그인은'이 공통 항을 갖는가. `stats` 명령의 어휘 격차 판정이 이에 의존한다."""
    a, b = set(tokenize("로그인을")), set(tokenize("로그인은"))
    assert a & b, "조사가 다른 형태끼리 겹치는 항이 있어야 함"


# --------------------------------------------------------- 사전분포 클램프

def test_prior_of_one_does_not_grant_certainty():
    """
    실제로 겪은 버그: 클라이언트가 최상위 후보에 사전확률 1.0을 주면
    Beta의 한쪽 모수가 0이 되어 한 번 관측에 신뢰도 1.0이 나왔다.
    """
    assert eb_lower(1, 0, prior_mean=1.0) < 0.75
    assert eb_lower(1, 0, prior_mean=1.0) == eb_lower(1, 0, prior_mean=PRIOR_CEIL)


def test_reliability_grows_with_evidence():
    prev = 0.0
    for h in [1, 2, 5, 10, 30]:
        cur = eb_lower(h, 0, prior_mean=0.3)
        assert cur > prev
        prev = cur


def test_prior_influence_vanishes_with_samples():
    """사전분포는 데이터가 없을 때만 일해야 한다."""
    gap_small = eb_lower(4, 3, 0.85) - eb_lower(4, 3, 0.05)
    gap_large = eb_lower(400, 300, 0.85) - eb_lower(400, 300, 0.05)
    assert gap_large < gap_small / 10


def test_eb_reduces_to_wilson_like_under_flat_prior():
    """균등 사전(0.5)이면 Wilson과 가까워야 한다 — EB가 그 일반화이므로."""
    assert abs(eb_lower(20, 5, 0.5) - wilson_lower(20, 5)) < 0.12


# --------------------------------------------------------- 경로 의존성 게이트

@pytest.mark.parametrize("q,kind", [
    ("온보딩 문서 어디 있어", QueryKind.LOCALIZATION),
    ("배포 가이드", QueryKind.LOCALIZATION),
    ("토큰이 어떻게 흐르나", QueryKind.TRACING),
    ("왜 이 정책이지", QueryKind.RATIONALE),
    # 실측 실패 사례: 마커 없는 7토큰 자연어 질의가 예전엔(임계값 3) UNKNOWN으로 빠졌다.
    ("컨텐츠 노출 권한 필터링에 대한 3가지 방법", QueryKind.LOCALIZATION),
    # 경계값 고정: 정확히 8토큰(마커 없음) -> LOCALIZATION, 9토큰(마커 없음) -> UNKNOWN
    ("컨텐츠 노출 권한 필터링에 대한 세가지 처리 절차", QueryKind.LOCALIZATION),
    ("컨텐츠 노출 권한 필터링에 대한 세가지 처리 절차 정리", QueryKind.UNKNOWN),
    # 임계값을 8로 올려도 "배경" 마커가 길이와 무관하게 먼저 걸려야 한다.
    ("이 기능을 이렇게 구현한 배경이 궁금해", QueryKind.RATIONALE),
])
def test_classify(q, kind):
    assert classify(q) is kind
