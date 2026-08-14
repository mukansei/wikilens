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
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

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
    auth_ok = True
    pages: list = []
    page_size = 2
    #: 검색 요청마다 부르는 훅. 테스트가 시계를 미는 데 쓴다.
    on_search = None
    rate_limit_once = False
    _limited = False
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
            lm = re.search(r'lastModified > "([^"]*)"', cql)
            if lm:
                cutoff = lm.group(1).replace(" ", "T")
                pool = [p for p in pool
                        if (p.get("version") or {}).get("when", "")[:len(cutoff)] > cutoff]
            chunk = pool[start:start + size]
            body = {"results": chunk}
            if start + size < len(pool):
                body["_links"] = {
                    "next": f"{type(self).prefix}/rest/api/content/search?start={start+size}"
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
    FakeConfluence.prefix = "/nonsense"
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    with pytest.raises(ConfluenceError) as e:
        c.detect_prefix()
    assert "REST API 를 찾을 수 없습니다" in str(e.value)


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

def test_sync_follows_pagination(server, tmp_path):
    """페이지 크기 2, 문서 5개 → next 링크를 3번 따라가야 전부 받는다."""
    c = ConfluenceClient(server, BasicAuth("me@corp", "tok"))
    rep = sync(tmp_path, c, ["PLATFORM"])
    assert rep.fetched == 5
    assert all(layout.raw_path(tmp_path, p["id"]).exists() for p in FakeConfluence.pages)


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
