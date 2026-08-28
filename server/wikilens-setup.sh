#!/usr/bin/env bash
#
# 서버판 첫 구축. **운영자에게만 안내가 없어서 만들었다.**
#
#   ./server/wikilens-setup.sh
#
# ### 왜 스크립트인가
#
# 두 사용자 갈래에는 `/wikilens-local:setup`·`/wikilens-client:setup` 이 있어서 값을
# 물어보고 대신 해 준다. **운영자만 README 의 네 명령을 손으로 옮겨 적었고**, 값
# 다섯(주소·PAT·스페이스 키·이미지 이름·식별자)을 스스로 알아내야 했다.
# 그 과정에서 나온 것이 아래 셋이고, 전부 여기서 없어진다.
#
#   이미지 이름   `<디렉터리>-wikilens` 라 clone 이름이 다르면 어긋난다. 그때 docker 는
#                 `pull access denied … may require 'docker login'` 이라고 말해서
#                 **인증 문제로 읽힌다**(실측). → `compose config --images` 로 물어본다.
#   스페이스 키   틀리면 0.6초에 종료 코드 0 으로 "싱크 완료" 가 뜨고 볼트 파일까지
#                 생긴다(실측: 받음 0 · 페이지 0건). → `doctor` 로 목록을 보여주고
#                 고르게 하고, 싱크 뒤 **페이지 수를 센다.**
#   확인 단계     "이미지만 있으면 된다" 고 해놓고 저장소 안 파이썬을 부른다.
#                 → 여기서 대신 부른다.
#
# ### 자격증명은 싱크에만 간다
#
# `serve` 컨테이너에는 안 들어간다. 그래야 "위키에 쓰기 금지" 가 규율이 아니라 설계
# 보장으로 남는다(`compose.yml` 첫머리 · `DECISIONS.md` D22). 계약이 검사한다.
set -euo pipefail

cd "$(dirname "$0")/.."          # 저장소 루트. compose.yml 이 여기 있다.

HOME_DIR="${WIKILENS_HOME:-$HOME/.wikilens}"
VAULT="$HOME_DIR/vault"
ENV_SH="${WIKILENS_ENV:-$HOME_DIR/env.sh}"
PORT="${WIKILENS_PORT:-8787}"
SERVER="${WIKILENS_SERVER:-http://localhost:$PORT}"
export WIKILENS_PORT

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 0. 전제 ────────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || fail "docker 가 없습니다. 먼저 설치하세요."
docker compose version >/dev/null 2>&1 || fail "docker compose 가 없습니다(v2 가 필요합니다)."
docker info >/dev/null 2>&1 || fail "docker 데몬이 안 떠 있습니다."

# ── 1. 이미지 ──────────────────────────────────────────────────────────────
# **이름을 추측하지 않는다.** compose 가 붙이는 이름을 compose 에게 묻는다 —
# 프로젝트 이름이 디렉터리에서 오므로 clone 이름이 다르면 이미지 이름도 다르다.
IMAGE=$(docker compose config --images 2>/dev/null | head -1)
[ -n "$IMAGE" ] || fail "compose 에서 이미지 이름을 못 읽었습니다. 저장소 루트가 맞습니까?"

say "1/5  이미지 — $IMAGE"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "  이미 있습니다. 다시 빌드하려면: docker compose build"
else
  echo "  빌드 중입니다 (처음이면 1~2분)…"
  docker compose build || fail "빌드에 실패했습니다."
fi

# ── 2. 자격증명 ────────────────────────────────────────────────────────────
# 읽는 자리를 CLI·refresh.sh 와 같은 곳으로 둔다(`credentials.py`). 환경변수가 이긴다.
say "2/5  Confluence 자격증명"
if [ -f "$ENV_SH" ]; then
  # shellcheck disable=SC1090
  . "$ENV_SH"
  echo "  $ENV_SH 에서 읽었습니다."
fi

if [ -z "${CONFLUENCE_URL:-}" ]; then
  read -r -p "  Confluence 주소 (예: https://회사.atlassian.net): " CONFLUENCE_URL
  [ -n "$CONFLUENCE_URL" ] || fail "주소가 필요합니다."
  read -r -p "  서비스 계정 토큰 (공개 위키면 그냥 엔터): " -s CONFLUENCE_TOKEN; echo
  export CONFLUENCE_URL
  if [ -n "${CONFLUENCE_TOKEN:-}" ]; then
    export CONFLUENCE_TOKEN
  else
    # **자동 폴백을 안 넣는다** — "토큰을 빠뜨림" 과 "공개 위키" 가 겉으로 같아서,
    # 여기서 물어본 것이 곧 명시다(`cli/wikilens/auth.py`).
    export CONFLUENCE_AUTH=none
    echo "  토큰이 없어 익명 읽기로 진행합니다."
  fi
  # 다음 실행과 cron 이 같은 자리를 읽도록 남긴다. 토큰이 들어가므로 600.
  mkdir -p "$HOME_DIR"; chmod 700 "$HOME_DIR" 2>/dev/null || true
  { echo "export CONFLUENCE_URL=$CONFLUENCE_URL"
    [ -n "${CONFLUENCE_TOKEN:-}" ] && echo "export CONFLUENCE_TOKEN=$CONFLUENCE_TOKEN"
    [ -n "${CONFLUENCE_AUTH:-}" ]  && echo "export CONFLUENCE_AUTH=$CONFLUENCE_AUTH"
  } > "$ENV_SH"
  chmod 600 "$ENV_SH"
  echo "  $ENV_SH 에 저장했습니다 (600). cron 도 여기서 읽습니다."
fi

creds=()
for k in CONFLUENCE_URL CONFLUENCE_TOKEN CONFLUENCE_EMAIL CONFLUENCE_AUTH \
         CONFLUENCE_HEADERS CONFLUENCE_PREFIX \
         IAM_TOKEN_URL IAM_CLIENT_ID IAM_CLIENT_SECRET IAM_SCOPE IAM_AUDIENCE; do
  v="${!k-}"
  [ -n "${v:-}" ] && creds+=(-e "$k=$v")
done

# ── 3. 스페이스 고르기 ─────────────────────────────────────────────────────
# **외우게 하지 않는다.** 틀린 키는 조용히 0건으로 끝나므로 목록에서 고르게 한다.
say "3/5  스페이스"
SPACES="${WIKILENS_SPACES:-}"
if [ -z "$SPACES" ]; then
  echo "  연결을 확인하고 목록을 받는 중…"
  docker run --rm "${creds[@]}" "$IMAGE" doctor || fail "연결·인증에 실패했습니다. 위 출력을 보세요."
  read -r -p "  싱크할 스페이스 키 (여럿이면 공백으로): " SPACES
  [ -n "$SPACES" ] || fail "스페이스 키가 필요합니다."
fi

space_args=()
for s in $SPACES; do space_args+=(--space "$s"); done

# **분석기는 색인 시점에 정해진다**(D14) — 나중에 바꾸려면 재색인해야 한다.
# 틀려도 에러가 안 나고 **검색 품질만 조용히 나빠지므로** 여기서 묻는다.
if [ -z "${WIKILENS_ANALYZER:-}" ]; then
  read -r -p "  위키 언어 [1] 한국어(기본) [2] 영어 [3] 그 밖: " ans
  case "${ans:-1}" in
    2) WIKILENS_ANALYZER=english ;;
    3) WIKILENS_ANALYZER=standard ;;
    *) WIKILENS_ANALYZER=korean ;;
  esac
fi
export WIKILENS_ANALYZER
echo "  분석기: $WIKILENS_ANALYZER"

# ── 4. 볼트 ────────────────────────────────────────────────────────────────
say "4/5  볼트 — $VAULT"
mkdir -p "$VAULT"
# **`|| true` 가 없으면 여기서 끝난다.** `find` 는 없는 디렉터리에 1 을 반환하고
# `pipefail` 이 그것을 파이프라인 상태로 올리며 `set -e` 가 대입문의 실패도 잡는다.
# `mirror/` 가 없는 **첫 구축이 정확히 그 경우**라, 실측에서 4단계 제목만 찍고 조용히
# 죽었다(2026-08-27). `refresh.sh` 의 curl 과 같은 실패이고 거기 주석을 적어 놓고도
# 같은 자리에서 또 물렸다.
count_pages() { find "$1/mirror" -name '*.md' 2>/dev/null | wc -l | tr -d ' ' || true; }

before=$(count_pages "$VAULT" || true)
if [ "$before" -gt 0 ]; then
  echo "  이미 $before 건이 있습니다. 증분으로 이어받습니다."
fi

# **마운트 지점과 `--root` 가 같은 줄에서 같은 상수를 쓴다** — 갈리면 싱크가 마운트
# 밖에 쓰고 그게 조용하다(`CLAUDE.md` 조용히 실패 29).
docker run --rm "${creds[@]}" \
  -v "$VAULT":/vault \
  "$IMAGE" sync --root /vault "${space_args[@]}" || fail "싱크에 실패했습니다."

# **"싱크 완료" 와 "페이지가 있다" 는 다르다.** 스페이스 키가 틀리면 받음 0 으로
# 성공하고 볼트 파일까지 생긴다(실측). 여기서 안 잡으면 5단계의 색인 0건으로
# 나타나는데, 그때는 원인 후보가 넷(경로·마운트·키·권한)으로 늘어난다.
# 싱크가 아무것도 못 받으면 `mirror/` 자체가 없을 수 있다 — 그때도 세야 한다.
after=$(count_pages "$VAULT" || true)
if [ "$after" -eq 0 ]; then
  fail "싱크는 끝났는데 페이지가 0건입니다 — 스페이스 키($SPACES)를 확인하세요.
    위 doctor 목록에 있는 키여야 하고, 대소문자도 같아야 합니다."
fi
echo "  페이지 $after 건"

# ── 5. 기동과 확인 ─────────────────────────────────────────────────────────
say "5/5  기동"
WIKILENS_HOME="$HOME_DIR" WIKILENS_ANALYZER="$WIKILENS_ANALYZER" \
  docker compose up -d || fail "기동에 실패했습니다."

echo "  기동을 기다리는 중…"
for _ in $(seq 1 60); do
  curl -sf "$SERVER/api/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -sf "$SERVER/api/health" >/dev/null 2>&1 \
  || fail "서버가 안 떴습니다: docker compose logs 를 보세요."

docker compose logs 2>/dev/null | grep -a "관리 토큰" | head -4 | sed 's/^/  /' || true

# **여기서 초록이 아니면 사용자는 "문서가 없다" 로 봅니다.** 이 시스템의 실패는
# 대부분 에러가 아니라 0건이고, 0건은 문서 부재와 구별되지 않는다.
say "확인"
if command -v python3 >/dev/null 2>&1; then
  WIKILENS_SERVER="$SERVER" WIKILENS_USER="setup@local" \
    python3 plugin/client/mcp/wikilens_mcp.py --status || true
else
  echo "  python3 가 없어 진단을 건너뜁니다 — 다른 머신에서 --status 를 돌려보세요."
fi

# **저장소 주소를 물어본다** — 조직판과 공개판이 다르고, 사용자가 마켓플레이스를
# 등록할 때 필요한 것이 그 주소다. 여기서 안 찍으면 운영자가 따로 찾아야 한다.
# **사용자 이름을 벗긴다.** `git remote` 에 `https://<계정>@github.com/…` 형태가
# 흔한데(자격증명 헬퍼가 붙인다) 그대로 찍으면 **운영자 계정이 사용자에게 전달된다**
# — 실측으로 그렇게 나왔다. `@` 앞을 잘라낸다.
REPO_URL=$(git remote get-url origin 2>/dev/null \
        || git remote get-url "$(git remote 2>/dev/null | head -1)" 2>/dev/null \
        || echo "<이 저장소의 URL>")
REPO_URL=$(printf '%s' "$REPO_URL" | sed -E 's|(https?://)[^/@]+@|\1|')

say "다음"
cat <<EOF
  사용자에게 알릴 것 — Claude Code 안에서 세 줄입니다:

    /plugin marketplace add $REPO_URL
    /plugin install wikilens-client@wikilens
    /wikilens-client:setup          서버 주소 $SERVER · 본인 식별자

  마켓플레이스 등록이 **첫 줄**입니다 — 그게 없으면 install 이 플러그인을 못 찾습니다.
  설치 뒤에는 Claude Code 재시작이 필요합니다(설정을 시작할 때 한 번 읽습니다).

  정기 갱신:  crontab -e
    0 9 * * 1 WIKILENS_IMAGE=$IMAGE $PWD/server/wikilens-refresh.sh $(printf -- '--space %s ' $SPACES)

  권한 시행은 기본이 꺼짐입니다 — 서버에 닿는 전원이 이 계정의 권한 범위를 봅니다.
  켜려면 docs/server-operations.md 를 보세요.
EOF
