#!/usr/bin/env bash
#
# 자격증명을 싣고 CLI 를 실행한다. 볼트를 만들거나 갱신하는 모든 경로가 여기를 지난다.
#
# 이게 없으면 `/wikilens-local:sync` 는 **Claude Code 안에서 동작하지 않는다.**
# 자격증명이 환경변수 전용이라(`auth.py`, `sync.py`), Claude Code 를 띄운 셸에
# export 가 없으면 `CONFLUENCE_URL 환경변수가 필요합니다` 로 죽는다. 사용자가
# 매번 자기 터미널에서 수동으로 싱크하게 되는 원인이었다(2026-08-05 실측).
#
# 볼트 경로가 env 를 버리고 `~/.wikilens/config.json` 으로 간 것과 같은 이유다 —
# export 는 그 셸에서만 살고 다음 세션엔 없다. 자격증명만 그 교훈에서 빠져 있었다.
#
# env.sh 를 config.json 이 아니라 **셸 스크립트**로 두는 이유: 사용자가 자기
# 터미널에서 `source ~/.wikilens/env.sh` 로 그대로 재사용할 수 있다. 수동 싱크를
# 계속 하게 되므로 그쪽 경로도 같은 파일 하나로 덮인다.
set -euo pipefail

ENV_FILE="${WIKILENS_ENV:-$HOME/.wikilens/env.sh}"
if [ -f "$ENV_FILE" ]; then
  # 이미 export 된 값이 있으면 그쪽이 이긴다 — 일회성 재정의를 파일이 덮으면 안 된다.
  _url="${CONFLUENCE_URL:-}"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  [ -n "$_url" ] && export CONFLUENCE_URL="$_url"
fi

if command -v wikilens >/dev/null 2>&1; then
  exec wikilens "$@"
fi

# 설치는 됐는데 콘솔 스크립트가 PATH 에 없는 경우.
#
# 프로브를 `/` 에서 돌리는 이유: 파이썬은 cwd 를 sys.path 에 넣으므로, 이 저장소 안에서
# 실행하면 `cli/wikilens/` 소스 디렉터리를 보고 "설치됨"으로 오판한다. 그러면 의존성이
# 없는 인터프리터로 실행돼 `No module named 'bs4'` 로 죽는다(테스트로 잡은 실제 오판).
# `wikilens` 가 아니라 `wikilens.cli` 를 import 하는 것도 의도적이다 — 의존성까지
# 딸려 들어오므로 "import 는 되는데 실행은 안 되는" 상태를 걸러낸다.
if (cd / && python3 -c 'import wikilens.cli') >/dev/null 2>&1; then
  # 같은 이유로 실행 때도 cwd 를 sys.path 에서 뺀다(3.11+; 그 이하에서는 무시된다).
  PYTHONSAFEPATH=1 exec python3 -m wikilens.cli "$@"
fi

echo "wikilens CLI 를 찾을 수 없습니다. /wikilens-local:setup 을 실행하세요." >&2
exit 127
