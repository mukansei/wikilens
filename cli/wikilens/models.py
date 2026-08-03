"""
도메인 모델.

이 파일의 핵심은 `canonical_json`이다. 구조 서명이 매 싱크마다 재생성되므로,
직렬화가 결정적이지 않으면 아무것도 안 바뀌었는데 전체 파일이 변경으로 잡힌다.
로컬판은 diff를 쓰지 않지만, 포맷을 나중에 고치면 서버판 전환 시
전체 재크롤이 필요해진다. 그래서 처음부터 고정한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class Link:
    """페이지 밖으로 나가는 링크 하나."""

    to: str | None          # 해석된 대상 페이지 ID. 미해결이면 None
    anchor: str             # 앵커 텍스트 = 사용자 어휘
    to_title: str | None = None   # 해석 실패 시 원본 제목 보존

    def key(self) -> tuple:
        return (self.to or "", self.to_title or "", self.anchor)


@dataclass
class StructureSignature:
    """
    무효화 판정에 쓰이는 구조 서명.

    본문에서 추출 가능한 중복 데이터인데 굳이 분리하는 이유:
    산문 수정은 pages/만 바꾸고 이 파일은 그대로여야 한다.
    서버판에서 `git diff -- structure/` 한 줄이 무효화 대상 목록이 된다.
    """

    page_id: str
    title: str
    space: str
    version: int
    headings: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "title": self.title,
            "space": self.space,
            "version": self.version,
            "headings": list(self.headings),
            # 정렬 필수: Confluence가 순서를 바꿔 내려주는 경우가 있다.
            "links": [
                {"to": l.to, "to_title": l.to_title, "anchor": l.anchor}
                for l in sorted(self.links, key=lambda x: x.key())
            ],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "StructureSignature":
        return StructureSignature(
            page_id=d["page_id"],
            title=d["title"],
            space=d.get("space", ""),
            version=int(d.get("version", 0)),
            headings=list(d.get("headings", [])),
            links=[
                Link(to=l.get("to"), anchor=l.get("anchor", ""), to_title=l.get("to_title"))
                for l in d.get("links", [])
            ],
        )


@dataclass
class Page:
    page_id: str
    title: str
    space: str
    version: int
    raw_xhtml: str
    markdown: str
    signature: StructureSignature


@dataclass
class AnchorEntry:
    """앵커 전치 결과. '대상 기준으로 뒤집은' 뷰."""

    target: str
    title: str
    path: str
    anchors: list[dict[str, str]]
    indeg: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "title": self.title,
            "path": self.path,
            "anchors": self.anchors,
            "indeg": self.indeg,
        }


def canonical_json(obj: Any) -> str:
    """
    결정적 직렬화.

    - sort_keys: 키 순서가 딕셔너리 삽입 순서에 의존하지 않게
    - separators: 공백 변동 제거
    - ensure_ascii=False: 한글이 \\uXXXX로 부풀지 않게 (grep 가능성 유지)
    - 끝에 개행: POSIX 텍스트 파일 규약, diff 노이즈 방지
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"


def jsonl_line(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
