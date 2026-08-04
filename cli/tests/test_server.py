"""
서버판 테스트.

여기 있는 것 대부분은 데모를 돌리다 실제로 겪은 버그다.
"동작한다"가 아니라 "이 방식으로 깨졌었다"를 잠그는 테스트들이다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wikilens.server.scoring import (
    PRIOR_CEIL, QueryKind, classify, eb_lower, wilson_lower,
)
from wikilens.server.store import Store, norm_terms
from wikilens.tokenizer import tokenize


def sess(store: Store, sid: str, query: str, reads: list[str]) -> None:
    store.on_query(sid, query, tokenize(query))
    for p in reads:
        store.on_read(sid, p)
    store.on_end(sid)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path)


# --------------------------------------------------------- 토크나이저 일치

def test_client_and_server_tokenize_identically():
    """
    실제로 겪은 버그: 클라이언트는 바이그램을 만들고 서버 폴백은 단순 분리를 해서
    항이 전혀 겹치지 않았다. 조회가 조용히 0건을 반환했다.
    """
    from wikilens.index import tokenize as client_tok
    from wikilens.server.app import _fallback_keywords as server_tok

    for q in ["로그인 붙이는 법", "OAuth 2.0 인가 코드", "배포 파이프라인 어디"]:
        assert client_tok(q) == server_tok(q) == tokenize(q)


def test_korean_bigrams_absorb_particles():
    """교착어 대응: '로그인을'과 '로그인은'이 공통 항을 갖는가."""
    a, b = set(tokenize("로그인을")), set(tokenize("로그인은"))
    assert a & b, "조사가 다른 형태끼리 겹치는 항이 있어야 함"


# --------------------------------------------------------- 항 단위 포스팅

def test_gate_is_stricter_without_priors(store):
    """사전확률이 없으면 같은 전적이라도 게이트를 통과하지 못한다."""
    for i in range(5):
        sess(store, f"n{i}", "로그인 붙이는 법", ["300000001"])
    assert store.hints(tokenize("로그인 붙이는 법")) == []
    assert store.hints(tokenize("로그인 붙이는 법"), priors={"300000001": 0.75})


def test_different_phrasings_share_credit(store):
    """
    실제로 겪은 버그: 키워드 '집합 전체'를 키로 쓰면 표현이 조금만 달라도
    다른 키가 되어 카운트가 흩어진다. 자연어 질의는 매번 표현이 다르다.
    """
    for i, q in enumerate([
        "로그인 붙이는 법 문서 어디 있어",
        "로그인 붙이는 법 알려줘",
        "로그인 붙이는 법",
        "로그인 붙이는 법 페이지",
        "로그인 붙이는 법 가이드",
    ]):
        sess(store, f"s{i}", q, ["300000001"])

    # 실제 클라이언트는 로컬 검색 점수를 사전확률로 함께 보낸다.
    # 사전확률 없이는 게이트가 더 엄격해진다 — 5승이면 EB 0.396으로 임계 미달.
    hs = store.hints(tokenize("로그인 붙이는 법"), priors={"300000001": 0.75})
    assert hs, "표현이 달라도 공통 항으로 신뢰도가 합쳐져야 함"
    assert hs[0].page_id == "300000001"
    assert hs[0].hits >= 5


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


def test_tracing_is_logged_but_creates_no_edge(store):
    for i in range(8):
        sess(store, f"t{i}", "토큰이 어떻게 흐르나", ["300000002", "300000001"])
    assert store.stats()["trajectories"] == 8, "궤적은 남아야 함 (분석용)"
    assert store.hints(tokenize("토큰이 어떻게 흐르나")) == [], "간선은 없어야 함"
    assert store.stats()["keys"] == 0


# --------------------------------------------------------- 목적지 분포

def test_ambiguous_query_is_distribution_not_failure(store):
    """
    같은 항에 다른 목적지가 오는 건 경쟁 상태가 아니라 질의가 모호한 것이다.
    숏컷을 실패로 벌주면 정확한 항목의 신뢰도까지 떨어진다.
    """
    for i in range(6):
        sess(store, f"a{i}", "설정 문서 어디", ["400000001"])
    for i in range(3):
        sess(store, f"b{i}", "설정 문서 어디", ["400000002"])

    st = store.stats()
    assert st["misses"] == 0, "모호함을 실패로 기록하면 안 됨"
    assert st["ambiguous_keys"] > 0

    hs = {h.page_id: h for h in store.hints(
        tokenize("설정 문서 어디"), priors={"400000001": 0.75, "400000002": 0.5}, limit=5)}
    assert "400000001" in hs
    assert hs["400000001"].hits == 6


# --------------------------------------------------------- 재구성 신호

def test_reformulation_marks_previous_attempt_failed(store):
    """키워드가 겹치는 질의가 뒤따르면 앞 시도는 실패였다는 약한 신호."""
    store.on_query("r1", "배포 파이프라인 어디", tokenize("배포 파이프라인 어디"))
    store.on_read("r1", "500000009")                      # 헛걸음
    store.on_query("r1", "배포 파이프라인 문서", tokenize("배포 파이프라인 문서"))
    store.on_read("r1", "500000001")                      # 정답
    store.on_end("r1")

    recs = [json.loads(l) for l in
            (store.traj_path).read_text(encoding="utf-8").splitlines() if l]
    assert len(recs) == 2
    assert recs[0]["dest"] == "500000009" and recs[0]["success"] is False
    assert recs[1]["dest"] == "500000001" and recs[1]["success"] is True
    assert store.stats()["misses"] == 1, "p_wrong 에 반영되어야 함"


# --------------------------------------------------------- 영속성

def test_edges_rebuilt_from_trajectory_log(tmp_path):
    """궤적만이 복구 불가능한 자산이고, 간선은 그 함수다."""
    s1 = Store(tmp_path)
    for i in range(6):
        sess(s1, f"p{i}", "온보딩 문서 어디", ["600000001"])
    before = s1.hints(tokenize("온보딩 문서 어디"))

    s2 = Store(tmp_path)          # 재기동 = 로그 재생
    after = s2.hints(tokenize("온보딩 문서 어디"))
    assert [h.to_dict() for h in before] == [h.to_dict() for h in after]


def test_read_without_query_is_ignored(store):
    """질의 없는 읽기는 궤적이 아니다. 훅이 잡은 무관한 파일 읽기를 거른다."""
    store.on_read("z1", "700000001")
    store.on_end("z1")
    assert store.stats()["trajectories"] == 0


# --------------------------------------------------------- 서버 무지

def test_server_stores_no_content(store, tmp_path):
    """서버 저장물에 제목·경로·본문이 없어야 한다. ACL 안전성의 근거."""
    sess(store, "c1", "인수합병 실사 자료 어디", ["800000001"])
    raw = (tmp_path / "state" / "trajectories.jsonl").read_text(encoding="utf-8")
    rec = json.loads(raw.splitlines()[0])
    assert set(rec) == {"ts", "session", "keywords", "kind", "reads", "dest", "success"}
    assert "mirror/" not in raw and ".md" not in raw
