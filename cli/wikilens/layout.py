"""
디스크 레이아웃 규칙.

권위 있는 노드 식별자는 Confluence 페이지 ID다. 제목이 아니다.
제목으로 경로를 잡으면 이름 변경이 삭제+생성으로 보여 diff가 오염되고,
나중에 서버판에서 학습 가중치가 통째로 날아간다.
"""
from __future__ import annotations

from pathlib import Path

# 한 디렉터리에 파일이 수천 개 쌓이는 것을 막는다.
# 10k 페이지 기준 리프당 평균 1개 미만이지만, 대형 위키에서도 선형으로 늘지 않는다.
SHARD_DEPTH = 2
SHARD_WIDTH = 2


def shard(page_id: str) -> str:
    """'123456789' -> '12/34'. ID가 짧으면 0으로 패딩한다."""
    pid = str(page_id).strip()
    if not pid:
        raise ValueError("빈 페이지 ID")
    padded = pid.rjust(SHARD_DEPTH * SHARD_WIDTH, "0")
    return "/".join(
        padded[i * SHARD_WIDTH : (i + 1) * SHARD_WIDTH] for i in range(SHARD_DEPTH)
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


def iter_structure_files(root: Path):
    base = root / "mirror" / "structure"
    if not base.exists():
        return
    yield from sorted(base.rglob("*.json"))
