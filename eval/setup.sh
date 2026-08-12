#!/usr/bin/env bash
# 에이전트 벤치(`agent.py`)에 필요한 격리 환경을 만든다/치운다.
#
#   ./setup.sh up cold   준비 (기본) — 궤적을 비운다. 검색 엔진 자체를 잰다
#   ./setup.sh up warm   준비        — 궤적을 유지한다. 학습 효과를 잰다
#   ./setup.sh down      정리
#
# **격리가 이 벤치의 전제다.** 둘을 만든다:
#
#   vault-nohint/   A 케이스(원시 grep)용. `mirror/pages` 만 심링크로 걸어 두어
#                   **ALIASES.md·TREE.md 가 존재하지 않는다.** 프롬프트로 "읽지
#                   마세요" 라고 하면 모델이 어길 수 있어 파일 자체를 뺐다.
#   :8790 서버      C 케이스용. **운영 서버(:8787)의 궤적을 오염시키지 않는다** —
#                   MCP 프록시는 항상 sessionId 를 보내므로 벤치 질의가 그대로
#                   학습으로 쌓인다. 궤적은 유일한 복구 불가 자산이다.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="$HOME/.wikilens/vault"
PORT=8790
NAME=wikilens-eval

MODE="${2:-cold}"

case "${1:-up}" in
up)
  if [ "$MODE" != "cold" ] && [ "$MODE" != "warm" ]; then
    echo "usage: $0 up [cold|warm]" >&2; exit 2
  fi
  # A 케이스 볼트.
  #
  # **하드링크로 복사한다 — 심링크를 쓰면 격리가 새어 나간다.** 심링크는 실제 볼트를
  # 가리키므로, 링크를 해석해 위로 올라가면 `ALIASES.md` 에 닿는다(실측: `pages` 통째
  # 링크는 2단계, 샤드별 링크도 3단계면 도달). `cp -al` 은 링크가 아니라 **디렉터리
  # 트리를 새로 만들고 파일만 inode 를 공유**하므로 위로 나갈 경로가 존재하지 않는다.
  #
  # 값이 싸다: 13,933개에 **6초**, 디스크 추가 사용 0(실측). 예전에 재본
  # `find -exec ln` 이 7분 30초였던 것은 파일마다 프로세스를 띄웠기 때문이다.
  rm -rf "$HERE/vault-nohint"
  mkdir -p "$HERE/vault-nohint/mirror"
  cp -al "$VAULT/mirror/pages" "$HERE/vault-nohint/mirror/pages"
  echo "  vault-nohint: 문서 $(find "$HERE/vault-nohint/mirror/pages" -name '*.md' | wc -l | tr -d ' ')개 · 심링크 $(find "$HERE/vault-nohint" -type l | wc -l | tr -d ' ')개 · ALIASES/TREE 도달 불가"

  # C 케이스 서버 — 이미지는 운영과 같은 것을 쓰되 상태만 격리.
  #
  # **궤적을 비울지가 곧 무엇을 재느냐다.**
  #
  #   cold(기본)  매번 비움 — **검색 엔진 자체**를 잰다(BM25+앵커 대 grep).
  #               학습이 없으므로 세 방식이 같은 출발선에 선다.
  #   warm        누적      — **학습이 값어치를 하나**를 잰다. 서버판의 존재 이유가
  #               "세션을 넘어 쌓이는 학습" 이므로, 이것을 안 재면 C 를 절반만 재는
  #               것이다. 회차가 갈수록 C 의 순위·토큰이 나아지면 그것이 증거다.
  #
  # **둘 다 필요하다.** warm 만 재면 서버판이 이겨도 그것이 형태소 분석 덕인지 학습
  # 덕인지 못 가린다. cold 와의 차이가 곧 학습의 기여분이다.
  #
  # **warm 은 비대칭이다** — C 만 이전 회차를 물려받고 A·B 는 매번 처음부터다.
  # 그것이 학습 층의 설계 그대로이지만(A·B 에는 학습이 아예 없다), 리포트가 그
  # 사실을 밝혀야 한다.
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  if [ "$MODE" = "warm" ] && [ -d "$HERE/srv-state" ]; then
    echo "  [warm] 궤적 유지: $(wc -l < "$HERE/srv-state/trajectories.jsonl" 2>/dev/null || echo 0)건"
  else
    rm -rf "$HERE/srv-state" "$HERE/srv-index"
  fi
  mkdir -p "$HERE/srv-state" "$HERE/srv-index"
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  ✗ 포트 $PORT 를 이미 누가 쓰고 있다 — 그대로 두면 벤치가 **엉뚱한 서버**를 잰다" >&2
    exit 1
  fi
  docker run -d --name "$NAME" -p "$PORT:8787" \
    -v "$VAULT":/home/wikilens/.wikilens/vault:ro \
    -v "$HERE/srv-state":/home/wikilens/.wikilens/state \
    -v "$HERE/srv-index":/home/wikilens/.wikilens/index \
    -e WIKILENS_ADMIN_TOKEN=eval wikilens-wikilens >/dev/null

  for _ in $(seq 1 40); do
    [ "$(curl -s -m 2 -o /dev/null -w '%{http_code}' "localhost:$PORT/api/health" 2>/dev/null)" = "200" ] && break
    sleep 2
  done
  curl -s -XPOST -H "X-WikiLens-Admin: eval" "localhost:$PORT/api/admin/reindex" >/dev/null
  curl -s "localhost:$PORT/api/stats" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("  :%s 서버: 문서 %s · 궤적 %s (운영과 격리)"
      % (sys.argv[1], format(d["indexedDocs"], ","), d["trajectories"]))' "$PORT" 
  ;;
down)
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$HERE/vault-nohint" "$HERE/srv-state" "$HERE/srv-index"
  echo "  정리 완료 (results/ 는 남긴다)"
  ;;
*)
  echo "usage: $0 [up [cold|warm]|down]" >&2; exit 2 ;;
esac
