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
