#!/usr/bin/env bash
# 에이전트 벤치(`agent.py`)에 필요한 격리 환경을 만든다/치운다.
#
#   ./setup.sh up     준비
#   ./setup.sh down   정리
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

case "${1:-up}" in
up)
  # A 케이스 볼트
  rm -rf "$HERE/vault-nohint"
  mkdir -p "$HERE/vault-nohint/mirror"
  ln -s "$VAULT/mirror/pages" "$HERE/vault-nohint/mirror/pages"
  echo "  vault-nohint: 문서 $(find -L "$HERE/vault-nohint/mirror/pages" -name '*.md' | wc -l | tr -d ' ')개 · ALIASES/TREE 없음"

  # C 케이스 서버 — 이미지는 운영과 같은 것을 쓰되 상태만 격리
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  mkdir -p "$HERE/srv-state" "$HERE/srv-index"
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
import json,sys; d=json.load(sys.stdin)
print(f"  :8790 서버: 문서 {d[\"indexedDocs\"]:,} · 궤적 {d[\"trajectories\"]} (운영과 격리)")'
  ;;
down)
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$HERE/vault-nohint" "$HERE/srv-state" "$HERE/srv-index"
  echo "  정리 완료 (results/ 는 남긴다)"
  ;;
*)
  echo "usage: $0 [up|down]" >&2; exit 2 ;;
esac
