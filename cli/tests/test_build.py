"""
build 파이프라인의 계약 불변식 테스트.

`build.py` 이름을 다는 이유: 여기서 지키는 것은 전부 **build 가 디스크에 남기는
결과**다 — 샤딩 경로, `canonical_json` 결정성, front matter, 앵커 전치,
`ALIASES.md`·`TREE.md` 렌더, 멱등성. `layout`·`convert`·`models` 도 함께 시험하지만
그것들은 build 의 입력이라 결과로 판정된다.

여기서 지키는 것들은 나중에 서버판으로 갈 때 재크롤을 강제하는 항목들이다.
포맷이 흔들리면 두 판의 호환이 깨진다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wikilens import layout
from wikilens.build import build, transpose
from wikilens.convert import extract_cross_space_refs, parse, render_page_file
from wikilens.models import Link, StructureSignature, canonical_json


# --------------------------------------------------------------- 픽스처

def make_vault(tmp_path: Path) -> Path:
    """
    작은 합성 위키. 어휘 격차를 의도적으로 심는다 —
    제목은 공식 용어, 앵커는 구어체.
    """
    pages = {
        "100000001": {
            "title": "OAuth 2.0 인가 코드 흐름",
            "space": "PLATFORM",
            "version": 3,
            "xhtml": """
                <h1>개요</h1><h2>토큰 갱신</h2>
                <p>세션은 <ac:link><ri:page ri:content-title="세션 저장소"/>
                <ac:plain-text-link-body><![CDATA[세션 보관]]></ac:plain-text-link-body></ac:link>에 있다.</p>
            """,
        },
        "100000002": {
            "title": "세션 저장소",
            "space": "PLATFORM",
            "version": 1,
            "xhtml": """
                <h1>구조</h1>
                <p>인증은 <ac:link><ri:page ri:content-title="OAuth 2.0 인가 코드 흐름"/>
                <ac:plain-text-link-body><![CDATA[로그인 붙이는 법]]></ac:plain-text-link-body></ac:link> 참고.</p>
            """,
        },
        "100000003": {
            "title": "온보딩 가이드",
            "space": "PLATFORM",
            "version": 7,
            "xhtml": """
                <p>먼저 <ac:link><ri:page ri:content-title="OAuth 2.0 인가 코드 흐름"/>
                <ac:plain-text-link-body><![CDATA[로그인 붙이는 법]]></ac:plain-text-link-body></ac:link>을 읽고,
                <a href="/wiki/spaces/PLATFORM/pages/100000002/Session">세션 얘기</a>도 보세요.</p>
            """,
        },
        "100000004": {
            "title": "아무도 링크하지 않는 문서",
            "space": "PLATFORM",
            "version": 1,
            "xhtml": "<p>고아입니다.</p>",
        },
    }

    root = tmp_path / "vault"
    state = {"cursor": None, "pages": {}}
    for pid, m in pages.items():
        p = layout.ensure_parent(layout.raw_path(root, pid))
        p.write_text(m["xhtml"], encoding="utf-8")
        state["pages"][pid] = {
            "title": m["title"], "space": m["space"],
            "version": m["version"], "updated": "",
        }
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    return root


# --------------------------------------------------------------- 레이아웃

def test_shard_takes_the_tail_not_the_head():
    """
    앞자리는 엔트로피가 낮아 앞에서 자르면 한 디렉터리에 뭉친다
    (실측: 1번째 자리 1.93 bit vs 9번째 3.32 bit, 앞2/앞4 최대 378개 vs 뒤2 37개).
    """
    assert layout.shard("123456789") == "89"
    assert layout.shard("7") == "07"      # 2자리로 좌측 0 패딩
    assert layout.rel_page_path("123456789") == "mirror/pages/89/123456789.md"
    # 앞부분이 같아도 흩어져야 한다 — 이게 앞자르기와 갈리는 지점이다
    assert len({layout.shard(f"1027280{i:02}") for i in range(10)}) == 10


def test_hi_lo_shaped_ids_spread_evenly():
    """
    Confluence 는 Hi/Lo 로 ID 를 만든다 — high 블록이 건너뛰고 그 안의 low 는 연속이다.
    그 모양을 재현해, 앞자르기는 뭉치고 뒤자르기는 흩어지는지 확인한다.

    실코퍼스 실측(2,377건)과 같은 방향이어야 한다: 앞2/앞4 최대 378 vs 뒤2 최대 37.
    """
    ids = [str(hi * 100_000 + lo) for hi in (355, 439, 1027, 2853) for lo in range(600)]

    def head(pid: str) -> str:            # 옛 규칙
        p = pid.rjust(4, "0")
        return p[:2] + "/" + p[2:4]

    from collections import Counter
    worst_head = max(Counter(head(i) for i in ids).values())
    worst_tail = max(Counter(layout.shard(i) for i in ids).values())
    assert worst_tail * 5 < worst_head, (worst_tail, worst_head)
    assert worst_tail <= len(ids) // 100 + 1, "뒤 2자리면 100개 디렉터리에 고르게 퍼져야 한다"


def test_page_id_is_the_identifier_not_title():
    """제목이 바뀌어도 경로가 유지되어야 한다."""
    a = layout.rel_page_path("100000001")
    b = layout.rel_page_path("100000001")
    assert a == b and "100000001" in a


# --------------------------------------------------------------- 직렬화

def test_canonical_json_is_deterministic():
    """키 삽입 순서가 달라도 같은 바이트가 나와야 한다."""
    s1 = canonical_json({"b": 1, "a": [3, 2], "c": "한글"})
    s2 = canonical_json({"c": "한글", "a": [3, 2], "b": 1})
    assert s1 == s2
    assert s1.endswith("\n")
    assert "한글" in s1, "ensure_ascii=False 여야 grep 가능"


def test_link_order_does_not_change_signature():
    """Confluence가 링크 순서를 바꿔 내려줘도 서명이 같아야 한다."""
    la = Link(to="2", anchor="가"), Link(to="3", anchor="나")
    s1 = StructureSignature("1", "T", "S", 1, ["h"], list(la))
    s2 = StructureSignature("1", "T", "S", 1, ["h"], list(reversed(la)))
    assert canonical_json(s1.to_dict()) == canonical_json(s2.to_dict())


# --------------------------------------------------------------- 파싱

@pytest.mark.parametrize(
    "xhtml,expect_to,expect_anchor",
    [
        ('<ac:link><ri:content-entity ri:content-id="999"/>'
         '<ac:link-body>본문</ac:link-body></ac:link>', "999", "본문"),
        ('<a href="/wiki/spaces/K/pages/888/T">앵커</a>', "888", "앵커"),
        ('<a href="/pages/viewpage.action?pageId=777">쿼리</a>', "777", "쿼리"),
    ],
)
def test_link_extraction_variants(xhtml, expect_to, expect_anchor):
    sig, _ = parse("1", "T", "S", 1, xhtml)
    assert any(l.to == expect_to and l.anchor == expect_anchor for l in sig.links)


def test_anchorless_link_falls_back_to_title():
    sig, _ = parse("1", "T", "S", 1,
                   '<ac:link><ri:page ri:content-title="용어집"/></ac:link>')
    assert sig.links[0].anchor == "용어집"


def test_ambiguous_title_is_not_resolved():
    """같은 제목이 여러 스페이스에 있으면 해석하지 않는다. 틀린 간선보다 없는 편이 낫다."""
    def resolve(title, space):
        return None
    sig, _ = parse("1", "T", "S", 1,
                   '<ac:link><ri:page ri:content-title="중복"/></ac:link>', resolve)
    assert sig.links[0].to is None
    assert sig.links[0].to_title == "중복"


def test_missing_space_key_defaults_to_source_page_space():
    """
    링크에 space-key가 없으면 Confluence 규칙상 소스 페이지와 같은 스페이스를
    가리킨다. 이걸 안 채우면 같은 제목이 다른 스페이스에도 있을 때 동명이인으로
    오판해 해석 가능한 링크까지 미해결로 남는다 (실제 겪은 버그).
    """
    seen = {}

    def resolve(title, space):
        seen["title"], seen["space"] = title, space
        return "resolved-id"

    sig, _ = parse("1", "T", "MYSPACE", 1,
                   '<ac:link><ri:page ri:content-title="Guide"/></ac:link>', resolve)
    assert seen == {"title": "Guide", "space": "MYSPACE"}
    assert sig.links[0].to == "resolved-id"


def test_extract_cross_space_refs_requires_explicit_space_key():
    """
    space-key가 명시된 링크만 뽑는다. 생략된 건 '같은 스페이스'라는 뜻이라
    build 단계가 이미 처리하므로 여기서 또 다루면 중복이다.
    """
    xhtml = (
        '<ac:link><ri:page ri:content-title="A" ri:space-key="OTHER"/></ac:link>'
        '<ac:link><ri:page ri:content-title="B"/></ac:link>'  # space-key 없음
        '<ac:link><ri:page ri:content-title="A" ri:space-key="OTHER"/></ac:link>'  # 중복
    )
    assert extract_cross_space_refs(xhtml) == [("OTHER", "A")]


def test_front_matter_carries_id():
    sig = StructureSignature("100000001", "제목: 콜론 포함", "SP", 3)
    out = render_page_file(sig, "본문")
    assert 'id: "100000001"' in out
    assert '"제목: 콜론 포함"' in out, "콜론 포함 제목은 인용되어야 YAML이 깨지지 않음"


# --------------------------------------------------------------- 전치

def test_transpose_inverts_links(tmp_path):
    root = make_vault(tmp_path)
    build(root)
    entries = {
        json.loads(l)["target"]: json.loads(l)
        for l in layout.anchors_path(root).read_text(encoding="utf-8").splitlines() if l
    }

    oauth = entries["100000001"]
    texts = sorted({a["text"] for a in oauth["anchors"]})
    # 두 페이지가 같은 구어체로 부른다 -> 전치의 핵심 산출물
    assert "로그인 붙이는 법" in texts
    assert oauth["indeg"] == 2
    assert oauth["path"] == "mirror/pages/01/100000001.md"

    # 제목에는 그 표현이 없다. 이것이 어휘 격차다.
    assert "로그인" not in oauth["title"]


def test_orphan_detected(tmp_path):
    root = make_vault(tmp_path)
    rep = build(root)
    # 인링크 0인 페이지가 고아다. '온보딩 가이드'는 나가는 링크만 있고
    # 들어오는 링크가 없으므로 역시 고아 — 위키 정리에서 실제로 유용한 신호다.
    assert rep.orphans == 2
    aliases = layout.aliases_path(root).read_text(encoding="utf-8")
    assert "아무도 링크하지 않는 문서" in aliases
    assert "온보딩 가이드" in aliases
    assert "고아 문서 후보" in aliases


def test_aliases_puts_path_next_to_alias(tmp_path):
    """
    grep 결과에서 경로가 같은 블록에 나와야 한다.
    이게 로컬판이 서버 없이 동작하는 이유다.
    """
    root = make_vault(tmp_path)
    build(root)
    text = layout.aliases_path(root).read_text(encoding="utf-8")
    hits = [l for l in text.splitlines() if "로그인 붙이는 법" in l]
    assert hits, "별칭이 색인에 있어야 함"
    # 컨텍스트 플래그 없이 grep 해도 경로가 나와야 한다
    assert "mirror/pages/01/100000001.md" in hits[0], "경로가 같은 줄에 있어야 함"


# --------------------------------------------------------------- 멱등성

def test_build_is_idempotent(tmp_path):
    """
    두 번 빌드해도 바이트가 동일해야 한다.
    아니면 서버판에서 git diff 기반 무효화가 매번 전체 발화한다.
    """
    root = make_vault(tmp_path)
    build(root)
    snap1 = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    build(root)
    snap2 = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    assert snap1.keys() == snap2.keys()
    for p in snap1:
        assert snap1[p] == snap2[p], f"멱등성 위반: {p}"


def test_resolution_rate_reported(tmp_path):
    root = make_vault(tmp_path)
    rep = build(root)
    assert rep.total_links == 4
    assert rep.resolved_links == 4
    assert rep.resolution_rate == 1.0


# --------------------------------------------------------------- stats

def test_stats_gap_ignores_case_and_order_but_catches_real_gap(tmp_path, capsys):
    """
    어휘 격차 판정은 토큰 집합 기준이어야 한다.
    대소문자·어순만 다른 앵커("api reference" vs "API Reference")는 검색
    토크나이저가 이미 흡수하므로 격차가 아니다. 완전히 다른 단어로 불리는
    경우("lookup" vs "Search Index")만 격차로 잡아야 한다.
    """
    from types import SimpleNamespace

    from wikilens.cli import _cmd_stats

    pages = {
        "200000001": {
            "title": "API Reference", "space": "DOCS", "version": 1,
            "xhtml": "<p>no outgoing links.</p>",
        },
        "200000002": {
            "title": "API Reference Guide", "space": "DOCS", "version": 1,
            "xhtml": (
                '<p>See <ac:link><ri:page ri:content-title="API Reference"/>'
                '<ac:plain-text-link-body><![CDATA[api reference]]></ac:plain-text-link-body>'
                "</ac:link>."
            ),
        },
        "200000003": {
            "title": "Search Index", "space": "DOCS", "version": 1,
            "xhtml": "<p>no outgoing links.</p>",
        },
        "200000004": {
            "title": "Search Index Guide", "space": "DOCS", "version": 1,
            "xhtml": (
                '<p>See <ac:link><ri:page ri:content-title="Search Index"/>'
                '<ac:plain-text-link-body><![CDATA[lookup]]></ac:plain-text-link-body>'
                "</ac:link>."
            ),
        },
    }
    root = tmp_path / "vault"
    state = {"cursor": None, "pages": {}}
    for pid, m in pages.items():
        p = layout.ensure_parent(layout.raw_path(root, pid))
        p.write_text(m["xhtml"], encoding="utf-8")
        state["pages"][pid] = {
            "title": m["title"], "space": m["space"],
            "version": m["version"], "updated": "",
        }
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    build(root)

    _cmd_stats(SimpleNamespace(root=str(root)))
    out = capsys.readouterr().out

    assert "제목과 어휘가 안 겹치는 별칭을 가진 페이지: 1 " in out


def test_stats_gap_ignores_untokenizable_title(tmp_path, capsys):
    """
    제목 자체가 토큰화되지 않으면(너무 짧거나 기호뿐) '겹침 없음'을 판단할
    신호가 없다 — 무조건 격차로 잡으면 안 된다. 앵커 쪽 빈 토큰을 격차로
    안 세는 것과 대칭이어야 한다.
    """
    from types import SimpleNamespace

    from wikilens.cli import _cmd_stats

    pages = {
        "300000001": {
            "title": "Q&A", "space": "DOCS", "version": 1,  # tokenize("Q&A") == []
            "xhtml": "<p>no outgoing links.</p>",
        },
        "300000002": {
            "title": "Q&A Source", "space": "DOCS", "version": 1,
            "xhtml": (
                '<p>See <ac:link><ri:page ri:content-title="Q&A"/>'
                '<ac:plain-text-link-body><![CDATA[Frequently Asked Questions]]></ac:plain-text-link-body>'
                "</ac:link>."
            ),
        },
    }
    root = tmp_path / "vault"
    state = {"cursor": None, "pages": {}}
    for pid, m in pages.items():
        p = layout.ensure_parent(layout.raw_path(root, pid))
        p.write_text(m["xhtml"], encoding="utf-8")
        state["pages"][pid] = {
            "title": m["title"], "space": m["space"],
            "version": m["version"], "updated": "",
        }
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    build(root)

    _cmd_stats(SimpleNamespace(root=str(root)))
    out = capsys.readouterr().out

    assert "제목과 어휘가 안 겹치는 별칭을 가진 페이지: 0 " in out


# --------------------------------------------------------------- TREE.md

def test_tree_reflects_parent_child_hierarchy(tmp_path):
    """
    ancestors 로 부모-자식을 그대로 중첩해서 보여줘야 한다. 앵커 색인과
    분리된 별도 산출물이라, 이 문서들에 서로 링크가 없어도(고아여도) 뜬다.
    """
    pages = {
        "100": {"title": "루트", "ancestors": []},
        "200": {"title": "자식A", "ancestors": [{"id": "100", "title": "루트"}]},
        "300": {
            "title": "손자B",
            "ancestors": [{"id": "100", "title": "루트"}, {"id": "200", "title": "자식A"}],
        },
    }
    root = tmp_path / "vault"
    state = {"cursor": None, "pages": {}}
    for pid, m in pages.items():
        p = layout.ensure_parent(layout.raw_path(root, pid))
        p.write_text("<p>내용 없음.</p>", encoding="utf-8")
        state["pages"][pid] = {
            "title": m["title"], "space": "DOCS", "version": 1, "updated": "",
            "ancestors": m["ancestors"],
        }
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    build(root)

    tree = layout.tree_path(root).read_text(encoding="utf-8")
    lines = [l for l in tree.splitlines() if l.strip().startswith("-")]
    assert lines[0].startswith("- 루트")
    assert lines[1].startswith("  - 자식A")
    assert lines[2].startswith("    - 손자B")


def test_tree_treats_out_of_sync_parent_as_root(tmp_path):
    """부모가 동기화 범위 밖(다른 콘텐츠 타입 등)이면 최상위로 취급한다."""
    pages = {
        "500": {
            "title": "고아 자식",
            "ancestors": [{"id": "999999", "title": "동기화 안 된 부모"}],
        },
    }
    root = tmp_path / "vault"
    state = {"cursor": None, "pages": {}}
    for pid, m in pages.items():
        p = layout.ensure_parent(layout.raw_path(root, pid))
        p.write_text("<p>내용 없음.</p>", encoding="utf-8")
        state["pages"][pid] = {
            "title": m["title"], "space": "DOCS", "version": 1, "updated": "",
            "ancestors": m["ancestors"],
        }
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    build(root)

    tree = layout.tree_path(root).read_text(encoding="utf-8")
    assert "- 고아 자식" in tree


def test_tree_survives_cyclic_ancestors(tmp_path):
    """
    순환 ancestors 에 갇힌 페이지가 TREE.md 에서 **사라지면 안 된다.**

    순환이면 어느 쪽도 루트가 아니라, 루트에서 내려가는 렌더링이 영영 못 닿는다.
    예외도 안 나고 개수도 안 맞춰보므로 조용히 유실된다 — 하필 TREE.md 가
    고아 문서에 닿는 유일한 경로다. 실측: A↔B 순환에서 3건 중 1건만 실렸다.

    서버판 `TreeRenderer` 는 같은 방어를 이미 갖고 있었다. 한쪽만 있으면
    같은 `.sync-state.json` 으로 두 판이 다른 트리를 낸다.
    """
    pages = {
        "1": {"title": "A", "ancestors": [{"id": "2", "title": "B"}]},
        "2": {"title": "B", "ancestors": [{"id": "1", "title": "A"}]},
        "3": {"title": "정상", "ancestors": []},
    }
    root = tmp_path / "vault"
    state = {"cursor": None, "pages": {}}
    for pid, m in pages.items():
        layout.ensure_parent(layout.raw_path(root, pid)).write_text(
            "<p>내용 없음.</p>", encoding="utf-8"
        )
        state["pages"][pid] = {
            "title": m["title"], "space": "DOCS", "version": 1, "updated": "",
            "ancestors": m["ancestors"],
        }
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    build(root)

    tree = layout.tree_path(root).read_text(encoding="utf-8")
    listed = [l for l in tree.splitlines() if l.strip().startswith("- ")]
    assert len(listed) == 3, f"순환에 갇힌 페이지가 유실됐다: {listed}"
    for title in ("A", "B", "정상"):
        assert any(f"- {title} [" in l for l in listed), f"{title} 가 없다"
