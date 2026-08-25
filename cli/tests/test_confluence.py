"""
Confluence 클라이언트 검증.

실제 Confluence 없이 검증하려고 두 가지 배포 형태를 흉내내는 가짜 서버를 띄운다.
Cloud(`/wiki` 접두사)와 Server/DC(접두사 없음), 그리고 인증 실패·레이트 리밋·
페이지네이션·중단 후 재개까지 확인한다.

여기서 잡히는 것들은 전부 "실제 Confluence에 처음 붙일 때 터질 만한" 것들이다.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from wikilens import layout
from wikilens.auth import BasicAuth, BearerAuth
from wikilens.sync import (
    ConfluenceClient,
    ConfluenceError,
    _cql_for_space,
    _cql_for_title,
    client_from_env,
    sync,
)


def make_pages(n: int, space: str = "PLATFORM"):
    return [
        {
            "id": str(500000000 + i),
            "title": f"문서 {i}",
            "space": {"key": space},
            "version": {"number": 1, "when": f"2026-07-30T0{i%10}:00:00.000Z"},
            "body": {"storage": {"value": f"<h1>문서 {i}</h1><p>본문</p>"}},
        }
        for i in range(n)
    ]


class FakeConfluence(BaseHTTPRequestHandler):
    prefix = "/wiki"          # 클래스 변수로 배포 형태를 바꾼다
    serves_context_meta = True   # Server/DC 는 알리고 Cloud(SPA)는 안 알린다
    auth_ok = True
    pages: list = []
    page_size = 2
    #: 검색 요청마다 부르는 훅. 테스트가 시계를 미는 데 쓴다.
    on_search = None
    rate_limit_once = False
    _limited = False
    #: 앞의 N 요청에 500 을 준다. **일시 장애를 흉내내는 장치**로, 클라이언트가
    #: 스스로 다시 묻는지 본다. 타임아웃은 소켓 수준이라 여기서 못 만들고
    #: 세션을 갈아끼워 시험한다(`test_transient_timeout_is_retried`).
    fail_next = 0
    referenced_pages: dict = {}   # (space, title) -> item, --follow-refs 낱개 조회용
    partial_wiki_gateway = False  # /wiki/rest/api/space 만 열리고 나머지는 로그인으로 새는 상황 흉내

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)

        if type(self).partial_wiki_gateway:
            # 실제로 겪은 상황 재현: 게이트웨이가 /wiki/rest/api/space만
            # 허용하고, 그 아래 다른 엔드포인트는 로그인 페이지로 리다이렉트한다.
            # requests가 리다이렉트를 자동으로 따라가면 최종 상태코드는 200인데
            # 본문은 JSON이 아닌 HTML이 된다 — 그 상태를 그대로 흉내낸다.
            if path == "/wiki/rest/api/space":
                self._json({"results": [{"key": "PLATFORM", "name": "플랫폼"}]}); return
            if path.startswith("/wiki/rest/api"):
                body = b"<html><body>login page</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/rest/api/space":
                self._json({"results": [{"key": "PLATFORM", "name": "플랫폼"}]}); return
            if path == "/rest/api/user/current":
                self._json({"email": "svc@corp", "displayName": "Service"}); return

        # **자기서술.** 실물 Server/DC 가 렌더한 HTML 에 마운트 위치를 넣어 준다
        # (실측: Apache `/confluence` · Acme `""`). Cloud 는 SPA 라 없으므로
        # `serves_context_meta = False` 로 그 판도 흉내낼 수 있다.
        if path == "/" and type(self).serves_context_meta:
            body = ('<html><head><meta name="ajs-context-path" content="'
                    + type(self).prefix + '"></head><body>ok</body></html>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return

        if not path.startswith(type(self).prefix + "/rest/api"):
            self.send_response(404); self.end_headers(); return
        api = path[len(type(self).prefix) + len("/rest/api"):]

        if not type(self).auth_ok:
            self._json({"message": "denied"}, 401); return

        if api == "/space":
            self._json({"results": [{"key": "PLATFORM", "name": "플랫폼"}]}); return

        if api == "/user/current":
            self._json({"email": "svc@corp", "displayName": "Service"}); return

        if api == "/content/search":
            cql = q.get("cql", [""])[0]
            m = re.search(r'title="([^"]*)"', cql)
            if m:
                # --follow-refs 낱개 조회 흉내: space+title 로 딱 하나 찾는다
                title = m.group(1)
                sm = re.search(r'space="([^"]*)"', cql)
                space = sm.group(1) if sm else None
                hit = type(self).referenced_pages.get((space, title))
                self._json({"results": [hit] if hit else []})
                return

            if type(self).fail_next > 0:
                type(self).fail_next -= 1
                self.send_response(500); self.end_headers(); return
            if type(self).rate_limit_once and not type(self)._limited:
                type(self)._limited = True
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers(); return
            # **요청마다 시계를 민다.** 싱크가 도는 동안 시간이 흐르는 것을 흉내내야
            # 커서를 언제 잡는지가 결과로 드러난다.
            if type(self).on_search:
                type(self).on_search()
            start = int(q.get("start", ["0"])[0])
            size = type(self).page_size
            # **`lastModified >` 를 실제로 거른다.** 안 거르면 증분 싱크의 정확성을
            # 재현할 수 없다 — 커서를 어떻게 잡든 전부 다시 받으므로 늘 통과한다.
            pool = type(self).pages
            # **스페이스도 실제로 거른다.** 안 거르면 어느 스페이스를 물어도 같은
            # 목록이 와서, "스페이스를 더했는데 안 받아진다" 는 결함이 **원리적으로
            # 재현되지 않는다**(실측 2026-08-23: 전역 커서 때문에 ONAP `DW`
            # 15,001건이 통째로 누락됐는데 테스트는 전부 초록이었다).
            sp = re.search(r'space\s*=\s*"([^"]*)"', cql)
            if sp:
                pool = [p for p in pool
                        if (p.get("space") or {}).get("key") == sp.group(1)]
            lm = re.search(r'lastModified > "([^"]*)"', cql)
            if lm:
                cutoff = lm.group(1).replace(" ", "T")
                pool = [p for p in pool
                        if (p.get("version") or {}).get("when", "")[:len(cutoff)] > cutoff]
            chunk = pool[start:start + size]
            body = {"results": chunk}
            if start + size < len(pool):
                # **실물 두 판을 그대로 흉내낸다**(실측 2026-08-22):
                #
                #   Server/DC  next=/rest/api/…  base=https://host       context=""
                #   Cloud      next=/rest/api/…  base=https://host/wiki  context="/wiki"
                #
                # 즉 `next` 는 **접두사를 뺀** 상대경로이고 `base` 가 접두사까지
                # 포함한 절대주소다. 예전 픽스처는 `next` 에 접두사를 넣어 줬는데,
                # 그건 접두사가 "" 인 Server/DC 에서만 우연히 맞는 모양이라
                # **코드의 오해를 그대로 복제해 페이징 버그를 못 잡았다.**
                host = self.headers.get("Host") or "127.0.0.1"
                body["_links"] = {
                    "base": f"http://{host}{type(self).prefix}",
                    "context": type(self).prefix,
                    # **`next` 는 질의를 그대로 물고 간다** — 실물이 그렇다
                    # (`…&start=100&cql=type%3Dpage+and+space%3D%22…%22`).
                    # 빼면 2쪽부터 조건이 사라져 **다른 스페이스가 섞여 들어온다.**
                    # 클라이언트는 next 에 params 를 안 붙이므로(붙이면 중복된다)
                    # 여기서 물려주지 않으면 재현이 어긋난다.
                    "next": (f"/rest/api/content/search?start={start+size}"
                             f"&cql={urllib.parse.quote(cql)}"),
                }
            self._json(body); return

        if "/restriction/byOperation/read" in path:
            # `wikilens acl` 이 쓰는 **낱개** 조회. 페이지네이션을 안 거치므로
            # 429 백오프가 `_paged` 안에만 있으면 이 경로는 보호를 못 받는다.
            if type(self).rate_limit_once and not type(self)._limited:
                type(self)._limited = True
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers(); return
            self._json({"restrictions": {"user": {"results": []},
                                         "group": {"results": []}}})
            return

        self.send_response(404); self.end_headers()


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeConfluence)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeConfluence.prefix = "/wiki"
    FakeConfluence.serves_context_meta = True
    FakeConfluence.fail_next = 0
    FakeConfluence.auth_ok = True
    FakeConfluence.pages = make_pages(5)
    FakeConfluence.page_size = 2
    FakeConfluence.rate_limit_once = False
    FakeConfluence._limited = False
    FakeConfluence.referenced_pages = {}
    FakeConfluence.partial_wiki_gateway = False
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


# ------------------------------------------------------------ 접두사

def test_detects_prefix_the_guess_list_cannot_reach(server):
    """
    **목록으로는 원리적으로 못 맞히는 마운트.** 자체 호스팅은 리버스 프록시가
    아무 데나 걸 수 있어 `("/wiki", "")` 를 늘리는 것으로는 못 따라간다.
    서버가 `ajs-context-path` 로 스스로 알리므로 그것을 읽는다.

    실측(2026-08-22): Apache 는 `/confluence` 라 예전 코드로는 연결 자체가 안 됐다.
    """
    FakeConfluence.prefix = "/confluence"
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    assert c.detect_prefix() == "/confluence"


def test_cloud_without_self_description_still_detected(server):
    """
    **자기서술이 없어도 예전과 같아야 한다.** Cloud 는 SPA 라 메타가 없다
    (실측: ONAP). 발견 경로가 죽으면 판별 전체가 죽는 것이 아니라 폴백 목록으로
    돌아갈 뿐이어야 한다.
    """
    FakeConfluence.prefix = "/wiki"
    FakeConfluence.serves_context_meta = False
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    assert c.detect_prefix() == "/wiki"


def test_self_description_is_not_trusted_blindly(server):
    """
    **자기서술도 검증을 거친다.** 리버스 프록시가 HTML 을 다시 쓰는 구성이
    실재하므로(`detect_prefix` 의 사고 기록), 알린 값이 틀리면 폴백으로 넘어가야
    한다. 서버가 `/nonsense` 라고 알리지만 실제 API 는 `/wiki` 에 있다.
    """
    FakeConfluence.prefix = "/wiki"
    orig = FakeConfluence.do_GET

    def lying(self):
        if self.path.split("?")[0] == "/":
            body = (b'<html><head><meta name="ajs-context-path" '
                    b'content="/nonsense"></head></html>')
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        return orig(self)

    FakeConfluence.do_GET = lying
    try:
        c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
        assert c.detect_prefix() == "/wiki", "거짓 자기서술을 그대로 믿었다"
    finally:
        FakeConfluence.do_GET = orig


def test_detects_cloud_prefix(server):
    """Cloud 는 /wiki 접두사를 쓴다."""
    FakeConfluence.prefix = "/wiki"
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    assert c.detect_prefix() == "/wiki"
    assert c.doctor().deployment == "Cloud"


def test_detects_server_dc_no_prefix(server):
    """
    Server/DC 는 대개 접두사가 없다.
    고정했다면 여기서 전부 404가 났을 것 — 실제 배포에서 가장 흔한 실패다.
    """
    FakeConfluence.prefix = ""
    c = ConfluenceClient(server, BearerAuth("pat", "Server/DC PAT"))
    assert c.detect_prefix() == ""
    assert c.doctor().deployment == "Server/DC"


def test_unreachable_api_gives_actionable_error(server):
    """
    **자기서술까지 없어야 진짜 못 닿는 것이다.** 예전에는 접두사를 `/nonsense` 로
    두는 것만으로 "못 닿음" 이 됐는데, 서버가 마운트 위치를 알리게 된 뒤로는
    그것만으로는 부족하다 — 알려 주면 실제로 닿고, 그때 성공하는 것이 **맞는
    동작**이다. 그래서 이 테스트는 알리지 않는 서버를 쓴다.

    안내에 `CONFLUENCE_PREFIX` 가 들어가는 것까지 본다. URL 은 맞고 접두사만 틀린
    상태에서 사용자가 URL 을 의심하게 되면 시간을 통째로 버린다.
    """
    FakeConfluence.prefix = "/nonsense"
    FakeConfluence.serves_context_meta = False
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    with pytest.raises(ConfluenceError) as e:
        c.detect_prefix()
    assert "REST API 를 찾을 수 없습니다" in str(e.value)
    assert "CONFLUENCE_PREFIX" in str(e.value)


def test_prefix_detection_rejects_partial_gateway_allowlist(server):
    """
    실제로 겪은 사고: 게이트웨이가 /wiki/rest/api/space만 허용하고
    나머지 /wiki/rest/api/* 는 로그인 페이지로 리다이렉트한다. 리다이렉트가
    자동으로 따라가져서 최종 상태코드가 200이 되므로, /space 하나만 보면
    "/wiki가 맞다"고 착각한다. 다른 엔드포인트로 한 번 더 검증해야
    빈 접두사(진짜 동작하는 쪽)로 넘어간다.
    """
    FakeConfluence.partial_wiki_gateway = True
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    assert c.detect_prefix() == ""


def test_prefix_override_skips_autodetect(server):
    """
    자동 판별이 또 다른 방식으로 속을 수 있다 — 사용자가 접두사를 직접
    강제 지정하면 detect_prefix()가 그 값을 그대로 쓰고 판별 자체를
    건너뛰어야 한다(빈 문자열 강제 포함).
    """
    FakeConfluence.partial_wiki_gateway = True  # 자동판별이면 걸렸을 상황
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"), prefix="")
    assert c.detect_prefix() == ""

    c2 = ConfluenceClient(server, BasicAuth("me@corp", "tok"), prefix="/wiki")
    assert c2.detect_prefix() == "/wiki"  # 강제 지정이면 검증 없이 그대로 신뢰


def test_client_from_env_reads_prefix_override(monkeypatch):
    """CONFLUENCE_PREFIX 미설정이면 자동판별(None), 설정하면(빈 문자열 포함) 그대로 강제."""
    monkeypatch.setenv("CONFLUENCE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "pat123")

    monkeypatch.delenv("CONFLUENCE_PREFIX", raising=False)
    assert client_from_env().prefix is None

    monkeypatch.setenv("CONFLUENCE_PREFIX", "")
    assert client_from_env().prefix == ""

    monkeypatch.setenv("CONFLUENCE_PREFIX", "/wiki")
    assert client_from_env().prefix == "/wiki"


# ------------------------------------------------------------ 인증

def test_auth_failure_names_the_mode(server):
    """401 스택트레이스 대신 어떤 인증 방식이었는지 알려준다."""
    FakeConfluence.auth_ok = False
    d = ConfluenceClient(server, BasicAuth("me@corp", "tok")).doctor()
    assert not d.ok
    assert any("Cloud API 토큰" in e for e in d.errors)

    d2 = ConfluenceClient(server, BearerAuth("pat", "Server/DC PAT")).doctor()
    assert any("Server/DC PAT" in e for e in d2.errors)


def test_doctor_reports_spaces_and_storage(server):
    d = ConfluenceClient(server, BasicAuth("me@corp", "tok")).doctor()
    assert d.ok
    assert d.authenticated and d.account == "svc@corp"
    assert ("PLATFORM", "플랫폼") in d.spaces
    assert d.storage_expandable


# ------------------------------------------------------------ 싱크

def test_added_space_is_fetched_on_a_synced_vault(server, tmp_path):
    """
    **이미 싱크한 볼트에 스페이스를 더하면 그 백로그를 받아야 한다.**

    커서가 볼트 전체에 하나였을 때는 안 받았다 — 새 스페이스의 문서는 전부 그
    시각보다 과거라 `lastModified > 커서` 에 하나도 안 걸린다. 에러도 안 나고
    `받음 0 · 실패 0` 으로 끝나 정상처럼 보인다(실측 2026-08-23: ONAP `DW`
    15,001건이 통째로 안 왔다).
    """
    FakeConfluence.pages = make_pages(3, "PLATFORM") + make_pages(2, "OTHER")
    for p in FakeConfluence.pages[3:]:
        p["id"] = str(int(p["id"]) + 900)      # 스페이스 간 ID 충돌 방지

    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    first = sync(tmp_path, c, ["PLATFORM"])
    assert first.fetched == 3

    # 여기서 **커서가 잡힌다.** OTHER 의 문서는 그보다 과거다.
    second = sync(tmp_path, c, ["PLATFORM", "OTHER"])
    assert second.fetched == 2, "더한 스페이스를 안 받았다 (커서가 전역이면 0이다)"

    state = json.loads((tmp_path / "mirror" / ".sync-state.json").read_text())
    spaces = {p["space"] for p in state["pages"].values()}
    assert spaces == {"PLATFORM", "OTHER"}


def test_cursor_of_an_unlisted_space_is_not_advanced(server, tmp_path, monkeypatch):
    """
    **이번에 안 훑은 스페이스의 커서는 안 옮긴다.** 전역 하나였을 때는 함께
    밀려서, 그 스페이스를 다시 목록에 넣으면 그 사이의 변경을 통째로 건너뛰었다.

    **시계를 명시적으로 민다.** 실제 커서는 분 단위라 두 싱크가 같은 분에 끝나면
    값이 같아져, 고치든 안 고치든 통과하는 **무의미한 단언**이 된다.
    """
    import wikilens.sync as m
    clock = {"t": 0}

    def tick():
        clock["t"] += 1
        return f"2026-08-01 10:{clock['t']:02d}"

    monkeypatch.setattr(m, "_now_cursor", tick)

    FakeConfluence.pages = make_pages(2, "PLATFORM") + make_pages(2, "OTHER")
    for p in FakeConfluence.pages[2:]:
        p["id"] = str(int(p["id"]) + 900)

    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    sync(tmp_path, c, ["PLATFORM", "OTHER"])
    first = json.loads((tmp_path / "mirror" / ".sync-state.json").read_text())["cursors"]

    sync(tmp_path, c, ["PLATFORM"])            # OTHER 를 뺀 채 한 번 더
    cur = json.loads((tmp_path / "mirror" / ".sync-state.json").read_text())["cursors"]

    assert cur["OTHER"] == first["OTHER"], "안 훑은 스페이스의 커서가 함께 밀렸다"
    assert cur["PLATFORM"] > first["PLATFORM"], "훑은 스페이스의 커서가 안 밀렸다"


def test_sync_follows_pagination(server, tmp_path):
    """페이지 크기 2, 문서 5개 → next 링크를 3번 따라가야 전부 받는다."""
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    rep = sync(tmp_path, c, ["PLATFORM"])
    assert rep.fetched == 5
    assert all(layout.raw_path(tmp_path, p["id"]).exists() for p in FakeConfluence.pages)


def test_transient_5xx_is_retried(server, tmp_path, monkeypatch):
    """
    **서버 쪽 일시 장애로 싱크 전체가 죽지 않는다.** 4xx 와 달리 5xx 는 다시
    물으면 되는 경우다.
    """
    import wikilens.sync as m
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    FakeConfluence.fail_next = 2
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    rep = sync(tmp_path, c, ["PLATFORM"])
    assert rep.fetched == 5 and rep.failed == 0


def test_transient_timeout_is_retried(server, tmp_path, monkeypatch):
    """
    **읽기 타임아웃 하나가 수천 건짜리 싱크를 죽이던 자리.**

    실측(2026-08-23, ONAP 공개 인스턴스): 15,000건을 받는 동안 `Read timed out`
    이 세 번 났고 그때마다 전체가 중단됐다. 이어받기가 있어 유실은 없지만 사람이
    다시 실행해야 하고, cron 에는 그 사람이 없다.
    """
    import wikilens.sync as m
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))

    real_get, calls = c.s.get, {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise requests.Timeout("read timed out")
        return real_get(*a, **kw)

    c.s.get = flaky
    rep = sync(tmp_path, c, ["PLATFORM"])
    assert rep.fetched == 5 and rep.failed == 0
    assert calls["n"] > 2, "재시도 없이 통과했다면 이 테스트는 아무것도 안 잠근다"


def test_persistent_timeout_still_raises(server, tmp_path, monkeypatch):
    """
    **영구 실패를 재시도로 감추지 않는다.** 끝까지 안 되면 원래 예외가 올라가야
    한다 — 조용히 빈 결과로 끝나면 그것이 더 나쁘다.
    """
    import wikilens.sync as m
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    c.s.get = lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout("dead"))
    with pytest.raises(ConfluenceError):
        sync(tmp_path, c, ["PLATFORM"])


def test_4xx_is_not_retried(server, monkeypatch):
    """
    **4xx 는 다시 물어도 같다.** 재시도하면 오류 하나에 다섯 번을 두드린다.
    """
    import wikilens.sync as m
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    calls = {"n": 0}
    real_get = c.s.get

    def counted(*a, **kw):
        calls["n"] += 1
        return real_get(*a, **kw)

    c.s.get = counted
    r = c._get(server + "/wiki/rest/api/nope")
    assert r.status_code == 404
    assert calls["n"] == 1, "4xx 를 재시도했다"


def test_sync_survives_rate_limit(server, tmp_path):
    FakeConfluence.rate_limit_once = True
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    assert sync(tmp_path, c, ["PLATFORM"]).fetched == 5


def test_second_sync_skips_unchanged(server, tmp_path):
    """
    두 번째 싱크는 아무것도 다시 받지 않는다.

    **`unchanged == 5` 를 더는 단언하지 않는다.** 가짜 서버가 `lastModified >` 를
    거르게 되면서(실물이 하는 일이다) 애초에 서버가 안 돌려주기 때문이다 —
    예전 값은 실물 동작이 아니라 **가짜의 느슨함**을 재고 있었다. `_ingest` 의
    unchanged 분기는 아래 테스트가 따로 덮는다.
    """
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    sync(tmp_path, c, ["PLATFORM"])
    rep = sync(tmp_path, c, ["PLATFORM"])
    assert rep.fetched == 0


def test_cursor_is_taken_before_the_scan(server, tmp_path, monkeypatch):
    """
    **싱크가 도는 동안 수정된 페이지를 다음 싱크가 받아야 한다.**

    커서를 스캔이 **끝난 뒤** 잡으면, 그 사이에 수정된 페이지는 이미 지나갔거나
    아직 안 온 상태인데 다음 싱크가 `lastModified > 끝시각` 으로 물으므로 **영영
    유실된다** — 그 페이지가 또 수정될 때까지. 13,933건 볼트면 그 창이 수십 분이다.

    시계를 검색 요청마다 10분씩 민다: 싱크 #1 은 10:00 에 시작해 10:30 쯤 끝난다.
    그 중간(10:15)에 수정된 페이지를 싱크 #2 가 받는지 본다.

      커서를 시작에 잡으면  10:00  →  10:15 > 10:00  →  받는다
      커서를 끝에 잡으면    10:30  →  10:15 > 10:30  →  못 받는다
    """
    import wikilens.sync as m

    clock = {"t": 0}
    monkeypatch.setattr(m, "_now_cursor", lambda: f"2026-08-01 10:{clock['t']:02d}")
    FakeConfluence.on_search = lambda: clock.__setitem__("t", min(clock["t"] + 10, 50))

    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    assert sync(tmp_path, c, ["PLATFORM"]).fetched == 5

    # 싱크 #1 이 도는 **중간**에 수정된 페이지 하나.
    FakeConfluence.pages[0]["version"] = {"number": 2, "when": "2026-08-01T10:15:00.000Z"}

    clock["t"] = 0   # 싱크 #2 도 같은 시각대에서 시작한다
    rep = sync(tmp_path, c, ["PLATFORM"])
    assert rep.fetched == 1, (
        "싱크 중 수정된 페이지를 놓쳤다 — 커서를 스캔이 끝난 뒤 잡고 있다"
    )


def test_redelivered_page_with_same_version_is_unchanged(server, tmp_path):
    """
    커서 이후로 넘어왔는데 버전이 그대로면 다시 안 받는다.

    커서가 분 단위로 절삭되므로 실물에서도 경계의 페이지가 다시 넘어온다 —
    그때 `_ingest` 가 버전을 보고 걸러야 한다. 위 테스트가 서버 필터에 가려
    못 보게 된 분기다.
    """
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    sync(tmp_path, c, ["PLATFORM"])
    # 커서보다 뒤로 밀어 서버 필터를 통과시키되 버전은 그대로 둔다.
    for p in FakeConfluence.pages:
        p["version"] = {**p["version"], "when": "2099-01-01T00:00:00.000Z"}
    rep = sync(tmp_path, c, ["PLATFORM"])
    assert rep.fetched == 0 and rep.unchanged == 5


def test_state_write_is_atomic(server, tmp_path):
    """상태 파일을 쓰다 죽어도 이전 상태가 온전해야 한다."""
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    sync(tmp_path, c, ["PLATFORM"])
    p = layout.sync_state_path(tmp_path)
    assert p.exists()
    assert not p.with_suffix(".tmp").exists(), "임시 파일이 남으면 안 됨"
    state = json.loads(p.read_text(encoding="utf-8"))
    assert state["cursor"] and state["partial"] is None
    assert len(state["pages"]) == 5


def test_checkpoint_enables_resume(server, tmp_path, monkeypatch):
    """
    중단되어도 이어받는지. 체크포인트 간격을 2로 줄이고 3번째에서 강제 중단한 뒤,
    상태 파일에 이미 받은 것이 남아 있는지 본다.
    """
    import wikilens.sync as m
    monkeypatch.setattr(m, "CHECKPOINT_EVERY", 2)

    calls = {"n": 0}
    real_write = Path.write_text

    def boom(self, *a, **kw):
        if self.suffix == ".xhtml":
            calls["n"] += 1
            if calls["n"] == 5:
                raise KeyboardInterrupt("중단")
        return real_write(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", boom)
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    with pytest.raises(KeyboardInterrupt):
        sync(tmp_path, c, ["PLATFORM"])

    monkeypatch.undo()
    state = json.loads(layout.sync_state_path(tmp_path).read_text(encoding="utf-8"))
    assert len(state["pages"]) >= 2, "체크포인트가 없으면 0건이 남는다"
    assert state["partial"] is not None, "중단 지점이 기록되어야 함"


# ------------------------------------------------------------ 참조 확장

def test_follow_refs_fetches_out_of_scope_link_target(server, tmp_path):
    """
    --follow-refs 없이는 지정 스페이스 밖 링크(명시적 space-key)가 미해결로
    남고, 켜면 그 페이지 하나만 낱개로 받아온다 — 스페이스 전체가 아니라.
    """
    FakeConfluence.pages = [
        {
            "id": "600000001",
            "title": "소스 문서",
            "space": {"key": "PLATFORM"},
            "version": {"number": 1, "when": "2026-07-30T00:00:00.000Z"},
            "body": {"storage": {"value": (
                '<p>참고: <ac:link><ri:page ri:content-title="Guide" '
                'ri:space-key="OTHER"/></ac:link></p>'
            )}},
        },
    ]
    FakeConfluence.referenced_pages = {
        ("OTHER", "Guide"): {
            "id": "700000001",
            "title": "Guide",
            "space": {"key": "OTHER"},
            "version": {"number": 1, "when": "2026-07-30T00:00:00.000Z"},
            "body": {"storage": {"value": "<p>다른 스페이스 문서</p>"}},
        },
    }

    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    rep = sync(tmp_path, c, ["PLATFORM"], follow_refs=False)
    assert rep.referenced == 0
    assert not layout.raw_path(tmp_path, "700000001").exists()

    rep2 = sync(tmp_path, c, ["PLATFORM"], follow_refs=True)
    assert rep2.referenced == 1
    assert layout.raw_path(tmp_path, "700000001").exists()
    state = json.loads(layout.sync_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["pages"]["700000001"]["space"] == "OTHER"


def test_full_resync_does_not_delete_referenced_pages(server, tmp_path):
    """
    위성 페이지(참조로 받은, 지정 스페이스 밖)는 --full 삭제 판정 대상이
    아니어야 한다. 대상이면 매번 지워졌다 다시 받아지는 낭비가 생긴다.
    """
    FakeConfluence.pages = [
        {
            "id": "600000001",
            "title": "소스 문서",
            "space": {"key": "PLATFORM"},
            "version": {"number": 1, "when": "2026-07-30T00:00:00.000Z"},
            "body": {"storage": {"value": (
                '<ac:link><ri:page ri:content-title="Guide" ri:space-key="OTHER"/></ac:link>'
            )}},
        },
    ]
    FakeConfluence.referenced_pages = {
        ("OTHER", "Guide"): {
            "id": "700000001", "title": "Guide", "space": {"key": "OTHER"},
            "version": {"number": 1, "when": "2026-07-30T00:00:00.000Z"},
            "body": {"storage": {"value": "<p>다른 스페이스 문서</p>"}},
        },
    }
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    sync(tmp_path, c, ["PLATFORM"], follow_refs=True)
    rep = sync(tmp_path, c, ["PLATFORM"], full=True, follow_refs=True)
    assert "700000001" not in rep.removed
    assert layout.raw_path(tmp_path, "700000001").exists()


def test_follow_refs_does_not_drift_across_repeated_calls(server, tmp_path):
    """
    위성 페이지 자신의 링크는 이번 호출은 물론 다음 호출에서도 스캔되면
    안 된다 — 안 그러면 cron 반복 실행마다 한 홉씩 조용히 번진다.
    OTHER/Guide(위성)가 THIRD/Deep을 명시적으로 링크해도, THIRD/Deep은
    실제로 존재하는(fake 서버에 등록된) 페이지인데도 받아지면 안 된다.
    """
    FakeConfluence.pages = [
        {
            "id": "600000001",
            "title": "소스 문서",
            "space": {"key": "PLATFORM"},
            "version": {"number": 1, "when": "2026-07-30T00:00:00.000Z"},
            "body": {"storage": {"value": (
                '<ac:link><ri:page ri:content-title="Guide" ri:space-key="OTHER"/></ac:link>'
            )}},
        },
    ]
    FakeConfluence.referenced_pages = {
        ("OTHER", "Guide"): {
            "id": "700000001", "title": "Guide", "space": {"key": "OTHER"},
            "version": {"number": 1, "when": "2026-07-30T00:00:00.000Z"},
            "body": {"storage": {"value": (
                # 위성 페이지 자신도 또 다른 스페이스를 명시적으로 링크한다
                '<ac:link><ri:page ri:content-title="Deep" ri:space-key="THIRD"/></ac:link>'
            )}},
        },
        ("THIRD", "Deep"): {
            "id": "800000001", "title": "Deep", "space": {"key": "THIRD"},
            "version": {"number": 1, "when": "2026-07-30T00:00:00.000Z"},
            "body": {"storage": {"value": "<p>세 번째 스페이스 문서</p>"}},
        },
    }
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))

    rep1 = sync(tmp_path, c, ["PLATFORM"], follow_refs=True)
    assert rep1.referenced == 1
    assert layout.raw_path(tmp_path, "700000001").exists()  # Guide (1홉)

    rep2 = sync(tmp_path, c, ["PLATFORM"], follow_refs=True)
    assert rep2.referenced == 0, "위성 페이지의 링크를 따라가면 안 된다 (2홉)"
    assert not layout.raw_path(tmp_path, "800000001").exists()


# ------------------------------------------------------------ CQL 이스케이프

def test_cql_escapes_embedded_quotes():
    """
    스페이스 키나 제목에 큰따옴표가 들어있으면 이스케이프해야 CQL 문법이
    안 깨진다. 실제 위키 제목에 인용구가 들어가는 경우가 있다.
    """
    cql = _cql_for_title('OTHER', 'Server "Alpha" Config')
    assert cql == 'type=page and space="OTHER" and title="Server \\"Alpha\\" Config"'

    cql2 = _cql_for_space('PLATFORM', since='2026-01-01 00:00')
    assert '"PLATFORM"' in cql2 and '"2026-01-01 00:00"' in cql2


def test_single_request_path_also_backs_off_on_429(server):
    """
    **429 백오프가 `_paged` 안에만 있었다.** 그런데 `wikilens acl` 은 페이지마다 낱개
    조회를 해서 `_get` 을 직접 부르고, 그게 이 프로젝트에서 API 를 가장 세게 쓰는
    경로다(13,921건이면 요청도 13,921개).

    보호가 없으면 429 하나가 곧 "조회 실패" 이고, 전부 실패하면 acl.json 이 비어
    나온다 — 서버는 그것을 **전 페이지가 아무에게도 안 보임**으로 읽는다.
    """
    FakeConfluence.rate_limit_once = True
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    prefix = c.detect_prefix()
    FakeConfluence._limited = False          # detect_prefix 가 이미 썼을 수 있다
    url = f"{server}{prefix}/rest/api/content/123/restriction/byOperation/read"

    r = c._get(url)

    assert r.status_code == 200, "429 에서 물러섰다가 다시 시도하지 않았다"


def test_failed_page_is_retried_by_the_next_sync(server, tmp_path, monkeypatch):
    """
    **한 건이 실패해도 커서는 그냥 넘어갔다.**

    `_ingest` 가 던지면 `report.failed` 만 올리고 지나가는데, 끝에서
    `state["cursor"] = next_cursor` 를 무조건 쓴다. 다음 싱크는
    `lastModified > 그 시각` 으로 물으므로 **그 페이지를 다시 안 받는다** —
    또 수정될 때까지 영영. 새 페이지였다면 미러에 아예 없는 채로 남는다.

    커서를 스캔 **전에** 잡는 것과 같은 계열의 유실인데(그 테스트가 바로 위에 있다),
    이쪽은 창이 아니라 확정이다.

    한 건만 첫 싱크에서 던지게 하고, 둘째 싱크가 그것을 받는지 본다.
    """
    import wikilens.sync as m

    real = m._ingest
    boom = {"on": True}

    def flaky(item, root, state, fallback_space, full):
        if boom["on"] and str(item["id"]) == "500000003":
            raise OSError("디스크 오류")
        return real(item, root, state, fallback_space, full)

    monkeypatch.setattr(m, "_ingest", flaky)

    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    rep = sync(tmp_path, c, ["PLATFORM"])
    assert rep.failed == 1 and rep.fetched == 4

    boom["on"] = False          # 일시적 오류가 사라졌다
    rep2 = sync(tmp_path, c, ["PLATFORM"])
    assert rep2.fetched == 1, "실패한 페이지를 다시 안 받았다 — 커서가 그냥 넘어갔다"
    assert layout.raw_path(tmp_path, "500000003").exists(), "미러에 아예 없다"


def test_full_resync_refuses_to_delete_on_empty_listing(server, tmp_path):
    """
    **`--full` 이 빈 목록을 받으면 볼트를 통째로 지웠다**(실측: 5건 → 0건).

    `search` 는 오류를 던지지만 "HTTP 200 · results=[]" 는 오류가 아니다 — 스페이스
    키가 바뀌었거나 Confluence 가 일시적으로 빈 응답을 주면 그렇게 온다. 지워지는
    것에 `mirror/raw/` 가 포함되므로 **"원본은 안 지우니 재빌드로 돌아온다" 는
    안전망까지 함께 사라지고**, 복구가 수 시간짜리 재싱크가 된다.

    서버가 빈 볼트로 색인을 덮지 않는 것과 같은 판단이다 — 그쪽은 재색인 15초면
    복구되는데 이쪽은 그렇지 않아 더 엄해야 한다.
    """
    FakeConfluence.pages = [
        {"id": f"61000000{i}", "title": f"문서 {i}", "space": {"key": "PLATFORM"},
         "version": {"number": 1, "when": "2026-07-30T00:00:00.000Z"},
         "body": {"storage": {"value": "<p>본문</p>"}}}
        for i in range(1, 4)
    ]
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    sync(tmp_path, c, ["PLATFORM"])
    before = sorted(p.name for p in (tmp_path / "mirror" / "raw").rglob("*.xhtml"))
    assert before, "픽스처가 아무것도 안 받았다"

    FakeConfluence.pages = []                      # 200 · results=[]
    rep = sync(tmp_path, c, ["PLATFORM"], full=True)

    after = sorted(p.name for p in (tmp_path / "mirror" / "raw").rglob("*.xhtml"))
    assert after == before, "빈 목록에 원본이 지워졌다 — 재싱크 말고는 복구가 없다"
    assert rep.removed == []
    # **조용하면 안 된다** — 사용자는 `--full` 이 삭제를 봤다고 믿는다.
    assert rep.delete_skipped == ["PLATFORM"]


def test_full_resync_still_deletes_when_listing_is_healthy(server, tmp_path):
    """가드가 삭제 자체를 막으면 `--full` 이 하는 일이 없어진다."""
    FakeConfluence.pages = [
        {"id": f"62000000{i}", "title": f"문서 {i}", "space": {"key": "PLATFORM"},
         "version": {"number": 1, "when": "2026-07-30T00:00:00.000Z"},
         "body": {"storage": {"value": "<p>본문</p>"}}}
        for i in range(1, 4)
    ]
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    sync(tmp_path, c, ["PLATFORM"])

    FakeConfluence.pages = FakeConfluence.pages[:2]      # 하나가 실제로 사라졌다
    rep = sync(tmp_path, c, ["PLATFORM"], full=True)
    assert rep.removed == ["620000003"], rep.removed
    assert rep.delete_skipped == []
    assert not layout.raw_path(tmp_path, "620000003").exists()
