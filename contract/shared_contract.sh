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
#
# ID 의 **뒤**를 쓴다는 것이 규칙의 핵심이다 — 앞자리는 엔트로피가 낮아 뭉친다
# (실측: 1번째 자리 1.93 bit vs 9번째 3.32 bit, 앞2/앞4 최대 378개 vs 뒤2 37개). 한쪽만 앞으로
# 되돌리면 서버가 파일을 못 찾는데 에러는 안 난다.
check "샤딩 규칙 Python/Kotlin/플러그인 일치(뒤에서 자름), Kotlin 정의처 1곳(Layout.kt)" \
  'grep -q "SHARD_DEPTH = 1" cli/wikilens/layout.py && grep -q "SHARD_DEPTH = 1" plugin/local/scripts/vault_status.py && grep -qF "takeLast(SHARD_WIDTH)" server/src/main/kotlin/dev/wikilens/vault/Layout.kt && [ $(grep -rl "fun relPagePath" server/src/main/kotlin | wc -l) -eq 1 ]'
check "사전확률 클램프 양쪽 동일 (0.05, 0.85)" \
  'grep -q "PRIOR_CEIL = 0.85" server/src/main/kotlin/dev/wikilens/learn/Scoring.kt && grep -q "PRIOR_FLOOR, PRIOR_CEIL = 0.05, 0.85" cli/wikilens/scoring_reference.py'
# `ALIASES.md` 한 줄에 스페이스가 들어간다. 여러 스페이스를 싱크하면 같은 낱말이
# 여러 영역에서 걸리는데, 줄에 스페이스가 없으면 grep 결과만 보고는 구분이 안 된다
# (경로도 스페이스로 안 나뉜다 — 샤딩은 페이지 ID 만 쓴다).
#
# **소스를 grep 하지 않고 체크인된 산출물을 본다.** 렌더 코드가 두 군데(별칭 있음/없음)
# 라 문자열 grep 은 한쪽만 고쳐도 통과한다 — 실제로 그렇게 빠져나가는 것을 확인했다.
check "ALIASES.md·TREE.md 산출물에 스페이스가 있고 스킬 설명과 일치" \
  'python3 -c "
import pathlib,re
F=pathlib.Path(\"contract/shared-fixture\")
al=[l for l in (F/\"ALIASES.md\").read_text().splitlines()
    if \" | \" in l and l.rsplit(\" | \",1)[1].startswith(\"mirror/pages/\")]
assert al, \"픽스처에 데이터 줄이 없다\"
assert all(l.split(\" | \")[0].strip()==\"DOCS\" for l in al), (\"첫 필드가 스페이스가 아니다\", al[0])
tr=[l for l in (F/\"TREE.md\").read_text().splitlines() if \"mirror/pages/\" in l]
assert tr and all(\"[DOCS]\" in l for l in tr), (\"TREE 줄에 스페이스가 없다\", tr[:1])
sk=pathlib.Path(\"plugin/local/skills/search/SKILL.md\").read_text()
assert \"스페이스 | 제목\" in sk, \"스킬 설명이 산출물과 갈라졌다\"
"'
# `LearnLayerTest.kt` 의 EB 기대값은 **손으로 적은 상수가 아니라** `scoring_reference.py` 의
# 산출물이다. Kotlin 에는 scipy 가 없어 Beta 분위수를 뉴턴법으로 직접 구현했고, 그게
# 맞는지 판정할 기준이 이 Python 구현뿐이다. 둘이 갈라지면 **양쪽 테스트가 각자
# 통과하면서** 서로 다른 값을 믿게 된다 — grep 으로는 못 잡으므로 실제로 계산해 본다.
check "Kotlin EB 기대값이 scoring_reference.py 산출과 1e-6 이내" \
  '(cd cli && ../.venv/bin/python -c "
import re,pathlib
from wikilens.scoring_reference import eb_lower
src=pathlib.Path(\"../server/src/test/kotlin/dev/wikilens/learn/LearnLayerTest.kt\").read_text()
cases=re.findall(r\"Triple\((\d+),\s*(\d+),\s*([\d.]+)\)\s*to\s*([\d.]+)\", src)
assert cases, \"LearnLayerTest 에서 EB 기대값을 못 찾았다\"
for h,m,p,exp in cases:
    got=eb_lower(int(h),int(m),float(p))
    assert abs(got-float(exp))<1e-6, (h,m,p,exp,got)
")'
check "canonical_json 결정적 직렬화" \
  'grep -q "sort_keys=True, ensure_ascii=False" cli/wikilens/models.py'
check "ancestors 스키마 Python↔Kotlin 일치 (sync.py 가 쓰고 VaultReader 가 같은 키로 읽음)" \
  'grep -qF "\"ancestors\": ancestors" cli/wikilens/sync.py && grep -qF "meta[\"ancestors\"]" server/src/main/kotlin/dev/wikilens/vault/VaultReader.kt'
check "Gate LOCALIZATION 폴백 임계값 Python/Kotlin 일치 (8토큰)" \
  'grep -qF ".size <= 8" server/src/main/kotlin/dev/wikilens/learn/Scoring.kt && grep -qF "len(query.strip().split()) <= 8" cli/wikilens/scoring_reference.py'
check "RATIONALE 마커 '배경' Python/Kotlin 양쪽 존재" \
  'grep -qF "\"배경\"" server/src/main/kotlin/dev/wikilens/learn/Scoring.kt && grep -qF "\"배경\"" cli/wikilens/scoring_reference.py'

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
# 설치는 **버전별 캐시로 복사**된다. 버전을 안 올리고 소스만 고치면 캐시가 그대로 남아
# 설치된 플러그인이 조용히 구버전으로 동작한다 — 버전 번호가 같으니 아무도 의심하지
# 않는다. 실제로 그 상태로 CLI 경로 해석이 통째로 반영 안 돼 있었다(2026-08-05 실측:
# 소스는 되는데 설치본은 `CLI=` 가 비고 래퍼가 CLI 를 못 찾았다).
check "설치된 플러그인이 소스와 같은 내용 (버전 동일 + 내용 상이 = 조용한 구버전)" \
  'python3 -c "
import json,pathlib,filecmp,sys
mp=json.loads(pathlib.Path(\".claude-plugin/marketplace.json\").read_text())
inst=pathlib.Path.home()/\".claude/plugins/installed_plugins.json\"
if not inst.exists(): sys.exit(0)
have=json.loads(inst.read_text())[\"plugins\"]
for e in mp[\"plugins\"]:
    for rec in have.get(e[\"name\"]+\"@\"+mp[\"name\"], []):
        if rec[\"version\"] != e[\"version\"]: continue   # 버전이 다르면 재설치하면 될 일
        cache=pathlib.Path(rec[\"installPath\"])
        src=pathlib.Path(e[\"source\"].lstrip(\"./\"))
        d=filecmp.dircmp(str(cache), str(src), ignore=[\"__pycache__\"])
        def diffs(c):
            out=list(c.diff_files)
            for s in c.subdirs.values(): out += diffs(s)
            return out
        bad=diffs(d)
        assert not bad, (e[\"name\"], e[\"version\"], bad[:3])
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
# CLI 위치 해석이 두 군데면 스킬은 "CLI 있음"이라 하는데 래퍼는 못 찾는 상태가 생긴다.
# venv·pipx 설치가 정확히 그 경우다 — PATH 에도 없고 기본 python 으로 import 도 안 된다
# (실측: 이 저장소의 cli/.venv 에 설치했더니 래퍼가 못 찾았다). 해석처는 vault_status 하나.
check "래퍼가 CLI 를 직접 찾지 않고 vault_status 에 물어봄 (해석처 1곳)" \
  'grep -q "vault_status.py\" --cli" plugin/local/scripts/wikilens_cli.sh && ! grep -qE "^[[:space:]]*(if )?command -v wikilens" plugin/local/scripts/wikilens_cli.sh'
# 두 판 모두 설정이 환경변수 전용이면 Claude Code 를 앱으로 띄웠을 때 조용히 빈다.
# 정본은 `~/.wikilens/` 하나다 — 비밀 아닌 설정은 config.json, 토큰류는 env.sh(600).
check "두 판 모두 설정을 ~/.wikilens 에서 읽음 (env 단독은 세션 간 안 삶)" \
  'grep -q "\.wikilens" plugin/client/mcp/wikilens_mcp.py && grep -q "\.wikilens" plugin/local/scripts/vault_status.py'
# 서버는 /api/health·/api/stats 를 갖고 있었는데 플러그인이 안 써서 사용자에게 닿지
# 않았다. 검색이 빈손일 때 주소·식별자·색인 중 무엇이 막혔는지 구분할 길이 없었다.
check "서버판에 진단 경로 있음 (--status 가 health/stats 를 실제로 씀)" \
  'grep -q -- "--status" plugin/client/mcp/wikilens_mcp.py && grep -q "/api/health" plugin/client/mcp/wikilens_mcp.py && grep -q "/api/stats" plugin/client/mcp/wikilens_mcp.py'
# 한때 "둘은 배타적" 이라고 적었지만 **강제할 수단이 없었다** — 이 머신에도 둘 다 켜진
# 채였고 아무 경고가 없었다. 게다가 서버판이 켜져 있으면 그 MCP 도구는 스킬 선택과
# 무관하게 항상 모델에게 보이므로 "로컬판이 이긴다" 는 애초에 성립 불가다. 그래서
# 배타성 대신 **우선순위**(서버판 우선)를 양쪽에 적었다 — D13. 한쪽만 되돌리면
# 두 스킬이 서로 양보하거나 서로 자기라고 주장하는 상태가 되므로 함께 검사한다.
check "두 스킬이 같은 우선순위를 말함 (서버판 우선 · 배타성 주장 없음)" \
  'grep -q "서버판 MCP 도구" plugin/local/skills/search/SKILL.md \
   && grep -q "이쪽이 우선입니다" plugin/client/skills/search/SKILL.md \
   && ! grep -q "둘은 배타적" plugin/local/skills/search/SKILL.md \
   && ! grep -q "둘은 배타적" plugin/client/skills/search/SKILL.md \
   && ! grep -q "하나만 고르세요" .claude-plugin/marketplace.json \
   && grep -q "OTHER=" plugin/local/scripts/vault_status.py'

# 두 판이 대소문자를 다르게 다루면 **판을 옮긴 사용자가 같은 질의에 다른 답을 받는다.**
# 두 경로가 어긋나기 쉬운 이유는 각자의 기본값이 반대라서다 — ripgrep(로컬판의 Grep)은
# 대소문자를 구분하고, 서버는 리터럴 경로가 ignoreCase 라 정규식도 거기 맞췄다.
# 실측: rg 로 `acme` 가 `Acme` 를 못 찾고, 서버는 찾는다. 합의가 파일로만 이어져 있어
# 한쪽만 고쳐도 아무 에러가 안 난다.
check "두 판이 대소문자를 똑같이 무시함 (로컬 Grep -i · 서버 RE2 CASE_INSENSITIVE)" \
  'grep -q "Re2.compile(pattern, Re2.CASE_INSENSITIVE)" server/src/main/kotlin/dev/wikilens/service/ContentService.kt \
   && grep -q "ignoreCase = true" server/src/main/kotlin/dev/wikilens/service/ContentService.kt \
   && ! grep -n "Grep(" plugin/local/skills/search/SKILL.md | grep -qv -- "-i=true"'

# 모델에게 지시하는 파일 다섯(스킬 2 · 커맨드 2 · 레퍼런스 1)은 독자가 같으므로 문체도
# 같아야 한다. 한때 스킬만 존댓말이고 커맨드·레퍼런스는 반말이었다 — 나눌 근거가 없었다.
# 더 중요한 건 **지시가 평서형으로 새는 것**이다: "말합니다 / 답합니다" 는 일어나는 일에
# 대한 서술이지 지시가 아니라서, 하필 환각 방지 규칙이 가장 약한 문형으로 적혀 있었다.
check "모델용 지시 문서가 명령형 존댓말로 통일됨 (평서형은 지시로 안 읽힌다)" \
  '! grep -qnE "^[0-9]+\. .*(말합니다|답합니다|제안합니다)$" plugin/local/skills/search/SKILL.md plugin/client/skills/search/SKILL.md \
   && ! grep -qnE "(말 것\.|한다\.$|않는다\.)" plugin/local/commands/setup.md plugin/local/commands/sync.md plugin/local/references/setup.md'


# 이 도구는 Cloud·Server/DC 어느 조직 인스턴스에도 붙는다. 그런데 개발 코퍼스가 한
# 회사 것이라 그 이름이 **배포물로 새기 쉽다** — 실제로 `setup` 이 만들어 주는
# `~/.wikilens/env.sh` 템플릿에 "Acme(wiki.example.com)라면" 이 들어가 있었다. 남의
# 회사 사람이 설치하면 자기 자격증명 파일에서 그 이름을 보게 된다.
# 검사 대상은 **사용자가 통째로 받는 플러그인**이다. `cli/wikilens/layout.py` 의
# "측정한 것 (Acme 2,377건)" 같은 주석은 남긴다 — 문서의 같은 표기와 마찬가지로
# 수치의 출처를 밝히는 라벨이고, 지우면 그 수가 어디서 나왔는지 알 수 없게 된다.
check "설치되는 플러그인에 회사 고유값이 없음 (측정 라벨은 주석·문서에만)" \
  '! grep -rniE "acme|cwdomesticdt|파트너사|파트너사" plugin/local plugin/client'


echo
if [ "$fail" -eq 0 ]; then
  echo "계약 ${total}개 모두 유지됨."
else
  echo "$fail/$total 개 계약이 깨졌습니다. CLAUDE.md 의 '절대 깨면 안 되는 계약'을 확인하세요."
fi
exit "$fail"
