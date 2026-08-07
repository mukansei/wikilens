"""
인증 계층 검증.

사내 SSO/IAM 환경을 흉내낸다. 가짜 IAM 이 만료되는 토큰을 발급하고,
가짜 Confluence 가 그 토큰을 검증한다. 토큰 만료·갱신·401 재시도까지 확인한다.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from wikilens.auth import (
    BasicAuth, BearerAuth, HeaderAuth, OAuth2ClientCredentials, auth_from_env,
)
from wikilens.sync import ConfluenceClient
from wikilens import credentials
from wikilens import sync as sync_mod


class FakeIAM(BaseHTTPRequestHandler):
    issued: list = []
    ttl = 3600
    fail = False
    require_scope: str | None = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(n).decode())
        if type(self).fail:
            self._json({"error": "invalid_client"}, 401); return
        if type(self).require_scope and form.get("scope", [""])[0] != type(self).require_scope:
            self._json({"error": "invalid_scope"}, 400); return
        if form.get("grant_type", [""])[0] != "client_credentials":
            self._json({"error": "unsupported_grant_type"}, 400); return

        tok = f"tok-{len(type(self).issued)}"
        type(self).issued.append(tok)
        self._json({"access_token": tok, "token_type": "Bearer",
                    "expires_in": type(self).ttl})

    def _json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class FakeConfluenceSSO(BaseHTTPRequestHandler):
    """마지막으로 발급된 토큰만 유효하다고 본다 — 만료를 흉내낸다."""

    valid_token = None
    seen_auth: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        type(self).seen_auth.append(auth)
        expected = "Bearer " + (type(self).valid_token or "")
        if auth != expected:
            self.send_response(401); self.end_headers(); return

        path = urlparse(self.path).path
        if path.endswith("/rest/api/space"):
            self._json({"results": [{"key": "PLATFORM", "name": "플랫폼"}]})
        elif path.endswith("/rest/api/user/current"):
            self._json({"email": "svc@corp"})
        elif path.endswith("/rest/api/content/search"):
            self._json({"results": [{"id": "1", "title": "T",
                                     "space": {"key": "PLATFORM"},
                                     "version": {"number": 1, "when": ""},
                                     "body": {"storage": {"value": "<p>x</p>"}}}]})
        else:
            self.send_response(404); self.end_headers()

    def _json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


@pytest.fixture
def iam():
    FakeIAM.issued = []
    FakeIAM.ttl = 3600
    FakeIAM.fail = False
    FakeIAM.require_scope = None
    srv, url = _serve(FakeIAM)
    yield url + "/oauth2/token"
    srv.shutdown()


@pytest.fixture
def conf():
    FakeConfluenceSSO.valid_token = None
    FakeConfluenceSSO.seen_auth = []
    srv, url = _serve(FakeConfluenceSSO)
    yield url
    srv.shutdown()


# ------------------------------------------------------------ 제공자 선택

@pytest.fixture(autouse=True)
def _isolate_env_file(tmp_path, monkeypatch):
    """
    `auth_from_env` 는 환경변수가 없으면 `~/.wikilens/env.sh` 를 읽는다 — cron 처럼
    `export` 가 없는 환경을 덮기 위해서다. 그래서 환경변수만 지우는 테스트는
    **개발 머신의 실제 자격증명에 노출된다.** 실제로 그것 때문에 한 테스트가
    "SystemExit 이 안 났다"로 깨졌고, 나머지는 우연히 통과하고 있었다.
    """
    monkeypatch.setattr(credentials, "ENV_PATH", tmp_path / "없는env.sh")


def test_env_selects_oauth_when_iam_configured(monkeypatch):
    monkeypatch.setenv("IAM_TOKEN_URL", "https://iam.corp/token")
    monkeypatch.setenv("IAM_CLIENT_ID", "cid")
    monkeypatch.setenv("IAM_CLIENT_SECRET", "sec")
    monkeypatch.delenv("CONFLUENCE_AUTH", raising=False)
    a = auth_from_env()
    assert isinstance(a, OAuth2ClientCredentials)


def test_env_selects_pat_when_only_token(monkeypatch):
    for k in ("IAM_TOKEN_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_HEADERS", "CONFLUENCE_AUTH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CONFLUENCE_TOKEN", "pat123")
    a = auth_from_env()
    assert isinstance(a, BearerAuth) and "PAT" in a.describe()


def test_env_selects_header_injection(monkeypatch):
    for k in ("IAM_TOKEN_URL", "CONFLUENCE_AUTH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CONFLUENCE_HEADERS", "X-Forwarded-User: me@corp; X-Auth: abc")
    a = auth_from_env()
    assert isinstance(a, HeaderAuth)
    assert a.headers == {"X-Forwarded-User": "me@corp", "X-Auth": "abc"}


def test_env_without_anything_explains_all_options(monkeypatch):
    for k in ("IAM_TOKEN_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_TOKEN",
              "CONFLUENCE_HEADERS", "CONFLUENCE_AUTH"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit) as e:
        auth_from_env()
    msg = str(e.value)
    # SSO 환경에서 무엇을 먼저 시도해야 하는지 알려줘야 한다
    assert "PAT" in msg and "IAM_TOKEN_URL" in msg


# ------------------------------------------------------------ OAuth 흐름

def test_oauth_fetches_token_and_authenticates(iam, conf):
    auth = OAuth2ClientCredentials(iam, "cid", "sec")
    c = ConfluenceClient(conf, auth)
    # 토큰을 받아온 뒤 그것이 유효하다고 설정
    auth.apply(c.s)
    FakeConfluenceSSO.valid_token = FakeIAM.issued[-1]

    d = c.doctor()
    assert d.ok, d.errors
    assert d.authenticated and d.account == "svc@corp"
    assert "OAuth2 client_credentials" in d.auth_mode
    assert len(FakeIAM.issued) == 1, "불필요하게 재발급하면 안 됨"


def test_expired_token_is_refreshed_before_use(iam, conf):
    """만료 60초 전에 선제 갱신한다."""
    FakeIAM.ttl = 30          # 60초 마진보다 짧으므로 매번 만료로 간주됨
    auth = OAuth2ClientCredentials(iam, "cid", "sec")
    s1 = ConfluenceClient(conf, auth).s
    first = FakeIAM.issued[-1]
    auth.apply(s1)
    assert len(FakeIAM.issued) == 2, "만료 임박 토큰은 재발급되어야 함"
    assert FakeIAM.issued[-1] != first


def test_401_triggers_refresh_and_single_retry(iam, conf):
    """
    서버가 토큰을 무효화한 경우(회전 등). 갱신 후 한 번 재시도해야 한다.
    재시도가 없으면 토큰이 돌 때마다 싱크가 통째로 실패한다.
    """
    auth = OAuth2ClientCredentials(iam, "cid", "sec")
    c = ConfluenceClient(conf, auth)
    before = len(FakeIAM.issued)
    FakeConfluenceSSO.valid_token = "서버가아는다른토큰"   # 현재 토큰 무효화

    r = c._get(conf + "/rest/api/space")

    assert r.status_code == 401, "재시도해도 여전히 무효이므로 401"
    assert len(FakeIAM.issued) == before + 1, "401 이면 정확히 한 번 갱신해야 함"


def test_iam_failure_is_actionable(iam):
    """자격증명이 틀리면 무엇을 확인해야 하는지 알려줘야 한다."""
    import requests
    FakeIAM.fail = True
    with pytest.raises(RuntimeError) as e:
        OAuth2ClientCredentials(iam, "bad", "bad").apply(requests.Session())
    msg = str(e.value)
    assert "IAM 토큰 발급 실패" in msg
    assert "IAM_TOKEN_URL" in msg


def test_scope_is_sent_when_configured(iam):
    """IAM 이 scope 를 요구하는 경우. 빠뜨리면 발급이 거부된다."""
    import requests
    FakeIAM.require_scope = "confluence:read"

    ok = OAuth2ClientCredentials(iam, "cid", "sec", scope="confluence:read")
    ok.apply(requests.Session())
    assert len(FakeIAM.issued) == 1

    missing_scope = OAuth2ClientCredentials(iam, "cid", "sec")
    with pytest.raises(RuntimeError):
        missing_scope.apply(requests.Session())


# ------------------------------------------------------------ 기타 제공자

def test_header_auth_applies_headers(conf):
    import requests
    s = requests.Session()
    HeaderAuth({"X-Forwarded-User": "me@corp"}).apply(s)
    assert s.headers["X-Forwarded-User"] == "me@corp"


def test_basic_auth_describes_account():
    assert "me@corp" in BasicAuth("me@corp", "t").describe()


def test_non_refreshable_providers_do_not_retry():
    """PAT·Basic 은 갱신 수단이 없으므로 401 에서 재시도하지 않아야 한다."""
    assert BearerAuth("t").refresh() is False
    assert BasicAuth("a", "b").refresh() is False
    assert HeaderAuth({"a": "b"}).refresh() is False


# --------------------------------------------------- env.sh 폴백 (2026-08-07)
#
# CLI 가 환경변수만 읽던 동안 `export` 없는 환경은 전부 죽었다 — Claude Code 안의
# 로컬판 sync(래퍼로 막았음)와 **서버판 cron**(안 막혀 있었음)이 같은 실패였다.
# 실측: `env -i HOME=... wikilens doctor` → "CONFLUENCE_URL 환경변수가 필요합니다".

def _write_env(tmp_path, body: str):
    p = tmp_path / "env.sh"
    p.write_text(body, encoding="utf-8")
    return p


def test_credentials_read_from_env_file(tmp_path, monkeypatch):
    for k in ("CONFLUENCE_URL", "CONFLUENCE_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    p = _write_env(tmp_path, "export CONFLUENCE_URL=https://w.example\n"
                             "export CONFLUENCE_TOKEN=pat-123\n")
    monkeypatch.setattr(credentials, "ENV_PATH", p)

    assert credentials.get("CONFLUENCE_URL") == "https://w.example"
    assert credentials.get("CONFLUENCE_TOKEN") == "pat-123"


def test_environment_beats_the_file(tmp_path, monkeypatch):
    """일회성 재정의(토큰 교체 등)를 파일이 덮으면 낡은 자격증명으로 조용히 인증한다."""
    p = _write_env(tmp_path, "export CONFLUENCE_TOKEN=old\n")
    monkeypatch.setattr(credentials, "ENV_PATH", p)
    monkeypatch.setenv("CONFLUENCE_TOKEN", "new")

    assert credentials.get("CONFLUENCE_TOKEN") == "new"


def test_empty_value_is_not_missing(tmp_path, monkeypatch):
    """`CONFLUENCE_PREFIX=""` 는 "Server/DC 라 접두사 없음"이지 미설정이 아니다."""
    monkeypatch.delenv("CONFLUENCE_PREFIX", raising=False)
    p = _write_env(tmp_path, 'export CONFLUENCE_PREFIX=""\n')
    monkeypatch.setattr(credentials, "ENV_PATH", p)

    assert credentials.get("CONFLUENCE_PREFIX") == ""


def test_unfilled_template_counts_as_missing(tmp_path, monkeypatch):
    """
    setup 이 출력하는 템플릿을 실행만 하고 편집을 잊으면 토큰 자리가
    `<발급받은 PAT를 여기에>` 다. 그걸 값으로 넘기면 인증이 엉뚱한 에러로 죽는다.
    """
    monkeypatch.delenv("CONFLUENCE_TOKEN", raising=False)
    p = _write_env(tmp_path, "export CONFLUENCE_TOKEN=<발급받은 PAT를 여기에>\n")
    monkeypatch.setattr(credentials, "ENV_PATH", p)

    assert credentials.get("CONFLUENCE_TOKEN") is None


def test_only_known_prefixes_are_read(tmp_path, monkeypatch):
    """env.sh 에 다른 것이 있어도 자격증명 해석에 섞이지 않는다."""
    p = _write_env(tmp_path, "export PATH=/evil\nexport CONFLUENCE_TOKEN=t\n")
    monkeypatch.setattr(credentials, "ENV_PATH", p)

    assert set(credentials.from_file(p)) == {"CONFLUENCE_TOKEN"}


def test_client_from_env_names_the_file_when_nothing_is_set(tmp_path, monkeypatch):
    for k in ("CONFLUENCE_URL", "IAM_TOKEN_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(credentials, "ENV_PATH", tmp_path / "없는env.sh")

    with pytest.raises(SystemExit) as e:
        sync_mod.client_from_env()
    # 어디를 봤는지 말해야 사용자가 고칠 수 있다.
    assert "env.sh" in str(e.value)
