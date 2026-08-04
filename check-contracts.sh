#!/usr/bin/env bash
#
# 계약 검증.
#
# Python 과 Kotlin 이 파일로만 연결되어 있어, 아래가 어긋나면 **에러 없이 조용히**
# 동작이 틀어진다. 테스트로는 잡히지 않는 교차 언어 계약을 여기서 확인한다.
# CLAUDE.md 의 "절대 깨면 안 되는 계약"과 1:1 대응한다.
set -u
cd "$(dirname "$0")"
fail=0
total=0

check() {
  total=$((total+1))
  if eval "$2" >/dev/null 2>&1; then
    printf '  \033[0;32mOK  \033[0m %s\n' "$1"
  else
    printf '  \033[0;31m깨짐\033[0m %s\n' "$1"; fail=$((fail+1))
  fi
}

echo "교차 언어 계약"
check "샤딩 규칙 Python/Kotlin 일치, Kotlin 정의처 1곳(Layout.kt)" \
  'grep -q "SHARD_WIDTH = 2" cli/wikilens/layout.py && grep -qE "substring\(0,[[:space:]]*2\)" server/src/main/kotlin/dev/wikilens/vault/Layout.kt && [ $(grep -rl "fun relPagePath" server/src/main/kotlin | wc -l) -eq 1 ]'
check "사전확률 클램프 양쪽 동일 (0.05, 0.85)" \
  'grep -q "PRIOR_CEIL = 0.85" server/src/main/kotlin/dev/wikilens/learn/Scoring.kt && grep -q "PRIOR_FLOOR, PRIOR_CEIL = 0.05, 0.85" cli/wikilens/server/scoring.py'
check "canonical_json 결정적 직렬화" \
  'grep -q "sort_keys=True, ensure_ascii=False" cli/wikilens/models.py'
check "ancestors 스키마 Python↔Kotlin 일치 (sync.py 가 쓰고 VaultReader 가 같은 키로 읽음)" \
  'grep -qF "\"ancestors\": ancestors" cli/wikilens/sync.py && grep -qF "meta[\"ancestors\"]" server/src/main/kotlin/dev/wikilens/vault/VaultReader.kt'
check "Gate LOCALIZATION 폴백 임계값 Python/Kotlin 일치 (8토큰)" \
  'grep -q "<= 8" server/src/main/kotlin/dev/wikilens/learn/Scoring.kt && grep -q "<= 8" cli/wikilens/server/scoring.py'
check "RATIONALE 마커 '배경' Python/Kotlin 양쪽 존재" \
  'grep -qF "\"배경\"" server/src/main/kotlin/dev/wikilens/learn/Scoring.kt && grep -qF "\"배경\"" cli/wikilens/server/scoring.py'

echo "빌드 구조"
check "learn/ 에 Spring·Lucene 의존 없음 (verify.sh 가 의존)" \
  '! grep -rqE "import (org\.springframework|org\.apache\.lucene)" server/src/main/kotlin/dev/wikilens/learn/'
check "Verify.kt 가 src/main 밖 (bootJar mainClass 충돌 방지)" \
  '[ ! -f server/src/main/kotlin/dev/wikilens/learn/Verify.kt ] && [ -f server/verify/Verify.kt ]'
check "src/main 에 main() 하나뿐" \
  '[ $(grep -rl "^fun main" server/src/main/kotlin | wc -l) -eq 1 ]'

echo "보안·설계 불변식"
check "권한 없음은 404 (403 은 존재를 알림)" \
  'grep -q "HttpStatus.NOT_FOUND" server/src/main/kotlin/dev/wikilens/api/Controller.kt'
check "detect_prefix 가 401/403 을 '찾음'으로 취급" \
  'grep -q "status_code in (401, 403)" cli/wikilens/sync.py'
check "서버판 플러그인에 훅 없음 (서버가 직접 관측)" \
  '[ ! -d plugin/server/hooks ]'
check "볼트 배포 엔드포인트 없음 (회수 불가 사본 방지)" \
  '! grep -rq "vault/manifest" server/src/main/kotlin/'
check "위키 쓰기 경로 없음" \
  '! grep -rqE "\.(put|post|delete)\(.*rest/api/content" cli/wikilens/sync.py'

echo "배포 사본 동기화 (plugin/ ↔ marketplace/, 수동 cp라 드리프트 실측된 적 있음)"
check "서버판 MCP 프록시 동일" \
  'diff -q plugin/server/mcp/wikilens_mcp.py marketplace/plugins/wikilens/mcp/wikilens_mcp.py'
check "서버판 SKILL.md 동일" \
  'diff -q plugin/server/skills/wikilens/SKILL.md marketplace/plugins/wikilens/skills/wikilens/SKILL.md'
check "로컬판 SKILL.md 동일" \
  'diff -q plugin/local/skills/wikilens/SKILL.md marketplace/plugins/wikilens-local/skills/wikilens/SKILL.md'
check "서버판 .mcp.json 동일" \
  'diff -q plugin/server/.mcp.json marketplace/plugins/wikilens/.mcp.json'
check "서버판 plugin.json 동일" \
  'diff -q plugin/server/.claude-plugin/plugin.json marketplace/plugins/wikilens/.claude-plugin/plugin.json'
check "로컬판 plugin.json 동일" \
  'diff -q plugin/local/.claude-plugin/plugin.json marketplace/plugins/wikilens-local/.claude-plugin/plugin.json'

echo
if [ "$fail" -eq 0 ]; then
  echo "계약 ${total}개 모두 유지됨."
else
  echo "$fail/$total 개 계약이 깨졌습니다. CLAUDE.md 의 '절대 깨면 안 되는 계약'을 확인하세요."
fi
exit "$fail"
