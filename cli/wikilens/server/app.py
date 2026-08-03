"""
WikiLens 공유 서비스.

혼자서는 모을 수 없는 통계만 모은다. 검색 엔진이 아니다 —
색인·토큰화·랭킹은 전부 클라이언트가 자기 볼트에 대해 수행한다.

그래서 이 서버는 페이지 ID와 키워드만 본다. 제목도 본문도 경로도 모른다.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .store import Store

app = FastAPI(title="WikiLens", version="0.1.0")
STORE = Store(Path(os.environ.get("WIKILENS_STATE", "./.wikilens-server")))


# ------------------------------------------------------------------ 스키마

class QueryEvent(BaseModel):
    session_id: str
    query: str
    keywords: list[str] = Field(default_factory=list)


class ReadEvent(BaseModel):
    session_id: str
    page_ids: list[str] = Field(default_factory=list)


class EndEvent(BaseModel):
    session_id: str


class BatchEvent(BaseModel):
    """
    훅이 세션 종료 시 한 번에 올리는 버퍼.

    Read 훅이 핫 패스라 매번 네트워크를 타면 세션이 느려진다.
    클라이언트가 로컬 파일에 append 하다가 종료 시 일괄 전송한다.
    """

    session_id: str
    events: list[dict] = Field(default_factory=list)


class HintRequest(BaseModel):
    keywords: list[str]
    # 클라이언트가 계산한 로컬 검색 점수. EB 사전분포로 쓴다.
    priors: dict[str, float] = Field(default_factory=dict)
    limit: int = 5


# ------------------------------------------------------------------ 관측

@app.post("/obs/query")
def obs_query(e: QueryEvent):
    STORE.on_query(e.session_id, e.query, e.keywords or _fallback_keywords(e.query))
    return {"ok": True}


@app.post("/obs/read")
def obs_read(e: ReadEvent):
    for pid in e.page_ids:
        STORE.on_read(e.session_id, pid)
    return {"ok": True, "count": len(e.page_ids)}


@app.post("/obs/end")
def obs_end(e: EndEvent):
    return {"ok": True, "finalized": STORE.on_end(e.session_id)}


@app.post("/obs/batch")
def obs_batch(b: BatchEvent):
    """버퍼 일괄 처리. 이벤트 순서가 궤적 조립의 전부이므로 순서를 지킨다."""
    n = 0
    for ev in b.events:
        t = ev.get("type")
        if t == "query":
            STORE.on_query(b.session_id, ev.get("query", ""),
                           ev.get("keywords") or _fallback_keywords(ev.get("query", "")))
        elif t == "read":
            STORE.on_read(b.session_id, str(ev.get("page_id", "")))
        n += 1
    finalized = STORE.on_end(b.session_id)
    return {"ok": True, "processed": n, "finalized": finalized}


# ------------------------------------------------------------------ 조회

@app.post("/hints")
def hints(r: HintRequest):
    hs = STORE.hints(r.keywords, priors=r.priors, limit=r.limit)
    return {"hints": [h.to_dict() for h in hs]}


@app.get("/stats")
def stats():
    return STORE.stats()


@app.post("/admin/sweep")
def sweep():
    return {"finalized": STORE.sweep()}


@app.get("/health")
def health():
    return {"ok": True}


def _fallback_keywords(query: str) -> list[str]:
    """
    클라이언트가 키워드를 안 보낸 경우(훅은 원문만 보낸다).

    **반드시 클라이언트와 같은 토크나이저여야 한다.** 다르면 항이 겹치지 않아
    포스팅 조회가 조용히 0건을 반환한다.
    """
    from ..tokenizer import tokenize

    return tokenize(query)
