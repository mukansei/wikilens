"""
디스크 레이아웃 규칙.

권위 있는 노드 식별자는 Confluence 페이지 ID다. 제목이 아니다.
제목으로 경로를 잡으면 이름 변경이 삭제+생성으로 보여 diff가 오염되고,
나중에 서버판에서 학습 가중치가 통째로 날아간다.
"""
from __future__ import annotations

from pathlib import Path

# 한 디렉터리에 파일이 수천 개 쌓이는 것을 막는다.
#
# **ID 의 앞이 아니라 뒤를 쓴다.** Confluence 페이지 ID 는 시간순 연속 할당이라
# 앞자리에 엔트로피가 거의 없다 — 한 스페이스의 문서는 대개 같은 시기에 만들어져
# `102728003`·`102728244` 처럼 앞부분을 공유한다. 앞자리로 쪼개면 뭉친다.
#
# Acme 실측(2,377건, 2026-08-06):
#
#   앞2/앞4 (구 규칙)   디렉터리   52 · 최대 378 · 평균 45.7   ← 10,000 리프 중 52개만 씀
#   뒤2/뒤4            디렉터리 2170 · 최대   3 · 평균  1.1   ← 파일당 디렉터리 1개, 과함
#   뒤2   (현 규칙)     디렉터리  100 · 최대  37 · 평균 23.8
#
# 구 규칙은 2단계를 쓰고도 최대 378개였다 — 1단계를 더해 466→378, 18% 줄었을 뿐이다.
# 뒤에서 쪼개면 **1단계만으로 열 배 낫다.** 디렉터리 수가 100으로 고정되므로 30만
# 페이지까지 "수천 개"를 넘지 않는다(10만 페이지 → 디렉터리당 약 1,000개).
#
# 2단계로 늘릴 이유가 없어 `SHARD_DEPTH` 를 1로 줄였다. 상수는 남겨둔다 — 코퍼스가
# 수십만으로 커지면 여기만 올리면 된다(대신 볼트 이전이 필요하다).
SHARD_DEPTH = 1
SHARD_WIDTH = 2


def shard(page_id: str) -> str:
    """
    '102728003' -> '03'. ID 가 짧으면 0으로 패딩한다.

    **뒤에서 잘라낸다.** 앞에서 자르면 시간순 ID 가 뭉쳐 한 디렉터리에 몰린다
    (위 주석의 실측 참조).
    """
    pid = str(page_id).strip()
    if not pid:
        raise ValueError("빈 페이지 ID")
    tail = pid.rjust(SHARD_DEPTH * SHARD_WIDTH, "0")[-(SHARD_DEPTH * SHARD_WIDTH):]
    return "/".join(
        tail[i * SHARD_WIDTH : (i + 1) * SHARD_WIDTH] for i in range(SHARD_DEPTH)
    )


def raw_path(root: Path, page_id: str) -> Path:
    return root / "mirror" / "raw" / shard(page_id) / f"{page_id}.xhtml"


def page_path(root: Path, page_id: str) -> Path:
    return root / "mirror" / "pages" / shard(page_id) / f"{page_id}.md"


def structure_path(root: Path, page_id: str) -> Path:
    return root / "mirror" / "structure" / shard(page_id) / f"{page_id}.json"


def rel_page_path(page_id: str) -> str:
    """anchors.jsonl과 ALIASES.md에 기록되는 상대 경로.

    이 값을 저장해두는 이유는 로컬판 사용자가 샤딩 규칙을 몰라도
    grep 결과에서 바로 파일을 열 수 있게 하기 위해서다.
    """
    return f"mirror/pages/{shard(page_id)}/{page_id}.md"


def anchors_path(root: Path) -> Path:
    return root / "derived" / "anchors.jsonl"


def aliases_path(root: Path) -> Path:
    return root / "ALIASES.md"


def tree_path(root: Path) -> Path:
    """계층 구조(부모-자식) 목차. 어휘를 몰라도 영역만 알면 위에서 내려가며 찾는 용도."""
    return root / "TREE.md"


def sync_state_path(root: Path) -> Path:
    return root / "mirror" / ".sync-state.json"


def ensure_parent(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
