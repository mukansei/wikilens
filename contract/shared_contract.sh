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
# 깨진 것의 이름을 모아 **끝에서 다시 말한다.** 이 스크립트를 다른 도구가 감싸면 보통
# 꼬리 몇 줄만 보여주는데, 그 자리가 OK 로 가득 차 정작 깨진 줄이 위로 밀려났다(실측).
broken=""

check() {
  total=$((total+1))
  if eval "$2" >/dev/null 2>&1; then
    printf '  \033[0;32mOK  \033[0m %s\n' "$1"
  else
    printf '  \033[0;31m깨짐\033[0m %s\n' "$1"
    fail=$((fail+1)); broken="$broken  - $1
"
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
  'grep -q "PRIOR_CEIL = 0.85" server/src/main/kotlin/dev/wikilens/learn/Reliability.kt && grep -q "PRIOR_FLOOR, PRIOR_CEIL = 0.05, 0.85" cli/wikilens/scoring_reference.py'
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
  'grep -qF ".size <= 8" server/src/main/kotlin/dev/wikilens/learn/Gate.kt && grep -qF "len(query.strip().split()) <= 8" cli/wikilens/scoring_reference.py'
check "RATIONALE 마커 '배경' Python/Kotlin 양쪽 존재" \
  'grep -qF "\"배경\"" server/src/main/kotlin/dev/wikilens/learn/Gate.kt && grep -qF "\"배경\"" cli/wikilens/scoring_reference.py'

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
# 실측: rg 로 `coway` 가 `Coway` 를 못 찾고, 서버는 찾는다. 합의가 파일로만 이어져 있어
# 한쪽만 고쳐도 아무 에러가 안 난다.
# 서버 쪽 스캔이 `ContentService` 에서 `JvmGrepEngine`·`RipgrepEngine` 으로 나뉘면서
# 이 검사도 함께 옮겼다 — **계약이 파일 경로를 grep 하므로 파일을 나눌 때 계약도
# 고쳐야 한다**(실제로 이 분할에서 빨개졌다).
check "세 경로가 대소문자를 똑같이 무시함 (로컬 Grep -i · JVM RE2 · rg -i)" \
  'grep -q "Re2.compile(q.pattern, Re2.CASE_INSENSITIVE)" server/src/main/kotlin/dev/wikilens/service/JvmGrepEngine.kt \
   && grep -q "ignoreCase = true" server/src/main/kotlin/dev/wikilens/service/JvmGrepEngine.kt \
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
# `~/.wikilens/env.sh` 템플릿에 "Coway(wiki.coway.com)라면" 이 들어가 있었다. 남의
# 회사 사람이 설치하면 자기 자격증명 파일에서 그 이름을 보게 된다.
# 검사 대상은 **사용자가 통째로 받는 플러그인**이다. `cli/wikilens/layout.py` 의
# "측정한 것 (Coway 2,377건)" 같은 주석은 남긴다 — 문서의 같은 표기와 마찬가지로
# 수치의 출처를 밝히는 라벨이고, 지우면 그 수가 어디서 나왔는지 알 수 없게 된다.
check "설치되는 플러그인에 회사 고유값이 없음 (측정 라벨은 주석·문서에만)" \
  '! grep -rniE "coway|cwdomesticdt|megazone|메가존" plugin/local plugin/client'


# 설정을 코드에 추가하고 `application.yml` 에 안 적으면, **있는 줄도 모르는 옵션**이 된다.
# 실제로 `analyzer`·`sweep-interval-millis`·`session-idle-millis` 셋이 그 상태였다 —
# 동작은 하는데(`--wikilens.analyzer=english` 로 켜짐을 실측) 파일에는 없었다.
# yml 이 운영자의 유일한 발견 창구다.
check "설정 클래스의 모든 항목이 application.yml 에 있음 (없으면 발견 불가)" \
  'python3 -c "
import re, sys, pathlib
# 설정 클래스가 둘로 나뉘어 있다(WikiLensProperties · LearnProps). **둘 다** 봐야 한다 —
# 한쪽만 보면 나머지 파일의 새 설정이 조용히 빠진다.
kt = \"\".join(pathlib.Path(f).read_text() for f in sys.argv[1:-1])
yml = pathlib.Path(sys.argv[-1]).read_text()
def kebab(s): return re.sub(r\"(?<!^)(?=[A-Z])\", \"-\", s).lower()
keys = [kebab(m) for m in re.findall(r\"^\\s*val (\\w+):\", kt, re.M)]
# 주석 줄은 안 친다 — \"# analyzer: korean\" 은 적힌 것이 아니다.
missing = [k for k in keys if not re.search(r\"^[ ]*\" + k + r\":\", yml, re.M)]
sys.exit(1 if missing else 0)
" server/src/main/kotlin/dev/wikilens/config/WikiLensProperties.kt server/src/main/kotlin/dev/wikilens/config/LearnProps.kt server/src/main/resources/application.yml\'


# 자격증명 파일 경로가 두 곳에 하드코딩돼 있다 — CLI(`credentials.py`)와 진단
# (`vault_status.py`). 갈리면 CLI 는 읽는데 진단은 "없다"고 하거나 그 반대가 된다.
# 래퍼(`wikilens_cli.sh`)도 같은 파일을 source 하므로 셋이 같아야 한다.
check "자격증명 파일 경로가 세 곳에서 같음 (~/.wikilens/env.sh)" \
  'grep -q "Path.home() / \".wikilens\" / \"env.sh\"" cli/wikilens/credentials.py \
   && grep -q "^ENV_PATH = CONFIG_DIR / \"env.sh\"" plugin/local/scripts/vault_status.py \
   && grep -q "HOME/.wikilens/env.sh" plugin/local/scripts/wikilens_cli.sh'

# `~/.wikilens/config.json` 의 볼트 키를 이제 **세 언어가** 읽는다 — Python 진단·설정
# (`vault_status.py`), Python setup(`setup_vault.py`), Kotlin 서버(`UserConfig.kt`).
# 서버가 읽는 이유는 심링크를 손으로 만드는 단계를 없애기 위해서다. 문자열로만
# 이어져 있어 키가 갈리면 **예외 없이 폴백만 조용히 멈추고**, 증상은 "볼트가 비었다"로
# 나타나 원인이 설정 키에 있다는 걸 알 방법이 없다.
check "볼트 설정 키가 세 곳에서 같음 (config.json 의 \"vault\")" \
  'grep -q "cfg.get(\"vault\")" plugin/local/scripts/vault_status.py \
   && grep -q "cfg\[\"vault\"\] = str(vault)" plugin/local/scripts/setup_vault.py \
   && grep -q "VAULT_KEY = \"vault\"" server/src/main/kotlin/dev/wikilens/config/UserConfig.kt'

# 폴백이 걸리는지 판단하려면 "사용자가 값을 줬는가"를 알아야 하는데 Spring 은 기본값과
# 명시값을 구분해주지 않는다. 상수와 yml 이 갈리면 **명시로 준 기본 경로가 폴백을 타서**
# 오타를 조용히 덮는다 — 명시가 이긴다는 규칙이 뒤집힌다.
# 색인(`IndexingService`)과 읽기(`ContentService`)가 각자 볼트를 풀던 시절, 후자는
# `toAbsolutePath()` 조차 안 걸어 실행 디렉터리에 매달려 있었다. 폴백이 들어오자 갈림이
# 결정적이 됐다 — 실측: 문서 3건 색인·검색 정상인데 **read 는 전부 404**.
check "볼트 경로 해석처가 한 곳 (VaultLocator — 갈리면 검색은 되고 읽기만 404)" \
  '[ "$(grep -rl "props\.vaultRoot" server/src/main/kotlin | wc -l | tr -d " ")" = "1" ] \
   && grep -q "props.vaultRoot" server/src/main/kotlin/dev/wikilens/vault/VaultLocator.kt \
   && grep -q "locator.root" server/src/main/kotlin/dev/wikilens/service/ContentService.kt \
   && grep -q "locator.root" server/src/main/kotlin/dev/wikilens/service/IndexingService.kt'

check "서버 볼트 기본값이 상수와 application.yml 에서 같음 (폴백 판정 근거)" \
  'grep -q "DEFAULT_VAULT_ROOT = \"./mirror-root\"" server/src/main/kotlin/dev/wikilens/config/WikiLensProperties.kt \
   && grep -q "^  vault-root: ./mirror-root$" server/src/main/resources/application.yml'

# CLI 위치를 정해진 자리 하나로 고정한 뒤로 "설치했는데 어디 있는지 찾는" 단계가 없어졌다.
# 되돌아오면 그 자리를 아는 곳이 둘이 되어(`vault_status` 와 문서) 조용히 갈린다.
check "CLI 설치 자리가 코드와 문서에서 같음 (~/.wikilens/venv)" \
  'grep -q "^VENV_CLI = CONFIG_DIR / \"venv\" / \"bin\" / \"wikilens\"" plugin/local/scripts/vault_status.py \
   && grep -q "wikilens/venv" plugin/local/references/setup.md \
   && ! grep -q "cli-path auto" plugin/local/references/setup.md'

# 서버판의 설정 경로가 "JSON 을 손으로 쓰기" 하나뿐이면 오타가 조용히 기본값(localhost)이
# 되고, 사용자 눈에는 "문서가 없다"로 보인다. --status 는 진단만 하고 고치지는 못했다.
check "서버판에 설정 경로 있음 (--configure 가 config.json 을 병합해 씀)" \
  'grep -q -- "--configure" plugin/client/mcp/wikilens_mcp.py \
   && grep -q "cfg.update" plugin/client/mcp/wikilens_mcp.py \
   && [ -f plugin/client/commands/setup.md ]'

# `null`·`[]`·`"문자열"` 은 **유효한 JSON** 이라 파싱만 확인하면 통과한 뒤 `.get()` 에서
# AttributeError 로 터진다. 로컬판은 진단 스크립트가 죽어 스킬이 traceback 을 받고(검색
# 불가), 서버판은 모듈 최상단이라 **프록시가 기동 중 죽어 도구 4개가 사라진다.**
# 손으로 고치는 파일이라 실제로 들어온다. 두 판이 같은 파일을 읽으므로 함께 검사한다.
# 이 도구는 Confluence Cloud·Server/DC 를 가리지 않고, 회사가 아닌 조직(오픈소스 팀·
# 비영리·개인)도 쓴다. "사내" 는 고용 관계와 회사 경계를 전제하는 말이라 **제품이 자기를
# 그렇게 규정하면** 그 범위 밖 사용자에게 "내 것이 아니다" 로 읽힌다. 회사 고유값 계약과
# 같은 이유이고, 그쪽은 값을, 이쪽은 서술을 본다.
# **`git grep` 이어야 한다.** 추적 파일만 보므로 볼트를 스캔하지 않는다 — 위키 문서에는
# 이 낱말이 당연히 들어 있고, `grep -R` 은 `server/mirror-root` 심링크를 따라가 그것을
# 잡는다(실측: 붙여놓고 재니 코퍼스 파일이 걸렸다). 범위를 저장소 전체로 넓히려면
# 코퍼스를 배제할 방법이 먼저 필요했고, 그게 `git grep` 이다.
# 계약 스크립트 자신은 이 주석에서 낱말을 쓰므로 뺀다.
check "제품 서술이 조직 형태를 전제하지 않음 ('사내' 없음, 저장소 전체)" \
  '! git grep -q "사내" -- ":!contract/shared_contract.sh"'

check "두 판 모두 dict 아닌 설정을 견딤 (유효한 JSON 이 곧 쓸 수 있는 설정은 아니다)" \
  'grep -q "isinstance(cfg, dict)" plugin/local/scripts/vault_status.py \
   && grep -q "isinstance(cfg, dict)" plugin/client/mcp/wikilens_mcp.py'


# 본문 스캔 경로가 둘이다(JVM·ripgrep). rg 가 없는 머신이 있어 폴백이 영구히 공존하므로
# **갈리는지 모르는 채로 두기** 와 **갈리면 빨개지게 하기** 중 후자를 골랐다. 그 장치가
# `GrepEngineParityTest` 다 — 없어지면 두 경로가 조용히 갈라진다.
check "두 grep 엔진의 답을 대조하는 테스트가 있음 (대소문자·ACL 포함)" \
  '[ -f server/src/test/kotlin/dev/wikilens/service/GrepEngineParityTest.kt ] \
   && grep -q "두 엔진이 같은 매치를 낸다" server/src/test/kotlin/dev/wikilens/service/GrepEngineParityTest.kt \
   && grep -q "대소문자를 무시한다" server/src/test/kotlin/dev/wikilens/service/GrepEngineParityTest.kt \
   && grep -q "목록 밖을 내보내지 않는다" server/src/test/kotlin/dev/wikilens/service/GrepEngineParityTest.kt'

# **엔진은 ACL 을 몰라야 한다.** 권한 해석이 엔진마다 갈리면 한쪽이 조용히 더 보여준다 —
# `AclRegistry` 에 스위치를 한 곳만 둔 것과 같은 이유다. 거르는 것은 ContentService 다.
check "grep 엔진이 ACL 을 직접 보지 않음 (호출부가 이미 거른 목록만 받는다)" \
  '! grep -lE "AclRegistry|canSee|tokensFor" \
      server/src/main/kotlin/dev/wikilens/service/JvmGrepEngine.kt \
      server/src/main/kotlin/dev/wikilens/service/RipgrepEngine.kt'

# `--no-config` 이 없으면 운영자의 `~/.ripgreprc` 가 플래그를 얹어 **같은 질의가 머신마다
# 다른 답**을 낸다. `-i` 는 두 판이 함께 지키는 대소문자 계약이다.
check "ripgrep 이 사용자 환경을 안 받고 대소문자를 무시함 (--no-config · -i)" \
  '[ "$(grep -cE "^ *add\(\"--no-config\"\)" server/src/main/kotlin/dev/wikilens/service/RipgrepEngine.kt)" = "1" ] \
   && [ "$(grep -cE "^ *add\(\"--no-ignore\"\)" server/src/main/kotlin/dev/wikilens/service/RipgrepEngine.kt)" = "1" ] \
   && [ "$(grep -cE "^ *add\(\"-i\"\)" server/src/main/kotlin/dev/wikilens/service/RipgrepEngine.kt)" = "1" ]'

# 관리 API 가 열려 있으면 서버에 닿는 누구나 `acl/user` 로 **스스로 권한을 부여**한다 —
# 권한을 아무리 정확히 수집해도 이게 열려 있으면 의미가 없다. 기본이 "열림" 이면
# 조용히 열린 채 배포되므로 **잠김이 기본**이어야 하고, 거부는 404 여야 한다(403 은
# 엔드포인트의 존재를 알린다 — `read` 와 같은 규칙).
check "관리 API 가 기본 잠김이고 경로로 잠김 (엔드포인트마다 세지 않는다)" \
  'grep -q "val adminToken: String = \"\"" server/src/main/kotlin/dev/wikilens/config/WikiLensProperties.kt \
   && grep -q "^  admin-token: \"\"$" server/src/main/resources/application.yml \
   && grep -q "ADMIN_PATHS = \"/api/admin/\*\*\"" server/src/main/kotlin/dev/wikilens/api/AdminGuardConfig.kt \
   && grep -q "addInterceptor(guard).addPathPatterns(ADMIN_PATHS)" server/src/main/kotlin/dev/wikilens/api/AdminGuardConfig.kt \
   && ! grep -rq "guard.check" server/src/main/kotlin/dev/wikilens/api/Controller.kt'

# `mirror/acl/acl.json` 은 CLI 가 쓰고 Kotlin 이 읽는 **파일로만 이어진 계약**이다.
# 갈리면 서버가 파일을 못 읽어 전 페이지가 `@public` 폴백이 된다 — 조용한 과다 노출.
check "ACL 파일 경로·형식이 Python 과 Kotlin 에서 같음 (mirror/acl/*.json)" \
  'grep -q "root / \"mirror\" / \"acl\"" cli/wikilens/acl.py \
   && grep -q "resolve(\"mirror\").resolve(\"acl\")" server/src/main/kotlin/dev/wikilens/vault/VaultReader.kt'

# 조회 실패를 "제한 없음" 으로 뭉개면 **못 읽은 페이지가 공개로 적힌다.** 네트워크 오류
# 한 번이 노출로 이어지면 안 된다 — 실패는 옛 값을 지키고, 처음 보는 페이지는 뺀다.
check "ACL 수집이 조회 실패를 공개로 바꾸지 않음" \
  'grep -q "if own is None:" cli/wikilens/acl.py \
   && grep -q "previous\[pid\]" cli/wikilens/acl.py'

# 페이지 자신의 실패는 막고 있었는데 **조상의 실패는 안 막고 있었다** — 못 읽은 조상을
# "제한 없음" 과 같이 취급해 계속 위로 올라가면 잠긴 부모 밑의 문서가 @space 를 받는다.
check "ACL 상속이 못 읽은 조상에서 멈춤 (계속 올라가면 자식이 열린다)" \
  'grep -q "if inherited is None:" cli/wikilens/acl.py \
   && grep -q "unresolved" cli/wikilens/acl.py \
   && grep -q "rep.unresolved" cli/wikilens/cli.py'


# 사용자 등록이 메모리 전용이면 재기동마다 전원이 사라지고, 그 상태가 "문서가 없다"와
# 구별되지 않는다(조용히 실패 10·12번).
check "사용자 등록이 재기동을 넘음 (상태 디렉터리에 원자적으로 저장)" \
  '[ -f server/src/main/kotlin/dev/wikilens/acl/UserStore.kt ] \
   && grep -q "ATOMIC_MOVE" server/src/main/kotlin/dev/wikilens/acl/UserStore.kt \
   && grep -q "store?.save(byUser)" server/src/main/kotlin/dev/wikilens/acl/AclRegistry.kt'

# `~/.wikilens/` 는 **두 판이 공유**하고 안에 토큰(`env.sh`)이 든다. 만드는 경로가 셋인데
# (템플릿의 umask 077 · 로컬판 setup · 서버판 --configure) 한 곳이라도 `mkdir()` 기본값을
# 쓰면 umask 022 로 755 가 되고, **먼저 쓰는 쪽이 권한을 정한다** — 설치 순서에 따라
# 결과가 갈린다(실측 2026-08-08: 로컬판만 고쳤더니 서버판이 755 로 만들었다).
check "설정 디렉터리를 만드는 두 판이 모두 700 으로 맞춤 (토큰이 든다)" \
  'grep -q "os.chmod(CONFIG_DIR, 0o700)" plugin/local/scripts/setup_vault.py \
   && grep -q "os.chmod(CONFIG_PATH.parent, 0o700)" plugin/client/mcp/wikilens_mcp.py'

# 사용자에게 **그대로 붙여넣으라고 건네는 셸 명령**은 경로를 인용해야 한다. 홈에 공백이
# 있으면(`/Users/Hyun Woo Park`) 인용 없는 `mkdir -p` 가 조각마다 디렉터리를 만든 뒤
# 리다이렉트가 죽어 **실패하면서 쓰레기까지 남긴다**(실측). 실제로 두 곳이 그랬다.
# **`grep -P` 를 쓰지 말 것.** macOS 기본 grep(BSD)에는 없고, 없으면 `invalid option` 으로
# 죽는데 종료코드가 비0이라 `! grep` 이 **참이 되어 검사가 통과한다** — 검사가 조용히
# 사라진다(실측: 대화형 셸에는 GNU grep 이 잡혀 있어 안 드러났다). 그래서 전방탐색 대신
# "명령 뒤에 보간이 있는 줄" 을 뽑고 `shlex.quote` 가 없는 줄이 남는지로 본다.
check "사용자에게 건네는 셸 명령이 경로를 인용함 (홈에 공백이 있어도)" \
  '[ -z "$(grep -E "(bash |cat > |mkdir -p |python3 -m venv )[{]" plugin/local/scripts/setup_vault.py | grep -v "shlex\.quote")" ]'

# ACL 시행 스위치는 **`AclRegistry` 한 곳**이다. 소비자(search·read·grep·tree·학습 힌트)가
# 각자 분기하면 한 곳이 빠져 **반쪽으로 열린다** — 겉으로는 정상이라 아무도 모른다.
# 소비자는 스위치의 존재를 몰라야 하고 `tokensFor`·`canSee` 만 거쳐야 한다.
check "ACL 시행 스위치가 한 곳뿐 (소비자는 스위치를 모른다)" \
  '! git grep -lE "isEnforced|aclEnforced" -- server/src/main/kotlin/dev/wikilens/service \
        server/src/main/kotlin/dev/wikilens/index server/src/main/kotlin/dev/wikilens/vault'

# 기본값이 꺼짐이면 **조용히 열린 채** 배포된다. 조용히 빈손인 쪽이 낫다 — 그건 눈에 띈다.
check "ACL 시행 기본값이 켜짐 (상수와 application.yml 이 함께)" \
  'grep -q "val aclEnforced: Boolean = true" server/src/main/kotlin/dev/wikilens/config/WikiLensProperties.kt \
   && grep -q "^  acl-enforced: true$" server/src/main/resources/application.yml'

# 꺼두면 계속 말해야 한다 — 기동 로그 한 번으로는 재기동 뒤 아무도 모른다.
check "ACL 시행이 꺼진 것이 기동·stats·--status 세 곳에서 보임" \
  'grep -q "ACL 시행이 꺼져 있습니다" server/src/main/kotlin/dev/wikilens/WikiLensApplication.kt \
   && grep -q "aclEnforced" server/src/main/kotlin/dev/wikilens/api/Controller.kt \
   && grep -q "ACL_ENFORCED" plugin/client/mcp/wikilens_mcp.py'

# 권한이 좁은 사용자는 상위 후보가 전부 안 보일 때 **힌트가 통째로 0** 이 된다 —
# 볼 수 있는 후보가 더 아래에 있어도 슬롯을 이미 뺏겼기 때문이다. `SearchService` 가
# 어휘 결과에서 이미 겪은 실패다(조용히 실패 8번: "take 를 필터 뒤로"). 지금은 전 페이지가
# @public 이라 안 보이고 **ACL 수집이 들어오는 순간** 나타난다.
check "학습 힌트를 자르기 전에 권한으로 거름 (안 그러면 좁은 권한은 힌트가 0)" \
  'grep -q "visible: (String) -> Boolean" server/src/main/kotlin/dev/wikilens/learn/TrajectoryStore.kt \
   && grep -q "if (!visible(pid))" server/src/main/kotlin/dev/wikilens/learn/TrajectoryStore.kt \
   && grep -qE "store\.hints\(.*\) \{ pid -> acl\.canSee\(tokens, pid\) \}" server/src/main/kotlin/dev/wikilens/service/SearchService.kt'

# 궤적에 남기는 것은 권한 **범위**(토큰 해시)이지 신원이 아니다. userKey 가 들어가면
# "누가 무엇을 검색했나" 가 영구 기록으로 남는데 그건 이 도구가 지금 안 하는 일이고,
# 해결하려는 문제(권한 폭에 따른 학습 오염)는 범위만 알면 풀린다.
check "궤적이 신원이 아니라 권한 범위를 남김 (userKey 필드 없음)" \
  'grep -q "val scope: String" server/src/main/kotlin/dev/wikilens/learn/Trajectory.kt \
   && ! grep -q "userKey" server/src/main/kotlin/dev/wikilens/learn/Trajectory.kt \
   && grep -q "MessageDigest" server/src/main/kotlin/dev/wikilens/acl/AclRegistry.kt'

# Lucene write.lock 은 재색인 동안만 잡힌다. 그 밖의 시간에 둘째 프로세스가 붙으면
# 각자 다른 포스팅을 들고 같은 궤적 로그에 쓴다 — 갈림이 재기동 전까지 안 드러난다.
check "상태 디렉터리 단일 쓰기 보증 (락 + 읽을 수 있는 기동 실패)" \
  '[ -f server/src/main/kotlin/dev/wikilens/learn/StateDirLock.kt ] \
   && grep -q "stateDirLock" server/src/main/kotlin/dev/wikilens/WikiLensApplication.kt \
   && grep -q "FailureAnalyzer" server/src/main/resources/META-INF/spring.factories'

# 로그 쓰기가 실패해도 메모리 학습은 계속되므로, 갈라지고 있다는 사실 자체를 밖으로
# 내야 한다. 예전에는 WARN 한 줄이 전부라 재기동 때까지 아무도 몰랐다.
check "궤적 로그 상태가 stats 와 --status 에 드러남 (쓰기 실패·재생 누락·증가)" \
  'grep -q "fun status()" server/src/main/kotlin/dev/wikilens/learn/FileTrajectorySink.kt \
   && grep -q "trajectoryLog" server/src/main/kotlin/dev/wikilens/api/Controller.kt \
   && grep -q "trajectoryLog" plugin/client/mcp/wikilens_mcp.py \
   && grep -q "writeFailures" plugin/client/mcp/wikilens_mcp.py \
   && grep -q "replaySkipped" plugin/client/mcp/wikilens_mcp.py'
# 로그는 append-only 라 줄지 않는다. 압축은 넣지 않았지만(실측: 100만 건 = 210MB·5.3초,
# 20명 팀이면 7년치) 아무도 안 보면 기동이 조용히 느려진다 — 임계에서 알리는 것이
# 그것을 대신한다. 근거는 DECISIONS.md D17.
check "궤적 로그 증가가 임계에서 경고됨 (압축 대신 관측)" \
  'grep -q "const val SLOW_REPLAY_MILLIS" server/src/main/kotlin/dev/wikilens/learn/FileTrajectorySink.kt \
   && grep -q "replayMillis > SLOW_REPLAY_MILLIS" server/src/main/kotlin/dev/wikilens/learn/FileTrajectorySink.kt \
   && grep -q "log.warn" server/src/main/kotlin/dev/wikilens/learn/FileTrajectorySink.kt'

# 검증 명령 목록을 아는 곳이 둘이었다 — CLAUDE.md 와 IntelliJ 실행 구성. 갈리면 한쪽만
# 돌리는 사람이 생기고 그건 조용하다. `check.sh` 가 정본이고 나머지는 그것을 부른다.
check "검증 명령 목록이 한 곳뿐 (check.sh 가 정본, 나머지는 그것을 부른다)" \
  '[ -x check.sh ] \
   && grep -q "./check.sh" CLAUDE.md \
   && grep -q "SCRIPT_TEXT\" value=\"./check.sh\"" .idea/runConfigurations/3________.xml \
   && ! grep -q "shared_contract.sh" .idea/runConfigurations/3________.xml'

# 출력을 grep 해 판정하면 파이프라인 종료 코드가 grep 의 것이 되어 도구가 죽어도 0 이
# 될 수 있다. 실제로 BUILD FAILED 를 못 보고 커밋한 적이 있다(2026-08-08).
check "check.sh 가 종료 코드로 판정함 (출력 grep 아님)" \
  'grep -q "exit \"\$fail\"" check.sh \
   && grep -q "printf .  PASS" check.sh && grep -q "printf .  FAIL" check.sh \
   && ! grep -qE "\|\| echo .*(통과|PASS)" check.sh'

# 계약도 `.venv` 로 EB 기대값을 대조한다. 없으면 그 검사가 깨지는데, 새로 clone 한
# 사람에게는 코드 결함처럼 보인다 — check.sh 가 앞질러 막고 만드는 법을 말한다.
check "개발용 venv 를 아는 두 곳이 같고, 없을 때 만드는 법이 나옴" \
  'grep -q "\.\./\.venv/bin/python" contract/shared_contract.sh \
   && grep -q "^VENV=\.venv" check.sh \
   && grep -q "python3 -m venv" check.sh'

# 두 판이 각자 합리적인 기본값을 골라 정반대가 되던 자리다. Python 은 확정 못 한
# 페이지를 **일부러 생략**하는데(fail-closed), 서버가 없는 항목을 @public 으로 채우면
# 그것이 fail-open 으로 뒤집힌다. 구별해야 하는 것은 "수집한 적 없음"과 "없는 항목"이다.
check "acl.json 에 없는 페이지가 공개로 바뀌지 않음 (Python 의 생략은 fail-closed 다)" \
  'grep -q "Map<String, List<String>>?" server/src/main/kotlin/dev/wikilens/vault/VaultReader.kt \
   && grep -q "aclByPage == null -> listOf(PUBLIC)" server/src/main/kotlin/dev/wikilens/vault/VaultReader.kt \
   && grep -q "emptyList<String>().also { unresolved++ }" server/src/main/kotlin/dev/wikilens/vault/VaultReader.kt \
   && grep -q "unresolved" cli/wikilens/acl.py'

# 본문 스캔 경로가 둘이다. 어느 쪽으로 처리됐는지가 밖에서 안 보이면 답이 왜 다른지
# 물을 수도 없다 — 기동 로그는 콘솔 전용이라 로그를 못 보는 운영자에게 안 닿는다.
check "어느 grep 엔진인지 stats 와 --status 에 드러남" \
  'grep -q "val engineName" server/src/main/kotlin/dev/wikilens/service/ContentService.kt \
   && grep -q "\"grepEngine\" to content.engineName" server/src/main/kotlin/dev/wikilens/api/Controller.kt \
   && grep -q "GREP_ENGINE=" plugin/client/mcp/wikilens_mcp.py'

# grep 은 죄고 있었는데 search 만 안 죄고 있었다. 500 두 경로(0 이하 · 곱셈 오버플로우)
# 보다 나쁜 것은 **서빙한 힌트가 궤적 로그에 영구히 남는다**는 것이다 — append-only 이고
# 유일한 복구 불가 자산이라 한 요청이 수천 개를 적어 넣을 수 있으면 안 된다.
check "클라이언트가 주는 limit 을 두 경로 모두 상한으로 죔 (search·grep)" \
  'grep -q "req.limit.coerceIn(1, MAX_LIMIT)" server/src/main/kotlin/dev/wikilens/service/SearchService.kt \
   && grep -q "limit.coerceIn(1, MAX_LIMIT)" server/src/main/kotlin/dev/wikilens/service/ContentService.kt'

# `acl` 은 페이지마다 낱개 조회를 해서 이 프로젝트에서 API 를 가장 세게 쓴다. 429 를
# 못 견디면 곧 "조회 실패" 이고, 전부 실패한 결과를 쓰면 서버가 그것을 **전 페이지
# 비공개**로 읽는다 — 못 읽은 것과 없는 것은 다르다.
check "429 백오프가 모든 GET 에 걸리고, 전부 실패하면 acl.json 을 안 씀" \
  'grep -q "if r.status_code != 429:" cli/wikilens/sync.py \
   && ! grep -q "if r.status_code == 429:" cli/wikilens/sync.py \
   && grep -q "rep.failed >= len(pages)" cli/wikilens/acl.py \
   && grep -q "rep.wrote" cli/wikilens/cli.py'

# 궤적 로그는 append-only 이고 유일한 복구 불가 자산이다. 거기로 흘러가는 것 — 항 목록·
# sessionId — 에 상한이 없으면 한 요청이 무한정 적어 넣는다. `limit` 과 `MAX_PATTERN` 만
# 죄고 이 둘은 안 죄던 것이 **같은 판단의 비대칭**이었다.
check "로그로 흘러가는 것에 상한이 있음 (질의·항·sessionId·세션 수)" \
  'grep -q "const val MAX_QUERY" server/src/main/kotlin/dev/wikilens/service/SearchService.kt \
   && grep -q "keywords.take(MAX_KEYWORDS)" server/src/main/kotlin/dev/wikilens/learn/TrajectoryStore.kt \
   && grep -q "sessionId.length > MAX_SESSION_ID" server/src/main/kotlin/dev/wikilens/learn/TrajectoryStore.kt \
   && grep -q "sessions.size >= MAX_SESSIONS" server/src/main/kotlin/dev/wikilens/learn/TrajectoryStore.kt'

# `LOCALIZATION 만 간선 생성` 은 계약으로 잠겨 있는데, 그 게이트가 실제로 무엇을 걸러내는지
# 밖에서 볼 방법이 없었다. UNKNOWN 이 거의 0 이면 게이트는 사실상 항등함수다.
check "게이트의 종류 분포가 stats 와 --status 에 드러남" \
  'grep -q "\"byKind\" to QueryKind.entries" server/src/main/kotlin/dev/wikilens/learn/TrajectoryStore.kt \
   && grep -q "QUERY_KINDS=" plugin/client/mcp/wikilens_mcp.py'

# 문턱 판정을 cdf 1회로 바꿨다(실측 5~18배). 빠른 길과 정확한 길이 어긋나면 **서빙 여부가
# 조용히 달라진다** — 검색은 정상으로 보이고 힌트만 다르게 나온다. 커버리지 축이 특히
# 중요하다: 실제 판정은 `ebLower * c >= 문턱` 이라 페이지별 문턱이 `문턱 / c` 다.
check "빠른 문턱 판정이 이분법과 대조됨 (커버리지 축 포함)" \
  'grep -q "fun meetsThreshold" server/src/main/kotlin/dev/wikilens/learn/Reliability.kt \
   && grep -q "Reliability.meetsThreshold" server/src/main/kotlin/dev/wikilens/learn/TrajectoryStore.kt \
   && grep -q "for (c in listOf" server/src/test/kotlin/dev/wikilens/learn/ReliabilityThresholdTest.kt'

# 거부된 질의는 검색이 아예 안 돈 것이라 관측할 것이 없다. 관측하면 세션 객체가 생기고
# `sinceStart` 의 원시 계측이 클라이언트 오류로 오염된다. 결과 0건과는 다르다 —
# 그건 진짜 시도이고 일부러 센다.
check "거부된 질의는 궤적으로 관측하지 않음 (0건과는 다르다)" \
  'grep -q "if (res.error != null) return res" server/src/main/kotlin/dev/wikilens/api/Controller.kt'

# 성능 측정이 실코퍼스에 매달리면 두 가지가 무너진다: 그 머신 밖에서는 검증이 안 되고
# (테스트가 통째로 건너뛴다), 나온 값이 소프트웨어가 아니라 그 위키에 대한 사실이 된다.
# 실제로 그렇게 적힌 상수 하나가 2배 틀린 채로 설계 결정의 근거가 돼 있었다.
check "성능 측정이 합성 볼트로 재현됨 (실코퍼스 없이도 돈다)" \
  '[ -f server/src/test/kotlin/dev/wikilens/SyntheticVault.kt ] \
   && grep -q "SyntheticVault" server/src/test/kotlin/dev/wikilens/service/RipgrepBudgetTest.kt \
   && grep -q "SyntheticVault" server/src/test/kotlin/dev/wikilens/service/GrepScaleTest.kt \
   && ! grep -q "System.getProperty(\"user.home\")" server/src/test/kotlin/dev/wikilens/service/RipgrepBudgetTest.kt'

echo
if [ "$fail" -eq 0 ]; then
  echo "계약 ${total}개 모두 유지됨."
else
  printf '%s' "$broken"
  echo "$fail/$total 개 계약이 깨졌습니다. CLAUDE.md 의 '절대 깨면 안 되는 계약'을 확인하세요."
fi
exit "$fail"
