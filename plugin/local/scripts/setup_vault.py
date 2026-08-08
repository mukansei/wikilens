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
import os
import re
import shlex
import sys
from pathlib import Path

# 경로 해석 로직은 vault_status 하나에만 둔다(복제하면 갈라진다). 다만 import 부산물인
# __pycache__ 가 플러그인 디렉터리에 남으면 설치 시 함께 복사되므로 막는다.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_status import (  # noqa: E402
    CONFIG_DIR, CONFIG_PATH, DEFAULT_VAULT, ENV_PATH, VENV_CLI, _config,
    cli_argv, cli_source, quarantine_unusable_config,
)

# env.sh 로 옮길 변수들. `auth.py` 가 읽는 것 전부 + `sync.py` 의 URL·접두사.
CRED_VARS = (
    "CONFLUENCE_URL", "CONFLUENCE_PREFIX", "CONFLUENCE_AUTH",
    "CONFLUENCE_TOKEN", "CONFLUENCE_EMAIL", "CONFLUENCE_HEADERS",
    "IAM_TOKEN_URL", "IAM_CLIENT_ID", "IAM_CLIENT_SECRET", "IAM_SCOPE", "IAM_AUDIENCE",
)


HEADER = ["#!/bin/sh",
          "# WikiLens 자격증명. `source ~/.wikilens/env.sh` 로 직접 쓸 수도 있습니다.",
          ""]


def _ensure_config_dir() -> None:
    """
    `~/.wikilens/` 를 **700 으로** 만든다(이미 있으면 맞춰준다).

    `mkdir()` 만 부르면 umask 기본값(022)이 먹어 755 가 된다. 그러면 `--capture-env`
    로 만든 사람과 템플릿(`umask 077`)으로 만든 사람이 **같은 자리에 다른 권한**을
    갖는다 — 실측으로 확인했다(2026-08-08). 토큰 파일은 600 이라 값 자체는 안전하지만,
    한 경로가 다른 경로보다 약한 상태를 남기면 어느 쪽이 기준인지 알 수 없다.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)


def capture_env() -> tuple[list[str], list[str], str]:
    """
    지금 셸에 export 된 자격증명을 `~/.wikilens/env.sh` 에 **병합**한다.

    반환: (기록한 변수, 파일에만 있어 보존한 변수, 파일 경로)

    값은 환경에서 파일로 곧장 흘러가고 **어디에도 출력되지 않는다** — 기록했다는
    사실과 변수 이름만 알린다.

    **덮어쓰지 않고 병합하는 이유:** 예전에는 파일을 통째로 새로 썼는데, 토큰이
    export 안 된 셸에서 한 번 더 실행하면 **파일에 있던 토큰이 사라졌다**. 문서
    세 곳이 이 명령을 안내하므로 두 번째 실행이 자연스럽게 일어난다. 주석과 사용자가
    직접 추가한 줄도 그대로 둔다 — 남의 파일을 다시 쓰는 쪽이 예외여야 한다.

    `CONFLUENCE_PREFIX` 는 **빈 문자열이 유효한 값**이라 `.get()` 이 아니라 `in` 으로
    판정한다 — `""` 는 "Server/DC 라 접두사가 없다"는 뜻이고 "미설정"(자동 판별)과 다르다.
    이걸 틀리면 인증이 조용히 실패한다.
    """
    present = [k for k in CRED_VARS if k in os.environ]
    if not present:
        return [], [], ""

    existing = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else list(HEADER)

    out, seen = [], set()
    for line in existing:
        key = None
        m = re.match(r"\s*export\s+([A-Z_][A-Z0-9_]*)=", line)
        if m and m.group(1) in present:
            key = m.group(1)
        if key:
            out.append(f"export {key}={shlex.quote(os.environ[key])}")   # 제자리 교체
            seen.add(key)
        else:
            out.append(line)                                             # 주석·타 변수 보존
    out += [f"export {k}={shlex.quote(os.environ[k])}" for k in present if k not in seen]

    kept = sorted({m.group(1) for line in existing
                   if (m := re.match(r"\s*export\s+([A-Z_][A-Z0-9_]*)=", line))} - set(present))

    _ensure_config_dir()
    # 토큰이 들어가므로 만들 때부터 600 이어야 한다 — 쓰고 나서 chmod 하면 그 사이가 열려 있다.
    fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(ENV_PATH, 0o600)   # 이미 있던 파일이면 O_CREAT 모드가 적용되지 않는다
    return present, kept, str(ENV_PATH)


def env_template() -> str:
    """
    자격증명이 셸에 없을 때, 사용자가 **직접** 실행할 명령. 토큰은 사용자만 만진다.

    **`mkdir -p` 가 필수다.** 없으면 `~/.wikilens/` 가 아직 없는 사람에게
    `No such file or directory` 로 죽는다 — 그런데 `CREDS=none` 은 **정의상 처음 쓰는
    사람**이라, 이 명령이 유일하게 실패하는 대상이 정확히 이 명령을 받는 사람이었다.
    개발 중에는 디렉터리가 이미 있어 한 번도 안 드러났고, 저장소를 초기 상태로 되돌려
    진짜 첫 실행을 밟자 바로 나왔다(2026-08-08 실측).

    `umask 077` 을 `mkdir` 앞에 두어 디렉터리도 700 으로 만든다 — 안에 토큰이 든다.

    **경로는 인용한다.** 홈에 공백이 있으면(`/Users/Hyun Woo Park`) 인용 없이는
    `mkdir -p` 가 `Hyun`·`Woo`·`Park` 세 디렉터리를 만든 뒤 리다이렉트가 죽어
    **실패하면서 쓰레기까지 남긴다**(실측). `install_command()` 가 같은 이유로
    `shlex.quote` 를 쓴다 — 사용자에게 건네는 셸 명령은 전부 같은 규칙이다.
    """
    return (
        f"(umask 077; mkdir -p {shlex.quote(str(ENV_PATH.parent))}"
        f" && cat > {shlex.quote(str(ENV_PATH))} <<'EOF'\n"
        "export CONFLUENCE_URL=https://wiki.example.com\n"
        "export CONFLUENCE_TOKEN=<발급받은 PAT를 여기에>\n"
        "# Cloud 는 보통 이메일도 필요합니다 (Server/DC 는 PAT 하나면 됩니다)\n"
        "# export CONFLUENCE_EMAIL=me@example.com\n"
        "# 주소 자동 판별이 게이트웨이 구성에 속으면 접두사를 직접 지정합니다\n"
        "#   Cloud=\"/wiki\" · Server/DC=\"\" (빈 문자열이 유효한 값입니다)\n"
        "# export CONFLUENCE_PREFIX=\"\"\n"
        "EOF\n"
        ")"
    )


def install_command(source: str) -> str:
    """
    CLI 를 **정해진 자리**에 설치하는 명령. 여기서 실행하지 않는다 — pip 는 네트워크를
    쓰므로 사용자 승낙 뒤 커맨드가 실행한다(이 스크립트의 규칙).

    자리를 고정하는 것이 핵심이다. 예전에는 `pip install` 뒤에 **어디에 깔렸는지 찾는
    단계**가 따로 있었고(`--cli-path auto`), 그게 못 찾으면 사용자가 경로를 직접 줘야
    했다. venv 를 우리가 만들면 답이 처음부터 정해져 있어 그 단계 전체가 없어진다.
    """
    venv = VENV_CLI.parent.parent
    return (f"python3 -m venv {shlex.quote(str(venv))} && "
            f"{shlex.quote(str(venv / 'bin' / 'pip'))} install --quiet --upgrade {shlex.quote(source)}")


def write_config(vault: Path | None, source: str | None, cli: str | None = None) -> dict:
    # 깨진 파일 위에 얹어 쓰면 원본이 통째로 사라진다 — 치워두고 알린다.
    if (moved := quarantine_unusable_config()):
        print(f"경고: 기존 {CONFIG_PATH.name} 을 읽지 못해 {moved} 로 옮겼습니다.")
        print("  그 안의 설정(볼트 경로 등)은 새 파일에 없습니다 — 필요하면 손으로 옮기세요.")
    cfg = _config()
    if vault is not None:
        cfg["vault"] = str(vault)
    if source:
        cfg["cli_source"] = source
    if cli:
        cfg["cli"] = cli
    _ensure_config_dir()
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
    ap.add_argument("--cli-path", metavar="PATH",
                    help="wikilens 실행파일 경로를 기록 (정해진 자리 밖에 손으로 설치했을 때)")
    ap.add_argument("--register-permissions", action="store_true",
                    help="볼트를 ~/.claude/settings.json 의 허용 디렉터리에 추가 (사용자 승낙 후에만)")
    ap.add_argument("--capture-env", action="store_true",
                    help="지금 셸에 export 된 Confluence 자격증명을 ~/.wikilens/env.sh 로 옮김")
    ap.add_argument("--show", action="store_true", help="현재 설정만 출력")
    args = ap.parse_args(argv)

    if args.show:
        print(json.dumps(_config(), ensure_ascii=False, indent=2) or "{}")
        return 0

    if args.capture_env:
        captured, kept, path = capture_env()
        if captured:
            print(f"ENV_FILE={path} (600)")
            print("CAPTURED=" + " ".join(captured))
            if kept:
                print("KEPT=" + " ".join(kept) + "   (파일에만 있던 값 — 그대로 둡니다)")
            missing = [k for k in ("CONFLUENCE_URL", "CONFLUENCE_TOKEN")
                       if k not in captured and k not in kept]
            if missing:
                print("\n아직 부족합니다: " + " ".join(missing))
                print("  이 값들이 없으면 sync 가 동작하지 않습니다.")
                return 2
            print("\n이제 다음 세션에도 /wikilens-local:sync 가 동작합니다.")
            return 0
        print("CAPTURED=")
        print("이 셸에 Confluence 자격증명이 없습니다. 아래를 **직접** 실행하세요:\n")
        print(env_template())
        return 2

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

    cli = None
    if args.cli_path:
        p = Path(args.cli_path).expanduser().resolve()
        if not (p.is_file() and os.access(p, os.X_OK)):
            print(f"실패: 실행 가능한 파일이 아닙니다 — {p}")
            return 2
        cli = str(p)

    cfg = write_config(vault, source, cli)
    print(f"CONFIG={CONFIG_PATH}")
    print(f"VAULT={cfg.get('vault')}")
    print(f"CLI_SOURCE={cfg.get('cli_source') or '(미정 — 비공개 git URL 이 필요합니다)'}")

    resolved = cli_argv(cfg)
    print(f"CLI={' '.join(resolved) if resolved else '(미설치)'}")
    if not resolved:
        # 답이 하나뿐이라 조건 분기가 없다 — 자리를 우리가 정했기 때문이다.
        if source:
            print("  설치하려면 (사용자 승낙 후):")
            print(f"    {install_command(source)}")
        else:
            print("  CLI_SOURCE 가 없어 설치 명령을 만들 수 없습니다 — 비공개 git URL 이 필요합니다.")

    if args.register_permissions:
        print(register_permissions(vault))
    else:
        print("PERMISSIONS=미등록 (--register-permissions 로 등록, 전역 설정 변경이라 승낙 필요)")

    if not vault.exists():
        wrapper = Path(__file__).resolve().parent / "wikilens_cli.sh"
        # 사용자가 그대로 붙여넣는 명령이므로 경로를 인용한다 — 플러그인은
        # `~/.claude/plugins/cache/...` 아래 있고 홈에 공백이 있을 수 있다.
        print(f"\n볼트가 아직 없습니다. 다음: bash {shlex.quote(str(wrapper))} sync --space <KEY>")
        print("  (래퍼가 --root 를 위 VAULT 로 채웁니다. sync 가 build 까지 한 번에 합니다.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
