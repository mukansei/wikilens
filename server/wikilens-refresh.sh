#!/usr/bin/env bash
#
# 볼트 갱신 → 서버 재색인. **cron 이 부르는 자리이고, 첫 구축에도 같은 것을 쓴다.**
#
#   ./server/wikilens-refresh.sh --space PLATFORM          첫 구축·정기 갱신
#   ./server/wikilens-refresh.sh --space A --space B       여러 스페이스
#   ./server/wikilens-refresh.sh --full --space PLATFORM   전체 재싱크
#
# ### 왜 스크립트인가
#
# 절차가 문서 네 곳에 흩어져 있었고 **`&&` 하나가 빠지면 조용히 반쪽이 된다** —
# 싱크가 실패했는데 재색인하면 못 받은 상태가 그대로 반영된다. 손으로 옮겨 적는
# 대신 여기 한 줄로 둔다.
#
# ### 자격증명은 여기서만 산다
#
# `sync` 컨테이너는 토큰을 받고 **서버 컨테이너는 안 받는다.** 그래야 "위키에 쓰기
# 금지" 가 규율이 아니라 설계 보장으로 남는다(`compose.yml` 첫머리). 계약이 서버
# 서비스에 자격증명이 붙는지 검사한다.
#
# `~/.wikilens/env.sh` 는 `export KEY=VAL` 형식인데 `docker --env-file` 은 그것을
# **거부한다**(`variable 'export FOO' contains whitespaces` — 실측). 그래서 여기서
# `source` 한 뒤 `-e` 로 넘긴다.
set -euo pipefail

IMAGE="${WIKILENS_IMAGE:-wikilens-wikilens}"
VAULT="${WIKILENS_VAULT:-$HOME/.wikilens/vault}"
ENV_SH="${WIKILENS_ENV:-$HOME/.wikilens/env.sh}"
SERVER="${WIKILENS_SERVER:-http://localhost:8787}"

[ $# -gt 0 ] || { echo "사용법: $0 --space <KEY> [--space <KEY>…] [--full]" >&2; exit 2; }

# **자격증명을 읽는 자리는 하나여야 한다** — CLI 도 여기서 읽는다(`credentials.py`).
# 환경변수가 이미 있으면 그것이 이긴다(cron 이 아니라 손으로 돌릴 때).
if [ -f "$ENV_SH" ]; then
  # shellcheck disable=SC1090
  . "$ENV_SH"
fi
: "${CONFLUENCE_URL:?CONFLUENCE_URL 이 없습니다 — $ENV_SH 를 확인하세요}"

mkdir -p "$VAULT"

# 넘길 자격증명만 골라 모은다. **없는 것은 안 넘긴다** — 빈 값을 주면 CLI 의
# 자동 판별이 "설정됐다" 로 오해한다.
creds=()
for k in CONFLUENCE_URL CONFLUENCE_TOKEN CONFLUENCE_EMAIL CONFLUENCE_AUTH \
         CONFLUENCE_HEADERS CONFLUENCE_PREFIX \
         IAM_TOKEN_URL IAM_CLIENT_ID IAM_CLIENT_SECRET IAM_SCOPE IAM_AUDIENCE; do
  v="${!k-}"
  [ -n "${v:-}" ] && creds+=(-e "$k=$v")
done

# **이미지가 없으면 `docker run` 이 Hub 에서 받으려다 죽는다** — `pull access denied`
# 는 이름이 틀렸다는 말로 안 읽힌다. 먼저 보고 있는 것을 알려준다.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "✗ 이미지 '$IMAGE' 가 없습니다. WIKILENS_IMAGE 로 지정하세요." >&2
  echo "  이 머신에 있는 것:" >&2
  docker images --format '    {{.Repository}}:{{.Tag}}' | grep -i wikilens >&2 || echo "    (없음 — 먼저 빌드하세요)" >&2
  exit 2
fi

# **마운트 지점과 `--root` 를 한 줄 안에서 같이 적는다.** 예전에는 컨테이너 쪽 경로를
# `/home/wikilens/.wikilens/vault` 로 박아 뒀는데, 이미지의 `HOME` 이 `/data` 로
# 바뀌자(2026-08-26) **싱크가 마운트 안 된 자리에 쓰고 컨테이너와 함께 사라졌다**
# (실측: 호스트 볼트가 빈 채로 남음). 게다가 뒤의 재색인은 서버가 보는 옛 볼트를
# 다시 색인하므로 `indexedDocs > 0` 을 통과한다 — **아무것도 안 했는데 초록이었다.**
# 이제 이미지의 `HOME` 을 모르는 자리에 두고, 마운트와 `--root` 가 같은 상수를 쓴다.
#
# `--root` 를 `"$@"` **앞에** 둔다 — 사용자가 직접 준 값이 이겨야 한다(CLI 규약).
echo "▶ 싱크 — $IMAGE"
docker run --rm "${creds[@]}" \
  -v "$VAULT":/vault \
  "$IMAGE" sync --root /vault "$@"

# **"싱크가 끝났다" 와 "볼트에 뭔가 있다" 는 다르다.** 위 경로 불일치가 정확히 이
# 틈으로 빠져나갔다 — 종료 코드는 0 이었다.
#
# **다만 이건 첫 실행에만 듣는다.** 볼트가 이미 있으면 싱크가 아무것도 안 써도
# 옛 파일 때문에 통과한다(실측) — 운영 환경은 항상 여기다. 진짜 방어는 마운트 지점과
# `--root` 를 같은 상수로 묶은 위쪽이고, 이건 그것이 또 어긋났을 때를 위한 얕은 그물이다.
# 갱신 여부까지 보려면 파일 mtime 을 재야 하는데, 변경 없는 증분 싱크와 구별되지
# 않아 안 한다.
if [ -z "$(ls -A "$VAULT" 2>/dev/null)" ]; then
  echo "  ✗ 싱크는 끝났는데 볼트가 비어 있습니다: $VAULT" >&2
  echo "    컨테이너가 다른 자리에 썼거나 스페이스가 비었습니다." >&2
  exit 1
fi

# **`&&` 대신 `set -e` 가 지킨다.** 위가 실패하면 여기 안 온다 — 반쪽 상태가
# 반영되는 것을 막는 것이 이 스크립트의 존재 이유다.
echo "▶ 재색인 — $SERVER"
if [ -z "${WIKILENS_ADMIN_TOKEN:-}" ]; then
  echo "  ✗ WIKILENS_ADMIN_TOKEN 이 없습니다. 관리 API 는 기본이 잠김이라" >&2
  echo "    안 주면 404 입니다 — 서버를 띄울 때 준 값과 같아야 합니다." >&2
  exit 1
fi
# **`|| true` 가 없으면 이 줄에서 스크립트가 끝난다.** `set -e` 는 대입문의 실패도
# 잡는데, curl 은 **서버가 안 떠 있으면 종료 코드 7** 이다 — 가장 흔한 실패에서
# 아래 안내가 한 줄도 안 나오고 cron 로그에는 `exit 7` 만 남는다(실측 2026-08-27).
code=$(curl -s -o /dev/null -w '%{http_code}' -XPOST \
  -H "X-WikiLens-Admin: $WIKILENS_ADMIN_TOKEN" "$SERVER/api/admin/reindex" || true)
if [ -z "$code" ] || [ "$code" = "000" ]; then
  # 000 은 curl 이 응답을 못 받은 것 — 주소·포트·기동 여부지 토큰 문제가 아니다.
  echo "  ✗ 서버에 닿지 못했습니다: $SERVER" >&2
  echo "    떠 있는지, WIKILENS_SERVER 가 맞는지 보세요(기본 http://localhost:8787)." >&2
  exit 1
fi
[ "$code" = "200" ] || {
  echo "  ✗ 재색인 HTTP $code — 404 면 토큰 불일치입니다(거부는 403 이 아니라 404)." >&2
  exit 1
}

# **끝났다고 쓸 수 있는 것은 아니다.** 색인이 0건이면 볼트를 못 읽은 것이고,
# 그 상태도 HTTP 200 이다(조용히 실패 14번의 계열).
# **먼저 받고 나서 읽는다 — 파이프로 이으면 원인이 뭉개진다.** 예전에는
# `curl | python3` 이라 서버 불통도 색인 0건도 같은 `||` 로 떨어져 **"색인이 0건입니다
# — 볼트 경로를 확인하세요" 라고 오진**했다(볼트는 멀쩡했다). 파이썬 트레이스백도
# 그대로 노출됐다. 실측 2026-08-27.
stats=$(curl -s "$SERVER/api/stats" || true)
if [ -z "$stats" ]; then
  echo "  ✗ 재색인은 200 이었는데 /api/stats 가 비었습니다: $SERVER" >&2
  echo "    서버가 그 사이에 죽었을 수 있습니다." >&2
  exit 1
fi
printf '%s' "$stats" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except ValueError:
    sys.stderr.write("  ✗ /api/stats 가 JSON 이 아닙니다 — 앞 200자: %s\n" % raw[:200])
    sys.exit(2)
n = d.get("indexedDocs", 0)
print("  색인 {:,}건 · 분석기 {}".format(n, d.get("analyzer")))
sys.exit(0 if n else 1)
' || {
  # **`$?` 를 먼저 잡는다** — 블록 안에서 명령을 하나라도 돌리면 덮인다.
  # 2 = 응답이 JSON 이 아님(파이썬이 이미 말했다) · 1 = 색인 0건
  rc=$?
  [ "$rc" = "1" ] && echo "  ✗ 색인이 0건입니다 — 볼트 경로를 확인하세요." >&2
  exit 1
}
