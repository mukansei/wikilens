#!/usr/bin/env python3
"""
볼트 위치와 상태를 한 번에 해석한다.

스킬이 매번 `$HOME` 확장·볼트 존재·싱크 여부·build 여부를 스스로 추론하면 실패 모드가
넷으로 갈리고 각각 다르게 실패한다. 그 판정을 여기 한 곳에 모아 결정적으로 만든다.
스킬은 이 출력의 `VAULT=` 값을 이후 모든 Grep/Read 경로 앞에 붙이기만 하면 된다.

**표준 라이브러리만 쓴다.** 로컬판의 정의적 성질이 "검색 경로 런타임 의존성 0"이라,
여기에 requests 같은 게 들어오면 볼트 검색이 파이썬 환경 문제로 실패할 수 있게 된다.
네트워크도 건드리지 않는다 — 이 스크립트는 디스크만 본다.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# 이 일수를 넘기면 stale. 위키는 매일 조금씩 바뀌므로 일주일이면 눈에 띄게 낡는다.
STALE_DAYS = 7

CONFIG_DIR = Path.home() / ".wikilens"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_VAULT = CONFIG_DIR / "vault"


def _config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def resolve_vault(cfg: dict | None = None) -> Path:
    """
    볼트 경로 정본.

    환경변수만으로는 안 된다 — Claude Code 가 띄우는 셸에서 export 해도 다음 세션엔
    없다. 그래서 `~/.wikilens/config.json` 이 진짜 정본이고, 환경변수는 일회성 재정의다.
    (기존에 다른 위치에 볼트를 만들어 둔 사용자는 config 한 줄로 그대로 쓴다.)
    """
    cfg = _config() if cfg is None else cfg
    for raw in (os.environ.get("WIKILENS_VAULT"), cfg.get("vault")):
        if raw:
            return Path(raw).expanduser().resolve()
    return DEFAULT_VAULT


def _parse_cursor(raw: str | None) -> datetime | None:
    """`sync` 는 '%Y-%m-%d %H:%M'(UTC)로 쓴다. ISO 형식도 받아준다."""
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            d = datetime.strptime(raw, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def find_cli(cfg: dict) -> str:
    """
    볼트를 만들 CLI 를 찾는다. 검색에는 필요 없고 setup/sync 에만 필요하다.

    마켓플레이스 등록 정보를 마지막 후보로 쓰는 이유: 이 저장소를 로컬 경로로 등록해
    쓰는 동안에는 그게 CLI 소스를 가리키는 유일한 단서다.
    """
    exe = shutil.which("wikilens")
    if exe:
        return exe

    # 설치는 됐는데 콘솔 스크립트가 PATH 에 없는 경우
    try:
        import importlib.util
        if importlib.util.find_spec("wikilens") is not None:
            return f"{sys.executable} -m wikilens.cli"
    except (ImportError, ValueError):
        pass

    if cfg.get("cli_source"):
        return ""   # 설치 소스는 알지만 아직 설치 안 됨

    known = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"
    try:
        for entry in json.loads(known.read_text(encoding="utf-8")).values():
            loc = entry.get("installLocation")
            if loc and (Path(loc) / "cli" / "pyproject.toml").exists():
                return ""
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return ""


def cli_source(cfg: dict) -> str:
    """`pip install` 에 넘길 대상. 없으면 빈 문자열 — setup 이 사용자에게 물어야 한다."""
    if cfg.get("cli_source"):
        return str(cfg["cli_source"])
    if os.environ.get("WIKILENS_CLI_SOURCE"):
        return os.environ["WIKILENS_CLI_SOURCE"]
    known = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"
    try:
        for entry in json.loads(known.read_text(encoding="utf-8")).values():
            loc = entry.get("installLocation")
            if loc and (Path(loc) / "cli" / "pyproject.toml").exists():
                return str(Path(loc) / "cli")
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return ""


def inspect(vault: Path) -> dict:
    """볼트 상태 판정. 단계가 순서대로라 어디서 멈췄는지가 곧 다음 할 일이다."""
    out: dict[str, object] = {"vault": str(vault), "pages": 0, "age_days": -1}

    if not vault.is_dir():
        out["status"] = "missing"
        return out

    state_path = vault / "mirror" / ".sync-state.json"
    if not state_path.exists():
        out["status"] = "unsynced"
        return out

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 손상된 상태 파일은 "안 받은 것"과 같게 취급한다 — 다시 싱크하면 복구된다.
        out["status"] = "unsynced"
        return out

    out["pages"] = len(state.get("pages") or {})

    if not (vault / "ALIASES.md").exists():
        out["status"] = "unbuilt"
        return out

    cursor = _parse_cursor(state.get("cursor"))
    if cursor:
        out["age_days"] = (datetime.now(timezone.utc) - cursor).days
    out["status"] = "stale" if isinstance(out["age_days"], int) and out["age_days"] > STALE_DAYS else "ok"
    return out


def main(argv: list[str]) -> int:
    cfg = _config()
    info = inspect(resolve_vault(cfg))
    info["cli"] = find_cli(cfg)
    info["cli_source"] = cli_source(cfg)

    if "--json" in argv:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(f"VAULT={info['vault']}")
        print(f"STATUS={info['status']}")
        print(f"PAGES={info['pages']}")
        print(f"AGE_DAYS={info['age_days']}")
        print(f"CLI={info['cli']}")
        print(f"CLI_SOURCE={info['cli_source']}")

    # 0 = 검색 가능(ok/stale), 2 = 설정 필요. 스킬이 종료코드만 봐도 분기할 수 있다.
    return 0 if info["status"] in ("ok", "stale") else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
