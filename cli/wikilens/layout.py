"""
디스크 레이아웃 규칙.

권위 있는 노드 식별자는 Confluence 페이지 ID다. 제목이 아니다.
제목으로 경로를 잡으면 이름 변경이 삭제+생성으로 보여 diff가 오염되고,
나중에 서버판에서 학습 가중치가 통째로 날아간다.
"""
from __future__ import annotations

from pathlib import Path

# 한 디렉터리에 파일이 쌓이는 것을 막는다.
#
# **샤딩의 이유는 성능이 아니라 나열하는 쪽이다.** 실측(APFS):
#
#   한 디렉터리 파일수   직접 열기 1000회   나열      glob 1건
#          100              23.5 ms        0.1 ms    0.04 ms
#       50,000              37.8 ms       25.3 ms    0.06 ms
#
# 코드는 `ALIASES.md` 의 경로로 **직접 열지** 나열하지 않으므로 500배를 넣어도 개당
# 0.024→0.038ms 다. **선형으로 느려지는 건 나열뿐이고**, 나열하는 것은 git·Finder·rsync·
# 편집기(Obsidian 으로 볼트를 여는 사용이 실제로 있다)와 APFS 만큼 관대하지 않은
# 파일시스템(dir_index 없는 ext3, 네트워크 FS)이다. 비용이 0 이라 보험으로 유지한다.
#
# **ID 의 앞이 아니라 뒤를 쓴다** — 앞자리에 엔트로피가 적다. 측정(2,377건):
#
#   - 자릿수별 엔트로피: 1번째 1.93 bit → 9번째 3.32 bit (10값 균등이 3.32)
#   - 정렬 후 인접 ID 간격 중앙값 13(46%가 10 이하). 폭 3.3억이라 균등이었다면 약 138,000 이어야
#     한다 — ID 가 **조밀한 연속 블록**을 이룬다.
#   - **ID 는 실제로 시간순이다**(표본 31개를 `history.createdDate` 와 대조:
#     스피어만 ρ=0.9964, 역순 465쌍 중 6쌍).
#
# 메커니즘: Confluence 는 `hibernate_unique_key` 의 `next_hi` 로 ID 를 만든다 — **Hi/Lo**
# 다. high 를 받아 low 범위를 채워 쓰므로 low 안은 연속이고 high 블록이 큰 구멍을 남긴다.
# 위 관측이 그대로 설명되고, Hi/Lo 는 공통 구현이라 다른 인스턴스도 같다.
#
# 분산 실측:
#
#   앞2/앞4 (구 규칙)   디렉터리   52 · 최대 378 · 평균 45.7   ← 10,000 리프 중 52개만 씀
#   뒤2/뒤4            디렉터리 2170 · 최대   3 · 평균  1.1   ← 파일당 디렉터리 1개, 과함
#   뒤2   (현 규칙)     디렉터리  100 · 최대  37 · 평균 23.8
#
# **뒤에서 쪼개면 1단계만으로 열 배 낫다.** 디렉터리 수는 문서량과 무관하게 100 고정이고
# 디렉터리당은 N/100 이라, 문서량 때문에 depth 를 올릴 일은 사실상 없다 — 늘린다면 근거는
# 성능이 아니라 나열하는 도구 쪽이고, 그때는 볼트 이전이 필요하다.
SHARD_DEPTH = 1
SHARD_WIDTH = 2


def shard(page_id: str) -> str:
    """'102728003' -> '03'. **뒤에서** 잘라낸다(위 주석). ID 가 짧으면 0으로 패딩."""
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
    """`anchors.jsonl`·`ALIASES.md` 에 적히는 상대 경로. 저장해두는 이유는 사용자가
    샤딩 규칙을 몰라도 grep 결과에서 바로 파일을 열 수 있어야 해서다."""
    return f"mirror/pages/{shard(page_id)}/{page_id}.md"


def anchors_path(root: Path) -> Path:
    return root / "derived" / "anchors.jsonl"


def aliases_path(root: Path) -> Path:
    return root / "ALIASES.md"


def tree_path(root: Path) -> Path:
    """계층 목차. 어휘를 몰라도 영역만 알면 위에서 내려가며 찾는 용도."""
    return root / "TREE.md"


def sync_state_path(root: Path) -> Path:
    return root / "mirror" / ".sync-state.json"


def ensure_parent(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
