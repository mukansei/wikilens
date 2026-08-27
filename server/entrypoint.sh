#!/bin/sh
#
# 한 이미지, 두 진입점. **배포 단위는 하나이고 실행은 나뉜다.**
#
#   wikilens serve      서버. Confluence 자격증명이 **없다**
#   wikilens sync …     싱크·ACL 수집. 자격증명을 받는다. 끝나면 종료
#
# ### 왜 나누나 — 읽기 전용이 설계 보장이어야 한다
#
# `sync`·`acl` 은 Confluence 자격증명이 필요하지만 **서버는 그걸 가질 이유가 없다.**
# 가지면 "위키에 쓰기 금지" 가 설계 보장에서 **규율로 내려간다** — 코드 리뷰로
# 지켜야 하는 약속이 된다.
#
# 자격증명은 이미지에 굽지 않고 `docker run` 시점에 준다. 그래서 `serve` 로 뜬
# 컨테이너에는 애초에 없다. `compose.yml` 의 서비스 정의에도 넣지 않는다 —
# 넣는 순간 이 근거가 무너진다. 계약이 그것을 검사한다.
set -e

# **관리 토큰을 첫 기동에 만든다 — "사람이 안 정한다" 와 "토큰이 없다" 는 다르다.**
#
#   ① `WIKILENS_ADMIN_TOKEN` 이 있으면 그것을 쓴다      명시가 항상 이긴다
#   ② `state/admin-token` 이 있으면 그것을 쓴다          재기동에 같은 값
#   ③ 둘 다 없으면 만들어 저장하고 로그에 한 번 찍는다
#
# **`AdminGuard` 는 안 건드린다.** 서버는 여전히 `wikilens.admin-token` 하나만 보고
# 비면 404 다 — 잠김이 기본이라는 성질이 그대로다. 진입점이 그 값을 채워 줄 뿐이라
# `docker run` 에서 `-e` 한 줄이 사라진다.
#
# **세 갈래가 전부 말한다 — 조용한 것은 "토큰이 없다" 와 구별되지 않는다.**
# 처음엔 ③ 만 찍었는데, 재기동하면 한 줄도 안 나와 **`docker logs` 가 돌아가면
# 운영자가 값도 자리도 찾을 길이 없었다**(실측 2026-08-27: 두 번째 기동 로그에
# `관리 토큰` 0건). 재사용·명시는 값을 다시 찍지 않는다 — 자리만 알면 `cat` 한 번이고,
# 로그에 비밀을 반복해 남길 이유가 없다. 없던 것은 자리였다.
ensure_admin_token() {
  state="$HOME/.wikilens/state"
  f="$state/admin-token"

  if [ -n "${WIKILENS_ADMIN_TOKEN:-}" ]; then
    echo "관리 토큰: 환경변수로 받았습니다 (명시가 이깁니다)."
    return 0
  fi

  if [ -r "$f" ]; then
    WIKILENS_ADMIN_TOKEN=$(cat "$f")
    export WIKILENS_ADMIN_TOKEN
    echo "관리 토큰: $f 에서 읽었습니다 (재색인·사용자 등록에 씁니다)."
    return 0
  fi

  # **여기가 못 쓰는 경우도 있다** — `state` 를 안 마운트하면 이미지 안에 쓰이고
  # 컨테이너와 함께 사라진다. 그게 맞는 동작이다(색인도 세션도 함께 사라진다).
  mkdir -p "$state" 2>/dev/null || true
  tok=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')

  # **"저장했다" 와 "다음에도 그 값이다" 는 다르다.** `state` 를 마운트 안 하면
  # 이미지 안에 쓰이고 컨테이너와 함께 사라진다 — 저장에는 성공하므로 그것만
  # 말하면 유지되는 줄 오해한다. 마운트 여부를 보고 말을 바꾼다.
  if (umask 077 && printf '%s' "$tok" > "$f") 2>/dev/null; then
    # **마운트 지점은 `~/.wikilens` 이지 그 아래 `state` 가 아니다**(실측:
    # mountinfo 에 `$HOME/.wikilens` 한 줄). 아래를 보면 양쪽 다 못 찾는다.
    if grep -q " $HOME/.wikilens " /proc/self/mountinfo 2>/dev/null; then
      where="$f 에 저장했습니다 — 재기동해도 같은 값입니다"
    else
      where="**이 컨테이너 안에만 있습니다 — 지우면 사라집니다.** 유지하려면 \`-v ~/.wikilens:$HOME/.wikilens\` 로 띄우세요"
    fi
  else
    where="저장하지 못했습니다 — 재기동하면 바뀝니다"
  fi

  WIKILENS_ADMIN_TOKEN="$tok"
  export WIKILENS_ADMIN_TOKEN
  echo "관리 토큰을 생성했습니다: $tok"
  echo "  $where"
  echo "  재색인·사용자 등록에 씁니다:  -H \"X-WikiLens-Admin: $tok\""
  echo "  직접 정하려면 -e WIKILENS_ADMIN_TOKEN=… 로 주세요 (그 값이 이깁니다)."
}

case "${1:-serve}" in
  serve)
    ensure_admin_token
    # `--enable-native-access` 는 성능이 아니라 로그 때문이다(Dockerfile 주석 참고).
    # 컨테이너 메모리를 힙 상한에 반영한다 — 안 주면 cgroup 한도를 넘어 OOMKill.
    exec java --enable-native-access=ALL-UNNAMED \
              -XX:MaxRAMPercentage=75 \
              -jar /app/wikilens.jar
    ;;
  sync|build|acl|stats|doctor)
    # CLI 서브커맨드를 그대로 넘긴다. `--root` 기본값이 `~/.wikilens/vault` 라
    # 서버와 같은 자리를 본다(호스트에서도 같다 — "이 배포는 경로가 다르다" 를
    # 기억할 일이 없어야 한다).
    exec /opt/venv/bin/wikilens "$@"
    ;;
  *)
    # java 나 sh 로 직접 들어가는 길도 막지 않는다 — 진단에 필요하다.
    exec "$@"
    ;;
esac
