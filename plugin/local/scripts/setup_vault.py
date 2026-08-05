#!/usr/bin/env python3
"""
로컬판 설정 보조. **아무것도 실행하지 않고, 결정과 기록만 한다.**

`pip install` 도 `sync` 도 여기서 돌리지 않는다 — 수천 건의 Confluence 요청과 수십 MB
쓰기는 사용자가 승낙한 뒤 커맨드가 직접 실행해야 한다. 이 스크립트가 하는 일은
"어디에 무엇을 쓸지 정하고 그걸 config 에 남기는 것"뿐이다.

표준 라이브러리만 쓴다(`vault_status.py` 와 같은 이유).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_status import CONFIG_DIR, CONFIG_PATH, DEFAULT_VAULT, _config, cli_source  # noqa: E402


def write_config(vault: Path | None, source: str | None) -> dict:
    cfg = _config()
    if vault is not None:
        cfg["vault"] = str(vault)
    if source:
        cfg["cli_source"] = source
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return cfg


def register_permissions(vault: Path) -> str:
    """
    볼트를 전역 설정의 접근 허용 디렉터리에 등록한다.

    볼트는 어느 프로젝트 밖에 있으므로, 등록하지 않으면 프로젝트마다 읽기 승인을
    다시 받아야 한다("어디서든 검색"이 "어디서든 승인 후 검색"이 된다).

    **전역 설정을 건드리므로 반드시 사용자 승낙 후에만 호출할 것.**
    """
    path = Path.home() / ".claude" / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError) as e:
        return f"실패: {path} 를 읽을 수 없습니다 ({e}). 직접 등록하세요."

    perms = settings.setdefault("permissions", {})
    dirs = perms.setdefault("additionalDirectories", [])
    target = str(vault)
    if target in dirs:
        return f"이미 등록돼 있습니다: {target}"

    dirs.append(target)
    # 기존 설정을 통째로 날리지 않도록 임시 파일에 쓴 뒤 교체한다.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return f"등록했습니다: {target} → {path}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="로컬판 볼트 설정을 기록합니다 (실행은 하지 않습니다)."
    )
    ap.add_argument("--vault", help=f"볼트 경로. 생략하면 {DEFAULT_VAULT}")
    ap.add_argument("--cli-source", help="pip install 대상 (경로 또는 git+URL)")
    ap.add_argument("--register-permissions", action="store_true",
                    help="볼트를 ~/.claude/settings.json 의 허용 디렉터리에 추가 (사용자 승낙 후에만)")
    ap.add_argument("--show", action="store_true", help="현재 설정만 출력")
    args = ap.parse_args(argv)

    if args.show:
        print(json.dumps(_config(), ensure_ascii=False, indent=2) or "{}")
        return 0

    # --vault 를 안 주면 이미 기록된 볼트를 그대로 쓴다. 기본 경로로 되돌리면
    # `--register-permissions` 만 다시 호출했을 때 엉뚱한 경로가 등록된다.
    existing = _config()
    if args.vault:
        vault = Path(args.vault).expanduser().resolve()
    elif existing.get("vault"):
        vault = Path(existing["vault"]).expanduser().resolve()
    else:
        vault = DEFAULT_VAULT
    source = args.cli_source or cli_source(existing)

    cfg = write_config(vault, source)
    print(f"CONFIG={CONFIG_PATH}")
    print(f"VAULT={cfg.get('vault')}")
    print(f"CLI_SOURCE={cfg.get('cli_source') or '(미정 — 사내 git URL 이 필요합니다)'}")

    if args.register_permissions:
        print(register_permissions(vault))
    else:
        print("PERMISSIONS=미등록 (--register-permissions 로 등록, 전역 설정 변경이라 승낙 필요)")

    if not vault.exists():
        print(f"\n볼트가 아직 없습니다. 다음: wikilens --root {vault} sync --space <KEY>")
        print("  (--root 는 서브커맨드 **앞**에 와야 합니다. sync 가 build 까지 한 번에 합니다.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
