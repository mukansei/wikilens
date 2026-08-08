"""
권한 수집.

여기서 잠그는 것은 **과다 노출로 이어지는 세 경로**다. 전부 "실패하면 열린다" 형태라
테스트가 없으면 조용히 새어 나간다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wikilens.acl import GROUP_PREFIX, SPACE_PREFIX, collect


class FakeClient:
    """`_direct_tokens` 가 쓰는 것만 흉내낸다."""

    base = "https://w.example.com"

    def __init__(self, direct: dict[str, list[str] | None]):
        self.direct = direct
        self.asked: list[str] = []

    def detect_prefix(self) -> str:
        return ""

    def _get(self, url):
        pid = url.split("/content/")[1].split("/")[0]
        self.asked.append(pid)
        val = self.direct.get(pid, [])
        if val is None:
            return _Resp(500, {})
        groups = [{"name": t[len(GROUP_PREFIX):]} for t in val if t.startswith(GROUP_PREFIX)]
        return _Resp(200, {"restrictions": {"user": {"results": []},
                                            "group": {"results": groups}}})


class _Resp:
    def __init__(self, code, payload):
        self.status_code = code
        self._p = payload

    def json(self):
        return self._p


def vault(tmp_path: Path, pages: dict) -> Path:
    root = tmp_path / "v"
    (root / "mirror").mkdir(parents=True)
    (root / "mirror" / ".sync-state.json").write_text(
        json.dumps({"cursor": None, "pages": pages}), encoding="utf-8")
    return root


def written(root: Path) -> dict:
    return json.loads((root / "mirror" / "acl" / "acl.json").read_text(encoding="utf-8"))


def test_unrestricted_page_gets_space_token_not_public(tmp_path):
    """
    `@public` 으로 적으면 등록된 누구나 **모든 스페이스**를 보게 된다. 여러 스페이스를
    한 볼트에 모으는 것이 정상 사용이므로 상한은 스페이스여야 한다.
    """
    root = vault(tmp_path, {"1": {"space": "ENG", "ancestors": []}})
    collect(root, FakeClient({}))
    assert written(root) == {"1": [SPACE_PREFIX + "ENG"]}


def test_restriction_is_inherited_from_the_nearest_ancestor(tmp_path):
    """
    `byOperation` 은 **직접 제한만** 준다. 상속을 안 풀면 잠긴 하위 문서가 전부
    `@space:` 로 적혀 **그 스페이스를 가진 전원에게 노출**된다.
    """
    root = vault(tmp_path, {
        "root":  {"space": "ENG", "ancestors": []},
        "mid":   {"space": "ENG", "ancestors": [{"id": "root", "title": "r"}]},
        "leaf":  {"space": "ENG", "ancestors": [{"id": "root", "title": "r"},
                                                {"id": "mid", "title": "m"}]},
    })
    collect(root, FakeClient({"mid": [GROUP_PREFIX + "secret"]}))
    got = written(root)
    assert got["mid"] == [GROUP_PREFIX + "secret"]
    assert got["leaf"] == [GROUP_PREFIX + "secret"], "상속이 안 풀려 하위가 노출됐다"
    assert got["root"] == [SPACE_PREFIX + "ENG"], "조상은 제한이 없다"


def test_nearest_ancestor_wins(tmp_path):
    """조상이 여럿 제한돼 있으면 **가장 가까운** 것이 이긴다 (Confluence 규칙)."""
    root = vault(tmp_path, {
        "top":  {"space": "ENG", "ancestors": []},
        "mid":  {"space": "ENG", "ancestors": [{"id": "top", "title": "t"}]},
        "leaf": {"space": "ENG", "ancestors": [{"id": "top", "title": "t"},
                                               {"id": "mid", "title": "m"}]},
    })
    collect(root, FakeClient({"top": [GROUP_PREFIX + "far"], "mid": [GROUP_PREFIX + "near"]}))
    assert written(root)["leaf"] == [GROUP_PREFIX + "near"]


def test_lookup_failure_keeps_the_old_value_instead_of_opening(tmp_path):
    """
    조회 실패를 "제한 없음" 으로 뭉개면 **못 읽은 페이지가 공개로 바뀐다.**
    네트워크 오류 한 번이 노출로 이어지면 안 된다.
    """
    root = vault(tmp_path, {"1": {"space": "ENG", "ancestors": []}})
    acl_dir = root / "mirror" / "acl"
    acl_dir.mkdir(parents=True)
    (acl_dir / "acl.json").write_text(json.dumps({"1": [GROUP_PREFIX + "secret"]}), encoding="utf-8")

    rep = collect(root, FakeClient({"1": None}))
    assert rep.failed == 1
    assert written(root)["1"] == [GROUP_PREFIX + "secret"], "실패가 공개로 바뀌었다"


def test_unknown_page_that_failed_is_omitted_not_opened(tmp_path):
    """처음 보는 페이지를 못 읽었으면 **아무에게도 안 보여야** 한다(fail-closed)."""
    root = vault(tmp_path, {"1": {"space": "ENG", "ancestors": []}})
    collect(root, FakeClient({"1": None}))
    assert "1" not in written(root)


def test_report_counts_what_matters(tmp_path):
    root = vault(tmp_path, {
        "a": {"space": "ENG", "ancestors": []},
        "b": {"space": "ENG", "ancestors": [{"id": "a", "title": "a"}]},
    })
    rep = collect(root, FakeClient({"a": [GROUP_PREFIX + "g"]}))
    assert (rep.pages, rep.restricted, rep.inherited, rep.failed) == (2, 2, 1, 0)
