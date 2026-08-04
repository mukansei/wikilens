"""
골든 픽스처 계약 테스트.

`contracts/fixtures/mini-vault/`는 Python 과 Kotlin 이 **공유하는 정본 볼트**다.
이 테스트는 Python 쪽만 확인한다 — `build()`가 픽스처의 원본(mirror/raw,
.sync-state.json)에서 체크인된 산출물(pages/, structure/, anchors.jsonl,
ALIASES.md, TREE.md)을 결정적으로 재생성하는지.

Kotlin 쪽은 같은 픽스처의 산출물을 `VaultReaderTest.kt`가 직접 읽어 검증한다.
두 언어가 각자 이 파일들을 정본으로 삼으므로, 한쪽이 포맷을 바꾸면 그쪽
테스트가 먼저 깨진다 — grep 기반 계약 검사보다 리팩터링에 강하다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from wikilens.build import build

FIXTURE = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "mini-vault"

# build() 가 만드는 산출물만 비교한다. mirror/raw, mirror/.sync-state.json 은
# 입력이라 애초에 그대로 복사되므로 비교 대상이 아니다.
GENERATED_DIRS = ["mirror/pages", "mirror/structure"]
GENERATED_FILES = ["derived/anchors.jsonl", "ALIASES.md", "TREE.md"]


def _rebuild(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    shutil.copytree(FIXTURE / "mirror" / "raw", root / "mirror" / "raw")
    shutil.copy(FIXTURE / "mirror" / ".sync-state.json", root / "mirror" / ".sync-state.json")
    build(root)
    return root


def test_build_reproduces_golden_fixture_output(tmp_path):
    root = _rebuild(tmp_path)

    for rel in GENERATED_FILES:
        got = (root / rel).read_text(encoding="utf-8")
        want = (FIXTURE / rel).read_text(encoding="utf-8")
        assert got == want, f"{rel} 가 체크인된 픽스처와 달라졌다 — 포맷 변경이면 Kotlin 쪽도 함께 고칠 것"

    for rel in GENERATED_DIRS:
        got_files = sorted(p.relative_to(root / rel) for p in (root / rel).rglob("*") if p.is_file())
        want_files = sorted(p.relative_to(FIXTURE / rel) for p in (FIXTURE / rel).rglob("*") if p.is_file())
        assert got_files == want_files, f"{rel} 의 파일 목록이 달라졌다"
        for f in got_files:
            got = (root / rel / f).read_text(encoding="utf-8")
            want = (FIXTURE / rel / f).read_text(encoding="utf-8")
            assert got == want, f"{rel / f} 가 체크인된 픽스처와 달라졌다"


def test_golden_fixture_covers_ancestors_and_orphan_contract():
    """
    픽스처 자체가 실제로 의도한 시나리오를 담고 있는지 확인한다 — 계층 2단(100→200)과
    동기화 범위 밖 부모를 가진 고아(300)가 둘 다 있어야 ancestors 계약과
    "앵커 없는 고아" 계약을 동시에 실증한다.
    """
    tree = (FIXTURE / "TREE.md").read_text(encoding="utf-8")
    assert "- 루트" in tree and "  - 자식A" in tree
    assert "- 고아B" in tree  # 동기화 범위 밖 부모 → 루트로 승격

    aliases = (FIXTURE / "ALIASES.md").read_text(encoding="utf-8")
    assert "고아B | (별칭 없음)" in aliases
