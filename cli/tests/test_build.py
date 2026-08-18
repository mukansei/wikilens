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

import contextlib
import io
import json
from pathlib import Path

import pytest

from wikilens import layout
from wikilens import build as build_mod
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


def test_title_only_links_are_not_reported_as_orphans(tmp_path):
    """
    **제목 그대로만 링크된 페이지를 고아로 보고하던 자리.**

    별칭은 "제목과 다른 표현" 이라 제목 그대로 링크하면 안 생긴다. 그런데 그런
    페이지가 "별칭 없는 페이지 … **고아 문서 후보**" 절에 들어가고, 인링크 수가
    **하드코딩된 `0`** 으로 찍혔다. 정반대다 — 다들 제목 그대로 부른다는 것은
    그 제목이 정본이라는 뜻이다.

    실코퍼스 실측(13,933건): 그 절 13,469건 중 **1,258건이 인링크가 있었고** 가장
    심한 것은 115개였다. 로컬판은 이 파일을 grep 해서 답하므로 그대로 모델에 간다.
    """
    root = tmp_path / "vault"
    pages = {
        "900000001": {"title": "결재 정책", "space": "DOCS",
                      "xhtml": "<p>정책입니다.</p>"},
        "900000002": {"title": "안내", "space": "DOCS", "xhtml": """
            <p><ac:link><ri:page ri:content-title="결재 정책"/>
            <ac:plain-text-link-body><![CDATA[결재 정책]]></ac:plain-text-link-body></ac:link></p>
        """},
        "900000003": {"title": "공지", "space": "DOCS", "xhtml": """
            <p><ac:link><ri:page ri:content-title="결재 정책"/>
            <ac:plain-text-link-body><![CDATA[결재 정책]]></ac:plain-text-link-body></ac:link></p>
        """},
    }
    state = {"cursor": None, "pages": {}}
    for pid, m in pages.items():
        layout.ensure_parent(layout.raw_path(root, pid)).write_text(m["xhtml"], encoding="utf-8")
        state["pages"][pid] = {"title": m["title"], "space": m["space"],
                               "version": 1, "updated": ""}
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")

    build(root)
    aliases = layout.aliases_path(root).read_text(encoding="utf-8")

    head, _, tail = aliases.partition("## 별칭 없는 페이지")
    assert "결재 정책" not in tail, "제목으로만 링크된 페이지가 고아 목록에 있다"
    assert "DOCS | 결재 정책 | (제목으로만) | 2 |" in head, (
        f"인링크 수가 실제 값이어야 한다 — 하드코딩된 0 이 아니다:\n{aliases}"
    )


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


# --------------------------------------------------------------- 문자 집합

def make_multilang_vault(tmp_path: Path) -> Path:
    """한국어 원본과 그 베트남어 번역본. 실코퍼스에서 이 쌍이 문제를 냈다."""
    pages = {
        "900000001": {"title": "GA 접속 가이드", "space": "DOCS",
                      "xhtml": "<h1>GA 접속</h1><p>GCP 콘솔에서 계정을 신청하세요.</p>"},
        "900000002": {"title": "Hướng dẫn truy cập GA", "space": "DOCS",
                      "xhtml": "<h1>Truy cập GA</h1><p>Sử dụng GCP Console để "
                               "yêu cầu tài khoản của bạn.</p>"},
    }
    root = tmp_path / "vault"
    state = {"cursor": None, "pages": {}}
    for pid, m in pages.items():
        layout.ensure_parent(layout.raw_path(root, pid)).write_text(m["xhtml"], encoding="utf-8")
        state["pages"][pid] = {"title": m["title"], "space": m["space"], "version": 1, "updated": ""}
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return root


def test_declared_scripts_keep_everything_by_default(tmp_path):
    """**기본이 꺼짐이다.** 켜짐이면 처음 쓰는 사람의 문서가 조용히 사라진다."""
    root = make_multilang_vault(tmp_path)
    rep = build(root)
    assert rep.excluded == []
    assert "Hướng dẫn" in layout.aliases_path(root).read_text(encoding="utf-8")


def test_foreign_script_page_leaves_the_vault(tmp_path):
    """
    선언 밖 문서는 **파생물 셋에서 전부** 빠진다.

    하나라도 남으면 그쪽 경로로 다시 올라온다 — 로컬판은 `ALIASES.md`·`TREE.md` 를
    grep 하고 서버는 `anchors.jsonl` 로 앵커를 얻는다.
    """
    root = make_multilang_vault(tmp_path)
    rep = build(root, ["hangul", "ascii"], 0.15)

    assert rep.excluded == ["900000002"]
    for path in (layout.aliases_path(root), layout.tree_path(root), layout.anchors_path(root)):
        assert "900000002" not in path.read_text(encoding="utf-8"), f"{path.name} 에 남았다"
        assert "Hướng dẫn" not in path.read_text(encoding="utf-8")
    assert "GA 접속 가이드" in layout.aliases_path(root).read_text(encoding="utf-8")


def test_exclusion_is_written_for_the_server(tmp_path):
    """
    **서버가 같은 결정을 따르는 유일한 통로다.**

    서버는 페이지 목록을 `.sync-state.json`(= `sync` 가 쓴다)에서 얻으므로, 파생물에서
    빼는 것만으로는 색인에 그대로 들어간다.
    """
    root = make_multilang_vault(tmp_path)
    build(root, ["hangul", "ascii"], 0.15)
    d = json.loads((root / "derived" / "excluded.json").read_text(encoding="utf-8"))
    assert d["excluded"] == ["900000002"]


def test_exclusion_file_is_written_even_when_empty(tmp_path):
    """파일이 없는 것과 "뺄 것이 없다" 가 구별되어야 서버가 옛 볼트를 안다."""
    root = make_multilang_vault(tmp_path)
    build(root, ["hangul", "ascii", "vietnamese"], 0.15)
    d = json.loads((root / "derived" / "excluded.json").read_text(encoding="utf-8"))
    assert d["excluded"] == []


def test_body_is_removed_so_both_editions_agree(tmp_path):
    """
    **본문도 지운다.** 안 지우면 로컬판이 본문 grep(스킬 3단계)으로 여전히 찾는데
    서버판은 색인에 없어 못 찾는다 — 같은 볼트에 두 판이 다른 답을 낸다.
    """
    root = make_multilang_vault(tmp_path)
    rep = build(root, ["hangul", "ascii"], 0.15)
    assert rep.pages_removed == 0, "처음 빌드면 애초에 안 썼으므로 지울 것이 없다"
    assert not layout.page_path(root, "900000002").exists()
    assert layout.page_path(root, "900000001").exists()


def test_bilingual_title_with_korean_body_survives(tmp_path):
    """
    **제목을 판정에 넣으면 계층이 깨진다.**

    이 코퍼스는 목차 페이지 제목을 이중언어로 단다(`01-07. Không có xử lý
    (기타)`). 그런 페이지는 본문이 거의 없어서, 제목을 넣으면 비율을 지배해 빠진다 —
    실측 44건이 그렇게 빠졌고 **자식 255건이 부모를 잃어 트리에서 루트로 승격**됐다.

    반대로 제목을 빼야만 걸리는 문서 4건은 **전부 진짜 베트남어**였다(제목만 한국어).
    """
    root = tmp_path / "vault"
    pages = {
        "900000010": ("01-07. Không có xử lý (기타 목차)", "<p>목록입니다</p>", None),
        "900000011": ("결재 정책", "<p>결재 정책 본문입니다 한국어로 길게 씁니다</p>", "900000010"),
    }
    state = {"cursor": None, "pages": {}}
    for pid, (t, x, par) in pages.items():
        layout.ensure_parent(layout.raw_path(root, pid)).write_text(x, encoding="utf-8")
        state["pages"][pid] = {"title": t, "space": "D", "version": 1, "updated": "",
                               **({"ancestors": [{"id": par, "title": pages[par][0]}]} if par else {})}
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")

    rep = build(root, ["hangul", "ascii"], 0.15)
    assert rep.excluded == [], "본문이 한국어인 목차 페이지가 빠졌다"

    tree = layout.tree_path(root).read_text(encoding="utf-8")
    assert "  - 결재 정책" in tree, "부모가 빠지면 자식이 루트로 승격돼 계층이 평평해진다"


def test_foreign_body_with_korean_title_is_excluded(tmp_path):
    """제목만 한국어인 번역본. **제목을 보면 놓친다** — 실코퍼스에 4건 있었다."""
    root = tmp_path / "vault"
    x = "<p>Giải thích Page. Quản lý Tình hình chuyển giao công việc tự xử lý.</p>"
    layout.ensure_parent(layout.raw_path(root, "900000020")).write_text(x, encoding="utf-8")
    layout.ensure_parent(layout.sync_state_path(root)).write_text(json.dumps(
        {"cursor": None, "pages": {"900000020": {
            "title": "이관현황 (당김/ 본인처리)", "space": "D", "version": 1, "updated": ""}}},
        ensure_ascii=False), encoding="utf-8")

    assert build(root, ["hangul", "ascii"], 0.15).excluded == ["900000020"]


def test_previously_written_body_is_cleaned_up(tmp_path):
    """설정을 바꿔 다시 빌드하면 **전에 쓴 파일도 지운다** — 남으면 로컬 grep 이 찾는다."""
    root = make_multilang_vault(tmp_path)
    build(root)                                   # 전부 편입
    assert layout.page_path(root, "900000002").exists()
    rep = build(root, ["hangul", "ascii"], 0.15)  # 이제 뺀다
    assert rep.pages_removed == 2, "pages/ 와 structure/ 둘"
    assert not layout.page_path(root, "900000002").exists()


def test_removal_is_reversible_without_resync(tmp_path):
    """
    원본 `raw/` 는 안 지우므로 설정만 바꿔 다시 빌드하면 되살아난다.
    재싱크(네트워크)가 필요하면 되돌리기가 비싸져 아무도 안 바꾼다.
    """
    root = make_multilang_vault(tmp_path)
    build(root, ["hangul", "ascii"], 0.15)
    assert not layout.page_path(root, "900000002").exists()

    rep = build(root, ["hangul", "ascii", "vietnamese"], 0.15)
    assert rep.excluded == []
    assert layout.page_path(root, "900000002").exists(), "본문이 되살아나야 한다"
    assert "Hướng dẫn" in layout.aliases_path(root).read_text(encoding="utf-8")


def test_unknown_script_name_is_refused(tmp_path):
    """조용히 무시하면 사용자는 필터가 걸린 줄 알고 쓴다(D14 와 같은 규칙)."""
    root = make_multilang_vault(tmp_path)
    with pytest.raises(ValueError, match="알 수 없는 문자 집합"):
        build(root, ["french"], 0.15)


@pytest.mark.parametrize("bad", [-1.0, -0.15, 1.5, 15.0])
def test_out_of_range_threshold_is_refused(tmp_path, bad):
    """
    **범위 밖 문턱은 조용히 파괴적이다.**

    `-1` 이면 전 문서가 제외되며 **본문 파일이 전부 지워지고**(실측 2/2), `15`(퍼센트로
    생각한 실수)면 아무것도 안 걸러 사용자는 걸린 줄 안다. `config.json` 쪽만 범위를
    보고 있어서 `--script-threshold` 로는 그대로 들어갔다.
    """
    root = make_multilang_vault(tmp_path)
    build(root)                                   # 먼저 정상 빌드 — 파일이 생긴다
    assert layout.page_path(root, "900000002").exists()

    with pytest.raises(ValueError, match="0~1"):
        build(root, ["hangul", "ascii"], bad)
    # **거부가 파괴보다 먼저여야 한다.** 판정을 돌린 뒤에 거부하면 그 사이에 이미
    # 파일이 지워진다 — 되돌리려면 재빌드가 필요한데 그 재빌드도 같은 인자면 또 죽는다.
    assert layout.page_path(root, "900000002").exists(), "거부했는데 파일이 지워졌다"


def test_range_spec_is_case_insensitive():
    """
    이름은 `.lower()` 로 정규화하는데 범위만 대문자를 요구하면 비일관적이다 —
    `u+0100-017f` 를 쓴 사람이 "알 수 없는 문자 집합" 과 **이름 목록**을 받는다
    (범위를 썼는데 이름을 고치라는 안내다).
    """
    from wikilens.scripts import resolve
    assert resolve(["u+0100-017f"]) == resolve(["U+0100-017F"])
    assert resolve(["HANGUL"]) == resolve(["hangul"])


def test_empty_scripts_argument_turns_the_filter_off(tmp_path):
    """`--scripts ""` 로 `config.json` 의 설정을 한 번만 끌 수 있어야 한다."""
    root = make_multilang_vault(tmp_path)
    assert build(root, [], 0.15).excluded == []


def test_excluded_file_records_why(tmp_path):
    """
    **왜 빠졌는지 답할 수 있어야 한다.** ID 목록만 남기면 운영자가 "이 문서가 왜 없지"
    를 물었을 때 볼트를 다시 계산해야 하고, 서버도 개수만 낼 뿐 무슨 설정이었는지
    모른다 — 서버에는 이 설정이 없으므로 **볼트가 스스로 말해야 한다.**
    """
    root = make_multilang_vault(tmp_path)
    build(root, ["hangul", "ascii"], 0.10)
    d = json.loads((root / "derived" / "excluded.json").read_text(encoding="utf-8"))
    assert d["excluded"] == ["900000002"]
    assert d["scripts"] == ["hangul", "ascii"]
    assert d["threshold"] == 0.10
    assert d["ratio"]["900000002"] > 0.10, "문턱을 넘은 실제 값이 남아야 한다"


def test_excluded_file_says_the_filter_was_off(tmp_path):
    """꺼짐과 "뺄 것이 없음" 이 구별되어야 한다 — 서버가 그 둘을 다르게 말한다."""
    root = make_multilang_vault(tmp_path)
    build(root)
    d = json.loads((root / "derived" / "excluded.json").read_text(encoding="utf-8"))
    assert d["scripts"] == [] and d["threshold"] is None


def test_ratio_is_normalization_independent():
    """
    **같은 문장이 표기 방식에 따라 다르게 판정되면 안 된다.**

    결합 기호(U+0300~036F)는 `isalpha()` 가 False 라 낱말 경계로 취급된다. 그러면
    NFD 로 쓴 `Sử` 가 `S`·`ư` 둘로 쪼개지고 성조 정보가 사라진다 — 실측으로 같은
    문장이 NFC 0.571 · NFD 0.111 로 **5배** 갈렸다(문턱 0.10 을 겨우 넘는 값이다).

    macOS 가 NFD 를 쓰고 Confluence 는 사용자 입력을 그대로 저장한다.
    """
    import unicodedata
    from wikilens.scripts import resolve, foreign_word_ratio
    r = resolve(["hangul", "ascii"])
    s = "Sử dụng GCP Console để truy cập"
    vals = {foreign_word_ratio(unicodedata.normalize(f, s), r) for f in ("NFC", "NFD", "NFKC", "NFKD")}
    assert len(vals) == 1, f"정규화 형태마다 다른 값이 나왔다: {vals}"
    assert vals.pop() > 0.5


# --------------------------------------------------------------- 진행 표시

def _big_vault(tmp_path: Path) -> tuple[Path, int]:
    """진행 표시 하한을 넘기는 볼트. 본문은 최소로 — 여기서 재는 것은 개수뿐이다."""
    root = tmp_path / "vault"
    state = {"cursor": None, "pages": {}}
    n = build_mod.PROGRESS_EVERY * 2 + 1
    for i in range(n):
        pid = str(300000000 + i)
        layout.ensure_parent(layout.raw_path(root, pid)).write_text(
            "<p>본문</p>", encoding="utf-8")
        state["pages"][pid] = {"title": f"문서 {i}", "space": "S",
                               "version": 1, "updated": ""}
    layout.ensure_parent(layout.sync_state_path(root)).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return root, n


def test_build_reports_progress_on_large_vaults(tmp_path):
    """
    13,947건 코퍼스에서 `build` 는 **2분 28초 동안 한 줄도 안 찍었다**(실측 2026-08-18).
    그 침묵이 `sync` 바로 뒤에 오는데 `sync` 는 페이지마다 찍으므로, 사용자에게는
    싱크가 멈춘 것으로 보인다. 첫 설정에서 이 침묵이 마지막 인상이다.

    간격을 세는 것이 아니라 **말을 하는지**를 잠근다 — 간격은 튜닝 값이다.
    """
    root, n = _big_vault(tmp_path)

    seen: list[tuple[int, int]] = []
    build(root, progress=lambda done, total: seen.append((done, total)))
    assert seen, "큰 볼트인데 진행 표시가 한 번도 안 나왔다"
    assert all(t == n for _, t in seen), "총계가 흔들리면 진행률로 못 읽는다"
    assert seen == sorted(seen), "단조 증가여야 한다"


def test_progress_failure_does_not_abort_the_build(tmp_path):
    """
    `wikilens build | head` 처럼 파이프를 일찍 닫으면 `print` 가 `BrokenPipeError` 를
    내고, 그것이 2패스 한가운데서 빌드를 끝낸다(실측: traceback 과 함께 죽었다).

    그러면 `mirror/pages/` 는 일부만 갱신됐는데 `ALIASES`·`TREE`·`anchors` 는 옛 집합을
    가리키는 상태로 남는다 — 파생물은 전부 마지막에 쓰기 때문이다. **진행 표시는
    장식이라 그것 때문에 산출물이 찢어지면 안 된다.**
    """
    root, n = _big_vault(tmp_path)

    calls = []

    def boom(done: int, total: int) -> None:
        calls.append(done)
        raise BrokenPipeError(32, "Broken pipe")

    rep = build(root, progress=boom)
    assert calls == [build_mod.PROGRESS_EVERY], "한 번 실패했으면 다시 부르지 않는다"
    assert rep.pages_written == n, "빌드가 끝까지 가야 한다"
    assert (root / "ALIASES.md").exists(), "파생물이 쓰여야 한다"


def test_build_is_silent_without_a_callback(tmp_path):
    """
    `build()` 는 라이브러리다 — 찍는 것은 CLI 의 몫이다. 기본이 시끄러우면
    테스트 출력과 프로그램 호출이 함께 오염된다.
    """
    root = make_vault(tmp_path)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        build(root)
    assert out.getvalue() == ""
