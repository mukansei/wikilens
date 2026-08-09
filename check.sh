#!/usr/bin/env bash
#
# 변경 후 이것 하나만 돌린다. 네 검증을 모두 실행하고 **한 줄로 판정**한다.
#
# ### 왜 스크립트인가
#
# 네 명령을 아는 곳이 둘이었다 — `CLAUDE.md` 와 IntelliJ 실행 구성. 둘이 갈리면 한쪽만
# 돌리는 사람이 생기고, 그건 조용하다. 여기 하나만 두고 나머지는 이걸 부른다.
#
# ### 왜 종료 코드로 판정하는가
#
# 출력을 grep 해서 판정하면 두 가지가 어긋난다:
#
#   - 파이프라인의 종료 코드는 **grep 의 것**이다. 도구가 죽어도 0 이 될 수 있다.
#   - 성공은 라벨인데 실패는 도구 원문이라 **눈이 미끄러진다.** 실제로 네 검증을 묶어
#     돌리다 `BUILD FAILED in 9s` 한 줄을 못 보고 커밋한 적이 있다(2026-08-08).
#
# 그래서 판정은 각 도구의 종료 코드고, 성공·실패가 같은 모양(`PASS`/`FAIL`)으로 나온다.
#
# ### 왜 첫 실패에서 멈추지 않는가
#
# 예전 IntelliJ 구성은 `set -e` 였다. 판정은 옳지만 **첫 실패에서 멈춰** 나머지가
# 멀쩡한지 알 수 없다. 한 번에 전체 그림을 보는 편이 고치는 순서를 정하기 좋다.
set -u
cd "$(dirname "$0")" || exit 1

# 개발용 venv. **이 경로를 아는 곳은 여기와 계약 둘뿐이고, 만드는 법은 여기에만 있다** —
# README 의 `~/.wikilens/venv` 는 사용자 설치라 다른 물건이다.
#
# 없으면 앞질러 막는다. 그냥 돌리면 pytest·MCP 가 `No such file or directory` 로 죽고
# 계약도 한 건 깨져 **새로 clone 한 사람에게 코드가 고장난 것처럼 보인다**(실측: 3/4 실패).
VENV=.venv
if [ ! -x "$VENV/bin/python" ]; then
  echo "개발용 venv 가 없습니다. 처음 한 번만:"
  echo
  echo "  python3 -m venv $VENV && $VENV/bin/pip install -e ./cli pytest"
  echo
  exit 1
fi

fail=0
total=0
tmp=""
trap 'rm -f "$tmp"' EXIT INT TERM         # Ctrl-C 로 끊어도 임시 로그를 안 남긴다

run() {                                   # run <이름> <명령...>
  local name=$1; shift
  local rc
  total=$((total + 1))
  tmp=$(mktemp)
  # 이름을 `%-8s` 로 패딩하지 않는다 — printf 는 **바이트**를 세므로 한글 이름이
  # 어긋난다. 판정 열(PASS/FAIL)은 앞에 있고 ASCII 라 그것만으로 충분하다.
  if "$@" >"$tmp" 2>&1; then
    printf '  PASS  %s — %s\n' "$name" "$(tail -1 "$tmp")"
  else
    rc=$?
    printf '  FAIL  %s (종료코드 %d)\n' "$name" "$rc"
    tail -20 "$tmp" | sed 's/^/          /'
    fail=$((fail + 1))
  fi
  rm -f "$tmp"; tmp=""
}

# `bash -c` 로 감싸는 이유: `env -C` 는 BSD/GNU 가 갈린다.
# gradle 에 `-q` 를 안 주는 이유: 로그는 **실패할 때만** 보이므로 자세할수록 낫다 —
# `-q` 는 어느 테스트가 깨졌는지를 지운다(개수만 남는다).
run 계약    bash contract/shared_contract.sh
run pytest "$VENV/bin/python" -m pytest -q
run MCP    "$VENV/bin/python" plugin/tests/test_mcp_proxy.py
# gradle 은 마지막 줄이 광고("Consider enabling…")라 PASS 요약이 그걸 집는다. 지운다.
# `pipefail` 이 있어야 파이프의 종료 코드가 gradle 것이 된다 — 없으면 grep 이 판정한다.
run JUnit  bash -c 'set -o pipefail
                    cd server && ./gradlew test --console=plain 2>&1 | grep -v "^Consider enabling"'

echo
if [ "$fail" -eq 0 ]; then
  echo "검증 넷 모두 통과"
else
  echo "$fail/$total 실패 — 위 로그를 보라"
fi
exit "$fail"
