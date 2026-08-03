"""
클라이언트 검색: 로컬 랭킹 + 서버 힌트 융합.

서버 힌트는 페이지 ID와 신뢰도뿐이다. 제목도 경로도 서버는 모른다.
클라이언트가 자기 볼트에서 ID를 찾아 채워 넣고, **볼트에 없으면 조용히 버린다.**
이것이 ACL 시행이다 — 볼 수 없는 문서는 애초에 볼트에 없다.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import index as idxmod
from . import layout


@dataclass
class Result:
    page_id: str
    title: str
    path: str
    score: float
    source: str            # "local" | "server" | "both"
    reliability: float | None = None


@dataclass
class SearchReport:
    query: str
    terms: list[str] = field(default_factory=list)
    max_idf: float = 0.0
    confident: bool = False
    local_candidates: int = 0
    server_hints: int = 0
    dropped_by_acl: int = 0
    results: list[Result] = field(default_factory=list)


RRF_K = 60.0
IDF_THRESHOLD_RATIO = 0.55


def entry_idf_threshold(n_docs: int) -> float:
    """
    코퍼스 크기에 맞춘 진입점 IDF 임계.

    BM25 IDF는 소규모 코퍼스에서 압축된다 — 문서 4개면 df=1 항의 IDF가 1.20에
    그치고, 10개면 1.99가 된다. 고정 상수로 두면 작은 볼트에서 모든 질의가
    Diffuse로 빠진다. 그 코퍼스에서 가능한 최대 IDF(df=1)의 일정 비율로 잡는다.
    """
    if n_docs < 2:
        return 0.0
    max_possible = math.log(((n_docs - 1 + 0.5) / 1.5) + 1.0)
    return IDF_THRESHOLD_RATIO * max_possible


def search(
    root: Path,
    query: str,
    server: str | None = None,
    limit: int = 8,
    timeout: float = 1.5,
) -> SearchReport:
    root = Path(root)
    idx = idxmod.load(root)
    terms = idxmod.tokenize(query)
    rep = SearchReport(query=query, terms=terms)

    if not idx.n or not terms:
        return rep

    local = idx.search(terms, limit=limit * 3)
    rep.local_candidates = len(local)
    rep.max_idf = idx.max_idf(terms)
    # 매칭된 항이 전부 흔한 토큰이면 어휘 경로가 실패한 것.
    # 아무것도 읽기 전에 내리는 판정이라 비용이 0이다.
    rep.confident = rep.max_idf >= entry_idf_threshold(idx.n)

    # 로컬 점수를 [0,1]로 정규화해 서버 EB 사전분포로 넘긴다.
    priors: dict[str, float] = {}
    if local:
        top = local[0][1] or 1.0
        priors = {pid: min(1.0, s / top) for pid, s in local}

    # 서버로는 **특이적인 항만** 보낸다. 흔한 토큰은 어느 페이지든 가리켜
    # 포스팅을 오염시키고, 프라이버시 측면에서도 덜 보내는 편이 낫다.
    keep = [t for t in terms if idx.idf(t) >= 0.4 * rep.max_idf] or terms
    hints = _fetch_hints(server, keep, priors, limit, timeout) if server else []
    rep.server_hints = len(hints)

    # ---- 융합 ----
    acc: dict[str, dict] = {}
    for rank, (pid, _s) in enumerate(local):
        acc[pid] = {"score": 1.0 / (RRF_K + rank + 1), "source": "local", "rel": None}

    for rank, h in enumerate(hints):
        pid = h["page_id"]
        if pid not in idx.docs:
            # 서버가 아는 페이지를 내 볼트에서는 볼 수 없다 -> 버린다.
            # 정상 동작이며, 동시에 ACL 정합성 검사이기도 하다.
            rep.dropped_by_acl += 1
            continue
        # 힌트는 순위가 아니라 신뢰도로 가중한다. Wilson/EB는 이미 확률이므로
        # 순위로 뭉개면 보정된 정보를 버리게 된다.
        boost = 1.6 * h["reliability"] / (RRF_K + rank + 1)
        if pid in acc:
            acc[pid]["score"] += boost
            acc[pid]["source"] = "both"
            acc[pid]["rel"] = h["reliability"]
        else:
            acc[pid] = {"score": boost, "source": "server", "rel": h["reliability"]}

    for pid, a in sorted(acc.items(), key=lambda kv: -kv[1]["score"])[:limit]:
        d = idx.docs[pid]
        rep.results.append(
            Result(pid, d.title, d.path, round(a["score"], 6), a["source"], a["rel"])
        )
    return rep


def _fetch_hints(server: str, terms: list[str], priors: dict[str, float],
                 limit: int, timeout: float) -> list[dict]:
    """서버가 없거나 느리면 조용히 건너뛴다. 로컬 검색은 항상 동작해야 한다."""
    try:
        import httpx

        r = httpx.post(
            f"{server.rstrip('/')}/hints",
            json={"keywords": terms, "priors": priors, "limit": limit},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("hints", [])
    except Exception:  # noqa: BLE001
        return []
