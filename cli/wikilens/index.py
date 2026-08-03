"""
로컬 색인. 자기 볼트에 대해서만 만든다.

서버가 색인을 갖지 않는 이유는 그게 ACL 문제의 원천 전부이기 때문이다.
클라이언트가 자기 권한 범위의 코퍼스에 대해 IDF를 계산하면 값도 더 정확하다 —
사용자가 볼 수 있는 문서에 대한 IDF가 그 사용자에게 맞는 값이다.

10k 페이지 색인 구축은 수 초라 기동 시 매번 다시 지어도 된다.
증분 갱신은 드리프트가 조용히 쌓이는 자리라 비용이 없으면 피한다.
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from . import layout

# 필드 가중치. 앵커가 가장 높은 이유는 그게 사용자 어휘이기 때문이다.
FIELD_WEIGHTS = {"anchor": 4.0, "title": 3.0, "body": 1.0}
K1, B = 1.2, 0.75

from .tokenizer import tokenize  # noqa: F401  클라이언트·서버 공유


@dataclass
class Doc:
    page_id: str
    title: str
    path: str


class LocalIndex:
    def __init__(self):
        self.docs: dict[str, Doc] = {}
        self.postings: dict[str, dict[str, float]] = defaultdict(dict)  # term -> pid -> tf
        self.doclen: dict[str, float] = {}
        self.df: dict[str, int] = defaultdict(int)
        self.avgdl = 1.0

    @property
    def n(self) -> int:
        return len(self.docs)

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        if not df or not self.n:
            return 0.0
        return math.log(((self.n - df + 0.5) / (df + 0.5)) + 1.0)

    def add(self, pid: str, title: str, path: str, fields: dict[str, str]) -> None:
        self.docs[pid] = Doc(pid, title, path)
        tf: dict[str, float] = defaultdict(float)
        for field, text in fields.items():
            w = FIELD_WEIGHTS.get(field, 1.0)
            for t in tokenize(text):
                tf[t] += w
        self.doclen[pid] = sum(tf.values()) or 1.0
        for t, v in tf.items():
            self.postings[t][pid] = v
            self.df[t] += 1

    def finalize(self) -> None:
        self.avgdl = (sum(self.doclen.values()) / len(self.doclen)) if self.doclen else 1.0

    def search(self, terms: list[str], limit: int = 20) -> list[tuple[str, float]]:
        acc: dict[str, float] = defaultdict(float)
        for t in terms:
            idf = self.idf(t)
            if idf <= 0:
                continue
            for pid, f in self.postings.get(t, {}).items():
                dl = self.doclen[pid]
                acc[pid] += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self.avgdl))
        return sorted(acc.items(), key=lambda kv: -kv[1])[:limit]

    def max_idf(self, terms: list[str]) -> float:
        vals = [self.idf(t) for t in terms]
        return max(vals) if vals else 0.0


def load(root: Path, with_body: bool = True) -> LocalIndex:
    """anchors.jsonl 과 pages/ 로 색인을 만든다."""
    root = Path(root)
    idx = LocalIndex()

    anchors_by_pid: dict[str, list[str]] = {}
    ap = layout.anchors_path(root)
    if ap.exists():
        for line in ap.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            anchors_by_pid[e["target"]] = [a["text"] for a in e.get("anchors", [])]

    sp = layout.sync_state_path(root)
    if not sp.exists():
        return idx
    pages = json.loads(sp.read_text(encoding="utf-8")).get("pages", {})

    for pid, meta in pages.items():
        body = ""
        if with_body:
            f = layout.page_path(root, pid)
            if f.exists():
                body = f.read_text(encoding="utf-8")
        idx.add(
            pid,
            meta.get("title", ""),
            layout.rel_page_path(pid),
            {
                "title": meta.get("title", ""),
                "anchor": " ".join(anchors_by_pid.get(pid, [])),
                "body": body,
            },
        )
    idx.finalize()
    return idx
