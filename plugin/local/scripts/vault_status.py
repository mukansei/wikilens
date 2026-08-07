#!/usr/bin/env python3
"""
볼트 위치와 상태를 한 번에 해석한다.

스킬이 매번 `$HOME` 확장·볼트 존재·싱크 여부·build 여부를 스스로 추론하면 실패 모드가
넷으로 갈리고 각각 다르게 실패한다. 그 판정을 여기 한 곳에 모아 결정적으로 만든다.
스킬은 이 출력의 `VAULT=` 값을 이후 모든 Grep/Read 경로 앞에 붙이기만 하면 된다.

`STRAY=` 는 샤딩 규칙을 벗어난 파일 수다. 검색을 막지는 않으므로 `STATUS` 와
종료코드에는 영향을 주지 않는다 — `find_strays()` 의 주석 참고.

**표준 라이브러리만 쓴다.** 로컬판의 정의적 성질이 "검색 경로 런타임 의존성 0"이라,
여기에 requests 같은 게 들어오면 볼트 검색이 파이썬 환경 문제로 실패할 수 있게 된다.
네트워크도 건드리지 않는다 — 이 스크립트는 디스크만 본다.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# 이 일수를 넘기면 stale. 위키는 매일 조금씩 바뀌므로 일주일이면 눈에 띄게 낡는다.
STALE_DAYS = 7

CONFIG_DIR = Path.home() / ".wikilens"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_VAULT = CONFIG_DIR / "vault"
ENV_PATH = CONFIG_DIR / "env.sh"

# CLI 를 **정해진 자리** 하나에 설치한다. 예전에는 어디에 설치될지 몰라서
# (`pip install` 이 PATH 로 갈지 venv 로 갈지 pipx 로 갈지) 그 불확실성 하나를 다루는
# 장치가 넷이었다 — `--cli-path auto` · `discover_cli()` · `config.json` 의 `cli` 키 ·
# 문서 세 곳의 "설치했는데도 못 찾음" 안내. 자리를 고정하면 그 넷이 한 줄이 된다.
#
# 볼트·설정과 같은 디렉터리를 쓰는 것도 의도다 — 로컬판을 지우는 방법이
# `rm -rf ~/.wikilens` 하나로 유지된다.
VENV_CLI = CONFIG_DIR / "venv" / "bin" / "wikilens"

# `cli/wikilens/layout.py` 의 값과 **반드시 같아야 한다.** 여기서 다시 정의하는 이유는
# 로컬판이 CLI 없이도 동작해야 해서다(CLI 는 볼트 구축에만 필요하고, 검색·진단에는
# 없을 수 있다). 계약 검사가 두 값의 일치를 강제한다.
#
# ID 의 **뒤**를 쓴다 — 앞자리는 엔트로피가 낮아 뭉친다. 실측 근거는 layout.py 주석.
SHARD_DEPTH = 1
SHARD_WIDTH = 2

# mirror 하위 디렉터리별로 확장자가 정해져 있다. 확장자까지 대조하므로
# `raw/` 에 `.md` 가 섞여 들어간 것도 잡힌다.
MIRROR_DIRS = {"raw": ".xhtml", "pages": ".md", "structure": ".json"}

# 이상 파일이 수백 개면 목록을 다 뱉어봐야 읽히지 않는다. 개수는 전부 세고 경로만 자른다.
STRAY_REPORT_CAP = 20


def _config() -> dict:
    """
    설정. **어떤 내용이 들어 있어도 dict 를 돌려준다.**

    파싱만 확인하면 부족하다 — `null`·`[]`·`"문자열"` 은 전부 **유효한 JSON** 이라
    통과한 뒤 `cfg.get(...)` 에서 `AttributeError` 로 터진다. 그러면 이 스크립트가
    통째로 죽고, 스킬은 `VAULT=` 대신 traceback 을 받는다 — **검색이 아예 안 되는데
    스킬에는 그 분기가 없다.** 손으로 고치는 파일이라 이런 내용이 실제로 들어온다.
    """
    if not CONFIG_PATH.exists():
        return {}
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def quarantine_unusable_config() -> str:
    """
    쓸 수 없는 `config.json` 을 옆으로 치우고 그 경로를 반환한다(없으면 빈 문자열).

    "쓸 수 없다" = 파싱이 안 되거나 **dict 가 아니거나**. 후자를 빼먹으면 `[]` 같은
    파일이 검사를 통과한 뒤 그대로 덮여 사라진다 — `null`·`[]`·`"문자열"` 은 전부
    유효한 JSON 이다.

    **쓰기 전에 반드시 부를 것.** `_config()` 는 깨진 파일을 `{}` 로 돌려주는데, 그
    빈 dict 위에 새 값을 얹어 저장하면 **원본이 통째로 사라진다.** 실측: `vault` 와
    `cli` 가 든 파일에 쉼표 하나가 잘못 들어가 있었더니 `--configure` 한 번에 둘 다
    없어지고 "설정했습니다" 라고 보고했다.

    이 파일은 **사람이 손으로 고치는 파일**이라 깨져 있는 것이 예외가 아니라 흔한
    경우다. 게다가 두 판이 같은 파일을 공유하므로, 한쪽이 지우면 다른 판의 설정이
    날아간다. 지우지 않고 치워두면 사용자가 되살릴 수 있다.
    """
    if not CONFIG_PATH.exists():
        return ""
    try:
        # **dict 인지까지 본다.** `[]` 는 파싱은 되지만 설정이 아니고, 그 위에 얹어
        # 쓰면 원본이 사라지는 것은 깨진 파일과 똑같다.
        if isinstance(json.loads(CONFIG_PATH.read_text(encoding="utf-8")), dict):
            return ""
    except (json.JSONDecodeError, OSError):
        pass
    backup = CONFIG_PATH.with_name(
        f"{CONFIG_PATH.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    try:
        CONFIG_PATH.replace(backup)
    except OSError:
        return ""
    return str(backup)


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


def cli_argv(cfg: dict | None = None) -> list[str]:
    """
    볼트를 만들 CLI 를 어떻게 실행할지 정한다. 검색에는 필요 없고 setup/sync 에만 쓴다.

    **여기가 유일한 해석처다.** `wikilens_cli.sh` 도 자기 힘으로 찾지 않고 이 함수의
    결과를 받아 쓴다 — 둘이 각자 찾으면 스킬은 "CLI 있음"이라 하고 래퍼는 못 찾는
    상태가 생긴다.

    순서는 **명시 > 정해진 자리 > PATH > 모듈**이다.

    `config.json` 의 `cli` 가 가장 먼저인 이유는 그것이 사용자가 직접 적은 값이라서다.
    그다음이 [VENV_CLI] — setup 이 CLI 를 거기 설치하므로 정상 경로는 여기서 끝난다.
    PATH 와 모듈은 그 자리를 안 쓰고 손으로 설치한 경우를 위한 폴백이다. venv·pipx 에
    설치하면 그 셸을 활성화하지 않는 한 PATH 에도 없고 기본 python 으로 import 도 안
    되는데, Claude Code 가 띄우는 셸이 바로 그런 셸이다(실측: 이 저장소의 `cli/.venv` 에
    설치했더니 래퍼가 CLI 를 못 찾았다).
    """
    cfg = _config() if cfg is None else cfg

    explicit = cfg.get("cli")
    if isinstance(explicit, str) and explicit:
        p = Path(explicit).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return [str(p)]

    if VENV_CLI.is_file() and os.access(VENV_CLI, os.X_OK):
        return [str(VENV_CLI)]

    exe = shutil.which("wikilens")
    if exe:
        return [exe]

    # 설치는 됐는데 콘솔 스크립트가 PATH 에 없는 경우. cwd 를 sys.path 에서 빼고 본다 —
    # 이 저장소 안에서 돌리면 `cli/wikilens/` 소스 디렉터리를 보고 "설치됨"으로 오판한다.
    try:
        import importlib.util
        saved = sys.path[:]
        sys.path = [q for q in sys.path if q not in ("", ".", os.getcwd())]
        try:
            found = importlib.util.find_spec("wikilens") is not None
        finally:
            sys.path = saved
        if found:
            return [sys.executable, "-m", "wikilens.cli"]
    except (ImportError, ValueError):
        pass
    return []


def find_cli(cfg: dict) -> str:
    """사람이 읽는 한 줄 표현. 분기 판단은 `cli_argv()` 를 쓸 것."""
    return " ".join(cli_argv(cfg))


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


#: `export KEY=VALUE` 또는 `KEY=VALUE`. CLI 의 `credentials._LINE` 과 같은 규칙이다.
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$")

#: setup 이 출력하는 템플릿의 자리표시자(`<발급받은 PAT를 여기에>`). 그대로면 안 채운 것이다.
#: 값에 공백이 있어 shlex 가 쪼개므로 **첫 조각의 시작 문자**로 판정한다.
_PLACEHOLDER_START = "<"


def _file_creds() -> dict[str, str]:
    """`env.sh` 에서 **실제로 값이 채워진** 자격증명만."""
    out: dict[str, str] = {}
    try:
        text = ENV_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if not m or not m.group(1).startswith(("CONFLUENCE_", "IAM_")):
            continue
        try:
            parts = shlex.split(m.group(2), comments=True)
        except ValueError:
            continue
        val = parts[0] if parts else ""
        # `<발급받은 PAT를 여기에>` 는 값이 아니다 — 채워진 것으로 세면 안 된다.
        if val.startswith(_PLACEHOLDER_START):
            continue
        out[m.group(1)] = val
    return out


def creds_state() -> str:
    """
    싱크에 쓸 Confluence 자격증명이 어디서 오는지.

    `file` 이어야 다음 세션에도 `/wikilens-local:sync` 가 동작한다. `shell` 은 지금
    이 셸에만 있다는 뜻이라, Claude Code 를 재시작하면 사라진다 — 검색은 되는데
    갱신만 조용히 안 되는 상태가 되므로 구분해서 알린다.

    **`partial` 이 있는 이유:** 예전에는 파일 **존재만** 보고 `file` 이라 했다. 그런데
    setup 절차가 정확히 그 구멍을 만든다 — 템플릿을 출력해 사용자가 실행하면 파일은
    생기지만 토큰 자리는 `<발급받은 PAT를 여기에>` 다. 편집을 잊고 다시 물어보면
    스킬이 "설정 끝났다"로 판단해 싱크를 제안하고, 그것이 인증 실패로 죽는다.
    """
    f = _file_creds()
    has_url = "CONFLUENCE_URL" in f or "IAM_TOKEN_URL" in f
    has_secret = any(k in f for k in ("CONFLUENCE_TOKEN", "CONFLUENCE_HEADERS", "IAM_CLIENT_SECRET"))
    if has_url and has_secret:
        return "file"
    if ENV_PATH.is_file() and (has_url or has_secret):
        return "partial"        # 파일은 있는데 절반만 채워짐
    if os.environ.get("CONFLUENCE_URL"):
        return "shell"
    if ENV_PATH.is_file():
        return "partial"        # 파일은 있는데 아무것도 안 채워짐
    return "none"


def other_plugin() -> str:
    """
    서버판(`wikilens-client`)도 함께 켜져 있는지.

    한때 "둘은 배타적" 이라고 문서에 적었는데 **강제할 수단이 없었다.** 이 머신에도
    둘 다 켜진 채였고 아무 경고도 없었다. 게다가 서버판이 켜져 있으면 그 MCP 도구
    4개는 스킬 선택과 무관하게 **항상** 모델에게 보여서, 로컬판이 이긴다는 규칙은
    애초에 성립할 수 없다.

    그래서 배타성 대신 **우선순위**로 바꿨고(`DECISIONS.md` D13), 이 값은 두 가지에
    쓰인다 — 스킬이 서버판에 양보할지 판단하는 근거, 그리고 정리하고 싶은 사용자에게
    `claude plugin disable` 을 안내할 자리.

    설정을 못 읽으면 빈 문자열이다. 진단 한 줄 때문에 검색이 막히면 안 된다.
    """
    try:
        settings = json.loads((Path.home() / ".claude" / "settings.json").read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    enabled = settings.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return ""
    for name, on in enabled.items():
        if on and name.split("@")[0] == "wikilens-client":
            return name
    return ""


def _expected_rel(page_id: str, kind: str, ext: str) -> str:
    """`layout.rel_page_path()` 와 같은 규칙. 여기선 세 디렉터리에 일반화했다."""
    n = SHARD_DEPTH * SHARD_WIDTH
    tail = page_id.rjust(n, "0")[-n:]
    seg = "/".join(tail[i * SHARD_WIDTH : (i + 1) * SHARD_WIDTH] for i in range(SHARD_DEPTH))
    return f"{kind}/{seg}/{page_id}{ext}"


def find_strays(vault: Path, cap: int = STRAY_REPORT_CAP) -> tuple[int, list[str]]:
    """
    샤딩 규칙을 벗어난 파일을 찾는다.

    `sync` 는 이런 파일을 **영원히 못 지운다** — 삭제 청소는 `.sync-state.json` 이 아는
    페이지 ID 만 대상으로 하는데, 이 파일들은 애초에 state 에 없기 때문이다.
    실제로 겪은 것: `.DS_Store`, 그리고 ALIASES.md 한 줄을 잘못 쪼개 **링크 텍스트를
    경로로 삼아** 만들어진 0바이트 파일(제목 속 `/` 가 디렉터리까지 만들었다).

    파일명이 곧 페이지 ID 라는 계약을 그대로 판정에 쓴다 — 각 파일의 stem 으로 기대
    경로를 다시 계산해 실제 위치와 대조한다. 그래서 잘못된 샤드에 놓인 파일도 잡힌다.

    전수 스캔이라 볼트 크기에 선형이다(실측: 2,377페이지 / 7,350항목 / 46ms).
    """
    mirror = vault / "mirror"
    strays: list[str] = []
    total = 0
    for kind, ext in MIRROR_DIRS.items():
        base = mirror / kind
        if not base.is_dir():
            continue
        try:
            entries = sorted(base.rglob("*"))
        except OSError:
            continue
        for p in entries:
            try:
                if p.is_dir():
                    continue
            except OSError:
                continue
            rel = p.relative_to(mirror).as_posix()
            if rel != _expected_rel(p.stem, kind, ext):
                total += 1
                if len(strays) < cap:
                    strays.append(rel)
    return total, strays


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

    # 래퍼 전용 경로. 볼트 스캔을 건너뛰고 실행할 argv 만 한 줄에 하나씩 내놓는다
    # (경로에 공백이 있어도 안전하다). 못 찾으면 아무것도 안 찍고 1 을 반환한다.
    if "--cli" in argv:
        parts = cli_argv(cfg)
        for part in parts:
            print(part)
        return 0 if parts else 1

    # 같은 이유의 래퍼 전용 경로. 래퍼가 `--root` 를 자동으로 채우려면 볼트 경로가
    # 필요한데, 스캔 결과 전체를 파싱하게 하면 bash 에서 잘라내야 하고 그 파싱이
    # 두 번째 해석처가 된다. 한 줄만 준다.
    if "--vault-path" in argv:
        print(resolve_vault(cfg))
        return 0

    vault = resolve_vault(cfg)
    info = inspect(vault)
    info["cli"] = find_cli(cfg)
    info["cli_source"] = cli_source(cfg)
    info["creds"] = creds_state()
    info["other"] = other_plugin()

    # 이상 파일은 검색을 막지 않으므로 STATUS 와 종료코드를 건드리지 않는다.
    # -1 은 "검사하지 않음"(볼트가 없거나 --no-scan).
    if info["status"] == "missing" or "--no-scan" in argv:
        info["stray"], stray_paths = -1, []
    else:
        info["stray"], stray_paths = find_strays(vault)
    info["stray_paths"] = stray_paths

    if "--json" in argv:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(f"VAULT={info['vault']}")
        print(f"STATUS={info['status']}")
        print(f"PAGES={info['pages']}")
        print(f"AGE_DAYS={info['age_days']}")
        print(f"CLI={info['cli']}")
        print(f"CLI_SOURCE={info['cli_source']}")
        print(f"CREDS={info['creds']}")
        print(f"STRAY={info['stray']}")
        print(f"OTHER={info['other']}")
        for rel in stray_paths:
            print(f"STRAY_PATH={rel}")

    # 0 = 검색 가능(ok/stale), 2 = 설정 필요. 스킬이 종료코드만 봐도 분기할 수 있다.
    return 0 if info["status"] in ("ok", "stale") else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
