"""MCP 서버 이름이 세 곳에서 같은지 본다.

정본은 `plugin/client/.mcp.json` 의 키다. 나머지 둘이 그것을 따라가야 한다:

    plugin/client/mcp/wikilens_mcp.py    serverInfo.name — 클라이언트가 받는 값
    plugin/local/skills/search/SKILL.md  로컬판이 "서버판이 보이면 양보" 를 판정하는 기준

**셋째가 이 검사의 이유다.** 로컬판 스킬은 서버판 도구 이름을 적어 두고 그것이
보이면 물러난다(D13). 이름이 바뀌었는데 스킬만 옛 이름이면 **그 조건이 영영 거짓이
되어, 두 판이 동시에 켜져 있어도 로컬판이 이긴다** — 겉으로는 둘 다 정상이고 아무
에러가 없다. 실측 2026-08-28: `wiki` → `librarian` 개명 때 정확히 그 상태가 됐다.

`shared_contract.sh` 본문이 아니라 여기 있는 이유는 인용 때문이다 — 계약은 `eval`
을 거치므로 중첩 따옴표가 한 겹 벗겨진다(`badge_versions.py` 와 같은 판단).
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MCP_JSON = ROOT / "plugin" / "client" / ".mcp.json"
PROXY = ROOT / "plugin" / "client" / "mcp" / "wikilens_mcp.py"
SKILL = ROOT / "plugin" / "local" / "skills" / "search" / "SKILL.md"


def main() -> int:
    name = next(iter(json.loads(MCP_JSON.read_text(encoding="utf-8"))["mcpServers"]))

    bad = []
    if f'"name": "{name}"' not in PROXY.read_text(encoding="utf-8"):
        bad.append(f"{PROXY.name} 의 serverInfo 가 '{name}' 이 아님")
    if f"{name}__search" not in SKILL.read_text(encoding="utf-8"):
        bad.append(f"로컬판 SKILL.md 가 '{name}__search' 를 안 가리킴 "
                   "— 두 판이 함께 켜지면 로컬판이 이긴다")

    for line in bad:
        print(f"  {line}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
