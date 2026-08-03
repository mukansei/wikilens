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
import signal
import sys
import urllib.error
import urllib.request
import uuid

SERVER = os.environ.get("WIKILENS_SERVER", "http://127.0.0.1:8787").rstrip("/")
USER = os.environ.get("WIKILENS_USER", "")
TIMEOUT = float(os.environ.get("WIKILENS_TIMEOUT", "15"))
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
            "사내 Confluence 위키를 검색합니다. 사내 문서·정책·런북·온보딩 자료·"
            "아키텍처 문서를 찾을 때 반드시 먼저 사용하세요. 문서 제목과 사람들이 쓰는 말이 "
            "다르므로(제목이 'OAuth 2.0 인가 코드 흐름'인데 다들 '로그인 붙이는 법'이라고 부름) "
            "일반 검색으로는 찾지 못합니다. 이 도구는 다른 문서들이 각 페이지를 실제로 부르는 "
            "이름(앵커 텍스트)까지 색인하므로 그 격차를 메웁니다. 결과는 pageId 목록이며, "
            "본문은 read 도구로 가져옵니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "자연어 질의. 사내 은어를 그대로 써도 됩니다."},
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
            "개념을 찾을 때는 search가 낫습니다."
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
]


def call_tool(name: str, args: dict) -> tuple[str, bool]:
    """(텍스트, isError) 반환."""
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
            ms = r.get("matches", [])
            if not ms:
                return (f"'{r.get('pattern')}' 일치 없음 (문서 {r.get('scanned',0)}개 스캔)", False)
            out = [f"{len(ms)}건" + (" (잘림)" if r.get("truncated") else "")]
            for m in ms:
                out.append(f"  [{m['pageId']}] {m['title']}:{m['line']}  {m['text']}")
            return ("\n".join(out), False)

        return (f"알 수 없는 도구: {name}", True)

    except urllib.error.HTTPError as e:
        if e.code == 404 and name == "read":
            return ("해당 페이지를 찾을 수 없습니다.", True)
        return (f"서버 오류 {e.code}", True)
    except urllib.error.URLError as e:
        return (f"WikiLens 서버에 연결할 수 없습니다 ({SERVER}): {e.reason}", True)
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
            "serverInfo": {"name": "wikilens", "version": "0.1.0"},
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
    if not USER:
        print("WIKILENS_USER 환경변수가 필요합니다 (ACL 식별자)", file=sys.stderr)
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
