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
  # 이미 export 된 값이 있으면 그쪽이 이긴다 — 일회성 재정의(토큰 교체 등)를 파일이
  # 덮으면 낡은 자격증명으로 조용히 인증한다.
  #
  # `export -p` 로 통째로 떠서 소싱 뒤 되돌린다. 변수를 하나씩 나열하면 새 변수를
  # 추가할 때 빠뜨리고, 실제로 그래서 CONFLUENCE_URL 만 보존되고 TOKEN·PREFIX 는
  # 파일이 덮고 있었다. `${!v}` 간접 확장을 안 쓰는 이유는 값이 빈 문자열인 경우
  # ("설정함"과 "미설정"이 다르다 — CONFLUENCE_PREFIX="" 가 Acme 필수 설정이다)
  # 를 구분하기 위해서다. `export -p` 는 빈 값도 선언째 보존한다.
  _pre="$(export -p | grep -E '(CONFLUENCE|IAM)_[A-Z_]+=' || true)"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  [ -n "$_pre" ] && eval "$_pre"
fi

# CLI 위치는 **직접 찾지 않는다.** `vault_status.py` 가 유일한 해석처이고
# (`config.json` 의 `cli` > PATH > 설치된 모듈), 여기서 각자 찾으면 스킬은 "CLI 있음"
# 이라 하는데 래퍼는 못 찾는 상태가 생긴다. 한 줄에 argv 하나씩 받아 공백이 든 경로도
# 안전하게 복원한다.
CLI=()
while IFS= read -r part; do
  [ -n "$part" ] && CLI+=("$part")
done < <(python3 "$(dirname "$0")/vault_status.py" --cli 2>/dev/null)

if [ ${#CLI[@]} -eq 0 ]; then
  echo "wikilens CLI 를 찾을 수 없습니다. /wikilens-local:setup 을 실행하세요." >&2
  echo "  (venv·pipx 에 설치했다면 PATH 에 없을 수 있습니다 —" >&2
  echo "   setup_vault.py --cli-path <경로> 로 실제 경로를 기록하세요.)" >&2
  exit 127
fi

# 모듈 실행으로 떨어졌을 때 cwd 가 sys.path 에 끼면 이 저장소의 `cli/wikilens/` 소스를
# 집어 의존성 없는 인터프리터로 돌게 된다(3.11+ 에서만 유효, 그 이하에서는 무시된다).
PYTHONSAFEPATH=1 exec "${CLI[@]}" "$@"
