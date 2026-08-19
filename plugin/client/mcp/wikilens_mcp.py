#!/usr/bin/env python3
"""
WikiLens MCP 프록시.

Claude Code(stdio) ↔ WikiLens 서버(HTTP) 사이의 얇은 어댑터.
**표준 라이브러리만 사용한다** — 사용자 머신에 pip 설치가 필요 없다.

프로세스 하나 = 세션 하나다. 시작 시 세션 ID를 만들고 모든 호출에 실어 보내며,
종료 시 서버에 세션 종료를 알린다. 그것이 궤적 경계가 된다.

훅이 필요 없는 이유: 읽기가 서버를 거치므로 서버가 궤적을 직접 관측한다.
클라이언트 버퍼링도, 핫 패스 비용도, 세션 조립도 없다.
"""
import atexit
import json
import os
import pathlib
import signal
import socket
import sys
import urllib.error
import urllib.request
import uuid

CONFIG_PATH = pathlib.Path.home() / ".wikilens" / "config.json"
DEFAULT_SERVER = "http://127.0.0.1:8787"


def _config() -> dict:
    """
    설정. **어떤 내용이 들어 있어도 dict 를 돌려준다.**

    파싱만 확인하면 부족하다 — `null`·`[]`·`"문자열"` 은 유효한 JSON 이라 통과한 뒤
    `_CFG.get(...)` 에서 `AttributeError` 가 난다. 이건 **모듈 최상단**에서 일어나므로
    프록시가 기동 중 죽고 **도구 4개가 통째로 사라진다** — 사용자에게는 위키 검색이
    없어진 것으로 보인다. 설정 오타가 플러그인 전체를 내리는 것은 과한 처벌이다
    (`_timeout()` 이 같은 이유로 방어한다).
    """
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


_CFG = _config()


def _setting(name: str, key: str, default: str = "") -> tuple[str, str]:
    """
    설정 하나를 해석하고 **출처까지** 반환한다: env > config.json > 기본값.

    config 를 읽는 이유: `export` 는 그 셸에서만 산다. Claude Code 를 앱으로 띄우면
    환경이 통째로 비어 서버 주소가 조용히 localhost 기본값이 되고, `WIKILENS_USER`
    가 없어 **모든 검색이 빈 결과**가 된다. 로컬판이 볼트 경로를 env 에서
    `~/.wikilens/config.json` 으로 옮긴 것과 같은 이유다(같은 파일을 쓴다).

    비밀이 아닌 설정만 여기 둔다 — 토큰류는 `~/.wikilens/env.sh`(600) 쪽이다.

    확장되지 않은 `${VAR}` 리터럴은 미설정으로 친다. 매니페스트가 env 를 넘길 때
    변수가 없으면 값이 문자열 `"${WIKILENS_SERVER}"` 로 들어와 URL 조립이 깨진다.
    """
    v = os.environ.get(name, "")
    if v and not (v.startswith("${") and v.endswith("}")):
        return v, "env"

    # JSON 은 숫자·불리언을 그대로 쓸 수 있다. `{"timeout": 30}` 은 사람이 자연스럽게
    # 쓰는 형태인데 문자열만 받으면 **조용히 무시되고 기본값이 쓰인다.** 값이 있으면
    # 문자열로 맞춰 받는다 (dict·list 는 설정값일 리 없으므로 제외).
    v = _CFG.get(key)
    if v is not None and not isinstance(v, (dict, list)):
        s = str(v).strip()
        if s:
            return s, "config"
    return default, "default"


def _timeout() -> float:
    """
    잘못된 값에 죽지 않는다. 예전에는 모듈 최상단에서 `float()` 을 그대로 불러
    `{"timeout": "abc"}` 오타 하나로 **MCP 서버가 기동 중 traceback 으로 죽었다.**
    설정 오타가 플러그인 전체를 내리는 것은 과한 처벌이다.
    """
    raw, origin = _setting("WIKILENS_TIMEOUT", "timeout", "15")
    try:
        t = float(raw)
        if t > 0:
            return t
    except ValueError:
        pass
    # stdout 은 JSON-RPC 전용이므로 진단은 반드시 stderr 로.
    print(f"타임아웃 값이 잘못됐습니다 ({origin}: {raw!r}). 15초를 씁니다.", file=sys.stderr)
    return 15.0


SERVER, SERVER_ORIGIN = _setting("WIKILENS_SERVER", "server", DEFAULT_SERVER)
SERVER = SERVER.rstrip("/")
USER, USER_ORIGIN = _setting("WIKILENS_USER", "user")
TIMEOUT = _timeout()

#: 세션 종료 통보 전용 타임아웃. `end_session` 의 KDoc 참고 — 종료 지연이 그대로
#: 사용자에게 보이므로 일반 타임아웃과 분리한다. 놓쳐도 서버가 거둔다.
END_TIMEOUT = 2.0
SESSION = f"mcp-{uuid.uuid4().hex[:12]}"

PROTOCOL = "2025-06-18"
SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18"}


# --------------------------------------------------------------- HTTP

def post(path: str, payload: dict, timeout: float | None = None) -> dict:
    req = urllib.request.Request(
        f"{SERVER}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
        return json.loads(r.read() or b"{}")


def get(path: str) -> dict:
    req = urllib.request.Request(f"{SERVER}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read() or b"{}")


def unreachable_hint(e: Exception) -> str:
    """
    왜 못 닿았는지 — **원인마다 다음 걸음이 다르다.**

    특히 `기동 중` 과 `고장` 이 예전에는 같은 줄이었다. 컨테이너로 띄우면 Docker 의
    포트 포워딩이 앱보다 **먼저** 열리므로, 색인하는 동안 TCP 는 붙고 HTTP 응답 없이
    끊긴다 — 13,933건에서 실제로 그랬다. 운영자는 `docker logs` 를 따로 봐야 그게
    정상 기동인지 알 수 있었다.

    예외 타입은 실측으로 갈랐다(추측하면 안 잡힌다):

        아무도 안 들음      URLError / reason=ConnectionRefusedError
        응답 없이 끊김      ConnectionResetError — `http.client.RemoteDisconnected`
                            가 그 서브클래스라 FIN(정상 종료)·RST 둘 다 여기로 온다
        붙었는데 무응답     TimeoutError
        주소를 못 풂        URLError / reason=gaierror
    """
    if isinstance(e, ConnectionResetError):
        return ("서버가 **기동 중**일 수 있습니다 — 포트는 열렸는데 아직 응답하지 않습니다.\n"
                "  큰 코퍼스는 색인에 수 분 걸립니다. 잠시 뒤 다시 확인하세요.\n"
                "  계속 이러면 운영자가 기동 로그를 봐야 합니다 (docker compose logs).")
    if isinstance(e, TimeoutError):
        return ("붙었는데 응답이 없습니다 — 기동 중이거나 서버가 막혀 있습니다.\n"
                "  운영자가 기동 로그와 부하를 확인해야 합니다.")
    reason = getattr(e, "reason", None)
    if isinstance(reason, ConnectionRefusedError):
        return ("그 주소에서 **아무도 듣고 있지 않습니다** — 서버가 안 떴거나 주소가 틀렸습니다.\n"
                "  운영자에게 서버가 떠 있는지, 주소·포트가 맞는지 확인하세요.")
    if isinstance(reason, socket.gaierror):
        return ("주소를 풀지 못했습니다 — 호스트 이름이 틀렸거나 DNS/VPN 문제입니다.")
    return ""


def _probe_reach() -> bool:
    """닿나. 못 닿으면 **원인마다 다른 다음 걸음**을 함께 낸다([unreachable_hint])."""
    try:
        get("/api/health")
        print("REACHABLE=yes")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"REACHABLE=no ({e})")
        hint = unreachable_hint(e)
        if hint:
            print(f"\n{hint}")
        if SERVER_ORIGIN == "default":
            print("\n서버 주소를 설정한 적이 없어 기본값(로컬)을 보고 있습니다.")
            print("  운영자에게 받은 주소를 넣으세요:")
            print(f"    python3 {pathlib.Path(__file__).name} --configure --server <주소> --user <본인 식별자>")
        return False


def _probe_index(s: dict) -> bool:
    """색인과 분석기. 겉으로 초록인데 못 쓰는 조합을 짚는다."""
    docs, pages = s.get("indexedDocs", 0), s.get("aclPages", 0)
    ok = True

    # 색인이 지어진 분석기와 서버 설정. **다를 때만** 의미가 있다 — 같으면 정상이라
    # 줄만 늘어난다. 다르면 재색인이 안 된 상태이고, 실질적으로는 볼트를 못 읽어
    # 기동 적재가 건너뛰어진 경우다. 그전에는 기동 로그로만 알 수 있었다.
    built, want = s.get("analyzer"), s.get("analyzerConfigured")
    if built and want and built != want:
        print(f"ANALYZER={built} (설정은 {want})")
        print(f"\n색인은 '{built}' 로 지어졌는데 서버 설정은 '{want}' 입니다.")
        print("  검색은 정상 동작하지만 설정이 반영되지 않았습니다 —"
              " 볼트를 못 읽어 기동 적재가 건너뛰어졌을 수 있습니다.")
        print("  운영자가 볼트 경로를 확인한 뒤 POST /api/admin/reindex 를 돌려야 합니다.")
        ok = False
    elif built:
        print(f"ANALYZER={built}")

    # 서버에 기동 훅이 없다. Lucene 색인은 디스크에 남아 재기동 후에도 검색되는데
    # ACL 페이지 맵은 메모리라 비어서 뜬다 — **검색은 되고 read 는 전부 404** 인
    # 상태다. 겉으로는 전부 초록이라 이 조합을 짚어주지 않으면 못 찾는다
    # (2026-08-06 실측: 재색인 전 read 404 → 재색인 후 200).
    if docs and not pages:
        print("\n색인은 있는데 ACL 페이지 맵이 비어 있습니다 — 검색은 되지만"
              " **읽기가 전부 실패**합니다.")
        print("  운영자가 POST /api/admin/reindex 를 한 번 돌리면 해결됩니다.")
        print("  (서버를 재기동할 때마다 필요합니다.)")
        ok = False
    if not docs:
        print("\n색인이 비어 있습니다. 운영자에게 재색인을 요청하세요.")
        ok = False

    # 문자 집합 필터로 빠진 문서. **빠진 것은 검색 결과에 안 나오는 것으로만 드러나고**
    # 그건 "문서가 없다" 와 구별되지 않는다 — 그래서 켜져 있으면 항상 찍는다.
    # 조용히 열린 것과 시끄럽게 열린 것이 다르다는 규칙(ACL_ENFORCED)과 같은 계열이다.
    dropped = s.get("droppedByScript") or 0
    scripts = s.get("indexScripts")
    if scripts and scripts != "꺼짐":
        print(f"INDEX_SCRIPTS={scripts} · 제외 {dropped}건")
        if dropped:
            print(f"\n문자 집합 필터가 {dropped}건을 색인에서 뺐습니다 —"
                  " 그 문서는 검색·읽기·grep·목차 어디에도 안 나옵니다.")
            print("  의도한 것이 아니면 운영자가 `wikilens.index-scripts` 를 확인해야 합니다"
                  " (비우면 전부 색인합니다).")

    # **색인 문서 수는 볼트가 낡아도 안 변한다.** 자동 싱크가 멈추면 지표가 전부
    # 초록인데 답만 몇 주 낡는다 — 에러가 아니라 침묵이라 아무도 안 본다.
    # 로컬판은 `AGE_DAYS`·`STATUS=stale` 로 이미 말하고 있었고 서버만 빠져 있었다.
    age = s.get("vaultAgeDays")
    if age is None:
        # **"안 낡았다" 가 아니라 "모른다" 다.** 첫 싱크 전이거나 상태 파일이 깨졌다.
        # 옛 서버라 필드 자체가 없는 경우도 여기로 오는데, 어느 쪽이든 나이를 모른다.
        print("VAULT_AGE=(모름 — 싱크 커서가 없거나 서버가 옛 판입니다)")
    else:
        print(f"VAULT_AGE={age}일 (싱크 {s.get('vaultSyncedAt')})")
        if s.get("vaultStale"):
            print(f"\n볼트가 {age}일 됐습니다 — **자동 싱크가 멈췄는지 확인하세요.**")
            print("  검색은 정상으로 보이지만 그만큼 낡은 답을 주고 있습니다.")
            print("  갱신은 호스트에서: wikilens sync … && curl -XPOST …/api/admin/reindex")
    return ok


def _probe_grep(s: dict) -> bool:
    """
    본문 스캔 경로가 둘(JVM·ripgrep)이라 같은 질의가 어느 쪽으로 처리됐는지가 답의
    근거가 된다. 기동 로그는 콘솔로만 나가고 응답의 `engine` 은 grep 을 던져야
    보이므로, 여기가 로그를 못 보는 사람에게 닿는 유일한 자리다.
    """
    eng = s.get("grepEngine")
    if eng:
        print(f"GREP_ENGINE={eng}")
    # 명시했는데 못 쓰는 상태. 동작은 하므로(매 요청 폴백) 겉으로는 정상이다.
    if eng and s.get("grepEngineUsable") is False:
        print(f"\n'{eng}' 로 설정돼 있는데 이 머신에서 쓸 수 없습니다 —"
              " 매 요청이 JVM 스캔으로 넘어갑니다.")
        print("  동작은 하지만 큰 코퍼스에서 grep 이 잘립니다. 운영자가 ripgrep 을"
              " 설치하거나 wikilens.grep-engine 을 고쳐야 합니다.")
        return False
    return True


def _probe_acl(s: dict) -> tuple[bool, bool]:
    """`(정상인가, 시행 중인가)`. 시행 여부가 뒤의 USER 검사까지 가른다."""
    users, pages = s.get("aclUsers", 0), s.get("aclPages", 0)
    ok = True

    # ACL 시행이 꺼져 있으면 등록이 필요 없다 — 아래 경고들이 다 헛말이 된다.
    enforced = s.get("aclEnforced", True)
    if not enforced:
        print("ACL_ENFORCED=no")
        print("\n이 서버는 권한을 확인하지 않습니다 — 접속한 누구나 색인된 전 문서를"
              " 봅니다. 운영자가 의도한 것인지 확인하세요.")
    if enforced and not users:
        print("\n등록된 사용자가 없습니다 — 아무도 아무것도 못 봅니다.")
        print("  운영자가 POST /api/admin/acl/user 로 계정을 등록해야 합니다.")
        ok = False
    # **등록만으로는 부족하다.** 토큰이 안 겹치면 등록이 있어도 전원이 빈손이고,
    # 그 상태가 "문서가 없다" 와 구별되지 않는다. `wikilens acl` 을 처음 돌리면
    # 반드시 걸리는 경로다 — 그전엔 전 페이지가 `@public` 폴백이라 `["@public"]`
    # 등록으로 다 보이는데, 수집 후엔 `@space:<KEY>` 로 바뀐다.
    elif enforced and users and pages and s.get("aclTokenOverlap") == 0:
        ut = ", ".join(s.get("aclUserTokens") or []) or "(없음)"
        pt = ", ".join(s.get("aclPageTokens") or []) or "(없음)"
        print("ACL_TOKEN_OVERLAP=0")
        print("\n등록된 사용자의 토큰이 **어느 페이지 토큰과도 안 겹칩니다** —"
              " 등록은 됐지만 전원이 빈손입니다.")
        print(f"  사용자 토큰: {ut}")
        print(f"  페이지 토큰: {pt}")
        print("  `wikilens acl` 을 처음 돌린 뒤라면 페이지 토큰이 @public 에서"
              " @space:<KEY> 로 바뀐 것입니다 — 그 값으로 다시 등록하세요.")
        ok = False
    return ok, enforced


def _probe_learning(s: dict) -> bool:
    """검색은 정상인데 **학습만 새는** 상태들. 전부 겉으로는 안 보인다."""
    ok = True

    # 게이트가 실제로 무엇을 거르는지. UNKNOWN 이 거의 0 이면 `LOCALIZATION 만
    # 간선 생성` 이 사실상 항등함수라는 뜻이고, 그건 밖에서 볼 방법이 없었다.
    kinds = s.get("byKind") or {}
    if sum(kinds.values()):
        print("QUERY_KINDS=" + " ".join(f"{k}={v}" for k, v in kinds.items() if v))

    # 0 이 아니면 세션 상한이나 sessionId 길이 상한에 걸려 관측을 버리는 중이다.
    if s.get("droppedSessions"):
        print(f"DROPPED_SESSIONS={s['droppedSessions']}")
        print("\n세션 관측을 버리고 있습니다 — 검색은 정상이지만 **그만큼 학습이"
              " 안 됩니다.** 클라이언트가 요청마다 새 sessionId 를 만들고 있거나"
              " 비정상적으로 긴 값을 보내는지 확인하세요.")
        ok = False

    # 궤적 로그 쓰기가 실패해도 메모리 학습은 계속된다. 그래서 이 값이 0 이 아니면
    # **메모리와 로그가 갈라지는 중**이고, 재기동하면 그만큼이 사라진다.
    # 궤적은 유일한 복구 불가 자산이라 서버 로그의 WARN 만으로는 부족하다.
    tl = s.get("trajectoryLog") or {}
    if tl.get("writeFailures"):
        print(f"LOG_WRITE_FAILURES={tl['writeFailures']}")
        print("\n궤적 로그 쓰기가 실패하고 있습니다 — 검색은 정상이지만 **재기동하면"
              " 그만큼의 학습이 사라집니다.**")
        print("  운영자가 상태 디렉터리의 여유 공간과 권한을 확인해야 합니다.")
        ok = False
    if tl.get("replaySkipped"):
        print(f"LOG_REPLAY_SKIPPED={tl['replaySkipped']}")
        print("\n기동 시 궤적 일부를 읽지 못했습니다 — **옛 학습이 버려지는 중**입니다"
              "(대개 스키마 변경). 로그를 지우지 말고 운영자에게 알리세요.")
        ok = False
    # 로그는 append-only 라 줄지 않는다. 조용히 느려지는 자리라 임계 전에 알린다.
    if (tl.get("replayMillis") or 0) > 10000:
        mb = (tl.get("bytes") or 0) // (1024 * 1024)
        print(f"LOG_REPLAY_MILLIS={tl['replayMillis']} (로그 {mb}MB)")
        print("\n궤적 로그가 커져 기동이 느려지고 있습니다. 동작에는 문제가 없지만"
              " 운영자가 체크포인트를 검토할 시점입니다.")

    # **진술 설계가 실제로 도는지.** 모델이 `answer` 를 부르면 `dest` 가 추정이 아니라
    # 진술이 된다 — 안 부르면 `reads.last()` 폴백이라 조용히 예전 동작으로 돌아간다.
    # 그 사실이 밖에서 보이는 창구가 여기뿐이다(서버 로그에도 안 남는다).
    #
    # 궤적이 있는데 진술이 0 이면 셋 중 하나다: 스킬·도구 설명이 안 먹히거나,
    # 플러그인이 낡아 `answer` 가 없거나, 서버가 낡아 404 를 준다.
    trj, dec = s.get("trajectories") or 0, s.get("declaredDest") or 0
    if trj:
        print(f"DECLARED_DEST={dec}/{trj}")
        if not dec:
            print("\n모델이 `answer` 를 한 번도 부르지 않았습니다 — 학습의 `dest` 가"
                  " 전부 **추정**(마지막 읽기)입니다.")
            print("  검색·읽기는 정상입니다. 플러그인과 서버가 최신인지 확인하세요"
                  " (옛 서버는 /api/answer 가 404 입니다).")
    # 추정이 얼마나 틀리는지. 진술이 있는 궤적에서만 대조할 수 있어 표본이 느리게 쌓인다.
    checked, agreed = s.get("fallbackChecked") or 0, s.get("fallbackAgreed") or 0
    if checked:
        print(f"FALLBACK_AGREE={agreed}/{checked} ({100 * agreed // checked}%)")

    # 권한 폭이 다른 사람들의 관측이 한 포스팅에 섞이면 rank 가중과 목적지 분포가
    # 사람마다 다른 의미를 갖는다. 지금은 전 페이지가 @public 이라 0 또는 1 이다.
    scopes = s.get("permissionScopes")
    if isinstance(scopes, int) and scopes > 1:
        print(f"PERMISSION_SCOPES={scopes}")
    return ok


def status() -> int:
    """
    설정과 서버 상태를 한 번에 진단한다 (로컬판 `vault_status.py` 에 대응).

    서버는 `/api/health` 와 `/api/stats` 를 이미 갖고 있는데 플러그인이 쓰지 않아
    사용자에게 닿지 않았다. 검색이 빈손일 때 원인이 셋(주소·식별자·색인) 중
    무엇인지 구분할 방법이 없었다.

    항목별 검사는 `_probe_*` 로 나뉘어 있고 **각자 자기 판정을 낸다** — 하나가
    실패해도 나머지를 계속 찍는다. 진단이 첫 문제에서 멈추면 사용자가 고치고 다시
    돌리기를 반복하게 된다.
    """
    print(f"SERVER={SERVER} ({SERVER_ORIGIN})")
    print(f"USER={USER or '(미설정)'} ({USER_ORIGIN})")
    print(f"CONFIG={CONFIG_PATH if CONFIG_PATH.exists() else '(없음)'}")

    if not _probe_reach():
        return 2

    ok = True
    enforced = True          # stats 를 못 받으면 보수적으로 본다
    try:
        s = get("/api/stats")
        print(f"INDEXED_DOCS={s.get('indexedDocs', 0)}")
        print(f"ACL_PAGES={s.get('aclPages', 0)}")
        print(f"ACL_USERS={s.get('aclUsers', 0)}")
        # `all(...)` 로 묶으면 단축 평가로 뒤 검사가 안 돈다 — 전부 찍어야 한다.
        ok = _probe_index(s) and ok
        ok = _probe_grep(s) and ok
        acl_ok, enforced = _probe_acl(s)
        ok = acl_ok and ok
        ok = _probe_learning(s) and ok
    except Exception as e:  # noqa: BLE001
        print(f"STATS=실패 ({e})")
        ok = False

    if not USER and enforced:
        print("\nUSER 가 없어 모든 검색이 빈 결과가 됩니다:")
        print(f"  python3 {pathlib.Path(__file__).name} --configure --user <본인 식별자>")
        return 2
    return 0 if ok else 2


def configure(argv: list[str]) -> int:
    """
    설정을 **병합**해 기록한다. 서버판의 유일한 설정 경로가 "JSON 을 손으로 쓰기"였다.

    그게 왜 문제냐면 실패가 조용하기 때문이다 — 키를 틀리거나 쉼표를 빠뜨리면
    `_config()` 가 `{}` 로 떨어지고 서버 주소가 기본값(localhost)이 되어, 사용자 눈에는
    **"문서가 없다"** 로 보인다. `--status` 는 그 상태를 진단만 할 뿐 고치지는 못했다.

    **덮어쓰지 않고 병합한다.** 이 파일은 로컬판의 볼트 경로(`vault`)와 CLI 경로(`cli`)도
    담는 공용 정본이라, 통째로 다시 쓰면 두 판을 같이 쓰는 사용자의 로컬판 설정이
    사라진다(`setup_vault.py --capture-env` 가 env.sh 를 병합하는 것과 같은 이유).
    """
    keys = ("--server", "--user", "--timeout")
    args = {}
    it = iter(argv)
    for a in it:
        key, eq, inline = a.partition("=")          # `--server=URL` 도 받는다
        if key not in keys:
            continue
        # `=` 가 있으면 그것이 값이다 — 비어 있어도 다음 토큰을 삼키면 안 된다.
        val = inline if eq else next(it, "")
        # **값을 빠뜨린 것을 값으로 삼으면 안 된다.** `--server --user me@corp` 는
        # `server="--user"` 를 쓰고 user 를 통째로 잃는다 — 손으로 쓴 JSON 이 조용히
        # 깨지는 것을 막으려는 도구가 스스로 그러면 안 된다.
        if val.startswith("-"):
            print(f"{key} 뒤에 값이 없습니다.", file=sys.stderr)
            return 2
        args[key.lstrip("-")] = val
    if not args:
        print("설정할 값이 없습니다. 예: --configure --server http://wikilens.corp:8787 "
              "--user me@corp", file=sys.stderr)
        return 2

    # 스킴이 없으면 `urlopen` 이 `unknown url type` 으로 죽는다. 여기가 **유일한 쓰기
    # 경로**라 여기서 막으면 그 오류가 아예 도달하지 않는다.
    server = args.get("server", "")
    if server and not server.startswith(("http://", "https://")):
        print(f"서버 주소에 스킴이 없습니다: {server}\n"
              f"  http:// 또는 https:// 를 붙이세요 (예: http://{server})", file=sys.stderr)
        return 2

    # **깨진 파일 위에 얹어 쓰면 원본이 통째로 사라진다.** `_config()` 는 파싱 실패를
    # `{}` 로 돌려주므로, 그대로 저장하면 로컬판의 `vault`·`cli` 가 경고 없이 없어진다
    # (실측: 쉼표 하나가 잘못된 파일에 `--configure` 한 번 → 둘 다 소실, 그런데 출력은
    # "설정했습니다"). 이 파일은 사람이 손으로 고치는 파일이라 깨져 있는 것이 흔하다.
    quarantined = ""
    if CONFIG_PATH.exists():
        try:
            # dict 가 아니면 깨진 것과 같다 — 얹어 쓰면 원본이 사라진다.
            if not isinstance(json.loads(CONFIG_PATH.read_text(encoding="utf-8")), dict):
                raise ValueError
        except (OSError, json.JSONDecodeError, ValueError):
            import datetime
            quarantined = f"{CONFIG_PATH}.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}"
            try:
                CONFIG_PATH.replace(quarantined)
            except OSError:
                print(f"{CONFIG_PATH} 를 읽지도 옮기지도 못했습니다. 직접 고치세요.",
                      file=sys.stderr)
                return 2

    cfg = _config()
    cfg.update({k: v for k, v in args.items() if v})
    # **700 으로 맞춘다.** `mkdir()` 만 부르면 umask 기본값(022)이 먹어 755 가 되는데,
    # 이 디렉터리는 두 판이 공유하고 안에 토큰(`env.sh`)이 든다 — 먼저 쓰는 쪽이 권한을
    # 정하므로 한쪽만 700 이면 설치 순서에 따라 결과가 갈린다(로컬판
    # `setup_vault._ensure_config_dir` 와 같은 이유).
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_PATH.parent, 0o700)
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    print(f"CONFIG={CONFIG_PATH}")
    if quarantined:
        print(f"경고: 기존 파일을 읽지 못해 {quarantined} 로 옮겼습니다.")
        print("  그 안의 설정(로컬판 볼트 경로 등)은 새 파일에 없습니다 — 필요하면 손으로 옮기세요.")
    for k in ("server", "user"):
        print(f"{k.upper()}={cfg.get(k) or '(미설정)'}")
    print("\n설정은 시작할 때 한 번 읽습니다 — **Claude Code 를 재시작**해야 반영됩니다.")
    return 0


def end_session() -> None:
    """
    세션 종료 통보. **일반 타임아웃을 쓰면 안 된다.**

    `atexit` 에서 도는데, 서버가 TCP 는 받고 응답을 안 하는 상태(개퍼짐·과부하·방화벽
    드롭)면 종료가 그만큼 멈춘다 — 실측: 기본 15초에서 프로세스가 15.1초 뒤에야 죽었다.
    Claude Code 를 껐다 켤 때마다 그 시간이 그대로 얹히고 사용자에게는 이유가 안 보인다.
    (`SessionSweeper` 가 5분마다 거두므로 놓쳐도 궤적은 확정된다.)
    """
    try:
        post("/api/session/end", {"sessionId": SESSION}, timeout=END_TIMEOUT)
    except Exception:  # noqa: BLE001
        pass


atexit.register(end_session)
for sig in (signal.SIGTERM, signal.SIGINT):
    try:
        signal.signal(sig, lambda *_: sys.exit(0))
    except ValueError:
        pass


# --------------------------------------------------------------- 도구

#: **여기 적는 배제 조항이 `SKILL.md` 의 것과 같아야 한다.**
#:
#: 서버판 MCP 도구는 **스킬 선택과 무관하게 항상 도구 목록에 뜬다**(D13). 그래서
#: 실제로 판단 근거가 되는 것은 스킬 description 이 아니라 이 문장이다 — 스킬에만
#: 배제를 적어 두면 아무것도 안 막는다. 실제로 그랬다: 스킬은 "코드베이스 자체에는
#: 쓰지 마세요" 라고 하는데 도구 설명은 "아키텍처 문서를 찾을 때 반드시 먼저" 만
#: 있어서, **지금 열려 있는 저장소의 아키텍처를 물으면 위키를 먼저 뒤질 근거**가
#: 됐다. 계약이 두 곳을 함께 검사한다.
NOT_FOR_CODEBASE = (
    "지금 열려 있는 코드베이스 자체(구조·빌드·테스트·이 저장소의 코드)에 대한 "
    "질문에는 쓰지 마세요 — 그것은 Read·Grep 으로 볼 것입니다. "
    "이 도구는 코드가 아니라 사람이 쓴 위키 문서를 찾습니다."
)

TOOLS = [
    {
        "name": "search",
        "description": (
            "팀의 Confluence 위키를 검색합니다. 팀·조직의 문서·정책·런북·온보딩 자료·"
            "아키텍처 문서를 찾을 때 반드시 먼저 사용하세요. 문서 제목과 사람들이 쓰는 말이 "
            "다르므로(제목이 'OAuth 2.0 인가 코드 흐름'인데 다들 '로그인 붙이는 법'이라고 부름) "
            "일반 검색으로는 찾지 못합니다. 이 도구는 다른 문서들이 각 페이지를 실제로 부르는 "
            "이름(앵커 텍스트)까지 색인하므로 그 격차를 메웁니다. 결과는 pageId 목록이며, "
            "본문은 read 도구로 가져옵니다. " + NOT_FOR_CODEBASE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "자연어 질의. 팀에서만 쓰는 말을 그대로 써도 됩니다."},
                "limit": {"type": "integer", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read",
        "description": (
            "위키 페이지 본문을 마크다운으로 가져옵니다. search 결과의 pageId를 넣으세요. "
            "권한이 없는 페이지는 존재하지 않는 것으로 응답합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"pageId": {"type": "string"}},
            "required": ["pageId"],
        },
    },
    {
        "name": "grep",
        "description": (
            "위키 본문에서 리터럴 문자열을 찾습니다. 형태소 분석을 거치지 않으므로 "
            "정확 일치가 필요할 때 씁니다 — 식별자, 코드 조각, 정확한 문구. "
            "개념을 찾을 때는 search가 낫습니다.\n"
            # **`search` 보다 오히려 새기 쉽다** — 설명에 "식별자·코드 조각" 이 있어
            # 코드베이스 질문에서 그대로 불릴 만하다. 뒤지는 곳은 위키 본문뿐이다.
            + NOT_FOR_CODEBASE + "\n"
            "대소문자는 어느 쪽이든 구분하지 않습니다. "
            "regex=true 는 ripgrep과 같은 RE2 문법입니다 — 역참조(\\1)와 "
            "전방탐색((?=))은 쓸 수 없고, 쓰면 이유를 알려줍니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "regex": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 40},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "tree",
        "description": (
            "위키 페이지의 부모-자식 계층을 마크다운 목차로 가져옵니다. 정확한 문서 이름은 "
            "모르지만 어느 영역(스페이스/카테고리)에 있는지는 알 때, 위에서부터 내려가며 "
            "찾을 때 씁니다. search는 앵커 텍스트(다른 문서가 이 문서를 부르는 이름)로 찾으므로 "
            "어디서도 링크되지 않은 '고아' 문서에는 약합니다 — 그럴 때 이 도구가 보완합니다.\n"
            "코퍼스가 크면 처음부터 전체를 받지 말고, depth=2 정도로 상위 구조만 먼저 보고 "
            "필요한 가지의 pageId를 rootId로 넣어 그 서브트리만 다시 가져오세요. 깊이 제한에 "
            "걸려 잘린 가지는 '… (+N개 하위, rootId=...)' 요약 줄로 표시됩니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "rootId": {"type": "string", "description": "이 pageId를 루트로 한 서브트리만 가져옵니다. 생략하면 전체."},
                "depth": {"type": "integer", "default": 0, "description": "최대 깊이. 0 = 무제한."},
            },
        },
    },
    {
        "name": "answer",
        "description": (
            "찾던 문서를 확정했을 때 그 pageId를 알려줍니다. 사용자에게 답을 전하기 직전에 "
            "read로 확인한 문서 중 하나를 지정하세요. 여러 문서를 열어봤다면 그중 실제로 "
            "답이 된 것을 고릅니다.\n"
            "이것을 부르지 않으면 서버는 '마지막으로 read한 문서'를 답으로 간주하는데, "
            "그것이 확인용이나 배제용으로 연 문서일 때 틀린 것을 학습합니다(실측: 문서를 "
            "2개 이상 읽은 경우 3건 중 2건이 어긋났습니다). 다음 사람의 검색 품질이 "
            "여기에 달려 있습니다.\n"
            "답을 못 찾았으면 부르지 마세요 — 못 찾았다는 사실도 신호입니다. "
            "이 호출은 검색 결과를 바꾸지 않고 기록만 남깁니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pageId": {"type": "string", "description": "답이 된 문서. read로 연 것이어야 합니다."},
            },
            "required": ["pageId"],
        },
    },
]


def _int(args: dict, key: str, default: int) -> int:
    """
    모델이 숫자 아닌 값을 줘도 죽지 않는다.

    그대로 `int()` 를 부르면 `오류: invalid literal for int() with base 10: '여덟'` 이
    응답으로 나가는데(실측), 모델이 그걸 보고 고칠 방법을 알 수 없어 같은 오류를
    반복한다. **상한은 서버가 이미 죄므로**(`SearchService.MAX_LIMIT`·
    `ContentService.MAX_LIMIT`) 여기서는 기본값으로 떨어뜨리면 된다.
    """
    try:
        return int(args.get(key, default))
    except (TypeError, ValueError):
        return default


def _tool_search(args: dict) -> tuple[str, bool]:
    r = post("/api/search", {
        "query": args.get("query", ""), "userKey": USER,
        "sessionId": SESSION, "limit": _int(args, "limit", 8),
    })
    # **거부 사유를 삼키면 안 된다.** 서버는 쓸 수 없는 질의에 200 + 빈 hits +
    # `error` 를 낸다(질의 길이 상한). 이걸 안 읽으면 아래의 "다른 표현으로
    # 시도하세요" 가 나가는데, 길이 때문에 거부된 질의에 재표현은 아무 도움이
    # 안 되므로 **정확히 틀린 조언**이다. `grep` 이 같은 모양을 처리하는 것과 짝이다.
    #
    # 질의 원문은 되돌려 싣지 않는다 — 최대 500자라 모델 문맥만 먹는다.
    if r.get("error"):
        return (f"질의를 쓸 수 없습니다: {r['error']}", True)
    hits = r.get("hits", [])
    if not hits:
        return ("결과 없음. 다른 표현으로 시도하거나 grep으로 리터럴 검색하세요.", False)
    lines = [f"{len(hits)}건 (어휘 후보 {r.get('lexicalCandidates',0)} · "
             f"학습 힌트 {r.get('learnedHints',0)})", ""]
    for i, h in enumerate(hits, 1):
        mark = {"lexical": " ", "learned": "S", "both": "*"}.get(h.get("source"), " ")
        rel = f" rel={h['reliability']:.2f}" if h.get("reliability") else ""
        lines.append(f"{mark} {i}. [{h['pageId']}] {h['title']}  ({h.get('space','')}){rel}")
    lines += ["", "본문은 read(pageId)로 가져오세요. * = 어휘+학습, S = 학습 힌트만"]
    return ("\n".join(lines), False)


def _tool_read(args: dict) -> tuple[str, bool]:
    r = post("/api/read", {
        "pageId": str(args.get("pageId", "")), "userKey": USER, "sessionId": SESSION,
    })
    return (f"# {r.get('title','')}  [{r.get('pageId')}]\n\n{r.get('markdown','')}", False)


def _tool_grep(args: dict) -> tuple[str, bool]:
    r = post("/api/grep", {
        # sessionId 를 안 보낸다 — grep 은 궤적 관측 대상이 아니다(서버의
        # `Controller.grep` 에 이유가 있다). 보내면 서버가 버리는데, 보내는
        # 쪽만 보면 "기록되고 있다" 로 읽힌다.
        "pattern": args.get("pattern", ""), "userKey": USER,
        "limit": _int(args, "limit", 40), "regex": bool(args.get("regex", False)),
    })
    # 패턴 자체가 거부된 경우. 이유를 안 보여주면 "쓸 수 없는 문법" 과
    # "정말 일치가 없음" 이 똑같이 0건으로 보인다.
    if r.get("error"):
        return (f"'{r.get('pattern')}' 을 쓸 수 없습니다: {r['error']}", True)
    ms = r.get("matches", [])
    if not ms:
        return (f"'{r.get('pattern')}' 일치 없음 (문서 {r.get('scanned',0)}개 스캔)", False)
    out = [f"{len(ms)}건" + (" (잘림)" if r.get("truncated") else "")]
    for m in ms:
        out.append(f"  [{m['pageId']}] {m['title']}:{m['line']}  {m['text']}")
    return ("\n".join(out), False)


def _tool_tree(args: dict) -> tuple[str, bool]:
    root_id = args.get("rootId")
    payload = {"userKey": USER, "depth": _int(args, "depth", 0)}
    if root_id:
        payload["rootId"] = str(root_id)
    md = post("/api/tree", payload).get("markdown", "")
    if md:
        return (md, False)
    if root_id:
        return ("계층 정보 없음 (해당 rootId를 찾을 수 없거나 권한이 없습니다).", False)
    return ("계층 정보 없음 (권한이 없거나 색인이 비어 있습니다).", False)


#: 이름 → 핸들러. [TOOLS] 의 `name` 과 **키가 같아야 한다** — 어긋나면 도구 목록에는
#: 보이는데 부르면 "알 수 없는 도구" 가 된다. `test_mcp_proxy` 가 둘을 대조한다.
def _tool_answer(args: dict) -> tuple[str, bool]:
    r = post("/api/answer", {
        "sessionId": SESSION, "pageId": str(args.get("pageId", "")),
    })
    # **거부돼도 오류가 아니다.** 이 호출은 부가 신호이고 안 부르면 서버가
    # `reads.last()` 로 추정한다 — 실패를 오류로 만들면 모델이 그걸 고치려 든다.
    # 다만 조용히 넘기지도 않는다: 읽지 않은 페이지를 답이라고 한 것이므로 알려준다.
    if not r.get("accepted"):
        return ("기록하지 못했습니다 — read 로 연 문서만 답으로 지정할 수 있습니다. "
                "답 자체는 그대로 사용자에게 전달하세요.", False)
    return ("기록했습니다.", False)


HANDLERS = {
    "search": _tool_search,
    "read": _tool_read,
    "grep": _tool_grep,
    "tree": _tool_tree,
    "answer": _tool_answer,
}


def call_tool(name: str, args: dict) -> tuple[str, bool]:
    """(텍스트, isError) 반환. 도구별 처리는 [HANDLERS] 에 있다."""
    # ACL 은 fail-closed 다 — userKey 가 없으면 서버가 정상적으로 빈 결과를 준다.
    # 그것을 "문서가 없다"로 오해하지 않도록 여기서 먼저 구분해 알린다.
    if not USER:
        return (
            "본인 식별자가 설정되지 않았습니다. 서버 ACL 이 요청자를 식별하지 못해 "
            "결과가 항상 비어 있습니다 (문서가 없는 것이 아닙니다).\n"
            f'  {CONFIG_PATH} 에 {{"user": "<본인 식별자>"}} 를 넣고 Claude Code 를 재시작하세요.\n'
            "  (환경변수 WIKILENS_USER 로도 되지만 그 셸에서만 유지됩니다.)",
            True,
        )
    handler = HANDLERS.get(name)
    if handler is None:
        return (f"알 수 없는 도구: {name}", True)
    try:
        return handler(args)
    except urllib.error.HTTPError as e:
        if e.code == 404 and name == "read":
            return ("해당 페이지를 찾을 수 없습니다.", True)
        return (f"서버 오류 {e.code}", True)
    except urllib.error.URLError as e:
        # 주소를 설정한 적이 없으면 조용히 localhost 를 보고 있다. "서버가 죽었다"와
        # "주소를 안 넣었다"는 완전히 다른 문제인데 메시지가 같으면 구분이 안 된다.
        hint = ""
        if SERVER_ORIGIN == "default":
            hint = (f"\n서버 주소를 설정한 적이 없어 기본값을 보고 있습니다. "
                    f'{CONFIG_PATH} 에 {{"server": "<운영자에게 받은 주소>"}} 를 넣으세요.')
        return (f"WikiLens 서버에 연결할 수 없습니다 ({SERVER}): {e.reason}{hint}", True)
    except Exception as e:  # noqa: BLE001
        return (f"오류: {e}", True)


# --------------------------------------------------------------- JSON-RPC

def reply(msg_id, result=None, error=None) -> None:
    m = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        m["error"] = error
    else:
        m["result"] = result
    sys.stdout.write(json.dumps(m, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        want = (msg.get("params") or {}).get("protocolVersion")
        reply(mid, {
            "protocolVersion": want if want in SUPPORTED else PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "wiki", "version": "0.1.0"},
        })
    elif method == "notifications/initialized":
        pass                                    # 알림에는 응답하지 않는다
    elif method == "tools/list":
        reply(mid, {"tools": TOOLS})
    elif method == "tools/call":
        p = msg.get("params") or {}
        text, is_err = call_tool(p.get("name", ""), p.get("arguments") or {})
        reply(mid, {"content": [{"type": "text", "text": text}], "isError": is_err})
    elif method == "ping":
        reply(mid, {})
    elif mid is not None:
        reply(mid, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> int:
    if "--configure" in sys.argv:
        return configure(sys.argv[sys.argv.index("--configure") + 1:])
    if "--status" in sys.argv:
        return status()
    if not USER:
        print("본인 식별자가 필요합니다 (ACL). --configure --user <식별자> 로 넣으세요.",
              file=sys.stderr)
    # `for line in sys.stdin` 을 쓰면 안 된다. TextIOWrapper 의 read-ahead 버퍼가
    # 청크를 채울 때까지 블로킹해서 stdio 서버가 응답하지 못한다.
    # readline() 은 한 줄 단위로 즉시 반환한다.
    while True:
        line = sys.stdin.readline()
        if not line:            # EOF — 클라이언트가 stdin 을 닫았다
            break
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except json.JSONDecodeError:
            continue
        except Exception as e:  # noqa: BLE001
            print(f"handler error: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
