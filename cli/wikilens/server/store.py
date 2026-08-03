"""
궤적 로그 + 간선 저장소.

서버가 보관하는 것은 이것뿐이다:
    keywords -> {page_id: (hits, misses)}
    trajectories(session, keywords, read page_ids, outcome)

**콘텐츠 없음. 제목 없음. 앵커 없음. 경로 없음.**
그래서 ACL 문제가 대부분 사라진다 — 볼 수 없는 페이지의 ID를 받아도
클라이언트가 자기 볼트에 없으면 조용히 버린다.

궤적 로그는 append-only이며 유일하게 복구 불가능한 자산이다.
간선은 궤적의 함수라 언제든 재집계할 수 있다.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .scoring import Hint, QueryKind, classify, eb_lower

# 질의 재구성 판정 임계. 앞 질의와 키워드가 이만큼 겹치면 앞 시도가 실패한 것으로 본다.
REFORMULATION_OVERLAP = 0.5


def norm_terms(keywords: list[str]) -> list[str]:
    """정규화된 항 목록. 집합 전체가 아니라 **항 단위로** 색인한다."""
    return sorted({k.strip().lower() for k in keywords if k.strip()})


@dataclass
class QuerySpan:
    """한 질의와 그 뒤에 이어진 읽기들."""

    keywords: list[str]
    kind: QueryKind
    reads: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finalized: bool = False      # on_query와 on_end가 같은 스팬을 두 번 확정하는 것을 막는다

    def add_read(self, page_id: str) -> None:
        # 같은 페이지를 연속으로 읽으면 한 번으로 센다
        if not self.reads or self.reads[-1] != page_id:
            self.reads.append(page_id)


@dataclass
class Session:
    session_id: str
    spans: list[QuerySpan] = field(default_factory=list)
    last_touch: float = field(default_factory=time.time)

    @property
    def current(self) -> QuerySpan | None:
        return self.spans[-1] if self.spans else None


class Store:
    def __init__(self, root: Path, serve_threshold: float = 0.45):
        self.root = Path(root)
        self.traj_path = self.root / "state" / "trajectories.jsonl"
        self.traj_path.parent.mkdir(parents=True, exist_ok=True)
        self.serve_threshold = serve_threshold

        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        # **항 단위 포스팅**: term -> page_id -> [hits, misses]
        #
        # 키워드 집합 전체를 키로 쓰면 "로그인 붙이는 법 어디"와 "로그인 붙이는 법"이
        # 다른 키가 되어 카운트가 흩어진다. 자연어 질의는 매번 표현이 달라지므로
        # 정확 집합 일치는 성립하지 않는다. 항 단위 포스팅이면 공통 항으로 합쳐진다.
        #
        # 페이지가 값이 아니라 분포인 것도 중요하다. 같은 항에 목적지가 여럿인 건
        # 경쟁 상태가 아니라 질의가 모호한 것이므로, 벌주지 않고 분포로 기록한다.
        self._edges: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        # 궤적 단위 카운터. 간선 카운터는 항마다 증가하므로 궤적 수와 다르다.
        # 손익분기 p_hit > p_wrong/(n-1) 의 p_wrong 은 **궤적 단위**여야 한다.
        self._traj_hits = 0
        self._traj_misses = 0
        self._replay()

    # ------------------------------------------------------------ 관측

    def on_query(self, session_id: str, query: str, keywords: list[str]) -> None:
        with self._lock:
            s = self._sessions.setdefault(session_id, Session(session_id))
            prev = s.current
            kind = classify(query)
            span = QuerySpan(keywords=keywords, kind=kind)
            if prev is not None:
                # 재구성 판정: 키워드가 크게 겹치면 앞 시도가 실패한 것
                reformulated = _overlap(prev.keywords, keywords) >= REFORMULATION_OVERLAP
                self._finalize(session_id, prev, success=not reformulated)
            s.spans.append(span)
            s.last_touch = time.time()

    def on_read(self, session_id: str, page_id: str) -> None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None or s.current is None:
                return  # 질의 없이 읽은 것은 궤적이 아니다
            s.current.add_read(page_id)
            s.last_touch = time.time()

    def on_end(self, session_id: str) -> int:
        with self._lock:
            s = self._sessions.pop(session_id, None)
            if s is None:
                return 0
            n = 0
            for span in s.spans:
                if span.reads and not span.finalized:
                    self._finalize(session_id, span, success=True)
                    n += 1
            return n

    def _finalize(self, session_id: str, span: QuerySpan, success: bool) -> None:
        """
        궤적을 확정하고 로그에 남긴다.

        '유용했다' 판정이 이 설계의 최대 미해결 지점이다. 훅은 무엇을 읽었는지만
        보여주고 그게 답이었는지는 알려주지 않는다. 여기서는 두 가지 약한 신호를 쓴다:

          - 마지막으로 읽은 페이지가 답일 확률이 높다 (탐색은 성공에서 멈춘다)
          - 키워드가 겹치는 질의가 뒤따르면 앞 시도는 실패였다 (재구성 신호)

        웹 검색의 abandonment 신호와 같은 구조이고, 마찬가지로 노이즈가 있다.
        `p_wrong`으로 그 노이즈의 크기를 측정한다.
        """
        if not span.reads or span.finalized:
            return
        span.finalized = True
        dest = span.reads[-1]
        rec = {
            "ts": round(time.time(), 3),
            "session": session_id,
            "keywords": sorted(set(span.keywords)),
            "kind": span.kind.value,
            "reads": list(span.reads),
            "dest": dest,
            "success": bool(success),
        }
        self._append(rec)
        self._apply(rec)

    # ------------------------------------------------------------ 조회

    def hints(self, keywords: list[str], priors: dict[str, float] | None = None,
              limit: int = 5) -> list[Hint]:
        """
        항 단위 포스팅을 조회해 후보를 모으고 커버리지로 가중한다.

        한 궤적이 자기 키워드 전부를 증가시키므로 항별 카운트를 더하면 중복 계산이
        된다. 대표값(최대)을 쓰고, 대신 **몇 개 항이 이 페이지를 가리켰는지**를
        커버리지로 반영한다.

        `priors`는 클라이언트가 계산한 로컬 검색 점수다. 서버는 콘텐츠를 모르므로
        사전분포를 스스로 만들 수 없고, 만들 필요도 없다.
        """
        priors = priors or {}
        terms = norm_terms(keywords)
        if not terms:
            return []

        matched: dict[str, list[int]] = {}
        cover: dict[str, int] = defaultdict(int)
        with self._lock:
            for t in terms:
                for pid, (h, m) in self._edges.get(t, {}).items():
                    cover[pid] += 1
                    cur = matched.get(pid)
                    if cur is None or (h - m) > (cur[0] - cur[1]):
                        matched[pid] = [h, m]

        out = []
        for pid, (h, m) in matched.items():
            c = cover[pid] / len(terms)
            rel = eb_lower(h, m, prior_mean=priors.get(pid, 0.3)) * c
            if rel >= self.serve_threshold:
                out.append(Hint(pid, h, m, rel))
        out.sort(key=lambda x: -x.reliability)
        return out[:limit]

    def stats(self) -> dict:
        with self._lock:
            keys = len(self._edges)          # 색인된 항 수
            pairs = sum(len(d) for d in self._edges.values())
            ambiguous = sum(1 for d in self._edges.values() if len(d) > 1)
            active = len(self._sessions)
            hits, misses = self._traj_hits, self._traj_misses
        trials = hits + misses
        return {
            "keys": keys,
            "key_page_pairs": pairs,
            "ambiguous_keys": ambiguous,
            "hits": hits,
            "misses": misses,
            # 손익분기 p_hit > p_wrong/(n-1) 의 분자. 적중률보다 이게 중요하다.
            "p_wrong": round(misses / trials, 4) if trials else None,
            "active_sessions": active,
            "trajectories": self._traj_count(),
        }

    # ------------------------------------------------------------ 영속화

    def _append(self, rec: dict) -> None:
        with self.traj_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def _apply(self, rec: dict) -> None:
        if not QueryKind(rec["kind"]).cacheable:
            return  # 경로 의존 질의는 간선을 만들지 않는다
        if rec["success"]:
            self._traj_hits += 1
        else:
            self._traj_misses += 1
        for t in norm_terms(rec["keywords"]):
            slot = self._edges[t][rec["dest"]]
            slot[0 if rec["success"] else 1] += 1

    def _replay(self) -> None:
        """간선은 궤적의 함수다. 기동 시 재생하면 복구된다."""
        if not self.traj_path.exists():
            return
        for line in self.traj_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    self._apply(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue

    def _traj_count(self) -> int:
        if not self.traj_path.exists():
            return 0
        return sum(1 for l in self.traj_path.open(encoding="utf-8") if l.strip())

    def sweep(self, idle_seconds: float = 1800) -> int:
        """세션 종료 훅을 못 받은 경우 대비. 유휴 세션을 확정한다."""
        now = time.time()
        stale = [sid for sid, s in self._sessions.items() if now - s.last_touch > idle_seconds]
        return sum(self.on_end(sid) for sid in stale)


def _overlap(a: list[str], b: list[str]) -> float:
    sa, sb = {x.lower() for x in a}, {x.lower() for x in b}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))
