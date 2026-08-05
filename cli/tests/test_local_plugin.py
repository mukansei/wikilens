"""
로컬판 플러그인 테스트.

로컬판은 지금까지 "형식 검증"만 돼 있었다 — 스킬이 마크다운 한 장이라 깨져도 아무도
몰랐다. 배포 대상이 된 이상 실제로 검증한다.

여기서 잠그는 것은 **배포 가능성의 전제** 셋이다:
  1. 볼트 경로 해석이 결정적인가 (env > config > 기본)
  2. 볼트 상태 판정이 정확한가 (missing/unsynced/unbuilt/stale/ok)
  3. 스킬이 참조하는 `ALIASES.md` 형식이 실제 산출물과 일치하는가

3번이 특히 중요하다. `layout.rel_page_path()` 가 바뀌면 스킬의 경로 결합 규칙이
조용히 틀어지는데, 그건 런타임에만 드러난다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugin" / "local"
SKILL = PLUGIN / "skills" / "wikilens" / "SKILL.md"
SETUP_REF = PLUGIN / "skills" / "wikilens" / "references" / "setup.md"
FIXTURE = REPO / "contract" / "shared-fixture"


def status(home: Path, **env) -> dict:
    """격리된 HOME 으로 vault_status 를 돌려 JSON 으로 받는다."""
    e = {"HOME": str(home), "PATH": "/usr/bin:/bin"}   # PATH 를 좁혀 CLI 탐지를 결정적으로
    e.update({k: str(v) for k, v in env.items()})
    r = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "vault_status.py"), "--json"],
        capture_output=True, text=True, env=e,
    )
    return json.loads(r.stdout)


# --------------------------------------------------------------- 경로 해석

def test_defaults_to_home_wikilens_vault(tmp_path):
    assert status(tmp_path)["vault"] == str(tmp_path / ".wikilens" / "vault")


def test_config_overrides_default(tmp_path):
    (tmp_path / ".wikilens").mkdir()
    (tmp_path / ".wikilens" / "config.json").write_text(
        json.dumps({"vault": str(tmp_path / "myvault")}), encoding="utf-8")
    assert status(tmp_path)["vault"] == str(tmp_path / "myvault")


def test_env_overrides_config(tmp_path):
    """
    환경변수가 이기지만, 그것만으로 정본을 삼으면 안 된다 — 세션이 끝나면 사라진다.
    그래서 config 가 정본이고 env 는 일회성 재정의다.
    """
    (tmp_path / ".wikilens").mkdir()
    (tmp_path / ".wikilens" / "config.json").write_text(
        json.dumps({"vault": str(tmp_path / "from-config")}), encoding="utf-8")
    got = status(tmp_path, WIKILENS_VAULT=str(tmp_path / "from-env"))["vault"]
    assert got == str(tmp_path / "from-env")


def test_tilde_in_config_is_expanded(tmp_path):
    (tmp_path / ".wikilens").mkdir()
    (tmp_path / ".wikilens" / "config.json").write_text(
        json.dumps({"vault": "~/tilde-vault"}), encoding="utf-8")
    assert status(tmp_path)["vault"] == str(tmp_path / "tilde-vault")


def test_corrupt_config_falls_back_instead_of_crashing(tmp_path):
    (tmp_path / ".wikilens").mkdir()
    (tmp_path / ".wikilens" / "config.json").write_text("{깨진", encoding="utf-8")
    assert status(tmp_path)["vault"] == str(tmp_path / ".wikilens" / "vault")


# --------------------------------------------------------------- 상태 판정

def test_missing_vault(tmp_path):
    assert status(tmp_path)["status"] == "missing"


def test_unsynced_when_no_state_file(tmp_path):
    (tmp_path / "v").mkdir()
    assert status(tmp_path, WIKILENS_VAULT=tmp_path / "v")["status"] == "unsynced"


def test_unbuilt_when_synced_but_no_aliases(tmp_path):
    v = tmp_path / "v"
    (v / "mirror").mkdir(parents=True)
    (v / "mirror" / ".sync-state.json").write_text(
        json.dumps({"cursor": None, "pages": {"1": {}}}), encoding="utf-8")
    got = status(tmp_path, WIKILENS_VAULT=v)
    assert got["status"] == "unbuilt" and got["pages"] == 1


def test_built_fixture_reports_pages(tmp_path):
    """체크인된 골든 픽스처는 build 까지 끝나 있으므로 ok 또는 stale 이어야 한다."""
    got = status(tmp_path, WIKILENS_VAULT=FIXTURE)
    assert got["status"] in ("ok", "stale")
    assert got["pages"] == 3


def test_corrupt_state_file_is_treated_as_unsynced(tmp_path):
    v = tmp_path / "v"
    (v / "mirror").mkdir(parents=True)
    (v / "mirror" / ".sync-state.json").write_text("{깨진", encoding="utf-8")
    assert status(tmp_path, WIKILENS_VAULT=v)["status"] == "unsynced"


def test_exit_code_distinguishes_searchable_from_setup_needed(tmp_path):
    """스킬이 종료코드만 봐도 분기할 수 있어야 한다."""
    def rc(**env):
        e = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
        e.update({k: str(v) for k, v in env.items()})
        return subprocess.run(
            [sys.executable, str(PLUGIN / "scripts" / "vault_status.py")],
            capture_output=True, env=e).returncode

    assert rc() == 2                              # missing
    assert rc(WIKILENS_VAULT=FIXTURE) == 0        # 검색 가능


# --------------------------------------------------------------- 샤드 밖 파일
#
# `sync` 는 이런 파일을 영원히 못 지운다 — 삭제 청소가 `.sync-state.json` 이 아는
# 페이지 ID 만 훑기 때문이다. 아래 세 개는 실제 볼트에서 나온 것들이다(2026-08-05).

def _vault_with(tmp_path, *rels: str) -> Path:
    v = tmp_path / "v"
    for rel in ("mirror/pages/20/00/200000001.md", "mirror/raw/20/00/200000001.xhtml", *rels):
        p = v / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    return v


def test_clean_vault_has_no_strays(tmp_path):
    v = _vault_with(tmp_path)
    assert status(tmp_path, WIKILENS_VAULT=v)["stray"] == 0


@pytest.mark.parametrize("rel", [
    "mirror/pages/.DS_Store",                        # Finder 부산물
    "mirror/pages/Project Task List.md",               # 제목이 파일명 (ID 계약 위반)
    "mirror/pages/상위기획] 제품/서비스 개선](.md",      # 링크 텍스트를 경로로 삼음
    "mirror/pages/20/00/200000002.xhtml",            # 확장자가 디렉터리와 안 맞음
    "mirror/pages/99/99/200000001.md",               # 샤드 위치가 ID 와 안 맞음
])
def test_stray_file_is_detected(tmp_path, rel):
    got = status(tmp_path, WIKILENS_VAULT=_vault_with(tmp_path, rel))
    assert got["stray"] == 1
    assert got["stray_paths"] == [rel[len("mirror/"):]]


def test_strays_do_not_change_status_or_exit_code(tmp_path):
    """이상 파일이 있어도 검색은 된다 — 진단이 검색을 막으면 안 된다."""
    v = _vault_with(tmp_path, "mirror/pages/.DS_Store")
    (v / "mirror" / ".sync-state.json").write_text(
        json.dumps({"pages": {"200000001": {}}, "cursor": None}), encoding="utf-8")
    (v / "ALIASES.md").write_text("# ALIASES\n", encoding="utf-8")

    got = status(tmp_path, WIKILENS_VAULT=v)
    assert got["stray"] == 1
    assert got["status"] == "ok"

    rc = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "vault_status.py")],
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "WIKILENS_VAULT": str(v)},
    ).returncode
    assert rc == 0


def test_missing_vault_reports_scan_skipped(tmp_path):
    """볼트가 없으면 0건이 아니라 '검사 안 함'이어야 한다 — 둘은 다른 뜻이다."""
    assert status(tmp_path)["stray"] == -1


def test_shard_rule_matches_the_cli(tmp_path):
    """
    `vault_status` 가 샤딩 규칙을 다시 정의하므로 CLI 와 갈라질 수 있다.
    갈라지면 정상 파일이 전부 이상 파일로 잡힌다 — 실제 `layout` 으로 대조해 막는다.
    """
    from wikilens import layout

    v = tmp_path / "v"
    for pid in ("200000001", "7", "123456789012"):
        p = v / layout.rel_page_path(pid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    assert status(tmp_path, WIKILENS_VAULT=v)["stray"] == 0


# --------------------------------------------------------------- 스킬 정합성

def test_skill_has_frontmatter_and_distinguishes_itself():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert "name: wikilens" in head
    # 서버판과 구별되지 않으면 둘 다 설치했을 때 모델이 잘못 고른다
    assert "로컬" in head and "wikilens-local" not in head.split("description")[0]


def test_skill_uses_plugin_root_not_hardcoded_path():
    """
    스킬 본문은 모든 사용자가 공유하는 플러그인 캐시에 있으므로 특정 사용자의 경로를
    담을 수 없다. 예시 경로(`/Users/me/wiki`)는 괜찮지만 **실제 홈**이 들어가면 안 된다.
    """
    text = SKILL.read_text(encoding="utf-8")
    assert "${CLAUDE_PLUGIN_ROOT}/scripts/vault_status.py" in text
    assert str(Path.home()) not in text, "이 머신의 실제 홈 경로가 하드코딩됐다"


def test_skill_has_no_cwd_relative_paths():
    """볼트는 프로젝트 밖이라 상대경로는 배포 시 반드시 깨진다."""
    text = SKILL.read_text(encoding="utf-8")
    assert 'path="ALIASES.md"' not in text
    assert 'path="TREE.md"' not in text
    assert "<VAULT>/ALIASES.md" in text


def test_skill_example_matches_real_aliases_format():
    """
    포맷 드리프트 방지. `layout.rel_page_path()` 가 바뀌면 스킬의 경로 결합 규칙이
    조용히 틀어지는데, 그건 런타임에만 드러난다.
    """
    from wikilens import layout

    # 형식 설명 줄도 파이프를 3개 갖고 있으므로, 경로 필드가 실제 경로인 줄만 고른다.
    lines = [l for l in (FIXTURE / "ALIASES.md").read_text(encoding="utf-8").splitlines()
             if l.count(" | ") == 3 and l.rsplit(" | ", 1)[1].startswith("mirror/pages/")]
    assert lines, "픽스처 ALIASES.md 에 색인 줄이 없다"

    path_field = lines[0].rsplit(" | ", 1)[1]
    page_id = path_field.rsplit("/", 1)[1].removesuffix(".md")
    assert path_field == layout.rel_page_path(page_id), (
        "ALIASES.md 의 경로 형식이 layout.rel_page_path() 와 갈라졌다 — "
        "스킬의 경로 결합 규칙도 함께 고쳐야 한다"
    )
    # 스킬이 안내하는 결합 규칙이 실제 형식과 맞는가
    assert "mirror/pages/" in SKILL.read_text(encoding="utf-8")


# --------------------------------------------------------------- 커맨드

@pytest.mark.parametrize("name", ["setup", "sync"])
def test_command_exists_with_frontmatter(name):
    p = PLUGIN / "commands" / f"{name}.md"
    assert p.exists(), f"{name} 커맨드가 없다"
    assert p.read_text(encoding="utf-8").startswith("---\n")


def test_setup_command_defers_to_the_single_source_of_truth():
    """절차를 복제하면 두 곳이 갈라진다 — 커맨드는 정본을 가리키기만 해야 한다."""
    text = (PLUGIN / "commands" / "setup.md").read_text(encoding="utf-8")
    assert "references/setup.md" in text
    assert "--space" not in text, "커맨드가 절차를 복제하고 있다"


def test_setup_reference_covers_the_argument_order_trap():
    """`--root` 를 서브커맨드 뒤에 두면 파싱 에러다 — 반드시 명시돼 있어야 한다."""
    text = SETUP_REF.read_text(encoding="utf-8")
    assert "--root <VAULT> sync" in text
    assert "앞에" in text
    assert "CONFLUENCE_PREFIX" in text, "Acme 필수 설정이 빠졌다"
