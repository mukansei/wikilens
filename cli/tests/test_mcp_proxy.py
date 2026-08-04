#!/usr/bin/env python3
"""
MCP 프록시 검증.

가짜 WikiLens 서버를 띄우고, 프록시에 실제 JSON-RPC 메시지를 흘려
핸드셰이크·도구 목록·도구 호출·세션 종료가 규약대로 동작하는지 확인한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROXY = Path(__file__).resolve().parents[2] / "plugin" / "server" / "mcp" / "wikilens_mcp.py"
PORT = 8899

received: list[tuple[str, dict]] = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 조용히
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        received.append((self.path, body))

        if self.path == "/api/search":
            payload = {
                "query": body.get("query"), "terms": ["로그인"],
                "lexicalCandidates": 3, "learnedHints": 1,
                "hits": [{"pageId": "200000001", "title": "OAuth 2.0 인가 코드 흐름",
                          "space": "PLATFORM", "score": 0.03,
                          "source": "both", "reliability": 0.74}],
            }
        elif self.path == "/api/read":
            if body.get("pageId") == "999":
                self.send_response(404); self.end_headers(); return
            payload = {"pageId": body.get("pageId"), "title": "OAuth 2.0 인가 코드 흐름",
                       "space": "PLATFORM", "markdown": "# 개요\n본문입니다."}
        elif self.path == "/api/grep":
            payload = {"pattern": body.get("pattern"), "scanned": 12, "truncated": False,
                       "matches": [{"pageId": "200000003", "title": "배포 파이프라인 규격",
                                    "line": 42, "text": "DEPLOY_TOKEN=..."}]}
        elif self.path == "/api/tree":
            payload = {"markdown": "- [SPACE] 팀 홈 — 111354187\n"
                                    "  - 참고. 조직 R&R 정리 — 167164533\n"}
        elif self.path == "/api/session/end":
            payload = {"finalized": 1}
        else:
            self.send_response(404); self.end_headers(); return

        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def rpc(proc, msg: dict) -> dict | None:
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    if "id" not in msg:
        return None
    line = proc.stdout.readline()
    if not line.strip():
        err = ""
        if proc.poll() is not None:
            err = proc.stderr.read()
        raise RuntimeError(f"프록시 응답 없음 (exit={proc.poll()}) {err}")
    return json.loads(line)


def main() -> int:
    srv = HTTPServer(("127.0.0.1", PORT), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.2)

    import os
    env = {**os.environ, "WIKILENS_SERVER": f"http://127.0.0.1:{PORT}",
           "WIKILENS_USER": "alice@corp"}
    proc = subprocess.Popen(
        [sys.executable, "-u", str(PROXY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env, bufsize=1)

    ok, fail = 0, 0

    def check(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {name}")
        else:
            fail += 1; print(f"  FAIL  {name} {detail}")

    try:
        print("=== 1. initialize 핸드셰이크 ===")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18",
                                  "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        check("응답 형식", r and r.get("jsonrpc") == "2.0" and r.get("id") == 1)
        check("프로토콜 버전 반환", r["result"]["protocolVersion"] == "2025-06-18")
        check("tools capability 선언", "tools" in r["result"]["capabilities"])
        check("serverInfo", r["result"]["serverInfo"]["name"] == "wikilens")

        rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        print("\n=== 2. tools/list ===")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in r["result"]["tools"]]
        check("도구 4개", names == ["search", "read", "grep", "tree"], f"실제={names}")
        check("스키마 있음", all("inputSchema" in t for t in r["result"]["tools"]))

        print("\n=== 3. search ===")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "search", "arguments": {"query": "로그인 붙이는 법"}}})
        text = r["result"]["content"][0]["text"]
        check("에러 아님", not r["result"].get("isError"))
        check("pageId 포함", "200000001" in text)
        check("신뢰도 표시", "rel=0.74" in text)
        _, body = received[-1]
        check("userKey 전달", body.get("userKey") == "alice@corp")
        check("sessionId 전달", str(body.get("sessionId", "")).startswith("mcp-"))
        session_id = body["sessionId"]

        print("\n=== 4. read ===")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "read", "arguments": {"pageId": "200000001"}}})
        check("본문 반환", "본문입니다" in r["result"]["content"][0]["text"])
        check("같은 세션", received[-1][1].get("sessionId") == session_id)

        print("\n=== 5. read 권한 없음 → 404 ===")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                       "params": {"name": "read", "arguments": {"pageId": "999"}}})
        check("isError", r["result"].get("isError") is True)
        check("존재 여부 노출 안 함",
              "찾을 수 없" in r["result"]["content"][0]["text"])

        print("\n=== 6. grep ===")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                       "params": {"name": "grep", "arguments": {"pattern": "DEPLOY_TOKEN"}}})
        check("매치 반환", "200000003" in r["result"]["content"][0]["text"])

        print("\n=== 7. tree ===")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": "tree", "arguments": {}}})
        check("계층 반환", "111354187" in r["result"]["content"][0]["text"])
        _, body = received[-1]
        check("userKey 전달", body.get("userKey") == "alice@corp")
        check("sessionId 미포함 (계층은 궤적 관측 대상 아님)", "sessionId" not in body)

        print("\n=== 8. 알 수 없는 메서드 ===")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 8, "method": "nope"})
        check("-32601 반환", r.get("error", {}).get("code") == -32601)

        print("\n=== 9. 종료 시 세션 정리 ===")
        proc.stdin.close()
        proc.wait(timeout=5)
        time.sleep(0.3)
        ends = [p for p, _ in received if p == "/api/session/end"]
        check("session/end 호출", len(ends) == 1, f"실제={len(ends)}")

        print("\n=== 10. 환경변수 처리 (겪은 버그) ===")
        # 플러그인 매니페스트가 미설정 변수를 넘기면 값이 '${WIKILENS_SERVER}'
        # 리터럴로 들어와 URL 조립이 깨졌다 ('unknown url type'). 기본값으로
        # 떨어져야 한다.
        env_lit = {**os.environ,
                   "WIKILENS_SERVER": "${WIKILENS_SERVER}",
                   "WIKILENS_USER": "alice@corp"}
        p2 = subprocess.Popen(
            [sys.executable, "-u", str(PROXY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env_lit, bufsize=1)
        try:
            rpc(p2, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "t", "version": "1"}}})
            r = rpc(p2, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "search", "arguments": {"query": "x"}}})
            text = r["result"]["content"][0]["text"]
            # 기본값(127.0.0.1:8787)이 적용되면 서버가 떠 있든 아니든 URL 조립은 성공한다.
            # 즉 정상 응답이거나 연결 실패이지, 'unknown url type' 은 아니어야 한다.
            check("확장 안 된 ${VAR} 를 기본값으로 대체", "unknown url type" not in text, text[:60])
        finally:
            if p2.poll() is None:
                p2.stdin.close(); p2.kill()

        # WIKILENS_USER 가 없으면 ACL 이 fail-closed 라 결과가 항상 비는데,
        # 그것을 "문서 없음"으로 오해하면 원인을 못 찾는다.
        env_nouser = {k: v for k, v in os.environ.items() if k != "WIKILENS_USER"}
        env_nouser["WIKILENS_SERVER"] = f"http://127.0.0.1:{PORT}"
        p3 = subprocess.Popen(
            [sys.executable, "-u", str(PROXY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env_nouser, bufsize=1)
        try:
            rpc(p3, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "t", "version": "1"}}})
            r = rpc(p3, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "search", "arguments": {"query": "x"}}})
            check("WIKILENS_USER 누락을 오류로 알림", r["result"].get("isError") is True)
            check("무엇을 하라는 안내 포함",
                  "WIKILENS_USER" in r["result"]["content"][0]["text"])
        finally:
            if p3.poll() is None:
                p3.stdin.close(); p3.kill()

    finally:
        if proc.poll() is None:
            proc.kill()
        srv.shutdown()

    print("\n" + "=" * 46)
    print(f"통과 {ok} · 실패 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
