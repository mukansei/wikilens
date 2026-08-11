"""
자격증명 해석. **환경변수 → `~/.wikilens/env.sh` 폴백** 하나로 통일한다.

CLI 는 원래 환경변수만 읽었다. 그래서 `export` 가 없는 환경에서는 전부 죽었다:

  - **Claude Code 안** — 로컬판 `/wikilens-local:sync` 가 한 번도 동작한 적이 없었다.
    검색은 파일만 읽으니 잘 되고 갱신만 조용히 죽어서, 사용자는 정상인 줄 알고
    자기 터미널에서 수동 싱크를 하고 있었다(2026-08-05 실측). 래퍼
    (`plugin/local/scripts/wikilens_cli.sh`)가 `source` 해서 막았다.
  - **cron** — 서버판 자동 싱크가 같은 실패를 그대로 갖고 있었다. 문서 네 곳이
    `wikilens sync ... && curl -XPOST .../admin/reindex` 를 안내하는데, cron 은 환경이
    최소라 `CONFLUENCE_URL 환경변수가 필요합니다` 로 죽는다(실측: `env -i` 로 재현).
    `&&` 덕에 절반 반영은 막히지만 **볼트가 낡아가는 것을 알 방법이 없다.**

로컬판만 래퍼로 막고 서버판은 안 막힌 상태였다. 여기서 읽으면 둘 다 사라지고,
자기 터미널에서 `source` 없이 쓰는 경로도 함께 덮인다.

**환경변수가 이긴다.** 파일이 일회성 재정의(토큰 교체 등)를 덮으면 낡은 자격증명으로
조용히 인증한다 — 래퍼가 `export -p` 로 지키는 것과 같은 우선순위다.

`env.sh` 를 JSON 으로 바꾸지 않는 이유는 D10 에 있다: 사용자가 자기 터미널에서
`source ~/.wikilens/env.sh` 로 그대로 재사용할 수 있어야 수동 싱크 경로까지 한 파일로
덮인다. 그래서 여기서는 **셸 스크립트의 부분집합**(`export K=V` 한 줄)만 읽는다 —
그 이상은 `source` 의 일이고, 이 폴백은 정상 케이스를 덮는 것이 목적이다.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

#: 비밀 아닌 설정은 `config.json`, 토큰류는 여기. 근거는 `DECISIONS.md` D10.
CONFIG_DIR = Path.home() / ".wikilens"
ENV_PATH = CONFIG_DIR / "env.sh"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_VAULT = CONFIG_DIR / "vault"


def vault_root() -> Path:
    """
    볼트 자리. **명시 `--root` > `config.json` 의 `vault` > `~/.wikilens/vault`.**

    CLI 가 `config.json` 을 읽는 이유는 그것이 **정본**이기 때문이다 — 서버(Kotlin)도,
    MCP 프록시도, 로컬판 진단도 같은 파일을 읽는데 **CLI 만 안 읽고 있었다.** 그래서
    래퍼(`wikilens_cli.sh`)가 `--root` 를 대신 채우는 장치를 갖고 있었고, 그 장치가
    없는 경로(서버 운영자가 CLI 를 직접 부르는 경우)는 `--root` 를 손으로 줘야 했다.

    기본값이 `.`(현재 디렉터리)였던 것도 위험했다 — 저장소 안에서 `sync` 를 실수로
    돌리면 **거기에 볼트가 생긴다.** 자리를 정해두면 그 실수가 성립하지 않는다.
    """
    from_cfg = ""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(cfg, dict):
            v = cfg.get("vault")
            # 값 타입까지 본다 — `{"vault": 123}` 이면 `Path()` 가 TypeError 를 낸다.
            from_cfg = v.strip() if isinstance(v, str) else ""
    except (OSError, ValueError):
        pass
    return Path(from_cfg).expanduser() if from_cfg else DEFAULT_VAULT

#: `export KEY=VALUE` 또는 `KEY=VALUE`. 값은 shlex 가 따옴표를 푼다.
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$")

#: 이 접두사만 읽는다. env.sh 에 다른 것이 있어도 프로세스 환경을 오염시키지 않는다.
_PREFIXES = ("CONFLUENCE_", "IAM_")


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if not m or not m.group(1).startswith(_PREFIXES):
            continue
        try:
            parts = shlex.split(m.group(2), comments=True)
        except ValueError:      # 따옴표가 안 닫힘 — 그 줄만 건너뛴다
            continue
        val = parts[0] if parts else ""
        # setup 템플릿의 `<발급받은 PAT를 여기에>` 를 값으로 넘기면 인증이 엉뚱한
        # 에러로 죽는다. 안 채운 것은 **없는 것**으로 취급해 "필요합니다" 로 안내한다.
        # (값에 공백이 있어 shlex 가 쪼개므로 첫 조각의 시작 문자로 본다.)
        if val.startswith("<"):
            continue
        # `KEY=` 는 빈 문자열이 유효한 값이다. `CONFLUENCE_PREFIX=""` 는
        # "Server/DC 라 접두사 없음"이지 미설정이 아니다.
        out[m.group(1)] = val
    return out


def from_file(path: Path | None = None) -> dict[str, str]:
    """`env.sh` 에 적힌 자격증명. 없거나 못 읽으면 빈 dict."""
    p = path or ENV_PATH
    try:
        return _parse(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


def get(name: str, path: Path | None = None) -> str | None:
    """
    자격증명 하나. **환경변수가 파일보다 우선한다.**

    `os.environ.get` 처럼 "미설정"(None)과 "빈 문자열"을 구분해 돌려준다 —
    `CONFLUENCE_PREFIX` 가 그 구분에 의존한다.
    """
    if name in os.environ:
        return os.environ[name]
    return from_file(path).get(name)


def source() -> str:
    """자격증명이 어디서 왔는지: `env` · `file` · `none`. 진단용."""
    if any(k in os.environ for k in ("CONFLUENCE_URL", "IAM_TOKEN_URL")):
        return "env"
    f = from_file()
    return "file" if ("CONFLUENCE_URL" in f or "IAM_TOKEN_URL" in f) else "none"
