"""
Confluence 클라이언트 + 증분 싱크.

인증은 사용자 개인 토큰(로컬판) 또는 서비스 계정(서버판)을 쓴다.

실전에서 걸리는 지점들을 명시적으로 다룬다:
  - Cloud 는 `/wiki` 접두사, Server/DC 는 대개 없음 → 자동 판별
  - 인증 실패가 스택트레이스로만 나오면 원인 파악 불가 → 진단 메시지
  - 대형 스페이스 첫 싱크 중 중단 시 처음부터 → 배치 체크포인트
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import layout
from .auth import AuthProvider, auth_from_env

from . import credentials

CHECKPOINT_EVERY = 100      # 이 건수마다 상태 저장 → 중단되어도 이어받는다
MAX_RETRY_WAIT = 120        # 429 백오프 상한. 무한 대기 방지
FIRST_RETRY_WAIT = 5        # 서버가 Retry-After 를 안 주면 여기서 시작해 배로 늘린다
MAX_RETRIES = 5             # 429·타임아웃·5xx 를 이만큼 해보고 호출부에 넘긴다


class ConfluenceError(RuntimeError):
    """원인을 사람이 읽을 수 있게 만든 예외."""


@dataclass
class Diagnosis:
    base_url: str
    auth_mode: str = ""
    prefix: str | None = None
    authenticated: bool = False
    account: str | None = None
    deployment: str | None = None
    spaces: list = field(default_factory=list)
    storage_expandable: bool = False
    errors: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.authenticated and self.prefix is not None and not self.errors


@dataclass
class SyncReport:
    fetched: int = 0
    unchanged: int = 0
    failed: int = 0
    removed: list = field(default_factory=list)
    #: 빈 목록이 와서 삭제 판정을 건너뛴 스페이스. **조용하면 안 된다** — 사용자는
    #: `--full` 이 삭제를 봤다고 믿는데 실제로는 아무것도 안 본 상태다.
    delete_skipped: list = field(default_factory=list)
    spaces: list = field(default_factory=list)
    elapsed_s: float = 0.0
    resumed_from: str | None = None
    referenced: int = 0  # --follow-refs 로 지정 스페이스 밖에서 낱개로 받은 건수


def _auth_hint(status: int, mode: str) -> str:
    if status == 401:
        return ("인증 실패 (401). 현재 방식: " + mode + "\n"
                "  · SSO 환경이라면 계정 비밀번호가 아니라 토큰이 필요합니다\n"
                "  · Server/DC 는 PAT 가 SSO 와 무관하게 동작합니다 — 먼저 시도해 보세요\n"
                "  · 자체 IAM 을 쓴다면 IAM_TOKEN_URL 등으로 OAuth 방식을 지정하세요")
    return ("권한 없음 (403). 인증은 됐지만 접근이 거부됐습니다. 현재 방식: " + mode + "\n"
            "  · 해당 스페이스 열람 권한을 확인하세요\n"
            "  · 서비스 계정이라면 스페이스 권한이 별도로 부여되어야 합니다")


class ConfluenceClient:
    def __init__(
        self, base_url: str, auth: AuthProvider, timeout: int = 30,
        prefix: str | None = None,
    ):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        # "/wiki" 또는 "" 로 확정되면 캐시된다. None이면 detect_prefix()가
        # 자동 판별한다. 여기서 미리 넘기면(빈 문자열 포함) 그 값을 그대로
        # 쓰고 자동 판별을 건너뛴다 — 게이트웨이가 자동 판별을 속이는
        # 구성(예: /space만 허용하고 나머지는 로그인으로 새는 경우)을
        # 코드가 전부 예측할 순 없어서 마련한 탈출구다.
        self.prefix = prefix
        self.auth = auth
        self.s = requests.Session()
        self.s.headers["Accept"] = "application/json"
        self.auth.apply(self.s)

    @property
    def auth_mode(self) -> str:
        return self.auth.describe()

    def _get(self, url: str, **kw):
        """
        모든 GET 이 지나는 자리. 401 갱신과 **429 백오프를 여기서** 한다.

        429 가 예전에는 `_paged` 안에만 있었다. 그런데 `acl` 은 페이지마다 낱개
        조회를 하느라 `_get` 을 직접 부르고, 그게 이 프로젝트에서 API 를 가장 세게
        쓰는 경로다(13,921건이면 요청도 13,921개). 실측: 429 를 주면 재시도 없이
        전부 "조회 실패" 가 되고 `acl.json` 이 비어 나온다 — 그 파일이 비면 서버는
        **전 페이지를 아무에게도 안 보이는 것**으로 읽는다.

        재시도를 여기 두면 부르는 쪽이 기억할 일이 없다. 같은 일을 두 곳에서 하지
        않도록 `_paged` 의 429 분기는 지웠다.

        **일시적 네트워크 실패도 같은 루프에서 다룬다.** 읽기 타임아웃 하나가
        수천 건짜리 싱크를 통째로 죽이던 자리다 — 실측(2026-08-23, ONAP 공개
        인스턴스): 15,000건을 받는 동안 `Read timed out` 이 세 번 났고 그때마다
        전체가 중단됐다. 이어받기가 있어 유실은 없지만 **사람이 다시 실행해야
        한다.** 자동 싱크(cron)에는 그 사람이 없다.

        **GET 만 재시도한다** — 이 클라이언트는 읽기 전용이라 전부 멱등이다.
        영구 실패는 그대로 던진다: 마지막 시도까지 실패하면 원래 예외가 올라간다.
        """
        wait = FIRST_RETRY_WAIT
        refreshed = False
        for attempt in range(MAX_RETRIES):
            try:
                r = self.s.get(url, timeout=self.timeout, **kw)
            except (requests.Timeout, requests.ConnectionError) as e:
                # **연결 자체가 안 선 것과 응답이 늦은 것을 같이 본다.** 둘 다
                # 다시 물으면 되는 경우이고, 아닌 경우는 아래에서 소진된다.
                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(min(wait, MAX_RETRY_WAIT))
                wait = min(wait * 2, MAX_RETRY_WAIT)
                continue
            # 갱신은 **한 번뿐이다.** 429 재시도 루프를 두르면서 이 조건을 안 걸었더니
            # 무효 토큰 하나에 IAM 을 다섯 번 두드렸다(테스트가 잡았다) — 토큰이 정말
            # 만료된 것이면 한 번으로 되고, 안 되면 더 해도 안 된다.
            if r.status_code == 401 and not refreshed and self.auth.refresh():
                refreshed = True
                self.auth.apply(self.s)
                continue
            # 5xx 는 서버 쪽 일시 장애다. 4xx 는 다시 물어도 같으므로 즉시 돌려준다.
            if r.status_code >= 500 and attempt < MAX_RETRIES - 1:
                time.sleep(min(wait, MAX_RETRY_WAIT))
                wait = min(wait * 2, MAX_RETRY_WAIT)
                continue
            if r.status_code != 429:
                return r
            # 서버가 알려준 값을 따르되 상한을 건다 — 무한 대기 방지.
            time.sleep(min(int(r.headers.get("Retry-After", wait) or wait), MAX_RETRY_WAIT))
            wait = min(wait * 2, MAX_RETRY_WAIT)
        return r

    # ------------------------------------------------------------ 진단

    def detect_prefix(self) -> str:
        """
        Cloud 는 `{base}/wiki/rest/api/...`, Server/DC 는 대개 `{base}/rest/api/...`.
        고정하면 한쪽에서 무조건 404 가 난다. 실제로 찔러보고 정한다.

        `/space`(목록 조회) 하나만으로 판정하지 않는다 — 실제로 겪은 사고:
        리버스 프록시가 `/wiki/rest/api/space`만 허용 목록에 넣어두고
        그 아래 다른 엔드포인트(`/user/current`, `/content/...`)는 로그인
        페이지로 리다이렉트하는 구성이 있었다. 리다이렉트를 자동으로
        따라가면 최종 상태코드가 200으로 떨어져 "접두사가 맞다"는 착시를
        일으키고, 그 뒤 모든 요청이 조용히 로그인 페이지로 새 나갔다.
        그래서 `/space`가 200이어도 **다른 엔드포인트로 한 번 더 검증**한다.
        """
        if self.prefix is not None:
            return self.prefix
        last = None
        for p in self._prefix_candidates():
            try:
                r = self._get(self.base + p + "/rest/api/space", params={"limit": 1})
            except requests.RequestException as e:
                last = str(e)
                continue
            # 401/403 은 "API 가 여기 있는데 인증이 거부됐다"는 뜻이므로
            # 접두사는 맞은 것이다. 이것을 실패로 처리하면 토큰 문제를
            # URL 문제로 오진하게 된다.
            if r.status_code in (401, 403):
                self.prefix = p
                return p
            if r.status_code != 200:
                last = r.status_code
                continue
            if not self._prefix_actually_works(p):
                last = f"{p or '(빈 접두사)'}: /space는 200이지만 다른 엔드포인트가 막힘"
                continue
            self.prefix = p
            return p
        raise ConfluenceError(
            "Confluence REST API 를 찾을 수 없습니다 (" + self.base + "). "
            "마지막 응답: " + str(last) + "\n"
            "  · URL 이 맞습니까? Cloud 는 https://<사이트>.atlassian.net 형식입니다\n"
            "  · 자체 호스팅이면 컨텍스트 경로가 다를 수 있습니다. 알아낸 경로를"
            " 그대로 주면 자동 판별을 건너뜁니다:\n"
            "      CONFLUENCE_PREFIX=/confluence   (예: Apache 계열)\n"
            "      CONFLUENCE_PREFIX=              (빈 값도 유효한 설정입니다)"
        )

    #: 표준 마운트 두 곳. **여기에 계속 더하는 것이 답이 아니다** — 자체 호스팅은
    #: 리버스 프록시가 아무 데나 걸 수 있어 목록으로는 원리적으로 못 따라간다.
    #: 그래서 [_context_path_from_html] 로 **서버에게 먼저 묻는다.**
    _FALLBACK_PREFIXES = ("/wiki", "")

    def _prefix_candidates(self) -> list:
        """
        시도할 접두사를 **자기서술 우선**으로 만든다.

        오늘(2026-08-22) 같은 모양의 결함을 둘 봤다 — 페이징은 응답의
        `_links.base` 가 답을 갖고 있는데 밖에서 짐작하다 Cloud 에서 404 를 냈고,
        접두사는 `ajs-context-path` 가 답을 갖고 있는데 목록으로 찍고 있었다.
        **응답 안에 답이 있으면 짐작하지 않는다.**

        실측 — 세 인스턴스:

            Apache  Server/DC  ajs-context-path="/confluence"  ← 목록으로는 못 맞힌다
            Acme   Server/DC  ajs-context-path=""
            ONAP    Cloud      메타 없음 (SPA) → 표준 "/wiki" 로 덮인다

        **자기서술을 믿고 끝내지는 않는다.** 리버스 프록시가 HTML 을 다시 쓰는
        구성이 실재하므로(이 메서드 위의 사고 기록), 여기서는 **후보만** 만들고
        판정은 기존 두 엔드포인트 검증이 그대로 한다.
        """
        found = self._context_path_from_html()
        if found is None:
            return list(self._FALLBACK_PREFIXES)
        # 중복 제거 — 자기서술이 표준값과 같으면 두 번 찌를 이유가 없다.
        return [found] + [p for p in self._FALLBACK_PREFIXES if p != found]

    def _context_path_from_html(self) -> str | None:
        """
        Confluence 가 렌더한 HTML 의 `<meta name="ajs-context-path">` 를 읽는다.
        Server/DC 는 이것으로 마운트 위치를 스스로 알린다. Cloud 는 SPA 라 없다.

        **못 읽는 것은 정상이다** — 여기서 실패해도 조용히 `None` 을 돌려주고
        폴백 목록으로 간다. 이 경로가 죽으면 접두사 판별 전체가 죽는 것이 아니라
        예전과 같아질 뿐이어야 한다.
        """
        try:
            r = self._get(self.base + "/")
            if r.status_code != 200:
                return None
            m = re.search(
                r'<meta[^>]+name=["\']ajs-context-path["\'][^>]*'
                r'content=["\']([^"\']*)["\']',
                r.text[:200_000], re.I)
            if not m:
                return None
        except requests.RequestException:
            return None
        except ValueError:
            return None
        path = m.group(1).strip()
        if path and not path.startswith("/"):
            return None            # 상대경로는 컨텍스트가 아니다
        return path.rstrip("/")

    def _prefix_actually_works(self, prefix: str) -> bool:
        """
        `/space`와 다른 성격의 엔드포인트(`/user/current`)까지 실제 JSON으로
        응답하는지 확인한다. 리다이렉트를 따라가 로그인 HTML에 떨어지면
        상태코드는 200이어도 `.json()`이 깨진다 — 그걸로 가짜 통과를 잡는다.
        """
        try:
            r = self._get(self.base + prefix + "/rest/api/user/current")
        except requests.RequestException:
            return False
        if r.status_code in (401, 403):
            return True  # API는 맞게 도달했고 인증만 거부된 것 — 접두사는 맞다
        if r.status_code != 200:
            return False
        try:
            r.json()
        except ValueError:
            return False
        return True

    def _url(self, path: str) -> str:
        return self.base + self.detect_prefix() + path

    def doctor(self) -> Diagnosis:
        """연결·인증·권한을 실행 전에 확인한다. sync 도중 예외로 터지는 것을 막는다."""
        d = Diagnosis(base_url=self.base, auth_mode=self.auth_mode)
        try:
            d.prefix = self.detect_prefix()
            d.deployment = "Cloud" if d.prefix == "/wiki" else "Server/DC"
        except ConfluenceError as e:
            d.errors.append(str(e))
            return d

        try:
            r = self._get(self._url("/rest/api/user/current"))
            if r.status_code == 200:
                j = r.json()
                d.authenticated = True
                d.account = j.get("email") or j.get("displayName") or j.get("username")
            elif r.status_code in (401, 403):
                d.errors.append(_auth_hint(r.status_code, self.auth_mode))
            else:
                d.errors.append("인증 확인 실패 (HTTP " + str(r.status_code) + ")")
        except requests.RequestException as e:
            d.errors.append("연결 실패: " + str(e))
            return d

        try:
            r = self._get(self._url("/rest/api/space"), params={"limit": 50})
            if r.status_code == 200:
                d.spaces = [(x.get("key", ""), x.get("name", ""))
                            for x in r.json().get("results", [])]
                if not d.spaces:
                    d.errors.append("접근 가능한 스페이스가 없습니다. 권한을 확인하세요")
        except requests.RequestException as e:
            d.errors.append("스페이스 조회 실패: " + str(e))

        # body.storage 확장은 인스턴스 설정에 따라 막히는 경우가 있다
        if d.spaces:
            try:
                r = self._get(
                    self._url("/rest/api/content/search"),
                    params={"cql": 'type=page and space="' + d.spaces[0][0] + '"',
                            "limit": 1, "expand": "body.storage,version,space"},
                )
                if r.status_code == 200:
                    res = r.json().get("results", [])
                    d.storage_expandable = bool(res) and "storage" in (res[0].get("body") or {})
                    if res and not d.storage_expandable:
                        d.errors.append(
                            "body.storage 확장이 응답에 없습니다. "
                            "인스턴스가 확장을 제한하고 있을 수 있습니다")
                else:
                    d.errors.append("CQL 검색 실패 (HTTP " + str(r.status_code) + ")")
            except requests.RequestException as e:
                d.errors.append("CQL 검색 실패: " + str(e))
        return d

    # ------------------------------------------------------------ 조회

    # 본문·버전·스페이스·계층. ancestors 는 TREE.md 용으로 루트부터 직속 부모까지 온다.
    FULL_EXPAND = "body.storage,version,space,ancestors"

    def search(self, cql: str, limit: int = 50, expand: str | None = None):
        """
        [expand] 를 좁히면 응답이 극적으로 작아진다. ID 집합만 필요한 호출이
        본문까지 받아오면 싱크가 사실상 본문을 두 번 내려받는다.
        """
        url = self._url("/rest/api/content/search")
        params = {"cql": cql, "limit": limit}
        # `expand or FULL` 로 쓰면 안 된다 — 빈 문자열이 falsy 라 "확장 없음"이
        # 조용히 전체 확장으로 되돌아간다. None(기본) 과 ""(명시적 없음) 을 구분한다.
        exp = self.FULL_EXPAND if expand is None else expand
        if exp:
            params["expand"] = exp
        # 429 백오프는 `_get` 이 한다 — 여기 두면 낱개 조회 경로가 그 보호를 못 받는다.
        while url:
            try:
                r = self._get(url, params=params)
            except requests.RequestException as e:
                raise ConfluenceError("요청 실패: " + str(e)) from e

            if r.status_code in (401, 403):
                raise ConfluenceError(_auth_hint(r.status_code, self.auth_mode))
            if r.status_code != 200:
                raise ConfluenceError("HTTP " + str(r.status_code) + ": " + r.text[:200])

            data = r.json()
            for item in data.get("results", []):
                yield item
            nxt = data.get("_links", {}).get("next")
            # **`next` 는 접두사 기준의 상대경로다.** `self.base + nxt` 로 이으면
            # Cloud 에서 `/wiki` 가 빠져 2쪽부터 404 가 난다 — 1쪽은 `_url()` 로
            # 만들어 정상이라 **첫 페이지만 보는 스페이스에서는 안 드러난다.**
            # Server/DC 는 접두사가 "" 라 우연히 맞아서, 그 판만 쓰는 동안은
            # 영영 안 보인다(실측 2026-08-22: ONAP Cloud 493건 싱크가 2쪽에서
            # `No endpoint GET /rest/api/content/search` 로 죽었다).
            #
            # 응답이 답을 갖고 있다 — `_links.base` 가 접두사까지 포함한 절대
            # 주소다. 없으면 `_url()` 로 붙인다.
            if nxt:
                api_base = data.get("_links", {}).get("base")
                url = (api_base.rstrip("/") + nxt) if api_base else self._url(nxt)
            else:
                url = None
            params = None      # next 링크에 쿼리가 이미 들어 있다

    def list_page_ids(self, space: str) -> set:
        """
        스페이스 전체 ID. 삭제 감지용 — 증분으로는 원리적으로 안 잡힌다.

        `expand=""` 가 핵심이다. 기본 확장을 쓰면 ID 하나 얻자고 페이지 본문을
        전부 내려받아, `--full` 싱크가 본문을 두 번 받는 꼴이 된다.
        """
        return {
            str(i["id"])
            for i in self.search(_cql_for_space(space), limit=100, expand="")
        }


# ---------------------------------------------------------------- 상태

def _cursor_for(state: dict, space: str, known: set) -> str | None:
    """
    그 스페이스의 증분 기준 시각. **없으면 `None` — 즉 전량을 받는다.**

    예전에는 커서가 볼트 전체에 하나였다. 그러면 **이미 싱크한 볼트에 스페이스를
    더해도 아무것도 안 받는다** — 새 스페이스의 백로그는 전부 그 시각보다 과거라
    `lastModified > 커서` 에 하나도 안 걸린다. 에러도 안 난다:
    `받음 0 · 실패 0` 으로 끝나 정상처럼 보인다(실측 2026-08-23, ONAP `DW`
    15,001건이 통째로 안 왔다). `--full` 로 우회할 수 있지만 그건 전량 재다운로드다.

    **옛 볼트를 깨지 않는다.** `cursors` 가 없던 볼트는 전역 커서를 갖고 있는데,
    그것은 *이미 받은 스페이스들*에 대해서는 맞는 값이다. 그래서 그 스페이스에만
    물려주고(`known`), 처음 보는 스페이스는 `None` 으로 둔다.
    """
    per = state.get("cursors") or {}
    if space in per:
        return per[space]
    return state.get("cursor") if space in known else None


def _now_cursor() -> str:
    """
    CQL `lastModified` 비교에 쓰는 시각. **이음매로 빼둔 이유는 테스트가 시계를 잡기
    위해서다** — 이 값이 언제 읽히느냐가 곧 증분 싱크의 정확성이다.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _load_state(root: Path) -> dict:
    p = layout.sync_state_path(root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            # 진단 가능한 메시지로 바꾼다. raw traceback 은 "무엇을 하라"를 안 알려준다.
            raise ConfluenceError(
                f"싱크 상태 파일이 손상됐습니다: {p}\n"
                f"  {e}\n"
                f"  이 파일을 지우면 전체를 다시 받습니다 (원본은 mirror/raw 에 남아 있습니다)."
            ) from e
    # `cursor` 는 **전역** 이고 `cursors` 가 **스페이스별** 이다. 둘 다 두는 이유는
    # 아래 `_cursor_for` 의 이관 때문 — 예전 볼트에는 전역만 있다.
    return {"cursor": None, "cursors": {}, "pages": {}, "partial": None}


def _save_state(root: Path, state: dict) -> None:
    p = layout.ensure_parent(layout.sync_state_path(root))
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    tmp.replace(p)      # 원자적 교체. 쓰다 죽어도 이전 상태가 온전하다


# ---------------------------------------------------------------- 싱크

def _cql_quote(s: str) -> str:
    """CQL 문자열 리터럴 이스케이프. 따옴표가 든 제목/스페이스 키가 실제로 있다."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _cql_for_space(space: str, since: str | None = None) -> str:
    cql = 'type=page and space="' + _cql_quote(space) + '"'
    if since:
        cql += ' and lastModified > "' + _cql_quote(since) + '"'
    return cql + " order by lastModified asc"


def _cql_for_title(space: str, title: str) -> str:
    return 'type=page and space="' + _cql_quote(space) + '" and title="' + _cql_quote(title) + '"'


def _ingest(item: dict, root: Path, state: dict, fallback_space: str, full: bool) -> str:
    """검색 결과 항목 하나를 raw/에 쓰고 state에 반영한다. "fetched"/"unchanged" 반환."""
    pid = str(item["id"])
    version = int((item.get("version") or {}).get("number", 0))
    prev = state["pages"].get(pid)
    if prev and int(prev.get("version", -1)) == version and not full:
        return "unchanged"

    body = ((item.get("body") or {}).get("storage") or {}).get("value", "")
    layout.ensure_parent(layout.raw_path(root, pid)).write_text(
        body, encoding="utf-8", newline="\n")

    # 루트부터 직속 부모까지 순서대로. TREE.md(계층 목차)를 만드는 데만 쓴다 —
    # 앵커 색인과는 완전히 분리된 별도 신호다(부모 제목을 앵커처럼 섞으면
    # 같은 부모 아래 문서 전부가 앵커를 공유해 모호성이 커진다).
    ancestors = [
        {"id": str(a["id"]), "title": a.get("title", "")}
        for a in (item.get("ancestors") or [])
    ]

    state["pages"][pid] = {
        "title": item.get("title", ""),
        "space": (item.get("space") or {}).get("key", fallback_space),
        "version": version,
        "updated": (item.get("version") or {}).get("when", ""),
        "ancestors": ancestors,
    }
    return "fetched"


def _expand_referenced(
    root: Path, client: ConfluenceClient, state: dict, spaces: list,
    cap: int, verbose: bool,
) -> int:
    """
    **딱 한 홉만** 따라간다. **지정 스페이스 소속 페이지의 링크만** 스캔
    대상으로 삼아, space-key가 명시돼 있고 지정 스페이스 목록 밖을 가리키는
    (space, title)을 낱개로 조회해서 받는다.

    참조로 받아온 위성 페이지(space가 spaces 밖) 자신의 링크는 이번 호출은
    물론 **다음 sync 호출에서도** 다시 스캔하지 않는다 — state에 남는 소속
    스페이스로 매번 걸러낸다. raw/ 전체를 무조건 다시 훑으면 위성 페이지가
    디스크에 남아있는 한 그 페이지의 링크가 계속 후보로 잡혀, cron으로 반복
    실행할 때 호출 한 번당 한 홉씩 조용히 번진다 — 재귀 확장을 막으려던
    이유(CategoryHomepage류가 25개 무관 스페이스에 존재) 그대로 재현된다.
    """
    from .convert import extract_cross_space_refs

    known = {(m.get("space"), m.get("title")) for m in state["pages"].values()}
    space_set = set(spaces)
    targets: set[tuple[str, str]] = set()
    for pid, meta in state["pages"].items():
        if meta.get("space") not in space_set:
            continue  # 위성 페이지는 스캔 대상에서 영구히 제외 (한 홉 보장)
        raw_file = layout.raw_path(root, pid)
        if not raw_file.exists():
            continue
        for space, title in extract_cross_space_refs(raw_file.read_text(encoding="utf-8")):
            if space in space_set or (space, title) in known:
                continue
            targets.add((space, title))

    fetched = 0
    for space, title in sorted(targets):
        if fetched >= cap:
            if verbose:
                print(f"  참조 확장 상한({cap}건) 도달, 나머지 건너뜀")
            break
        cql = _cql_for_title(space, title)
        try:
            item = next(client.search(cql, limit=1), None)
            if item is None:
                continue  # 다른 스페이스에도 없는 죽은 링크
            if _ingest(item, root, state, space, full=False) == "fetched":
                fetched += 1
                if verbose:
                    print("  + [참조] " + space + "/" + title)
        except Exception as e:  # noqa: BLE001
            # 메인 루프와 대칭: 항목 하나 실패가 sync() 전체를 죽이면 안 된다
            # (디스크 오류·예상 밖 응답 형태 등으로 _ingest 가 던질 수 있다).
            if verbose:
                print("  ! 참조 조회 실패 " + space + "/" + title + ": " + str(e))
            continue
    return fetched


def sync(root: Path, client: ConfluenceClient, spaces: list,
         full: bool = False, verbose: bool = False,
         follow_refs: bool = False, follow_refs_cap: int = 300) -> SyncReport:
    """
    증분 싱크. raw XHTML 만 받는다. 파싱은 build 가 한다.

    CHECKPOINT_EVERY 건마다 상태를 저장하므로 중단되어도 이어받는다.
    대형 스페이스 첫 싱크에서 실질적인 차이가 난다.
    """
    root = Path(root)
    state = _load_state(root)
    if full:
        state["cursor"] = None
        state["cursors"] = {}
        state["partial"] = None

    report = SyncReport(spaces=list(spaces))
    report.resumed_from = state.get("partial")
    started = time.time()
    known = {p.get("space") for p in state.get("pages", {}).values()}
    # **다음 커서를 스캔 **전에** 잡는다.**
    #
    # 끝나고 잡으면 스캔이 도는 동안 수정된 페이지가 유실된다 — 이미 지나갔거나 아직
    # 안 온 상태인데 다음 싱크는 `lastModified > 끝시각` 으로 묻기 때문이다. 그 페이지가
    # 또 수정될 때까지 영영 안 온다. 13,933건 볼트면 그 창이 수십 분이다.
    #
    # 겹치는 쪽 대가는 없다 — 재수신은 `_ingest` 가 버전으로 걸러 `unchanged` 가 된다.
    next_cursor = _now_cursor()
    since_checkpoint = 0

    for space in spaces:
        cql = _cql_for_space(space, since=(None if full else _cursor_for(state, space, known)))

        for item in client.search(cql):
            try:
                outcome = _ingest(item, root, state, space, full)
                if outcome == "unchanged":
                    report.unchanged += 1
                    continue

                state["partial"] = (item.get("version") or {}).get("when", "")
                report.fetched += 1
                since_checkpoint += 1
                if verbose:
                    version = (item.get("version") or {}).get("number", 0)
                    print(f"  + {item['id']} v{version} {item.get('title', '')}")
            except Exception as e:  # noqa: BLE001
                report.failed += 1
                if verbose:
                    print(f"  ! 실패 {item.get('id')}: {e}")
                continue

            # 체크포인트 저장은 **try 밖**이어야 한다. 안에 두면 디스크 오류가
            # "페이지 1건 실패"로 감춰지고, 상태는 영영 저장되지 않은 채 싱크가
            # 끝나 다음 실행이 처음부터 전부 다시 받는다.
            if since_checkpoint >= CHECKPOINT_EVERY:
                _save_state(root, state)
                since_checkpoint = 0
                if verbose:
                    print(f"  ... 체크포인트 {report.fetched}건")

    if follow_refs:
        report.referenced = _expand_referenced(root, client, state, spaces, follow_refs_cap, verbose)

    if full:
        # **스페이스별로 나눠 받는다.** 합쳐 놓으면 한 스페이스가 빈 목록을 줘도
        # 다른 스페이스의 결과에 묻혀 아래 가드가 못 본다.
        live_by_space = {space: client.list_page_ids(space) for space in spaces}

        # **빈 목록으로는 지우지 않는다.** `search` 는 오류를 던지지만 "HTTP 200 ·
        # results=[]" 는 오류가 아니다 — 스페이스 키가 바뀌었거나 Confluence 가
        # 일시적으로 빈 응답을 주면 그렇게 온다. 그대로 두면 **그 스페이스의 원본까지
        # 통째로 지워진다**(실측: 5건 → 0건). `mirror/raw/` 가 사라지면 "원본은 안
        # 지우므로 재빌드로 돌아온다" 는 안전망도 함께 없어져, 복구가 수 시간짜리
        # 재싱크가 된다.
        #
        # 서버가 빈 볼트로 색인을 덮지 않는 것과 같은 판단이다(조용히 실패 14번).
        # 그쪽은 재색인 15초면 복구되는데 이쪽은 그렇지 않아 더 엄해야 한다.
        #
        # **부분 응답(5,000건 중 3건)은 이 가드가 못 잡는다.** 비율 문턱은 손으로
        # 정하는 값이라 안 넣었다 — 필요해지면 재고 넣을 자리다.
        for space, live_ids in live_by_space.items():
            if not live_ids and any(m.get("space") == space for m in state["pages"].values()):
                report.delete_skipped.append(space)
        if report.delete_skipped:
            spaces = [sp for sp in spaces if sp not in report.delete_skipped]

        live = set().union(*live_by_space.values()) if live_by_space else set()
        for pid in list(state["pages"]):
            # --follow-refs 로 받은 위성 페이지는 지정 스페이스 소속이 아니므로
            # 이 삭제 판정 대상이 아니다 — 아니면 매번 삭제됐다 다시 받아진다.
            if state["pages"][pid].get("space") not in spaces:
                continue
            if pid not in live:
                for f in (layout.raw_path(root, pid), layout.page_path(root, pid),
                          layout.structure_path(root, pid)):
                    f.unlink(missing_ok=True)
                del state["pages"][pid]
                report.removed.append(pid)

    # **실패가 있으면 커서를 안 옮긴다.**
    #
    # 옮기면 다음 싱크가 `lastModified > next_cursor` 로 묻는데, 실패한 페이지의
    # `lastModified` 는 그보다 앞이라 **다시 안 온다** — 그 페이지가 또 수정될 때까지
    # 영영. 새 페이지였다면 미러에 아예 없는 채로 남는다(실측: 첫 싱크에서 1건이
    # 던지자 둘째 싱크가 `fetched=0 · unchanged=0`).
    #
    # 위에서 커서를 스캔 **전에** 잡는 것과 같은 계열인데, 이쪽은 창이 아니라 확정이다.
    # 겹치는 쪽 대가는 거기와 같은 이유로 없다 — 재수신은 `_ingest` 가 버전으로 걸러
    # `unchanged` 가 된다.
    #
    # **대가는 실패가 계속되면 매번 전체를 다시 훑는 것이다.** 그건 보이는 비용이고
    # (`실패 N` 이 매번 찍힌다) 반대쪽은 조용한 유실이다.
    if not report.failed:
        # **훑은 스페이스만 옮긴다.** 전역 하나였을 때는 `--space` 목록에 없던
        # 스페이스의 커서까지 함께 밀려, 다음에 그 스페이스를 도로 넣으면 그
        # 사이의 변경을 통째로 건너뛰었다.
        state.setdefault("cursors", {})
        for sp in spaces:
            state["cursors"][sp] = next_cursor
        state["cursor"] = next_cursor      # 옛 판이 읽어도 동작하도록 남긴다
    state["partial"] = None
    _save_state(root, state)
    report.elapsed_s = time.time() - started
    return report


def client_from_env() -> ConfluenceClient:
    # 환경변수 → `~/.wikilens/env.sh` 순. cron 처럼 `export` 가 없는 환경을 덮는다.
    base = credentials.get("CONFLUENCE_URL")
    if not base:
        raise SystemExit(
            "CONFLUENCE_URL 이 필요합니다 — 환경변수에도 "
            f"{credentials.ENV_PATH} 에도 없습니다.\n"
            "  로컬판을 쓴다면 /wikilens-local:setup 이 만들어 줍니다."
        )
    # CONFLUENCE_PREFIX 미설정 시 None → 자동 판별. "" 로 명시하면 그 값
    # 그대로(빈 접두사 강제) 쓴다 — `credentials.get` 은 둘을 구분해서 돌려준다.
    return ConfluenceClient(base, auth_from_env(), prefix=credentials.get("CONFLUENCE_PREFIX"))
