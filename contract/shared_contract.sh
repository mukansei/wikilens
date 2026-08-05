#!/usr/bin/env bash
#
# 공유 계약 검증 — 소스를 grep 해서 규율이 지켜지는지 정적으로 확인한다.
#
# Python 과 Kotlin 이 파일로만 연결되어 있어, 아래가 어긋나면 **에러 없이 조용히**
# 동작이 틀어진다. 테스트로는 잡히지 않는 교차 언어 계약을 여기서 확인한다.
# CLAUDE.md 의 "절대 깨면 안 되는 계약"과 1:1 대응한다.
#
# 짝이 되는 장치가 옆의 `shared-fixture/` 다. 이쪽이 "코드에 이 문자열이 있나"를
# 본다면 저쪽은 **실제로 파싱·생성해보고 결과를 비교**한다 — grep 은 빠르지만
# 리팩터링에 취약하고(문자열만 바뀌어도 오탐), 픽스처는 느리지만 동작 자체를 잠근다.
set -u
# 검사 경로가 전부 저장소 루트 기준이므로, 어디서 호출하든 루트로 이동한다.
cd "$(dirname "$0")/.."
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
# vault_status.py 가 샤딩 규칙을 **다시 정의**한다(CLI 없이 동작해야 하므로 import 불가).
# 갈라지면 정상 파일이 전부 '샤드 밖'으로 잡힌다. 동작 대조는 test_local_plugin.py 가 한다.
check "샤딩 규칙 Python/Kotlin/플러그인 일치, Kotlin 정의처 1곳(Layout.kt)" \
  'grep -q "SHARD_WIDTH = 2" cli/wikilens/layout.py && grep -q "SHARD_WIDTH = 2" plugin/local/scripts/vault_status.py && grep -qE "substring\(0,[[:space:]]*2\)" server/src/main/kotlin/dev/wikilens/vault/Layout.kt && [ $(grep -rl "fun relPagePath" server/src/main/kotlin | wc -l) -eq 1 ]'
check "사전확률 클램프 양쪽 동일 (0.05, 0.85)" \
  'grep -q "PRIOR_CEIL = 0.85" server/src/main/kotlin/dev/wikilens/learn/Scoring.kt && grep -q "PRIOR_FLOOR, PRIOR_CEIL = 0.05, 0.85" cli/wikilens/server/scoring.py'
check "canonical_json 결정적 직렬화" \
  'grep -q "sort_keys=True, ensure_ascii=False" cli/wikilens/models.py'
check "ancestors 스키마 Python↔Kotlin 일치 (sync.py 가 쓰고 VaultReader 가 같은 키로 읽음)" \
  'grep -qF "\"ancestors\": ancestors" cli/wikilens/sync.py && grep -qF "meta[\"ancestors\"]" server/src/main/kotlin/dev/wikilens/vault/VaultReader.kt'
check "Gate LOCALIZATION 폴백 임계값 Python/Kotlin 일치 (8토큰)" \
  'grep -qF ".size <= 8" server/src/main/kotlin/dev/wikilens/learn/Scoring.kt && grep -qF "len(query.strip().split()) <= 8" cli/wikilens/server/scoring.py'
check "RATIONALE 마커 '배경' Python/Kotlin 양쪽 존재" \
  'grep -qF "\"배경\"" server/src/main/kotlin/dev/wikilens/learn/Scoring.kt && grep -qF "\"배경\"" cli/wikilens/server/scoring.py'

echo "빌드 구조"
# 학습 레이어는 프레임워크와 분리돼 있어야 한다. EB·게이트·궤적은 순수 알고리즘이고,
# 여기에 Spring 이나 Lucene 이 새어 들어오면 랭킹·색인 관심사와 뒤엉켜 단위 테스트가
# 통합 테스트로 변질된다. (예전엔 kotlinc 만으로 도는 verify.sh 가 이 계약을 컴파일로
# 강제했는데, JUnit 이 같은 35개 검증을 모두 흡수해 2026-08-05 제거했다.
# 계약 자체는 이 grep 이 계속 지킨다.)
check "learn/ 에 Spring·Lucene 의존 없음 (순수 알고리즘 유지)" \
  '! grep -rqE "import (org\.springframework|org\.apache\.lucene)" server/src/main/kotlin/dev/wikilens/learn/'
check "src/main 에 main() 하나뿐 (bootJar mainClass 해석 충돌 방지)" \
  '[ $(grep -rl "^fun main" server/src/main/kotlin | wc -l) -eq 1 ]'

echo "보안·설계 불변식"
check "권한 없음은 404 (403 은 존재를 알림)" \
  'grep -q "HttpStatus.NOT_FOUND" server/src/main/kotlin/dev/wikilens/api/Controller.kt'
check "detect_prefix 가 401/403 을 '찾음'으로 취급" \
  'grep -q "status_code in (401, 403)" cli/wikilens/sync.py'
check "훅 없음 (서버가 읽기를 직접 관측하므로 훅이 불필요)" \
  '[ ! -d plugin/client/hooks ] && [ ! -d plugin/local/hooks ]'
check "위키 쓰기 경로 없음 (사람 링크와 기계 링크가 섞이면 정답 신호가 오염됨)" \
  '! grep -rqE "\.(put|post|delete)\(.*rest/api/content" cli/wikilens/'

# 마켓플레이스 매니페스트는 **저장소 루트**의 `.claude-plugin/` 에 있어야 한다.
# 하위 디렉터리(`marketplace/.claude-plugin/…`)에 두면 등록은 되는데 설치가
# "source type your Claude Code version does not support" 로 실패한다 —
# 플러그인 `source` 의 상대 경로가 기대와 다르게 해석되기 때문이다(2026-08-04 실측).
# 루트로 옮기고 `source` 가 plugin/ 을 직접 가리키게 하자 설치가 통과했고,
# 덤으로 `marketplace/plugins/` 수동 cp 사본 6개도 통째로 없어졌다.
echo "배포 구조"
check "마켓플레이스 매니페스트가 저장소 루트에 있음 (하위에 두면 설치 실패)" \
  '[ -f .claude-plugin/marketplace.json ] && [ ! -d marketplace ]'
check "source 가 실제 plugin/ 디렉터리를 직접 가리킴 (사본 없음)" \
  'grep -qF "\"./plugin/client\"" .claude-plugin/marketplace.json && grep -qF "\"./plugin/local\"" .claude-plugin/marketplace.json'
check "source 가 가리키는 경로에 plugin.json 이 실재함" \
  '[ -f plugin/client/.claude-plugin/plugin.json ] && [ -f plugin/local/.claude-plugin/plugin.json ]'
# 이름이 marketplace·plugin.json·스킬 디렉터리·스킬 name 네 군데에 흩어져 있다.
# 어긋나면 설치는 되는데 스킬이 안 잡히거나 엉뚱한 이름으로 노출된다. 버전도 두 곳에
# 있어(marketplace 와 plugin.json) 어긋난 채로 발견된 적이 있다 — 설치는 버전별
# 캐시로 복사되므로 버전이 안 오르면 고친 코드가 반영되지 않는다.
#
# 스킬 이름을 플러그인 이름과 **다르게** 두는 것이 의도다. 스킬은 `플러그인:스킬` 로
# 노출되므로 같게 두면 `wikilens-local:wikilens-local` 이 된다(실측). 플러그인 이름이
# 무엇인지를, 스킬 이름이 무엇을 하는지를 말한다 — 커맨드 `:setup`·`:sync` 와 같은 층위다.
check "플러그인 이름·버전 일치, 스킬은 search 로 통일 (어긋나면 설치가 조용히 어긋남)" \
  'python3 -c "
import json,pathlib,re,sys
mp=json.loads(pathlib.Path(\".claude-plugin/marketplace.json\").read_text())
for e in mp[\"plugins\"]:
    src=pathlib.Path(e[\"source\"].lstrip(\"./\"))
    pj=json.loads((src/\".claude-plugin/plugin.json\").read_text())
    sk=[d.name for d in (src/\"skills\").iterdir() if d.is_dir()]
    nm=[re.search(r\"^name: (.+)\$\",(src/\"skills\"/s/\"SKILL.md\").read_text(),re.M).group(1) for s in sk]
    assert pj[\"name\"]==e[\"name\"], e[\"name\"]
    assert pj[\"version\"]==e[\"version\"], e[\"name\"]
    assert sk==nm==[\"search\"], (e[\"name\"], sk, nm)
"'
# 플러그인 `name` 은 **불변 슬러그**다. 사용자가 그 이름으로 설치해 두므로 바꾸면
# 기존 설치가 `plugin-not-found` 로 깨진다 (공식 마켓플레이스 README 가 명시).
# 탈출구가 `renames` 맵이고, 로더가 이걸 읽어 옛 슬러그를 새 슬러그로 다시 쓴다.
# 지금 `wikilens → wikilens-client` 한 줄이 든 것은 2026-08-05 개명 때문인데,
# 아직 아무도 구 이름으로 설치한 적이 없어 기능적으로는 불필요하다. 남겨두는 이유는
# **다음에 이름을 바꿀 사람에게 여기 적으라고 알리는 것**이다. 근거는 DECISIONS.md D9.
#
# 잘못 적으면 조용히 아무 일도 안 한다 — 목표가 실재하지 않으면 이전이 안 되고,
# 살아 있는 이름을 키로 두면 그 플러그인이 자기 자신에서 밀려난다.
check "renames 가 실재하는 플러그인을 가리키고 살아있는 이름을 밀어내지 않음" \
  'python3 -c "
import json,pathlib
mp=json.loads(pathlib.Path(\".claude-plugin/marketplace.json\").read_text())
live={e[\"name\"] for e in mp[\"plugins\"]}
for old,new in (mp.get(\"renames\") or {}).items():
    assert new in live, (old,new,\"대상이 실재하지 않음\")
    assert old not in live, (old,\"살아있는 플러그인 이름을 키로 씀\")
"'
# .mcp.json 에서 미설정 변수를 넘기면 값이 '${WIKILENS_SERVER}' 리터럴로 전달돼
# 프록시의 기본값을 덮어쓰고 'unknown url type' 으로 죽는다 (2026-08-04 실측).
# 프록시가 os.environ 에서 직접 읽으므로 여기서 넘길 필요 자체가 없다.
check ".mcp.json 이 WIKILENS_* env 를 넘기지 않음 (미설정 시 리터럴 주입 방지)" \
  '! grep -qE "^[[:space:]]*\"WIKILENS_[A-Z]+\"[[:space:]]*:" plugin/client/.mcp.json'

# 로컬판은 볼트가 프로젝트 밖에 있으므로 cwd 상대경로를 쓰면 볼트 안에서만 동작한다.
# 그러면 전역 설치가 무의미해진다 — 배포 가능성의 핵심 조건이다.
check "로컬 스킬이 cwd 상대경로를 쓰지 않음 (볼트가 프로젝트 밖이라 배포 시 깨짐)" \
  '! grep -qE "path=\"(ALIASES|TREE)\.md\"" plugin/local/skills/search/SKILL.md'
# 두 스킬 name 은 이제 둘 다 `search` 다 — 네임스페이스(wikilens-local: / wikilens-client:)
# 가 다르므로 충돌은 아니지만, 구별할 근거가 다시 description 하나뿐이라는 뜻이다.
# 둘은 상호 배타라 설명까지 같아지면 모델이 어느 쪽을 부를지 갈리고, 로컬은 볼트가
# 서버는 서버가 없어 각각 실패한다.
check "로컬·클라이언트 스킬 description 이 서로 구별됨 (동시 설치 시 오선택 방지)" \
  '! diff -q <(sed -n "/^description:/,/^---$/p" plugin/local/skills/search/SKILL.md) <(sed -n "/^description:/,/^---$/p" plugin/client/skills/search/SKILL.md) >/dev/null'
# 플러그인은 설치 시 버전별 캐시로 복사되고 구버전은 청소된다. CLI 를 동봉하면
# 캐시가 지워질 때 설치가 죽고, marketplace/plugins/ 수동 사본과 같은 실수가 된다.
check "plugin/local 에 CLI 사본 없음 (캐시 청소 시 죽고, 사본 금지 계약 위반)" \
  '[ ! -d plugin/local/cli ] && [ ! -d plugin/local/wikilens ]'
# 로컬판의 정의적 성질이 "검색 경로 런타임 의존성 0" 이다. 여기에 서드파티가 들어오면
# 볼트 검색이 파이썬 환경 문제로 실패할 수 있게 된다.
check "로컬판 스크립트가 표준 라이브러리만 씀 (검색 경로 의존성 0 유지)" \
  '! grep -rqE "^[[:space:]]*(import|from) (requests|bs4|markdownify)\b" plugin/local/scripts/'
# 설치하면 손에 쥐는 건 플러그인 디렉터리뿐이다 — 저장소 README 는 볼 수 없다.
# 사용자용 안내가 플러그인 **안에** 있어야 배포된다.
check "두 판 모두 사용자용 안내를 플러그인에 포함 (설치자는 저장소를 못 본다)" \
  '[ -f plugin/local/README.md ] && [ -f plugin/client/README.md ]'
# 자격증명은 환경변수 전용이라(auth.py, sync.py) Claude Code 를 띄운 셸에 export 가 없으면
# CLI 가 그대로 죽는다. 래퍼가 ~/.wikilens/env.sh 를 실어 그 구멍을 막으므로, 맨 wikilens 를
# 부르는 경로가 하나라도 남으면 그쪽만 조용히 실패한다 (2026-08-05 실측).
check "로컬판이 CLI 를 항상 래퍼로 호출 (맨 wikilens 는 자격증명 없이 죽음)" \
  '[ -f plugin/local/scripts/wikilens_cli.sh ] && ! grep -rnE "(^|[\`\" '"'"'])wikilens (doctor|stats|--root|sync|build)" plugin/local/'
# 두 판 모두 설정이 환경변수 전용이면 Claude Code 를 앱으로 띄웠을 때 조용히 빈다.
# 정본은 `~/.wikilens/` 하나다 — 비밀 아닌 설정은 config.json, 토큰류는 env.sh(600).
check "두 판 모두 설정을 ~/.wikilens 에서 읽음 (env 단독은 세션 간 안 삶)" \
  'grep -q "\.wikilens" plugin/client/mcp/wikilens_mcp.py && grep -q "\.wikilens" plugin/local/scripts/vault_status.py'
# 서버는 /api/health·/api/stats 를 갖고 있었는데 플러그인이 안 써서 사용자에게 닿지
# 않았다. 검색이 빈손일 때 주소·식별자·색인 중 무엇이 막혔는지 구분할 길이 없었다.
check "서버판에 진단 경로 있음 (--status 가 health/stats 를 실제로 씀)" \
  'grep -q -- "--status" plugin/client/mcp/wikilens_mcp.py && grep -q "/api/health" plugin/client/mcp/wikilens_mcp.py && grep -q "/api/stats" plugin/client/mcp/wikilens_mcp.py'

echo
if [ "$fail" -eq 0 ]; then
  echo "계약 ${total}개 모두 유지됨."
else
  echo "$fail/$total 개 계약이 깨졌습니다. CLAUDE.md 의 '절대 깨면 안 되는 계약'을 확인하세요."
fi
exit "$fail"
