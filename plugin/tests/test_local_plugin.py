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
SKILL = PLUGIN / "skills" / "search" / "SKILL.md"
SETUP_REF = PLUGIN / "skills" / "search" / "references" / "setup.md"
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
    for rel in ("mirror/pages/01/200000001.md", "mirror/raw/01/200000001.xhtml", *rels):
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
    "mirror/pages/02/200000002.xhtml",               # 확장자가 디렉터리와 안 맞음
    "mirror/pages/99/200000001.md",                  # 샤드 위치가 ID 와 안 맞음
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


# --------------------------------------------------------------- 자격증명 고정
#
# 자격증명이 환경변수 전용이라 `/wikilens-local:sync` 가 Claude Code 안에서 한 번도
# 동작한 적이 없었다(2026-08-05 실측). env.sh 가 그 구멍을 막는다.

def setup_vault(home: Path, *args: str, **env) -> subprocess.CompletedProcess:
    e = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    e.update({k: str(v) for k, v in env.items()})
    return subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "setup_vault.py"), *args],
        capture_output=True, text=True, env=e,
    )


def test_creds_none_when_nothing_is_set(tmp_path):
    assert status(tmp_path)["creds"] == "none"


def test_creds_shell_is_distinguished_from_file(tmp_path):
    """`shell` 은 '지금은 되지만 다음 세션엔 안 된다' 는 뜻이라 `file` 과 달라야 한다."""
    assert status(tmp_path, CONFLUENCE_URL="https://w.example.com")["creds"] == "shell"


def test_capture_env_persists_credentials(tmp_path):
    r = setup_vault(tmp_path, "--capture-env",
                    CONFLUENCE_URL="https://w.example.com", CONFLUENCE_TOKEN="tok")
    assert r.returncode == 0
    assert status(tmp_path)["creds"] == "file"


def test_capture_env_never_prints_the_token(tmp_path):
    """값이 화면에 찍히면 로그·스크롤백에 남는다. 이름만 알린다."""
    r = setup_vault(tmp_path, "--capture-env",
                    CONFLUENCE_URL="https://w.example.com", CONFLUENCE_TOKEN="s3cr3t")
    assert "s3cr3t" not in r.stdout + r.stderr
    assert "CONFLUENCE_TOKEN" in r.stdout


def test_env_file_is_not_world_readable(tmp_path):
    setup_vault(tmp_path, "--capture-env",
                CONFLUENCE_URL="https://w.example.com", CONFLUENCE_TOKEN="tok")
    mode = (tmp_path / ".wikilens" / "env.sh").stat().st_mode & 0o777
    assert mode == 0o600, f"토큰 파일 권한이 {oct(mode)}"


def test_empty_prefix_survives_capture(tmp_path):
    """
    `CONFLUENCE_PREFIX=""` 는 Coway 필수 설정이고 **빈 문자열이 유효한 값**이다.
    `.get()` 으로 판정하면 조용히 빠지고 인증이 실패한다.
    """
    setup_vault(tmp_path, "--capture-env", CONFLUENCE_URL="https://w.example.com",
                CONFLUENCE_TOKEN="tok", CONFLUENCE_PREFIX="")
    body = (tmp_path / ".wikilens" / "env.sh").read_text(encoding="utf-8")
    assert "export CONFLUENCE_PREFIX=''" in body


def test_capture_env_merges_instead_of_overwriting(tmp_path):
    """
    예전에는 파일을 통째로 새로 써서, 토큰이 export 안 된 셸에서 한 번 더 실행하면
    **파일에 있던 토큰이 사라졌다.** 문서 세 곳이 이 명령을 안내하므로 두 번째
    실행은 자연스럽게 일어난다 — 데이터 손실이었다.
    """
    d = tmp_path / ".wikilens"
    d.mkdir()
    (d / "env.sh").write_text(
        "export CONFLUENCE_URL=https://old\n"
        "export CONFLUENCE_TOKEN=long-lived\n"
        "# 사용자가 직접 적은 줄\n"
        "export MY_OTHER_THING=keepme\n", encoding="utf-8")

    setup_vault(tmp_path, "--capture-env", CONFLUENCE_URL="https://new")

    body = (d / "env.sh").read_text(encoding="utf-8")
    assert "export CONFLUENCE_URL=https://new" in body, "새 값이 제자리 교체돼야 한다"
    assert "https://old" not in body
    # 교체이지 추가가 아니다 — 같은 키가 두 번 있으면 나중 것이 이겨 혼란스럽다
    assert body.count("export CONFLUENCE_URL=") == 1
    assert "export CONFLUENCE_TOKEN=long-lived" in body, "토큰이 사라졌다"
    assert "# 사용자가 직접 적은 줄" in body, "주석이 사라졌다"
    assert "export MY_OTHER_THING=keepme" in body, "무관한 변수가 사라졌다"


def test_capture_env_reports_when_still_incomplete(tmp_path):
    """URL 만 있고 토큰이 없는데 '이제 동작합니다'라고 하면 거짓 보고다."""
    r = setup_vault(tmp_path, "--capture-env", CONFLUENCE_URL="https://only-url")
    assert r.returncode == 2, r.stdout
    assert "CONFLUENCE_TOKEN" in r.stdout
    assert "동작합니다" not in r.stdout


def test_capture_env_keeps_file_permissions_on_merge(tmp_path):
    d = tmp_path / ".wikilens"
    d.mkdir()
    (d / "env.sh").write_text("export CONFLUENCE_TOKEN=t\n", encoding="utf-8")
    (d / "env.sh").chmod(0o644)          # 느슨한 상태에서 시작해도
    setup_vault(tmp_path, "--capture-env", CONFLUENCE_URL="https://x")
    assert (d / "env.sh").stat().st_mode & 0o777 == 0o600


def test_capture_env_without_credentials_offers_a_template(tmp_path):
    """대신 토큰을 넣어주지 않고, 사용자가 직접 실행할 명령을 내놔야 한다."""
    r = setup_vault(tmp_path, "--capture-env")
    assert r.returncode == 2
    assert "CONFLUENCE_TOKEN=" in r.stdout
    assert "umask 077" in r.stdout, "권한 없이 만들어지는 템플릿이다"


def test_values_with_spaces_are_quoted(tmp_path):
    """헤더 방식은 값에 공백이 들어간다 — 인용하지 않으면 source 가 깨진다."""
    setup_vault(tmp_path, "--capture-env", CONFLUENCE_URL="https://w.example.com",
                CONFLUENCE_HEADERS="X-Forwarded-User: me@corp")
    env_sh = tmp_path / ".wikilens" / "env.sh"
    got = subprocess.run(
        ["sh", "-c", f'. "{env_sh}"; printf %s "$CONFLUENCE_HEADERS"'],
        capture_output=True, text=True)
    assert got.stdout == "X-Forwarded-User: me@corp"


# --------------------------------------------------------------- CLI 실행 래퍼

WRAPPER = PLUGIN / "scripts" / "wikilens_cli.sh"


def test_wrapper_loads_credentials_from_env_file(tmp_path):
    """래퍼의 존재 이유 — env.sh 를 실어야 CLI 가 자격증명을 본다."""
    setup_vault(tmp_path, "--capture-env",
                CONFLUENCE_URL="https://w.example.com", CONFLUENCE_TOKEN="tok")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "wikilens").write_text('#!/bin/sh\nprintf %s "$CONFLUENCE_URL"\n')
    (fake / "wikilens").chmod(0o755)

    got = subprocess.run(
        ["bash", str(WRAPPER), "doctor"], capture_output=True, text=True,
        env={"HOME": str(tmp_path), "PATH": f"{fake}:/usr/bin:/bin"})
    assert got.stdout == "https://w.example.com"


@pytest.mark.parametrize("var,shell_value", [
    ("CONFLUENCE_URL", "https://from-shell"),
    ("CONFLUENCE_TOKEN", "token-from-shell"),
    ("CONFLUENCE_PREFIX", "/from-shell"),
    ("CONFLUENCE_HEADERS", "X-Forwarded-User: me@corp"),
])
def test_exported_value_beats_the_file(tmp_path, var, shell_value):
    """
    일회성 재정의(토큰 교체 등)가 파일에 덮이면 **낡은 자격증명으로 조용히 인증**한다.

    변수 하나만 검사하던 탓에 구멍이 가려져 있었다 — 실제로는 `CONFLUENCE_URL` 만
    보존되고 TOKEN·PREFIX 는 파일이 덮고 있었다. 그래서 전부 돌린다.
    """
    setup_vault(tmp_path, "--capture-env",
                CONFLUENCE_URL="https://from-file", CONFLUENCE_TOKEN="token-from-file",
                CONFLUENCE_PREFIX="/from-file", CONFLUENCE_HEADERS="from-file")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "wikilens").write_text(f'#!/bin/sh\nprintf %s "${var}"\n')
    (fake / "wikilens").chmod(0o755)

    got = subprocess.run(
        ["bash", str(WRAPPER), "doctor"], capture_output=True, text=True,
        env={"HOME": str(tmp_path), "PATH": f"{fake}:/usr/bin:/bin", var: shell_value})
    assert got.stdout == shell_value, got.stderr


def test_empty_prefix_from_shell_beats_the_file(tmp_path):
    """빈 문자열은 '미설정'이 아니라 유효한 값이다 — Coway 필수 설정."""
    setup_vault(tmp_path, "--capture-env",
                CONFLUENCE_URL="https://x", CONFLUENCE_PREFIX="/from-file")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "wikilens").write_text('#!/bin/sh\nprintf "[%s]" "$CONFLUENCE_PREFIX"\n')
    (fake / "wikilens").chmod(0o755)

    got = subprocess.run(
        ["bash", str(WRAPPER), "doctor"], capture_output=True, text=True,
        env={"HOME": str(tmp_path), "PATH": f"{fake}:/usr/bin:/bin", "CONFLUENCE_PREFIX": ""})
    assert got.stdout == "[]", got.stdout


def _fake_cli(d: Path, name: str = "wikilens") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text('#!/bin/sh\nprintf %s "$CONFLUENCE_URL"\n')
    p.chmod(0o755)
    return p


def test_config_cli_path_is_used_when_not_on_path(tmp_path):
    """
    venv·pipx 에 설치하면 PATH 에도 없고 기본 python 으로 import 도 안 된다.
    Claude Code 가 띄우는 셸이 정확히 그 상태다 — 명시 경로가 유일한 답이다.
    """
    exe = _fake_cli(tmp_path / "somewhere" / "bin")
    setup_vault(tmp_path, "--cli-path", str(exe))
    assert status(tmp_path)["cli"] == str(exe)

    got = subprocess.run(
        ["bash", str(WRAPPER), "doctor"], capture_output=True, text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin",
             "CONFLUENCE_URL": "https://w.example.com"})
    assert got.stdout == "https://w.example.com", got.stderr


def test_config_cli_path_beats_path_lookup(tmp_path):
    """명시 경로를 적어두고도 PATH 의 다른 설치가 이기면 기록한 의미가 없다."""
    chosen = _fake_cli(tmp_path / "chosen" / "bin")
    other = tmp_path / "other"
    _fake_cli(other)
    setup_vault(tmp_path, "--cli-path", str(chosen))
    assert status(tmp_path, PATH=f"{other}:/usr/bin:/bin")["cli"] == str(chosen)


def test_stale_config_cli_path_falls_back(tmp_path):
    """기록해둔 경로가 사라졌는데 그것만 믿으면 되살릴 방법이 없다."""
    gone = tmp_path / "gone" / "bin" / "wikilens"
    (tmp_path / ".wikilens").mkdir(exist_ok=True)
    (tmp_path / ".wikilens" / "config.json").write_text(
        json.dumps({"cli": str(gone)}), encoding="utf-8")
    on_path = tmp_path / "onpath"
    _fake_cli(on_path)
    assert status(tmp_path, PATH=f"{on_path}:/usr/bin:/bin")["cli"] == str(on_path / "wikilens")


def test_cli_path_rejects_non_executable(tmp_path):
    plain = tmp_path / "not-exec"
    plain.write_text("")
    r = setup_vault(tmp_path, "--cli-path", str(plain))
    assert r.returncode == 2
    assert "실행 가능한 파일이 아닙니다" in r.stdout


def test_cli_mode_prints_argv_one_per_line(tmp_path):
    """경로에 공백이 있어도 래퍼가 온전히 복원해야 한다."""
    exe = _fake_cli(tmp_path / "has space" / "bin")
    setup_vault(tmp_path, "--cli-path", str(exe))
    r = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "vault_status.py"), "--cli"],
        capture_output=True, text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0
    assert r.stdout.splitlines() == [str(exe)]

    got = subprocess.run(
        ["bash", str(WRAPPER), "doctor"], capture_output=True, text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin",
             "CONFLUENCE_URL": "https://w.example.com"})
    assert got.stdout == "https://w.example.com", got.stderr


def test_wrapper_and_resolver_agree_on_the_cli(tmp_path):
    """
    둘이 각자 찾으면 스킬은 "CLI 있음"이라 하는데 래퍼는 못 찾는 상태가 생긴다.
    래퍼가 vault_status 에 물어보므로 어긋날 수 없어야 한다.

    래퍼와 **같은 인터프리터**(맨 `python3`)로 대조한다 — 해석은 그것을 실행하는
    파이썬 기준이라, venv 파이썬으로 물어보면 답이 달라지는 게 정상이다.
    """
    on_path = _fake_cli(tmp_path / "p").parent
    for extra in ({}, {"PATH": f"{on_path}:/usr/bin:/bin"}):
        e = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", **extra}
        resolved = subprocess.run(
            ["python3", str(PLUGIN / "scripts" / "vault_status.py"), "--cli"],
            capture_output=True, text=True, env=e)
        rc = subprocess.run(["bash", str(WRAPPER), "doctor"],
                            capture_output=True, text=True, env=e).returncode
        assert (resolved.returncode == 0) == (rc != 127), (extra, resolved.stdout, rc)


def test_wrapper_points_at_setup_when_cli_is_absent(tmp_path):
    got = subprocess.run(
        ["bash", str(WRAPPER), "doctor"], capture_output=True, text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert got.returncode == 127
    assert "setup" in got.stderr


def test_commands_call_the_cli_through_the_wrapper():
    """맨 `wikilens` 를 부르면 자격증명 없이 죽는다 — 커맨드가 래퍼를 거쳐야 한다."""
    for name in ("sync",):
        text = (PLUGIN / "commands" / f"{name}.md").read_text(encoding="utf-8")
        assert "wikilens_cli.sh" in text, f"{name} 커맨드가 래퍼를 안 쓴다"


# --------------------------------------------------------------- 스킬 정합성

def test_skill_has_frontmatter_and_distinguishes_itself():
    """
    스킬 이름은 플러그인 이름과 같아야 한다. 예전엔 두 판의 스킬이 **둘 다**
    `wikilens` 라 이름만으로는 구별이 불가능했고, description 이 유일한 단서였다.

    이름을 갈라놓은 뒤에도 description 검사는 남긴다 — 둘은 상호 배타라 설명까지
    같아지면 모델이 어느 쪽을 부를지 갈리고, 로컬은 볼트가 없어 실패한다.
    """
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert "name: search" in head, "스킬 이름이 바뀌었다 — 계약과 어긋난다"
    assert "로컬" in head, "서버판과 구별되는 표지가 description 에 없다"


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
    assert "CONFLUENCE_PREFIX" in text, "Coway 필수 설정이 빠졌다"


# --------------------------------------------------------------- 검색 절차
#
# grep 은 리터럴이라 여러 낱말 질의를 그대로 넣으면 0건이 된다. 스킬이 그걸 모르면
# 3번(본문 grep)으로 떨어지는데, 스킬 자신이 그건 틀린 답을 준다고 경고한다.
# 실측(Coway 2,377건): "출장 신청 승인" 그대로 0건, 낱말 AND 로는 정확히 1건.

def test_skill_warns_that_grep_is_literal():
    text = SKILL.read_text(encoding="utf-8")
    assert "리터럴" in text, "grep 이 리터럴이라는 사실이 없다"
    assert "쪼개" in text, "낱말로 쪼개라는 지시가 없다"


def test_phrase_grep_fails_where_token_and_succeeds(tmp_path):
    """
    스킬이 시키는 것(낱말 AND)이 실제로 통하고, 시키지 않는 것(구절 그대로)이
    실제로 실패하는지 — 픽스처 형식 그대로 재현해 확인한다.
    """
    aliases = tmp_path / "ALIASES.md"
    aliases.write_text(
        "## 색인\n"
        "02-05-01. [POLICY] 정책 | 출장 신청 승인 절차 | 3 | mirror/pages/01/1.md\n"
        "10-1. 마케팅 수신 동의 | 동의 데이터 흐름 | 1 | mirror/pages/02/2.md\n",
        encoding="utf-8")
    rows = [l for l in aliases.read_text(encoding="utf-8").splitlines() if "mirror/" in l]

    assert [l for l in rows if "출장 신청 승인" in l], "이 픽스처에선 구절이 실제로 있다"

    # 사용자가 흔히 쓰는 어순 — 구절 그대로는 안 걸린다
    assert not [l for l in rows if "승인 출장" in l]
    # 낱말 AND 는 걸린다
    hit = [l for l in rows if all(t in l for t in ("<직군명>", "동의"))]
    assert len(hit) == 1 and "POLICY" in hit[0]
