"""
CLI 배선 테스트.

지금까지 `cli.py` 는 `_cmd_stats` 외에는 테스트가 전혀 없어서, argparse 배선이
깨져도(서브커맨드 제거·인자 이름 변경 등) 아무것도 잡히지 않았다. 여기서 검증하는
것은 로직이 아니라 **배선**이다 — 어떤 서브커맨드가 존재하고, 어떤 함수로 가고,
비정상 입력에 죽지 않고 진단 메시지를 내는지.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wikilens import layout
from wikilens.cli import _cmd_build, _cmd_stats, main


# --------------------------------------------------------------- 배선

def test_only_the_documented_subcommands_exist(capsys):
    """
    README 가 안내하는 워크플로는 sync→doctor→build→stats 넷이다.
    폐기된 서버판 잔재(serve/search/hook)가 되살아나면 여기서 걸린다.
    """
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for cmd in ("sync", "doctor", "build", "stats"):
        assert cmd in out, f"{cmd} 서브커맨드가 사라졌다"
    for gone in ("serve", "search", "hook"):
        assert f"    {gone}  " not in out, f"제거된 {gone} 서브커맨드가 되살아났다"


def test_subcommand_requires_a_choice():
    """서브커맨드 없이 부르면 argparse 가 막아야 한다."""
    with pytest.raises(SystemExit):
        main([])


def test_unknown_subcommand_is_rejected():
    with pytest.raises(SystemExit):
        main(["없는명령"])


@pytest.mark.parametrize("argv,expected", [
    (["--root", "/A", "build"], "/A"),          # 최상위
    (["build", "--root", "/B"], "/B"),          # 서브커맨드 뒤 — 예전엔 파싱 에러였다
    (["--root", "/A", "build", "--root", "/B"], "/B"),   # 뒤가 이긴다
    (["build"], "."),                            # 기본값이 살아 있다
])
def test_root_is_accepted_on_both_sides(argv, expected, monkeypatch):
    """
    `--root` 가 최상위에만 있으면 `wikilens sync --root ~/wiki` 가 파싱 에러다.
    자연스러운 순서가 거부되는 것이라 문서 세 곳이 그 함정을 경고하고 있었다.

    **뒤가 이겨야 한다** — 래퍼가 앞에 볼트 경로를 채우므로, 사용자가 뒤에 준 값이
    지면 일회성 재정의가 불가능해진다.
    """
    seen = {}

    def spy(args):
        seen["root"] = args.root
        return 0

    monkeypatch.setattr("wikilens.cli._cmd_build", spy)
    main(argv)
    assert seen["root"] == expected


@pytest.mark.parametrize("argv", [["-v", "build"], ["build", "-v"], ["build", "--verbose"]])
def test_verbose_is_accepted_on_both_sides(argv, monkeypatch):
    """`--root` 만 고치고 `-v` 를 남겨뒀더니 `acl -v` 가 죽었다 — 같은 규칙이어야 한다."""
    seen = {}
    monkeypatch.setattr("wikilens.cli._cmd_build", lambda a: seen.setdefault("v", a.verbose) or 0)
    main(argv)
    assert seen["v"] is True


def test_doctor_hint_uses_the_actual_root(capsys, monkeypatch):
    """
    다음 단계 안내가 `~/wiki` 를 박아두고 있었다. 래퍼를 거쳐 들어온 사용자는 볼트가
    `~/.wikilens/vault` 라 **엉뚱한 경로를 안내받는다** — 그대로 복사하면 빈 볼트가 하나 더 생긴다.
    """
    class FakeDoctor:
        base_url = "https://w.example.com"
        deployment, prefix, auth_mode = "Server/DC", "", "PAT"
        authenticated, account, storage_expandable, ok = True, "me", True, True
        spaces, errors = [("PLATFORM", "플랫폼")], []

    monkeypatch.setattr("wikilens.sync.client_from_env",
                        lambda: type("C", (), {"doctor": lambda self: FakeDoctor()})())
    main(["--root", "/my/vault", "doctor"])
    out = capsys.readouterr().out
    assert "--root /my/vault sync --space PLATFORM" in out, out
    assert "~/wiki" not in out, "볼트 경로가 다시 박혔다"


def test_sync_requires_space():
    """--space 가 required 라 빠지면 즉시 실패해야 한다 (네트워크 호출 전에)."""
    with pytest.raises(SystemExit):
        main(["sync"])


# --------------------------------------------------------------- 진단 메시지

def test_stats_without_build_explains_what_to_do(tmp_path, capsys):
    rc = _cmd_stats(SimpleNamespace(root=str(tmp_path)))
    assert rc == 1
    assert "wikilens build" in capsys.readouterr().out, "무엇을 하라는 안내가 있어야 한다"


def test_stats_on_empty_anchors_does_not_crash(tmp_path, capsys):
    """
    빈 anchors.jsonl 에서 `100*len(...)/total` 이 ZeroDivisionError 로 터졌다.
    싱크된 페이지가 0건인 볼트는 정상적으로 일어날 수 있는 상태다.
    """
    root = tmp_path / "vault"
    layout.ensure_parent(layout.anchors_path(root)).write_text("", encoding="utf-8")

    rc = _cmd_stats(SimpleNamespace(root=str(root)))
    assert rc == 1
    assert "비어" in capsys.readouterr().out


def test_build_on_empty_vault_reports_zero(tmp_path, capsys):
    """싱크 상태 파일이 없으면 0건으로 조용히 끝나야 한다 (크래시 아님)."""
    root = tmp_path / "vault"
    rc = _cmd_build(SimpleNamespace(root=str(root)))
    assert rc == 0
    assert "빌드 완료" in capsys.readouterr().out


def test_corrupt_sync_state_gives_a_diagnostic(tmp_path):
    """
    손상된 상태 파일이 raw traceback 으로 터지면 사용자가 무엇을 할지 알 수 없다.
    """
    from wikilens.sync import ConfluenceError, _load_state

    root = tmp_path / "vault"
    layout.ensure_parent(layout.sync_state_path(root)).write_text("{깨진", encoding="utf-8")

    with pytest.raises(ConfluenceError) as e:
        _load_state(root)
    assert "손상" in str(e.value)


# --------------------------------------------------------------- build 배선

def test_build_writes_all_three_derived_artifacts(tmp_path, capsys):
    """build 가 ALIASES.md·TREE.md·anchors.jsonl 셋을 모두 만들고 경로를 안내하는지."""
    root = tmp_path / "vault"
    state = {"cursor": None, "pages": {}}
    for pid, title in [("100", "루트"), ("200", "자식")]:
        p = layout.ensure_parent(layout.raw_path(root, pid))
        p.write_text("<p>본문.</p>", encoding="utf-8")
        state["pages"][pid] = {
            "title": title, "space": "DOCS", "version": 1, "updated": "",
            "ancestors": [] if pid == "100" else [{"id": "100", "title": "루트"}],
        }
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )

    assert _cmd_build(SimpleNamespace(root=str(root))) == 0
    for p in (layout.aliases_path(root), layout.tree_path(root), layout.anchors_path(root)):
        assert p.exists(), f"{p.name} 이 생성되지 않았다"
    assert Path(layout.tree_path(root)).read_text(encoding="utf-8").count("- ") == 2
