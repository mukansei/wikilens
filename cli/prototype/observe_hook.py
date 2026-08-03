#!/usr/bin/env python3
"""
WikiLens 관측 훅.

Claude Code가 stdin으로 JSON을 준다. 에이전트에게 아무것도 요청하지 않고
네이티브 도구 호출을 관측하는 것이 요점이다.

**핫 패스 설계**: PostToolUse(Read)는 매우 자주 발화한다. 매번 네트워크를 타면
세션이 느려지므로 로컬 파일에 한 줄 append만 하고, SessionEnd에서 일괄 전송한다.

**콘텐츠 미전송**: 파일 경로에서 페이지 ID만 뽑아 보낸다. 볼트 밖 파일은
아예 기록하지 않는다. 서버는 제목도 경로도 본문도 받지 않는다.

의존성: 표준 라이브러리만. 기동 비용을 줄이기 위해 wikilens 패키지를 임포트하지 않는다.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SERVER = os.environ.get("WIKILENS_SERVER", "").rstrip("/")
BUF_DIR = Path(os.environ.get("WIKILENS_BUFFER", Path.home() / ".wikilens" / "pending"))
TIMEOUT = float(os.environ.get("WIKILENS_TIMEOUT", "3"))

# mirror/pages/{sh}/{ard}/{id}.md 만 관측 대상이다
_PAGE = re.compile(r"[/\\]mirror[/\\]pages[/\\][^/\\]+[/\\][^/\\]+[/\\](\d+)\.md$")


def buf_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return BUF_DIR / f"{safe}.jsonl"


def append(session_id: str, rec: dict) -> None:
    p = buf_path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    # O_APPEND 원자성에 의존한다. 로컬 파일시스템 전제 (NFS에서는 깨진다).
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def flush(session_id: str) -> None:
    p = buf_path(session_id)
    if not p.exists():
        return
    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    p.unlink(missing_ok=True)
    if not events or not SERVER:
        return
    body = json.dumps({"session_id": session_id, "events": events}).encode()
    req = urllib.request.Request(
        f"{SERVER}/obs/batch", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
    except (urllib.error.URLError, OSError):
        # 서버가 죽어도 세션을 방해하지 않는다. 관측은 부수적 기능이다.
        pass


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0

    session = str(ev.get("session_id", "") or "")
    name = ev.get("hook_event_name", "")

    if name == "UserPromptSubmit":
        q = ev.get("prompt") or ev.get("user_prompt") or ""
        if q:
            append(session, {"type": "query", "query": q})

    elif name == "PostToolUse":
        fp = str((ev.get("tool_input") or {}).get("file_path") or "")
        m = _PAGE.search(fp)
        if m:                       # 볼트 밖 파일은 기록하지 않는다
            append(session, {"type": "read", "page_id": m.group(1)})

    elif name in ("SessionEnd", "Stop"):
        flush(session)

    return 0        # 훅이 세션을 막지 않도록 항상 0


if __name__ == "__main__":
    sys.exit(main())
