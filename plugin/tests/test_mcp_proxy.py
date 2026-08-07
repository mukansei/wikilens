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

PROXY = Path(__file__).resolve().parents[2] / "plugin" / "client" / "mcp" / "wikilens_mcp.py"
PORT = 8899

received: list[tuple[str, dict]] = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 조용히
        pass

    def _json(self, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        # `--status` 가 쓰는 진단 엔드포인트. 서버엔 원래 있었는데 플러그인이 안 썼다.
        if self.path == "/api/health":
            self._json({"ok": True})
        elif self.path == "/api/stats":
            self._json({"indexedDocs": 2378, "aclUsers": 3})
        else:
            self.send_response(404); self.end_headers()

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
    import tempfile
    # 프록시가 `~/.wikilens/config.json` 을 읽으므로 HOME 을 격리하지 않으면 개발자
    # 머신의 설정이 새어 들어와 결과가 기계마다 달라진다 — 특히 "USER 미설정" 검사가
    # 그 파일의 "user" 때문에 조용히 통과해버린다.
    HOME = tempfile.mkdtemp(prefix="wl-proxy-home-")
    env = {**os.environ, "HOME": HOME,
           "WIKILENS_SERVER": f"http://127.0.0.1:{PORT}",
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
        check("serverInfo", r["result"]["serverInfo"]["name"] == "wiki")

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
        env_nouser["HOME"] = HOME
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

        # --- 11. 설정 지속성 (겪은 버그) ---------------------------------
        #
        # 설정이 환경변수 전용이라, Claude Code 를 앱으로 띄우면 환경이 비어 서버
        # 주소가 조용히 localhost 가 되고 모든 검색이 빈 결과가 됐다.
        print("\n=== 11. config.json 설정 (겪은 버그) ===")
        import pathlib
        cfg_home = tempfile.mkdtemp(prefix="wl-proxy-cfg-")
        (pathlib.Path(cfg_home) / ".wikilens").mkdir()
        (pathlib.Path(cfg_home) / ".wikilens" / "config.json").write_text(
            json.dumps({"server": f"http://127.0.0.1:{PORT}", "user": "bob@corp"}),
            encoding="utf-8")

        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith("WIKILENS_")}
        clean["HOME"] = cfg_home
        p4 = subprocess.Popen(
            [sys.executable, "-u", str(PROXY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=clean, bufsize=1)
        try:
            rpc(p4, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "t", "version": "1"}}})
            before = len(received)
            r = rpc(p4, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "search", "arguments": {"query": "로그인"}}})
            check("환경변수 없이 config.json 만으로 동작", r["result"].get("isError") is not True,
                  r["result"]["content"][0]["text"][:80])
            sent = received[before:]
            check("config 의 user 가 요청에 실림",
                  bool(sent) and sent[0][1].get("userKey") == "bob@corp")
        finally:
            if p4.poll() is None:
                p4.stdin.close(); p4.kill()

        # 환경변수는 일회성 재정의로 남아야 한다 — 파일이 env 를 덮으면 안 된다.
        over = dict(clean, WIKILENS_USER="carol@corp")
        p5 = subprocess.Popen(
            [sys.executable, "-u", str(PROXY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=over, bufsize=1)
        try:
            rpc(p5, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "t", "version": "1"}}})
            before = len(received)
            rpc(p5, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "search", "arguments": {"query": "로그인"}}})
            sent = received[before:]
            check("환경변수가 config 보다 우선",
                  bool(sent) and sent[0][1].get("userKey") == "carol@corp")
        finally:
            if p5.poll() is None:
                p5.stdin.close(); p5.kill()

        # --- 12. --status 진단 -------------------------------------------
        print("\n=== 12. --status 진단 ===")
        st = subprocess.run([sys.executable, str(PROXY), "--status"],
                            capture_output=True, text=True, env=clean)
        check("설정 출처를 밝힘 (env/config/default 구분)", "(config)" in st.stdout, st.stdout[:120])
        check("서버 도달 여부 보고", "REACHABLE=yes" in st.stdout, st.stdout[:120])

        # 설정한 적이 없는 상태는 출처가 default 로 드러나야 한다. (도달 여부는
        # 기본 포트에 무엇이 떠 있느냐에 달려 있으므로 여기서 단정하지 않는다 —
        # 실제로 실서버가 8787 에 떠 있어 한 번 오탐했다.)
        none_home = dict(clean, HOME=tempfile.mkdtemp(prefix="wl-proxy-none-"))
        st2 = subprocess.run([sys.executable, str(PROXY), "--status"],
                             capture_output=True, text=True, env=none_home)
        check("설정 안 한 상태를 default 로 표시", "(default)" in st2.stdout, st2.stdout[:200])
        check("USER 없으면 0 이 아닌 종료코드", st2.returncode != 0, str(st2.returncode))

        # 핵심은 '주소를 안 넣었다'와 '서버가 죽었다'의 구분이다. 주소를 넣어둔 채
        # 죽은 서버를 보면 그 안내가 **나오면 안 된다.**
        dead_home = tempfile.mkdtemp(prefix="wl-proxy-dead-")
        (pathlib.Path(dead_home) / ".wikilens").mkdir()
        (pathlib.Path(dead_home) / ".wikilens" / "config.json").write_text(
            json.dumps({"server": "http://127.0.0.1:1", "user": "bob@corp"}), encoding="utf-8")
        st3 = subprocess.run([sys.executable, str(PROXY), "--status"],
                             capture_output=True, text=True, env=dict(clean, HOME=dead_home))
        check("서버 다운을 도달 실패로 보고", "REACHABLE=no" in st3.stdout, st3.stdout[:200])

        # --- 13. 잘못된 설정에 죽지 않기 (코드 리뷰에서 나온 결함) ------------
        #
        # timeout 을 최상단에서 float() 로 그대로 파싱해, 오타 하나로 MCP 서버가
        # 기동 중 traceback 으로 죽었다. 그리고 JSON 숫자는 문자열이 아니라는 이유로
        # 조용히 무시돼 기본값이 쓰였다.
        print("\n=== 13. 잘못된 설정에 죽지 않기 ===")

        def with_config(payload: dict, *args):
            home = tempfile.mkdtemp(prefix="wl-proxy-cfg2-")
            (pathlib.Path(home) / ".wikilens").mkdir()
            (pathlib.Path(home) / ".wikilens" / "config.json").write_text(
                json.dumps(payload), encoding="utf-8")
            return subprocess.run([sys.executable, str(PROXY), *args],
                                  capture_output=True, text=True,
                                  env=dict(clean, HOME=home))

        bad = with_config({"server": f"http://127.0.0.1:{PORT}", "user": "u",
                           "timeout": "abc"}, "--status")
        check("timeout 오타에 기동이 죽지 않음", "REACHABLE=yes" in bad.stdout, bad.stderr[:150])
        check("잘못된 값임을 stderr 로 알림", "타임아웃" in bad.stderr, bad.stderr[:150])
        check("stdout 은 오염되지 않음 (JSON-RPC 전용)",
              "타임아웃" not in bad.stdout, bad.stdout[:150])

        num = with_config({"server": f"http://127.0.0.1:{PORT}", "user": "u",
                           "timeout": 30}, "--status")
        check("JSON 숫자로 쓴 설정이 무시되지 않음", "REACHABLE=yes" in num.stdout, num.stdout[:150])
        check("주소를 넣은 사용자에겐 '설정한 적 없다' 안내를 안 함",
              "설정한 적이 없어" not in st3.stdout, st3.stdout[:200])

        # --- 14. --configure (코드 리뷰에서 나온 결함 둘) ---------------------
        #
        # 손으로 쓴 JSON 이 조용히 깨지는 것을 막으려고 만든 경로다. 그 도구가
        # 스스로 깨진 값을 쓰면 존재 이유가 사라진다.
        print("\n=== 14. --configure ===")

        def configure(home: str, *args):
            return subprocess.run([sys.executable, str(PROXY), "--configure", *args],
                                  capture_output=True, text=True,
                                  env=dict(clean, HOME=home))

        def cfg_of(home: str) -> dict:
            p = pathlib.Path(home) / ".wikilens" / "config.json"
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

        h = tempfile.mkdtemp(prefix="wl-conf-")
        r14 = configure(h, "--server", "http://wikilens.corp:8787", "--user", "me@corp")
        check("설정을 기록함", cfg_of(h).get("server") == "http://wikilens.corp:8787"
              and cfg_of(h).get("user") == "me@corp", r14.stdout[:150])

        # 로컬판이 쓰는 키를 지우면 두 판을 같이 쓰는 사용자의 볼트 설정이 사라진다.
        h2 = tempfile.mkdtemp(prefix="wl-conf-merge-")
        (pathlib.Path(h2) / ".wikilens").mkdir()
        (pathlib.Path(h2) / ".wikilens" / "config.json").write_text(
            json.dumps({"vault": "/home/me/wiki", "cli": "/x/bin/wikilens"}), encoding="utf-8")
        configure(h2, "--server", "http://s:1", "--user", "u")
        check("로컬판 설정을 보존 (덮어쓰기 아님)",
              cfg_of(h2).get("vault") == "/home/me/wiki" and cfg_of(h2).get("cli") == "/x/bin/wikilens",
              str(cfg_of(h2)))

        # `--server --user me@corp` → server="--user" 를 쓰고 user 를 통째로 잃었다.
        h3 = tempfile.mkdtemp(prefix="wl-conf-missing-")
        r = configure(h3, "--server", "--user", "me@corp")
        check("값을 빠뜨리면 거부 (다음 플래그를 값으로 삼지 않음)",
              r.returncode == 2 and cfg_of(h3) == {}, f"rc={r.returncode} cfg={cfg_of(h3)}")

        # 스킴 없는 주소는 `urlopen` 이 'unknown url type' 으로 죽는다. 유일한 쓰기
        # 경로가 여기이므로 여기서 막으면 그 오류가 도달하지 않는다.
        h4 = tempfile.mkdtemp(prefix="wl-conf-scheme-")
        r = configure(h4, "--server", "wikilens.corp:8787", "--user", "u")
        check("스킴 없는 주소를 거부", r.returncode == 2 and cfg_of(h4) == {},
              f"rc={r.returncode} cfg={cfg_of(h4)}")
        check("무엇을 고칠지 알려줌", "http://" in r.stderr, r.stderr[:150])

        h5 = tempfile.mkdtemp(prefix="wl-conf-eq-")
        configure(h5, "--server=http://s:1", "--user=u")
        check("--키=값 형식도 받음", cfg_of(h5).get("user") == "u", str(cfg_of(h5)))

        # 이 파일은 **사람이 손으로 고치는 파일**이라 깨져 있는 것이 흔하다. `_config()`
        # 가 파싱 실패를 {} 로 돌려주므로 그 위에 얹어 저장하면 로컬판의 vault·cli 가
        # 경고 없이 사라진다 — 실측: 쉼표 하나가 잘못된 파일에 --configure 한 번에
        # 둘 다 소실되고 출력은 "설정했습니다" 였다.
        h6 = tempfile.mkdtemp(prefix="wl-conf-broken-")
        (pathlib.Path(h6) / ".wikilens").mkdir()
        (pathlib.Path(h6) / ".wikilens" / "config.json").write_text(
            '{ "vault": "/home/me/wiki", "cli": "/x/w", }', encoding="utf-8")
        r = configure(h6, "--server", "http://s:1", "--user", "u")
        baks = list((pathlib.Path(h6) / ".wikilens").glob("config.json.bak-*"))
        check("깨진 설정을 지우지 않고 옆에 치워둠", len(baks) == 1, str(baks))
        check("치웠다는 사실을 알림", "옮겼습니다" in r.stdout, r.stdout[:200])
        check("원본 내용이 백업에 살아 있음",
              bool(baks) and "/home/me/wiki" in baks[0].read_text(encoding="utf-8"))
        check("새 설정은 정상 기록", cfg_of(h6).get("user") == "u", str(cfg_of(h6)))

        # `null`·`[]` 는 유효한 JSON 이라 파싱 검사를 통과한 뒤 `_CFG.get()` 에서 터진다.
        # 그 자리가 **모듈 최상단**이라 프록시가 기동 중 죽고 도구 4개가 통째로 사라진다.
        for junk in ("null", "[]", '"hello"', "123"):
            hj = tempfile.mkdtemp(prefix="wl-conf-junk-")
            (pathlib.Path(hj) / ".wikilens").mkdir()
            (pathlib.Path(hj) / ".wikilens" / "config.json").write_text(junk, encoding="utf-8")
            st = subprocess.run([sys.executable, str(PROXY), "--status"],
                                capture_output=True, text=True, env=dict(clean, HOME=hj))
            check(f"dict 아닌 설정({junk})에 기동이 죽지 않음",
                  "SERVER=" in st.stdout, st.stderr[:120])

    finally:
        if proc.poll() is None:
            proc.kill()
        srv.shutdown()

    print("\n" + "=" * 46)
    print(f"통과 {ok} · 실패 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
