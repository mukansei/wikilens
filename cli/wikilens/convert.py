"""
Confluence storage format(XHTML) 파서.

앵커 텍스트 추출이 이 프로젝트의 핵심 가치이므로, 마크다운으로 변환한 뒤가 아니라
**원본 XHTML에서** 링크를 뽑는다. storage format은 링크가 구조화돼 있어
(`ac:link` + `ri:page` + `ac:plain-text-link-body`) 정확히 뽑을 수 있지만,
마크다운으로 내린 뒤에는 그 구조가 평평해져 앵커와 대상의 대응이 흐려진다.

네임스페이스 주의: API가 내려주는 body.storage는 `ac:`/`ri:` 접두사를 쓰지만
네임스페이스 선언이 없다. lxml XML 파서는 여기서 실패하므로 html.parser를 쓴다.
html.parser는 접두사를 태그 이름의 일부로 그대로 보존한다.
"""
from __future__ import annotations

import re
from typing import Callable

from bs4 import BeautifulSoup
from markdownify import markdownify

from .models import Link, StructureSignature

# /wiki/spaces/KEY/pages/123456789/Title 또는 /pages/viewpage.action?pageId=123
_URL_PAGE_ID = re.compile(r"/pages/(?:viewpage\.action\?pageId=)?(\d+)")
_QUERY_PAGE_ID = re.compile(r"[?&]pageId=(\d+)")

# 본문에 남기지 않는 매크로 (렌더링 산물이라 내용이 없음)
_DROP_MACROS = {"toc", "children", "pagetree", "recently-updated", "contributors"}

TitleResolver = Callable[[str, str | None], str | None]
"""(제목, 스페이스키) -> 페이지 ID 또는 None"""


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split())


def _extract_links(
    soup: BeautifulSoup, resolve: TitleResolver | None, source_space: str | None = None
) -> list[Link]:
    links: list[Link] = []

    # 1) Confluence 네이티브 링크
    for el in soup.find_all("ac:link"):
        body = el.find("ac:plain-text-link-body") or el.find("ac:link-body")
        anchor = _text(body) if body else ""

        target_id = None
        target_title = None

        entity = el.find("ri:content-entity")
        if entity and entity.get("ri:content-id"):
            target_id = str(entity["ri:content-id"])

        page = el.find("ri:page")
        if page:
            target_title = page.get("ri:content-title")
            # space-key 생략은 Confluence 규칙상 "링크가 속한 페이지와 같은 스페이스".
            # 이걸 안 채우면 같은 제목이 다른 스페이스에도 있을 때 동명이인으로
            # 오판해 해석 가능한 링크까지 미해결로 남는다.
            space = page.get("ri:space-key") or source_space
            if target_id is None and target_title and resolve:
                target_id = resolve(target_title, space)

        # 앵커가 비면 대상 제목이 곧 앵커다 (Confluence 기본 렌더링과 동일)
        if not anchor and target_title:
            anchor = target_title

        if anchor and (target_id or target_title):
            links.append(Link(to=target_id, anchor=anchor, to_title=target_title))

    # 2) 일반 <a href> — URL에 페이지 ID가 박혀 있는 경우
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        m = _URL_PAGE_ID.search(href) or _QUERY_PAGE_ID.search(href)
        if not m:
            continue
        anchor = _text(a)
        if anchor:
            links.append(Link(to=m.group(1), anchor=anchor))

    # 같은 (대상, 앵커) 중복 제거. 한 페이지에서 같은 표현으로 여러 번 링크해도 1표.
    seen, out = set(), []
    for l in links:
        k = l.key()
        if k not in seen:
            seen.add(k)
            out.append(l)
    return out


def extract_cross_space_refs(xhtml: str) -> list[tuple[str, str]]:
    """
    space-key가 **명시적으로** 붙은 링크 대상만 (space, title)로 뽑는다.
    sync가 지정 스페이스 밖 대상을 낱개로 따라가 받아올지 판단하는 데 쓴다.

    space-key 생략 링크(같은 스페이스로 간주)는 여기서 다루지 않는다 —
    그건 build 단계의 title→ID 해석이 이미 처리한다(convert.parse 참고).
    한 홉만 본다: 이렇게 받아온 페이지의 링크를 또 따라가지 않는다 —
    안 그러면 보일러플레이트 템플릿 페이지 하나가 무관한 스페이스 수십 개를
    연쇄적으로 끌고 들어올 수 있다.
    """
    soup = BeautifulSoup(xhtml or "", "html.parser")
    out: set[tuple[str, str]] = set()
    for el in soup.find_all("ac:link"):
        page = el.find("ri:page")
        if not page:
            continue
        title = page.get("ri:content-title")
        space = page.get("ri:space-key")
        if title and space:
            out.add((space, title))
    return sorted(out)


def _extract_headings(soup: BeautifulSoup) -> list[str]:
    out = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        t = _text(tag)
        if t:
            out.append(t)
    return out


def _normalize_for_markdown(soup: BeautifulSoup) -> BeautifulSoup:
    """마크다운 변환 전에 Confluence 전용 요소를 표준 HTML로 바꾼다."""
    for el in list(soup.find_all("ac:link")):
        body = el.find("ac:plain-text-link-body") or el.find("ac:link-body")
        page = el.find("ri:page")
        entity = el.find("ri:content-entity")
        label = _text(body) if body else (page.get("ri:content-title") if page else "")
        a = soup.new_tag("a")
        if entity and entity.get("ri:content-id"):
            a["href"] = f"#page-{entity['ri:content-id']}"
        elif page and page.get("ri:content-title"):
            a["href"] = f"#title-{page['ri:content-title']}"
        a.string = label or "(link)"
        el.replace_with(a)

    for macro in list(soup.find_all("ac:structured-macro")):
        name = (macro.get("ac:name") or "").lower()
        if name in _DROP_MACROS:
            macro.decompose()
            continue
        if name == "code":
            body = macro.find("ac:plain-text-body")
            pre = soup.new_tag("pre")
            code = soup.new_tag("code")
            code.string = body.get_text() if body else ""
            pre.append(code)
            macro.replace_with(pre)
            continue
        # 나머지 매크로: 내용은 살리고 종류만 주석으로 남긴다 (무손실 원본은 raw/에 있음)
        rich = macro.find("ac:rich-text-body")
        div = soup.new_tag("div")
        if rich:
            for child in list(rich.children):
                div.append(child.extract())
        div.insert(0, soup.new_string(f"[macro:{name}] "))
        macro.replace_with(div)

    for tag in list(soup.find_all(re.compile(r"^(ac|ri):"))):
        tag.unwrap()

    return soup


def parse(
    page_id: str,
    title: str,
    space: str,
    version: int,
    xhtml: str,
    resolve: TitleResolver | None = None,
) -> tuple[StructureSignature, str]:
    """(구조 서명, 마크다운) 반환."""
    soup = BeautifulSoup(xhtml or "", "html.parser")

    links = _extract_links(soup, resolve, source_space=space)
    headings = _extract_headings(soup)

    md = markdownify(str(_normalize_for_markdown(soup)), heading_style="ATX").strip()
    md = re.sub(r"\n{3,}", "\n\n", md)

    sig = StructureSignature(
        page_id=str(page_id),
        title=title,
        space=space,
        version=int(version),
        headings=headings,
        links=links,
    )
    return sig, md


def render_page_file(sig: StructureSignature, markdown: str) -> str:
    """front matter + 본문. 권위 있는 식별자는 front matter의 id."""
    fm = [
        "---",
        f'id: "{sig.page_id}"',
        f"title: {_yaml_scalar(sig.title)}",
        f"space: {_yaml_scalar(sig.space)}",
        f"version: {sig.version}",
        "---",
        "",
    ]
    return "\n".join(fm) + markdown + "\n"


def _yaml_scalar(s: str) -> str:
    s = s or ""
    if any(c in s for c in ':#{}[]&*!|>%@`"\'\n') or s != s.strip():
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s
