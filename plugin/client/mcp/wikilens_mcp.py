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
SESSION = f"mcp-{uuid.uuid4().hex[:12]}"

PROTOCOL = "2025-06-18"
SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18"}


# --------------------------------------------------------------- HTTP

def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{SERVER}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read() or b"{}")


def get(path: str) -> dict:
    req = urllib.request.Request(f"{SERVER}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read() or b"{}")


def status() -> int:
    """
    설정과 서버 상태를 한 번에 진단한다 (로컬판 `vault_status.py` 에 대응).

    서버는 `/api/health` 와 `/api/stats` 를 이미 갖고 있는데 플러그인이 쓰지 않아
    사용자에게 닿지 않았다. 검색이 빈손일 때 원인이 셋(주소·식별자·색인) 중
    무엇인지 구분할 방법이 없었다.
    """
    print(f"SERVER={SERVER} ({SERVER_ORIGIN})")
    print(f"USER={USER or '(미설정)'} ({USER_ORIGIN})")
    print(f"CONFIG={CONFIG_PATH if CONFIG_PATH.exists() else '(없음)'}")

    try:
        get("/api/health")
        print("REACHABLE=yes")
    except Exception as e:  # noqa: BLE001
        print(f"REACHABLE=no ({e})")
        if SERVER_ORIGIN == "default":
            print("\n서버 주소를 설정한 적이 없어 기본값(로컬)을 보고 있습니다.")
            print("  운영자에게 받은 주소를 넣으세요:")
            print(f"    python3 {pathlib.Path(__file__).name} --configure --server <주소> --user <본인 식별자>")
        return 2

    ok = True
    enforced = True          # stats 를 못 받으면 보수적으로 본다
    try:
        s = get("/api/stats")
        docs, users = s.get("indexedDocs", 0), s.get("aclUsers", 0)
        pages = s.get("aclPages", 0)
        print(f"INDEXED_DOCS={docs}")
        print(f"ACL_PAGES={pages}")
        print(f"ACL_USERS={users}")

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

        # 본문 스캔 경로가 둘(JVM·ripgrep)이라 같은 질의가 어느 쪽으로 처리됐는지가
        # 답의 근거가 된다. 기동 로그는 콘솔로만 나가고 응답의 `engine` 은 grep 을
        # 던져야 보이므로, 여기가 로그를 못 보는 사람에게 닿는 유일한 자리다.
        eng = s.get("grepEngine")
        if eng:
            print(f"GREP_ENGINE={eng}")
        # 명시했는데 못 쓰는 상태. 동작은 하므로(매 요청 폴백) 겉으로는 정상이다.
        if eng and s.get("grepEngineUsable") is False:
            print(f"\n'{eng}' 로 설정돼 있는데 이 머신에서 쓸 수 없습니다 —"
                  " 매 요청이 JVM 스캔으로 넘어갑니다.")
            print("  동작은 하지만 큰 코퍼스에서 grep 이 잘립니다. 운영자가 ripgrep 을"
                  " 설치하거나 wikilens.grep-engine 을 고쳐야 합니다.")
            ok = False

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
            print(f"\n등록된 사용자의 토큰이 **어느 페이지 토큰과도 안 겹칩니다** —"
                  f" 등록은 됐지만 전원이 빈손입니다.")
            print(f"  사용자 토큰: {ut}")
            print(f"  페이지 토큰: {pt}")
            print("  `wikilens acl` 을 처음 돌린 뒤라면 페이지 토큰이 @public 에서"
                  " @space:<KEY> 로 바뀐 것입니다 — 그 값으로 다시 등록하세요.")
            ok = False

        # 게이트가 실제로 무엇을 거르는지. UNKNOWN 이 거의 0 이면 `LOCALIZATION 만
        # 간선 생성` 이 사실상 항등함수라는 뜻이고, 그건 밖에서 볼 방법이 없었다.
        kinds = s.get("byKind") or {}
        total_kinds = sum(kinds.values())
        if total_kinds:
            shown = " ".join(f"{k}={v}" for k, v in kinds.items() if v)
            print(f"QUERY_KINDS={shown}")

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

        # 권한 폭이 다른 사람들의 관측이 한 포스팅에 섞이면 rank 가중과 목적지 분포가
        # 사람마다 다른 의미를 갖는다. 지금은 전 페이지가 @public 이라 0 또는 1 이다.
        scopes = s.get("permissionScopes")
        if isinstance(scopes, int) and scopes > 1:
            print(f"PERMISSION_SCOPES={scopes}")
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
        json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"CONFIG={CONFIG_PATH}")
    if quarantined:
        print(f"경고: 기존 파일을 읽지 못해 {quarantined} 로 옮겼습니다.")
        print("  그 안의 설정(로컬판 볼트 경로 등)은 새 파일에 없습니다 — 필요하면 손으로 옮기세요.")
    for k in ("server", "user"):
        print(f"{k.upper()}={cfg.get(k) or '(미설정)'}")
    print("\n설정은 시작할 때 한 번 읽습니다 — **Claude Code 를 재시작**해야 반영됩니다.")
    return 0


def end_session() -> None:
    try:
        post("/api/session/end", {"sessionId": SESSION})
    except Exception:  # noqa: BLE001
        pass


atexit.register(end_session)
for sig in (signal.SIGTERM, signal.SIGINT):
    try:
        signal.signal(sig, lambda *_: sys.exit(0))
    except ValueError:
        pass


# --------------------------------------------------------------- 도구

TOOLS = [
    {
        "name": "search",
        "description": (
            "팀의 Confluence 위키를 검색합니다. 팀·조직의 문서·정책·런북·온보딩 자료·"
            "아키텍처 문서를 찾을 때 반드시 먼저 사용하세요. 문서 제목과 사람들이 쓰는 말이 "
            "다르므로(제목이 'OAuth 2.0 인가 코드 흐름'인데 다들 '로그인 붙이는 법'이라고 부름) "
            "일반 검색으로는 찾지 못합니다. 이 도구는 다른 문서들이 각 페이지를 실제로 부르는 "
            "이름(앵커 텍스트)까지 색인하므로 그 격차를 메웁니다. 결과는 pageId 목록이며, "
            "본문은 read 도구로 가져옵니다."
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
]


def call_tool(name: str, args: dict) -> tuple[str, bool]:
    """(텍스트, isError) 반환."""
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
    try:
        if name == "search":
            r = post("/api/search", {
                "query": args.get("query", ""), "userKey": USER,
                "sessionId": SESSION, "limit": int(args.get("limit", 8)),
            })
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

        if name == "read":
            r = post("/api/read", {
                "pageId": str(args.get("pageId", "")), "userKey": USER, "sessionId": SESSION,
            })
            return (f"# {r.get('title','')}  [{r.get('pageId')}]\n\n{r.get('markdown','')}", False)

        if name == "grep":
            r = post("/api/grep", {
                "pattern": args.get("pattern", ""), "userKey": USER, "sessionId": SESSION,
                "limit": int(args.get("limit", 40)), "regex": bool(args.get("regex", False)),
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

        if name == "tree":
            root_id = args.get("rootId")
            payload = {"userKey": USER, "depth": int(args.get("depth") or 0)}
            if root_id:
                payload["rootId"] = str(root_id)
            r = post("/api/tree", payload)
            md = r.get("markdown", "")
            if not md:
                if root_id:
                    return ("계층 정보 없음 (해당 rootId를 찾을 수 없거나 권한이 없습니다).", False)
                return ("계층 정보 없음 (권한이 없거나 색인이 비어 있습니다).", False)
            return (md, False)

        return (f"알 수 없는 도구: {name}", True)

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
