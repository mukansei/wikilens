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
    spaces: list = field(default_factory=list)
    elapsed_s: float = 0.0
    resumed_from: str | None = None
    referenced: int = 0  # --follow-refs 로 지정 스페이스 밖에서 낱개로 받은 건수


def _auth_hint(status: int, mode: str) -> str:
    if status == 401:
        return ("인증 실패 (401). 현재 방식: " + mode + "\n"
                "  · SSO 환경이라면 계정 비밀번호가 아니라 토큰이 필요합니다\n"
                "  · Server/DC 는 PAT 가 SSO 와 무관하게 동작합니다 — 먼저 시도해 보세요\n"
                "  · 사내 IAM 을 쓴다면 IAM_TOKEN_URL 등으로 OAuth 방식을 지정하세요")
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
        # 쓰고 자동 판별을 건너뛴다 — 사내 게이트웨이가 자동 판별을 속이는
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
        """401 이면 인증을 갱신하고 한 번 재시도한다. OAuth 토큰 만료 대응."""
        r = self.s.get(url, timeout=self.timeout, **kw)
        if r.status_code == 401 and self.auth.refresh():
            self.auth.apply(self.s)
            r = self.s.get(url, timeout=self.timeout, **kw)
        return r

    # ------------------------------------------------------------ 진단

    def detect_prefix(self) -> str:
        """
        Cloud 는 `{base}/wiki/rest/api/...`, Server/DC 는 대개 `{base}/rest/api/...`.
        고정하면 한쪽에서 무조건 404 가 난다. 실제로 찔러보고 정한다.

        `/space`(목록 조회) 하나만으로 판정하지 않는다 — 실제로 겪은 사고:
        사내 리버스 프록시가 `/wiki/rest/api/space`만 허용 목록에 넣어두고
        그 아래 다른 엔드포인트(`/user/current`, `/content/...`)는 로그인
        페이지로 리다이렉트하는 구성이 있었다. 리다이렉트를 자동으로
        따라가면 최종 상태코드가 200으로 떨어져 "접두사가 맞다"는 착시를
        일으키고, 그 뒤 모든 요청이 조용히 로그인 페이지로 새 나갔다.
        그래서 `/space`가 200이어도 **다른 엔드포인트로 한 번 더 검증**한다.
        """
        if self.prefix is not None:
            return self.prefix
        last = None
        for p in ("/wiki", ""):
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
            "  · URL 이 맞습니까? Cloud 는 https://<회사>.atlassian.net 형식입니다\n"
            "  · 사내 인스턴스면 컨텍스트 경로가 다를 수 있습니다"
        )

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
        wait = 5
        while url:
            try:
                r = self._get(url, params=params)
            except requests.RequestException as e:
                raise ConfluenceError("요청 실패: " + str(e)) from e

            if r.status_code == 429:
                delay = min(int(r.headers.get("Retry-After", wait)), MAX_RETRY_WAIT)
                time.sleep(delay)
                wait = min(wait * 2, MAX_RETRY_WAIT)
                continue
            if r.status_code in (401, 403):
                raise ConfluenceError(_auth_hint(r.status_code, self.auth_mode))
            if r.status_code != 200:
                raise ConfluenceError("HTTP " + str(r.status_code) + ": " + r.text[:200])

            data = r.json()
            for item in data.get("results", []):
                yield item
            nxt = data.get("_links", {}).get("next")
            url = (self.base + nxt) if nxt else None
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
    return {"cursor": None, "pages": {}, "partial": None}


def _save_state(root: Path, state: dict) -> None:
    p = layout.ensure_parent(layout.sync_state_path(root))
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                   encoding="utf-8")
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
    layout.ensure_parent(layout.raw_path(root, pid)).write_text(body, encoding="utf-8")

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
        state["partial"] = None

    report = SyncReport(spaces=list(spaces))
    report.resumed_from = state.get("partial")
    started = time.time()
    cursor = state.get("cursor")
    since_checkpoint = 0

    for space in spaces:
        cql = _cql_for_space(space, since=(cursor if not full else None))

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
        live = set()
        for space in spaces:
            live |= client.list_page_ids(space)
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

    state["cursor"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
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
