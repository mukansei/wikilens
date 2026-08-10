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
    """
    처음 보는 페이지를 못 읽었으면 **아무에게도 안 보여야** 한다(fail-closed).

    성공하는 페이지를 하나 같이 둔다 — 전부 실패하면 파일을 아예 안 쓰는 별도 규칙에
    걸려서, 여기서 재려는 **부분** 실패가 아니게 된다.
    """
    root = vault(tmp_path, {"1": {"space": "ENG", "ancestors": []},
                            "2": {"space": "ENG", "ancestors": []}})
    collect(root, FakeClient({"1": None, "2": []}))
    assert "1" not in written(root)
    assert "2" in written(root)


def test_report_counts_what_matters(tmp_path):
    root = vault(tmp_path, {
        "a": {"space": "ENG", "ancestors": []},
        "b": {"space": "ENG", "ancestors": [{"id": "a", "title": "a"}]},
    })
    rep = collect(root, FakeClient({"a": [GROUP_PREFIX + "g"]}))
    assert (rep.pages, rep.restricted, rep.inherited, rep.failed) == (2, 2, 1, 0)


def test_ancestor_lookup_failure_does_not_open_the_child(tmp_path):
    """
    **네 번째 경로다.** 페이지 자신의 실패는 막고 있었는데 조상의 실패는 안 막고
    있었다 — 못 읽은 조상을 "제한 없음" 과 똑같이 취급하고 계속 위로 올라가서,
    잠긴 부모 밑의 문서가 `@space:` 를 받았다(실측).
    """
    root = vault(tmp_path, {
        "parent": {"space": "SEC", "ancestors": []},
        "child": {"space": "SEC", "ancestors": [{"id": "parent", "title": "p"}]},
    })
    rep = collect(root, FakeClient({"parent": None, "child": []}))

    assert "child" not in written(root), "조상을 못 읽었는데 자식이 열렸다"
    assert rep.unresolved == 1


def test_ancestor_failure_keeps_the_child_old_value(tmp_path):
    """확정을 못 할 뿐이므로, 옛 값이 있으면 그것을 지킨다 — 갑자기 사라지지 않는다."""
    root = vault(tmp_path, {
        "parent": {"space": "SEC", "ancestors": []},
        "child": {"space": "SEC", "ancestors": [{"id": "parent", "title": "p"}]},
    })
    acl_dir = root / "mirror" / "acl"
    acl_dir.mkdir(parents=True)
    (acl_dir / "acl.json").write_text(
        json.dumps({"child": [GROUP_PREFIX + "old"]}), encoding="utf-8")

    collect(root, FakeClient({"parent": None, "child": []}))
    assert written(root)["child"] == [GROUP_PREFIX + "old"]


def test_ancestor_outside_the_synced_set_is_still_queried(tmp_path):
    """
    싱크 집합에 없는 조상을 안 물어보면 "안 가져왔다" 와 "제한이 없다" 가 구별되지
    않는다. 실측(13,921건)에서 그런 조상은 2개뿐이라 비용은 없다시피 하다.
    """
    root = vault(tmp_path, {
        "child": {"space": "SEC", "ancestors": [{"id": "outside", "title": "o"}]},
    })
    client = FakeClient({"outside": [GROUP_PREFIX + "locked"], "child": []})
    collect(root, client)

    assert "outside" in client.asked, "싱크 밖 조상을 안 물어봤다"
    assert written(root)["child"] == [GROUP_PREFIX + "locked"]


def test_unusable_previous_file_does_not_crash_or_open(tmp_path):
    """`null` 도 유효한 JSON 이다 — 파싱된다고 쓸 수 있는 값은 아니다."""
    root = vault(tmp_path, {"1": {"space": "ENG", "ancestors": []},
                            "2": {"space": "ENG", "ancestors": []}})
    acl_dir = root / "mirror" / "acl"
    acl_dir.mkdir(parents=True)
    (acl_dir / "acl.json").write_text("null", encoding="utf-8")

    collect(root, FakeClient({"1": None, "2": []}))
    assert "1" not in written(root)


def test_total_failure_does_not_overwrite_the_old_file(tmp_path):
    """
    배운 게 없는데 덮으면 `{}` 가 남고, 서버는 그것을 **전 페이지 비공개**로 읽는다
    (fail-closed 라 맞는 해석이다). 볼트를 못 읽은 재기동이 멀쩡한 색인을 지우던 것과
    같은 자리 — 못 읽은 것과 없는 것은 다르다.
    """
    root = vault(tmp_path, {"1": {"space": "ENG", "ancestors": []},
                            "2": {"space": "ENG", "ancestors": []}})
    acl_dir = root / "mirror" / "acl"
    acl_dir.mkdir(parents=True)
    (acl_dir / "acl.json").write_text(json.dumps({"1": [GROUP_PREFIX + "old"]}), encoding="utf-8")

    rep = collect(root, FakeClient({"1": None, "2": None}))

    assert rep.wrote is False
    assert written(root) == {"1": [GROUP_PREFIX + "old"]}, "옛 파일이 덮였다"


def test_first_run_total_failure_writes_nothing(tmp_path):
    """옛 파일이 없으면 만들지도 않는다 — 빈 파일이 '수집했다'로 읽히면 안 된다."""
    root = vault(tmp_path, {"1": {"space": "ENG", "ancestors": []}})
    rep = collect(root, FakeClient({"1": None}))

    assert rep.wrote is False
    assert not (root / "mirror" / "acl" / "acl.json").exists()
