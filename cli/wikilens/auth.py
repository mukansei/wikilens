"""
인증 제공자.

Confluence 접근 방식은 조직마다 다르고, SSO 를 쓰면 계정 비밀번호로는 API 에
접근할 수 없다. 다만 **SSO 가 API 인증까지 막는 경우는 생각보다 드물다** —
대부분의 배포는 SSO(브라우저 로그인)와 별개로 토큰 인증을 허용한다.

  1. PAT            Server/DC 7.9+. SSO 와 무관하게 발급·동작. **가장 먼저 시도할 것**
  2. API 토큰        Cloud. id.atlassian.com 에서 발급. SSO 와 무관
  3. OAuth2 CC      IAM 이 토큰을 발급하고 Confluence 가 검증. 서비스 계정에 적합
  4. 외부 토큰       다른 수단으로 받은 Bearer 를 그대로 주입
  5. 헤더 주입       리버스 프록시가 SSO 를 처리하고 헤더를 넣는 구성

이 모듈이 `sync` 밖으로 분리된 이유: 인증은 조직마다 다르지만 수집·파싱·색인은
같기 때문이다. 새 방식이 필요하면 여기에만 추가하면 된다.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

import requests

from . import credentials


class AuthProvider:
    """세션에 인증을 적용한다. 401 을 받으면 refresh 후 한 번 재시도한다."""

    def apply(self, session: requests.Session) -> None:
        raise NotImplementedError

    def refresh(self) -> bool:
        """갱신했으면 True. 갱신 수단이 없으면 False (재시도하지 않음)."""
        return False

    def describe(self) -> str:
        return self.__class__.__name__


@dataclass
class BasicAuth(AuthProvider):
    """Cloud: 이메일 + API 토큰."""

    email: str
    token: str

    def apply(self, session):
        session.auth = (self.email, self.token)

    def describe(self):
        return f"Cloud API 토큰 (이메일 {self.email})"


@dataclass
class BearerAuth(AuthProvider):
    """Server/DC PAT 또는 외부에서 받은 access token."""

    token: str
    label: str = "Bearer 토큰"

    def apply(self, session):
        session.headers["Authorization"] = "Bearer " + self.token

    def describe(self):
        return self.label


@dataclass
class HeaderAuth(AuthProvider):
    """리버스 프록시가 SSO 를 처리하고 신원 헤더를 주입하는 구성."""

    headers: dict = field(default_factory=dict)

    def apply(self, session):
        session.headers.update(self.headers)

    def describe(self):
        return "헤더 주입 (" + ", ".join(sorted(self.headers)) + ")"


@dataclass
class OAuth2ClientCredentials(AuthProvider):
    """
    별도 IAM 서버에서 client_credentials 로 토큰을 받아 Bearer 로 쓴다.

    SSO 환경의 서비스 계정에 흔한 형태다. 토큰이 만료되므로 401 을 받으면
    갱신 후 한 번 재시도한다. 만료 60초 전에 선제 갱신한다.
    """

    token_url: str
    client_id: str
    client_secret: str
    scope: str | None = None
    audience: str | None = None
    timeout: int = 30

    _token: str | None = field(default=None, init=False, repr=False)
    _expires_at: float = field(default=0.0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _fetch(self) -> None:
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.scope:
            data["scope"] = self.scope
        if self.audience:
            data["audience"] = self.audience

        r = requests.post(
            self.token_url, data=data,
            headers={"Accept": "application/json"}, timeout=self.timeout,
        )
        if r.status_code != 200:
            raise RuntimeError(
                "IAM 토큰 발급 실패 (HTTP " + str(r.status_code) + "): " + r.text[:200] + "\n"
                "  · IAM_TOKEN_URL / IAM_CLIENT_ID / IAM_CLIENT_SECRET 확인\n"
                "  · scope 나 audience 가 필요한 IAM 인지 확인"
            )
        j = r.json()
        tok = j.get("access_token")
        if not tok:
            raise RuntimeError("IAM 응답에 access_token 이 없습니다: " + str(j)[:200])
        self._token = tok
        # 만료 60초 전에 갱신한다. expires_in 이 없으면 5분으로 가정.
        self._expires_at = time.time() + float(j.get("expires_in", 300)) - 60

    def _ensure(self) -> str:
        with self._lock:
            if self._token is None or time.time() >= self._expires_at:
                self._fetch()
            return self._token  # type: ignore[return-value]

    def apply(self, session):
        session.headers["Authorization"] = "Bearer " + self._ensure()

    def refresh(self) -> bool:
        with self._lock:
            self._token = None
            self._expires_at = 0.0
        return True

    def describe(self):
        return "OAuth2 client_credentials (" + self.token_url + ")"


# ---------------------------------------------------------------- 선택

def auth_from_env() -> AuthProvider:
    """
    환경변수로 인증 방식을 고른다. `CONFLUENCE_AUTH` 로 명시하거나 자동 판별한다.

      basic   CONFLUENCE_EMAIL + CONFLUENCE_TOKEN
      pat     CONFLUENCE_TOKEN
      oauth   IAM_TOKEN_URL + IAM_CLIENT_ID + IAM_CLIENT_SECRET [+ IAM_SCOPE, IAM_AUDIENCE]
      header  CONFLUENCE_HEADERS='Key: Value; Key2: Value2'
    """
    # 환경변수가 없으면 `~/.wikilens/env.sh` 에서 읽는다 — cron 과 Claude Code 처럼
    # `export` 가 없는 환경을 덮는다. 우선순위·근거는 `credentials` 모듈에.
    mode = (credentials.get("CONFLUENCE_AUTH") or "").strip().lower()
    email = credentials.get("CONFLUENCE_EMAIL")
    token = credentials.get("CONFLUENCE_TOKEN")
    headers_raw = credentials.get("CONFLUENCE_HEADERS")
    iam_url = credentials.get("IAM_TOKEN_URL")

    if not mode:
        if iam_url:
            mode = "oauth"
        elif headers_raw:
            mode = "header"
        elif email and token:
            mode = "basic"
        elif token:
            mode = "pat"

    if mode == "oauth":
        cid = credentials.get("IAM_CLIENT_ID")
        sec = credentials.get("IAM_CLIENT_SECRET")
        if not (iam_url and cid and sec):
            raise SystemExit(
                "OAuth 인증에는 IAM_TOKEN_URL, IAM_CLIENT_ID, IAM_CLIENT_SECRET 이 필요합니다.\n"
                "  선택: IAM_SCOPE, IAM_AUDIENCE"
            )
        return OAuth2ClientCredentials(
            iam_url, cid, sec,
            scope=credentials.get("IAM_SCOPE"),
            audience=credentials.get("IAM_AUDIENCE"),
        )

    if mode == "header":
        headers = {}
        for part in (headers_raw or "").split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                headers[k.strip()] = v.strip()
        if not headers:
            raise SystemExit("CONFLUENCE_HEADERS 형식: 'Key: Value; Key2: Value2'")
        return HeaderAuth(headers)

    if mode == "basic":
        if not (email and token):
            raise SystemExit("basic 인증에는 CONFLUENCE_EMAIL 과 CONFLUENCE_TOKEN 이 필요합니다.")
        return BasicAuth(email, token)

    if mode == "pat":
        if not token:
            raise SystemExit("pat 인증에는 CONFLUENCE_TOKEN 이 필요합니다.")
        return BearerAuth(token, "Server/DC PAT")

    raise SystemExit(
        "인증 방식을 판별할 수 없습니다. 다음 중 하나를 설정하세요.\n\n"
        "  Cloud API 토큰:\n"
        "    CONFLUENCE_EMAIL=me@corp  CONFLUENCE_TOKEN=<API 토큰>\n\n"
        "  Server/DC PAT  (SSO 를 써도 대개 이것이 동작합니다 — 먼저 시도하세요):\n"
        "    CONFLUENCE_TOKEN=<PAT>\n\n"
        "  자체 IAM (OAuth2 client_credentials):\n"
        "    IAM_TOKEN_URL=https://iam.corp/oauth2/token\n"
        "    IAM_CLIENT_ID=...  IAM_CLIENT_SECRET=...  [IAM_SCOPE=...]\n\n"
        "  리버스 프록시 헤더 주입:\n"
        "    CONFLUENCE_HEADERS='X-Forwarded-User: me@corp'\n"
    )
