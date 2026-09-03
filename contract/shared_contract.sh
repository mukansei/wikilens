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

# 주석을 걷어낸 뒤 grep 한다.
#
# **주석에 걸리는 계약은 코드가 사라져도 통과한다.** 실측: `UserStore` 의
# `ATOMIC_MOVE` 를 지웠는데 KDoc 이 그 낱말을 갖고 있어 계약 88개가 전부 초록이었다 —
# 등록 파일이 원자 교체를 잃었는데 아무도 안 말한다.
#
# 산문을 검사하는 계약(스킬 문체·README)은 이것을 쓰면 안 된다. 그쪽은 주석이 아니라
# 문서 자체가 대상이다.
code_has() {   # code_has <파일> <패턴>
  sed -E 's|//.*$||; s|^[[:space:]]*[*].*$||; s|^[[:space:]]*#.*$||' "$1" | grep -q "$2"
}

check() {
  total=$((total+1))
  # **서브셸로 돌린다.** `eval` 을 현재 셸에서 돌리면 계약 본문의 `exit` 이
  # 스크립트 전체를 끝낸다 — 실측(2026-08-26): "조직판이면 해당 없음" 을 뜻하는
  # `|| exit 0` 이 든 계약 두 개가 2026-08-20 부터 **뒤따르는 계약 50개를 통째로
  # 건너뛰게** 했고, 종료코드는 0 이라 `check.sh` 가 6일 내내 초록이었다.
  # 가장 나쁜 모양이다 — 검사가 줄어든 것은 성공으로 보인다.
  if ( eval "$2" ) >/dev/null 2>&1; then
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
  'grep -q "SHARD_DEPTH = 1" cli/wikilens/layout.py && grep -q "SHARD_DEPTH = 1" plugin/local/scripts/vault_status.py && grep -qF "takeLast(SHARD_WIDTH)" server/src/main/kotlin/io/wikilens/vault/Layout.kt && [ $(grep -rl "fun relPagePath" server/src/main/kotlin | wc -l) -eq 1 ]'
check "사전확률 클램프 양쪽 동일 (0.05, 0.85)" \
  'grep -q "PRIOR_CEIL = 0.85" server/src/main/kotlin/io/wikilens/learn/Reliability.kt && grep -q "PRIOR_FLOOR, PRIOR_CEIL = 0.05, 0.85" cli/wikilens/scoring_reference.py'
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
src=pathlib.Path(\"../server/src/test/kotlin/io/wikilens/learn/LearnLayerTest.kt\").read_text()
cases=re.findall(r\"Triple\((\d+),\s*(\d+),\s*([\d.]+)\)\s*to\s*([\d.]+)\", src)
assert cases, \"LearnLayerTest 에서 EB 기대값을 못 찾았다\"
for h,m,p,exp in cases:
    got=eb_lower(int(h),int(m),float(p))
    assert abs(got-float(exp))<1e-6, (h,m,p,exp,got)
")'
check "canonical_json 결정적 직렬화" \
  'grep -q "sort_keys=True, ensure_ascii=False" cli/wikilens/models.py'
check "ancestors 스키마 Python↔Kotlin 일치 (sync.py 가 쓰고 VaultReader 가 같은 키로 읽음)" \
  'grep -qF "\"ancestors\": ancestors" cli/wikilens/sync.py && grep -qF "meta[\"ancestors\"]" server/src/main/kotlin/io/wikilens/vault/VaultReader.kt'
# `build` 가 문자 집합으로 뺀 문서를 서버가 색인에서 빼는 통로. **파일로만 이어져 있다** —
# 키 이름이 갈리면 서버가 조용히 전부 색인하고, 필터가 걸린 줄 아는 채로 배포된다.
# 서버는 페이지 목록을 `.sync-state.json`(sync 가 쓴다)에서 얻으므로 파생물에서 빼는
# 것만으로는 안 걸러진다 — 이 파일이 유일한 통로다.
check "excluded.json 스키마 Python↔Kotlin 일치 (build 의 결정을 서버가 읽음)" \
  'grep -qF "\"excluded\": sorted(report.excluded)" cli/wikilens/build.py \
   && grep -qF "m[\"excluded\"]" server/src/main/kotlin/io/wikilens/vault/VaultReader.kt \
   && grep -qF "\"scripts\": list(scripts)" cli/wikilens/build.py \
   && grep -qF "m[\"scripts\"]" server/src/main/kotlin/io/wikilens/vault/VaultReader.kt'

# 빠진 문서는 검색 결과에 안 나오는 것으로만 드러나고 그건 "문서가 없다" 와 구별되지
# 않는다. 셋이 함께 말해야 한다 — ACL 시행이 꺼진 것을 셋이 말하는 것과 같은 규칙이다.
check "문자 집합 제외가 조용하지 않음 (CLI · stats · --status)" \
  'grep -qF "문자 집합 밖이라 뺀 문서" cli/wikilens/cli.py \
   && grep -qF "droppedByScript" server/src/main/kotlin/io/wikilens/api/Controller.kt \
   && grep -qF "droppedByScript" plugin/client/mcp/wikilens_mcp.py'

# 문턱을 고르려면 분포를 봐야 하는데, 미리보기가 `build` 와 다른 입력을 쓰면 그 표로
# 고른 값이 안 맞는다. 둘 다 **본문만** 봐야 한다 — 제목을 넣으면 이중언어 제목의
# 계층 노드가 잘못 빠진다(실측 44건, 자식 255건의 계층이 깨졌다).
check "문자 집합 판정이 본문만 봄 (미리보기와 build 가 같은 입력)" \
  'grep -qF "foreign_word_ratio(md, ranges)" cli/wikilens/build.py \
   && grep -qF "foreign_word_ratio(md, ranges)" cli/wikilens/cli.py'

# **테스트 디렉터리가 `testpaths` 에서 빠지면 조용히 안 돌아간다.** `check.sh` 는
# pytest 의 종료 코드로 판정하는데, 수집이 줄어든 것은 성공이므로 `PASS pytest —
# 138 passed` 로 정상처럼 보인다(실측: `bench/tests plugin/tests` 를 빼자 105개가
# 사라졌는데 넷 다 초록). 테스트를 지우는 것과 안 돌리는 것은 같은 결과인데
# 후자는 diff 한 줄이다.
check "테스트 디렉터리가 전부 pytest 수집 대상 (빠지면 조용히 안 돈다)" \
  'for d in cli/tests plugin/tests bench/tests; do
     grep -q "^testpaths = .*$d" pytest.ini || exit 1
   done'

# 두 판이 같은 볼트에 다른 날짜로 "낡았다" 고 말하면 판을 옮긴 사용자가 다른 진단을
# 받는다. 문턱은 한 값이어야 한다 — Python 7 · Kotlin 7.
check "볼트 stale 문턱이 두 판에서 같음 (7일)" \
  'grep -q "STALE_DAYS = 7$" plugin/local/scripts/vault_status.py \
   && grep -q "STALE_DAYS = 7L" server/src/main/kotlin/io/wikilens/vault/VaultAge.kt'
# 색인 문서 수는 볼트가 낡아도 안 변한다 — cron 이 멈추면 지표가 전부 초록인데 답만
# 몇 주 낡는다. 자동 재색인을 안 만든 대가로 이 관측이 있으므로, 사라지면 안 된다.
check "볼트 나이가 stats 와 --status 에 드러남 (cron 이 죽어도 조용하지 않게)" \
  'code_has server/src/main/kotlin/io/wikilens/api/Controller.kt "vaultAgeDays" \
   && grep -qF "vaultAgeDays" plugin/client/mcp/wikilens_mcp.py'

# 모델이 `answer` 를 안 부르면 `dest` 가 조용히 추정으로 돌아간다 — 검색은 정상이라
# 겉으로 안 보인다. 서버 로그에도 안 남으므로 stats·--status 가 유일한 창구다.
check "진술된 답이 stats 와 --status 에 드러남 (안 부르면 조용히 추정으로 돌아감)" \
  'code_has server/src/main/kotlin/io/wikilens/learn/TrajectoryStore.kt "declaredDest" \
   && grep -qF "declaredDest" plugin/client/mcp/wikilens_mcp.py'

check "Gate LOCALIZATION 폴백 임계값 Python/Kotlin 일치 (8토큰)" \
  'grep -qF ".size <= 8" server/src/main/kotlin/io/wikilens/learn/Gate.kt && grep -qF "len(query.strip().split()) <= 8" cli/wikilens/scoring_reference.py'
check "RATIONALE 마커 '배경' Python/Kotlin 양쪽 존재" \
  'grep -qF "\"배경\"" server/src/main/kotlin/io/wikilens/learn/Gate.kt && grep -qF "\"배경\"" cli/wikilens/scoring_reference.py'

echo "빌드 구조"
# 학습 레이어는 프레임워크와 분리돼 있어야 한다. EB·게이트·궤적은 순수 알고리즘이고,
# 여기에 Spring 이나 Lucene 이 새어 들어오면 랭킹·색인 관심사와 뒤엉켜 단위 테스트가
# 통합 테스트로 변질된다. (예전엔 kotlinc 만으로 도는 verify.sh 가 이 계약을 컴파일로
# 강제했는데, JUnit 이 같은 35개 검증을 모두 흡수해 2026-08-05 제거했다.
# 계약 자체는 이 grep 이 계속 지킨다.)
check "learn/ 에 Spring·Lucene 의존 없음 (순수 알고리즘 유지)" \
  '! grep -rqE "import (org\.springframework|org\.apache\.lucene)" server/src/main/kotlin/io/wikilens/learn/'
check "src/main 에 main() 하나뿐 (bootJar mainClass 해석 충돌 방지)" \
  '[ $(grep -rl "^fun main" server/src/main/kotlin | wc -l) -eq 1 ]'

echo "보안·설계 불변식"
check "권한 없음은 404 (403 은 존재를 알림)" \
  'grep -q "HttpStatus.NOT_FOUND" server/src/main/kotlin/io/wikilens/api/Controller.kt'
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
# **버전이 넷으로 흩어져 있었다.** CLI 0.2.0 · server 0.1.0 · plugin 0.17.8/0.14.15 라
# "이 조합이 함께 검증됐다" 를 가리킬 이름이 없었다. 정본은 `VERSION` 하나이고 넷은
# 사본이다 — 형식이 각자 강제해서(정적 JSON 은 생성이 안 된다) 사본을 없앨 수 없다.
# 그래서 README 배지와 같은 방식으로 **계약이 대조한다.**
#
# 올리는 것은 `contract/bump-version.sh` 가 한다. 손으로 넷을 고치면 하나를 빠뜨린다.
check "버전 넷이 VERSION 과 같음 (흩어지면 릴리스를 가리킬 이름이 없다)" \
  'python3 -c "
import json,pathlib,re,sys
want=pathlib.Path(\"VERSION\").read_text().strip()
bad=[]
def take(p,pat):
    m=re.search(pat, pathlib.Path(p).read_text(encoding=\"utf-8\"), re.M)
    return m.group(1) if m else \"(못 찾음)\"
got={
 \"cli/pyproject.toml\": take(\"cli/pyproject.toml\", r\"^version = .(.*).$\"),
 \"server/build.gradle.kts\": take(\"server/build.gradle.kts\", r\"^version = .(.*).$\"),
}
for f in (\"plugin/local\",\"plugin/client\"):
    got[f]=json.loads(pathlib.Path(f+\"/.claude-plugin/plugin.json\").read_text())[\"version\"]
for k,v in got.items():
    if v!=want: bad.append(f\"{k}={v}\")
if bad:
    print(\"VERSION=\"+want+\" 과 다름: \"+\", \".join(bad), file=sys.stderr); sys.exit(1)
"'

check "설치된 플러그인이 소스와 같은 내용 (버전 동일 + 내용 상이 = 조용한 구버전)" \
  'python3 -c "
import json,pathlib,filecmp,sys
mp=json.loads(pathlib.Path(\".claude-plugin/marketplace.json\").read_text())
inst=pathlib.Path.home()/\".claude/plugins/installed_plugins.json\"
if not inst.exists(): sys.exit(0)
have=json.loads(inst.read_text())[\"plugins\"]
for e in mp[\"plugins\"]:
    for rec in have.get(e[\"name\"]+\"@\"+mp[\"name\"], []):
        if rec[\"version\"] != e[\"version\"]: continue   # 버전 차이는 아래 계약이 본다
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
# **버전이 뒤처진 것은 위 계약이 건너뛴다**(내용 비교가 무의미하므로 그건 맞다). 그런데
# 아무도 재설치하라고 말해주지 않아, 소스만 올리고 설치본은 몇 버전 뒤에 남는다 —
# 실측(2026-08-19): 하루에 다섯 번 올렸더니 `client` 가 설치 0.14.10 · 소스 0.14.13 으로
# 벌어졌는데 `./check.sh` 는 내내 초록이었다. **그 사이 만든 진단(`DECLARED_DEST`·
# `VAULT_AGE`)이 사용자에게는 존재하지 않는 상태였다** — 저장소 소스로 테스트하면 보이고
# 설치본으로는 안 보인다.
#
# 실패로 잡는 이유: 이 저장소의 판정 기준이 **저장소가 아니라 설치본**이다(조용히 실패
# 11번). 설치본이 뒤처졌다는 것은 지금 검증하는 것이 배포될 것과 다르다는 뜻이다.
#
# **재설치만으로는 안 고쳐진다 — 층이 셋이다**(2026-08-19 실측). `/plugin install` 은
# 작업본이 아니라 **마켓플레이스 클론**(`~/.claude/plugins/marketplaces/<name>`)에서
# 복사하고, 그 클론은 원격에서 받는다. 그날 클론이 33커밋 뒤였고, 재설치했더니
# `✓ Installed` 가 뜨면서 **같은 옛 버전이 다시 깔렸다.**
#
#     작업본 → (push) → 원격 → (marketplace update) → 클론 → (install) → 설치본
#
# 그래서 아래 메시지가 세 단계를 다 말한다. 하나만 하면 조용히 제자리다.
# **서버는 Confluence 자격증명을 갖지 않는다 — 읽기 전용이 설계 보장이어야 한다.**
#
# 한 이미지가 서버와 CLI 를 모두 담게 되면서(2026-08-26) 그 경계가 흐려질 자리가
# 생겼다. `sync` 는 자격증명이 필요하지만 **`serve` 는 아니다** — 가지면 "위키에
# 쓰기 금지" 가 설계 보장에서 **규율로 내려간다**(코드 리뷰로 지켜야 하는 약속).
#
# 자격증명은 `docker run` 시점에 준다. `compose.yml` 의 서비스 정의나 Dockerfile 의
# `ENV` 에 넣으면 그 근거가 무너지므로 여기서 막는다.
check "서버 이미지·compose 에 Confluence 자격증명이 없음 (읽기 전용은 설계 보장이다)" \
  '! grep -qE "^[^#]*(CONFLUENCE_|IAM_)" compose.yml \
   && ! grep -qE "^ENV .*(CONFLUENCE_|IAM_)" server/Dockerfile'

# **그리고 서버 코드가 자격증명을 아예 안 읽는다.** 마운트가 `~/.wikilens` 통째라
# `env.sh`(600)가 컨테이너에서 보이지만, **읽을 코드가 없다** — 보이는 것과 쓰는
# 것은 다르고 후자가 없어야 보장이다. 주석은 걷어내고 본다(`code_has` 와 같은 이유).
check "서버 코드에 Confluence 자격증명 참조가 없음 (env.sh 가 마운트에 보인다)" \
  '[ -z "$(find server/src/main/kotlin -name "*.kt" -exec sed -E "s|//.*$||; s|^[[:space:]]*[*].*$||" {} + \
           | grep -E "CONFLUENCE_|IAM_TOKEN_URL|env\\.sh")" ]'

# **싱크 컨테이너의 마운트 지점과 `--root` 가 같아야 한다 — 갈리면 조용히 버려진다.**
#
# `refresh.sh` 가 컨테이너 쪽 경로를 `/home/wikilens/.wikilens/vault` 로 박아 뒀는데
# 이미지의 `HOME` 이 `/data` 로 바뀌자(2026-08-26) 싱크가 **마운트 안 된 자리에 쓰고
# 컨테이너와 함께 사라졌다.** 실측(2026-08-27): 호스트 볼트가 빈 채로 남았고,
# 뒤의 재색인은 서버가 보는 옛 볼트를 다시 색인해 `indexedDocs > 0` 을 통과했다 —
# **종료 코드 0 · 전부 초록 · 싱크는 아무것도 안 함.**
#
# 그래서 둘을 **같은 줄에서 같은 상수로** 쓰고, 이미지의 `HOME` 에 안 매달린다.
#
# 부정 검사는 `code_has` 로 본다 — 옛 경로를 **설명하는 주석**에 걸려 빨개졌다.
# 긍정 쪽이 주석에 걸린 것(조용히 실패 27)과 반대 방향의 같은 실수다.
check "싱크 마운트 지점과 --root 가 같음 (갈리면 볼트가 조용히 버려진다)" \
  'grep -q -- "-v \"\$VAULT\":/vault" server/wikilens-refresh.sh \
   && grep -q -- "sync --root /vault" server/wikilens-refresh.sh \
   && ! code_has server/wikilens-refresh.sh "/home/wikilens"'

# **싱크가 끝났다고 볼트에 뭔가 있는 것은 아니다.** 위 결함이 정확히 이 틈으로 빠졌다.
check "refresh 가 싱크 후 빈 볼트를 잡음 (종료 코드만으로는 못 본다)" \
  'grep -q "ls -A \"\$VAULT\"" server/wikilens-refresh.sh'

# **`set -e` 는 대입문의 실패도 잡는다 — 그래서 curl 은 `|| true` 가 필요하다.**
# 서버가 안 떠 있으면 curl 이 종료 코드 7 이고, 그러면 뒤따르는 안내가 **한 줄도
# 실행되지 않은 채** 스크립트가 끝난다(실측 2026-08-27: cron 로그에 `exit 7` 만).
# 가장 흔한 실패가 가장 적은 정보를 내던 자리다.
check "refresh 의 curl 이 set -e 에 안 죽음 (서버 불통이 가장 흔한 실패다)" \
  'c=$(grep -c "|| true" server/wikilens-refresh.sh); [ "$c" -ge 2 ] \
   && code_has server/wikilens-refresh.sh "서버에 닿지 못했습니다"'

# **응답을 파이프로 바로 읽지 않는다 — 원인이 뭉개진다.** `curl | python3` 이던 시절
# 서버 불통과 색인 0건이 같은 `||` 로 떨어져 **"볼트 경로를 확인하세요" 로 오진**했고
# 파이썬 트레이스백이 그대로 노출됐다. 받아 두고 나서 판정한다.
# 부정 패턴의 파이프는 문자 클래스로 감싼다 — 안 감싸면 교대로 읽혀
# **긍정 쪽 줄에 매칭되어 계약이 뒤집힌다.** 그리고 `[|]` 만으로는 부족했다 —
# 고친 코드의 `|| true` 에 걸렸다(실측). 파이프 **뒤에 오는 명령**까지 봐야 한다.
check "refresh 가 stats 를 받아 두고 판정 (파이프로 이으면 원인이 뭉개진다)" \
  'code_has server/wikilens-refresh.sh "stats=[$][(]curl" \
   && ! code_has server/wikilens-refresh.sh "api/stats. [|] *python"'

# **토큰 파일은 `-s` 로 본다 — `-r` 은 0바이트도 통과시킨다.** 쓰다 죽으면 빈 파일이
# 남는데, 그러면 빈 토큰을 쥐고 "읽었습니다" 를 찍는다. 서버가 404 로 닫아 안전하긴
# 하지만 **운영자가 토큰을 의심하지 않게 된다.**
check "관리 토큰 파일을 -s 로 검사 (-r 은 빈 파일을 통과시킨다)" \
  'code_has server/entrypoint.sh "\[ -s \"\$f\" \]" \
   && ! code_has server/entrypoint.sh "\[ -r \"\$f\" \]"'

# **MCP 프록시는 파이썬 3.9 에서 돌아야 한다 — macOS 기본이 3.9 다.**
#
# 사용자는 CLI 를 안 깔기 때문에 `cli/pyproject.toml` 의 requires-python 보호를 못
# 받는다. `.mcp.json` 이 python3 로 부르고, 그 python3 가 3.9 면 프록시가 **기동 중
# 죽어 MCP 도구 다섯이 통째로 사라진다**(실측 2026-08-27: `float | None` 주석과
# write_text 의 newline 인자가 둘 다 3.10+ 였다). 사용자에게는 traceback 도 안 보인다.
#
# **문법만 보는 검사라 완전하지 않다** — 진짜 확인은 3.9 인터프리터로
# plugin/tests/test_mcp_proxy.py 를 돌리는 것이고, 그 판이 없는 머신이 있어 못 넣었다.
check "MCP 프록시에 3.10+ 전용 문법이 없음 (사용자 python3 가 3.9 면 도구가 사라진다)" \
  '! code_has plugin/client/mcp/wikilens_mcp.py "[a-z_]+: [a-zA-Z]+ [|] (None|str|int)" \
   && ! code_has plugin/client/mcp/wikilens_mcp.py "write_text[(].*newline" \
   && code_has plugin/client/mcp/wikilens_mcp.py "version_info < [(]3, 9[)]"'

# **커밋 제목이 명사로 끝나는지 본다 — 규칙이 문서에만 있어 아무것도 안 막았다.**
#
# `CLAUDE.md` 가 "서술형(…한다·…였다) 금지, 대시 뒤가 특히 샌다" 라고 적어 뒀는데
# 계약이 없었다. 실측(2026-08-27): 하루에 만든 커밋 19개 중 **8개가 샜고 전부 대시
# 뒤였다** — 거기 오는 것이 대개 실측 결과라 `…통과했다`·`…아니다` 를 부른다.
# 그 사이 `./check.sh` 는 내내 초록이었다.
#
# **직전 다섯만 본다.** 그 앞에는 위반이 남아 있다 — 고치려면 푸시된 이력을 다시
# 써야 하고, 커밋 제목 어미보다 강제 푸시 쪽이 위험이 커서 두기로 했다(2026-08-27).
# 목적은 지난 것을 지우는 게 아니라 **다음 것을 막는 것**이고, 커밋한 다음 번
# `./check.sh` 에서 걸린다. 한국어 명사가 `다` 로 끝나는 일은 사실상 없어 오탐이 없다.
check "커밋 제목이 명사로 끝남 (서술형 금지 — 규칙이 문서에만 있었다)" \
  '[ -z "$(git log --format=%s -5 | grep -E "다[.]?$")" ]'

# **`Co-Authored-By` 도 같이 본다** — `CLAUDE.md` 가 같은 줄에서 금지하는데 역시
# 계약이 없었다. 붙으면 공개판 세탁에서 저자 계약과 부딪힌다.
check "커밋 본문에 Co-Authored-By 가 없음 (공개판 저자 계약과 부딪힌다)" \
  '[ -z "$(git log --format=%b -5 | grep -i "Co-Authored-By")" ]'

# **정기 갱신 스크립트가 이미지에 들어 있다** — README 가 "저장소 없이" 를 안내하는데
# 갱신만 clone 을 요구하면 그 경로가 반쪽이다.
check "이미지가 refresh 스크립트를 나름 (clone 없는 경로가 성립해야 한다)" \
  'grep -q "server/wikilens-refresh.sh /app/wikilens-refresh.sh" server/Dockerfile'

# **운영자에게도 안내 스크립트가 있다** — 사용자 두 갈래에는 setup 커맨드가 있는데
# 운영자만 README 의 네 명령을 손으로 옮겨 적었다. 그 과정에서 나온 함정 셋(이미지
# 이름 · 스페이스 키 · 확인 단계)이 전부 거기서 없어진다.
check "운영자 setup 스크립트가 있고 README 가 그것을 가리킴" \
  '[ -x server/wikilens-setup.sh ] && grep -q "wikilens-setup.sh" README.md'

# **setup 이 찍는 저장소 URL 에서 계정을 벗긴다.** `git remote` 에는
# `https://<계정>@github.com/…` 형태가 흔한데(자격증명 헬퍼가 붙인다) 그대로 찍으면
# **운영자 계정이 사용자 안내에 실려 나간다** — 실측으로 그렇게 나왔다(2026-08-28).
# 조직 계정이 새는 자리라 조용하다.
check "setup 이 안내하는 저장소 URL 에 계정이 안 붙음 (remote 에 흔한 형태다)" \
  'code_has server/wikilens-setup.sh "REPO_URL=.*sed" \
   && code_has server/wikilens-setup.sh "marketplace add"'

# **`find` 를 대입문에 바로 쓰지 않는다.** 없는 디렉터리에 1 을 반환하고 pipefail 이
# 그것을 올려 `set -e` 가 죽인다 — `mirror/` 가 없는 **첫 구축이 정확히 그 경우**라
# 실측(2026-08-27)에서 4단계 제목만 찍고 조용히 끝났다. refresh.sh 의 curl 과 같은
# 실패인데 거기 주석을 적어 놓고 같은 자리에서 또 물렸다.
check "setup 의 페이지 세기가 set -e 에 안 죽음 (첫 구축이 그 경우다)" \
  'code_has server/wikilens-setup.sh "count_pages" \
   && ! code_has server/wikilens-setup.sh "=[$][(]find"'

# **분석기는 색인 시점 값이라 틀려도 에러가 안 난다**(D14) — 검색 품질만 조용히
# 나빠진다. compose 로 노출하고 setup 이 묻는다.
check "분석기를 compose 로 바꿀 수 있고 setup 이 물음 (틀려도 에러가 안 난다)" \
  'code_has compose.yml "WIKILENS_ANALYZER: [$][{]" \
   && code_has server/wikilens-setup.sh "WIKILENS_ANALYZER"'

# **setup 이 호스트 uid 를 넘긴다 — 리눅스에서 안 넘기면 색인이 0건이다.**
#
# bind mount 가 호스트 소유권을 그대로 쓰는데 이미지는 uid 10001 로 돌고
# `~/.wikilens` 는 700 이라 컨테이너가 traverse 조차 못 한다. 에러가 아니라 **0건**이라
# "문서가 없다" 와 구별되지 않는다. **macOS 는 Docker Desktop 이 uid 를 가려 이 문제가
# 안 보인다** — 그래서 이 머신에서의 통과가 리눅스를 보증하지 않는다(`compose.yml` 주석).
check "setup 이 호스트 uid 를 넘김 (리눅스에서 안 넘기면 색인이 조용히 0건)" \
  'code_has server/wikilens-setup.sh "WIKILENS_UID=" \
   && code_has server/wikilens-setup.sh "WIKILENS_GID=" \
   && code_has compose.yml "WIKILENS_UID:-10001"'

# **관리 토큰은 세 갈래 전부 말한다.** 재사용·명시가 조용하면 `docker logs` 가 돌아간
# 뒤 자리를 찾을 길이 없다(실측: 두 번째 기동 로그에 `관리 토큰` 0건).
#
# **개수로 세지 않는다.** 처음엔 `grep -c "관리 토큰" -ge 3` 이었는데, 바로 위
# 주석 블록이 그 낱말을 갖고 있어 `echo` 를 지워도 통과했다(실측). 세 갈래를
# 각각 이름으로 본다 — 주석은 `code_has` 가 걷어낸다.
check "관리 토큰 안내가 세 갈래 전부에 있음 (조용한 것은 없는 것과 구별 안 된다)" \
  'code_has server/entrypoint.sh "관리 토큰: 환경변수" \
   && code_has server/entrypoint.sh "관리 토큰: \$f 에서 읽었" \
   && code_has server/entrypoint.sh "관리 토큰을 생성했습니다"'

check "설치된 플러그인이 소스 버전 이상 (뒤처지면 만든 것이 사용자에게 없다)" \
  'python3 -c "
import json,pathlib,sys
def ver(v): return tuple(int(x) for x in str(v).split(\".\"))
mp=json.loads(pathlib.Path(\".claude-plugin/marketplace.json\").read_text())
inst=pathlib.Path.home()/\".claude/plugins/installed_plugins.json\"
if not inst.exists(): sys.exit(0)          # 설치한 적 없는 머신에서는 판정하지 않는다
have=json.loads(inst.read_text())[\"plugins\"]
stale=[]
for e in mp[\"plugins\"]:
    recs=have.get(e[\"name\"]+\"@\"+mp[\"name\"], [])
    if not recs: continue                  # 안 깔았으면 이 계약의 대상이 아니다
    for rec in recs:
        if ver(rec[\"version\"]) < ver(e[\"version\"]):
            stale.append(e[\"name\"]+\" 설치 \"+rec[\"version\"]+\" < 소스 \"+e[\"version\"])
assert not stale, (\"설치본이 뒤처짐: \"+\" · \".join(stale)
    +\" — 재설치만으로는 안 된다. push → /plugin marketplace update <name> → /plugin install\")
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
# **자격증명 때문이 아니다** — CLI 가 `credentials.py` 로 `~/.wikilens/env.sh` 를 직접
# 읽게 된 뒤로 맨 `wikilens doctor` 도 산다(실측: `env -i HOME=… wikilens doctor` → 인증 성공).
# 남은 이유는 **볼트 경로**다. 래퍼가 `config.json` 을 읽어 `--root` 를 채우므로, 맨
# `wikilens` 를 부르는 경로는 작업 디렉터리에 매달려 엉뚱한 자리를 본다.
# **cron 은 예외다** — 래퍼 경로에 플러그인 버전이 박혀 있어 업데이트에 조용히 깨진다.
# 거기서는 CLI 를 직접 부르고 `--root` 를 명시한다(`references/setup.md` 4단계).
check "로컬판이 CLI 를 항상 래퍼로 호출 (맨 wikilens 는 --root 없이 엉뚱한 자리를 본다)" \
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

# **MCP 서버 이름이 세 곳에 적혀 있다 — 갈리면 조용히 틀어진다.**
# 특히 로컬판 스킬이 그 이름으로 "서버판이 보이면 양보" 를 판정하므로(D13), 스킬만
# 옛 이름이면 **두 판이 함께 켜져도 로컬판이 이긴다.** 근거와 실측은 헬퍼 독스트링.
check "MCP 서버 이름이 .mcp.json·프록시·로컬 스킬에서 같음 (갈리면 판 우선순위가 뒤집힌다)" \
  'python3 contract/mcp_server_name.py'

# **MCP 프록시는 셸을 안 쓴다 — 그것이 Windows 지원의 경계다.**
#
# D31 이 "Windows 는 Git for Windows 를 요구한다" 로 닫으면서, **서버판 사용자만은
# 예외**로 뒀다 — 프록시가 순수 파이썬이라 셸이 없어도 돈다. 여기에 `subprocess` 나
# `os.system` 이 한 줄 들어오는 순간 그 예외가 사라지고, **Git 없는 Windows 사용자가
# 조용히 잘려 나간다**(설치는 되는데 도구가 안 뜬다).
#
# **교대(`|`)를 쓰지 않고 셋으로 나눈다** — 이 머신의 `grep` 에서 부정 검사의
# 교대가 안 먹었다(실측: `subprocess` 를 넣어도 통과). D30 과 같은 계열이다.
check "MCP 프록시가 셸을 안 씀 (서버판 사용자의 Windows 경로가 여기 걸려 있다)" \
  '! code_has plugin/client/mcp/wikilens_mcp.py "subprocess" \
   && ! code_has plugin/client/mcp/wikilens_mcp.py "os[.]system" \
   && ! code_has plugin/client/mcp/wikilens_mcp.py "os[.]popen"'

# **배제 조항은 스킬만으로는 아무것도 안 막는다.** 서버판 MCP 도구는 스킬 선택과
# 무관하게 항상 도구 목록에 뜨므로(바로 위 항목·D13), 모델이 실제로 읽는 것은 도구
# 설명이다. 한때 스킬에만 "코드베이스 자체에는 쓰지 마세요" 가 있고 도구 설명에는
# "아키텍처 문서를 찾을 때 반드시 먼저" 만 있어서, **지금 열려 있는 저장소의 아키텍처를
# 물으면 위키를 먼저 뒤질 근거**가 됐다(치명적이진 않다 — 읽기 전용이고 왕복 한두 번을
# 버린다). `grep` 은 설명에 "식별자·코드 조각" 이 있어 `search` 보다 오히려 새기 쉽다.
check "코드베이스 배제가 스킬과 MCP 도구 설명 양쪽에 있음 (도구 설명이 실제 관문)" \
  'grep -q "코드베이스 자체" plugin/local/skills/search/SKILL.md \
   && grep -q "코드베이스 자체" plugin/client/skills/search/SKILL.md \
   && grep -q "코드베이스 자체" plugin/client/mcp/wikilens_mcp.py \
   && [ "$(grep -c "NOT_FOR_CODEBASE" plugin/client/mcp/wikilens_mcp.py)" -ge 3 ]'

# **측정 장치가 프로덕션과 같은 질의를 써야 한다.** `Bm25LengthNormTest` 는 유사도를
# 갈아끼우려고 자기 `IndexSearcher` 를 여는데, 그러면 질의도 자기가 만들게 된다. 한때
# 분석기와 파서를 손으로 다시 만들고 "같은 구성이어야 한다" 는 주석으로 이어 뒀는데,
# 그 주석은 아무것도 안 막았다 — 프로덕션이 바뀌면 **측정만 옛 질의를 재게 된다.**
# 이제 `LuceneQuery` 가 정의처이고 둘 다 그것을 부른다(실측: `LuceneQuery` 의 분석기를
# KOREAN→Standard 로 바꾸니 측정이 23/30 → 16/30 으로 따라 움직였다).
# **부스트 변경은 `FieldBoostTest` 가 잡는다** — 이 검사는 복제가 되살아나는 것을 막는다.
check "측정 테스트가 질의를 손으로 다시 만들지 않음 (LuceneQuery 가 정의처)" \
  'grep -q "object LuceneQuery" server/src/main/kotlin/io/wikilens/index/LuceneQuery.kt \
   && grep -q "LuceneQuery.textQuery" server/src/main/kotlin/io/wikilens/index/LuceneIndex.kt \
   && grep -q "LuceneQuery.textQuery" server/src/test/kotlin/io/wikilens/index/Bm25LengthNormTest.kt \
   && ! grep -q "MultiFieldQueryParser\|PerFieldAnalyzerWrapper" server/src/test/kotlin/io/wikilens/index/Bm25LengthNormTest.kt'

# 두 판이 대소문자를 다르게 다루면 **판을 옮긴 사용자가 같은 질의에 다른 답을 받는다.**
# 두 경로가 어긋나기 쉬운 이유는 각자의 기본값이 반대라서다 — ripgrep(로컬판의 Grep)은
# 대소문자를 구분하고, 서버는 리터럴 경로가 ignoreCase 라 정규식도 거기 맞췄다.
# 실측: rg 로 `coway` 가 `Coway` 를 못 찾고, 서버는 찾는다. 합의가 파일로만 이어져 있어
# 한쪽만 고쳐도 아무 에러가 안 난다.
# 서버 쪽 스캔이 `ContentService` 에서 `JvmGrepEngine`·`RipgrepEngine` 으로 나뉘면서
# 이 검사도 함께 옮겼다 — **계약이 파일 경로를 grep 하므로 파일을 나눌 때 계약도
# 고쳐야 한다**(실제로 이 분할에서 빨개졌다).
check "세 경로가 대소문자를 똑같이 무시함 (로컬 Grep -i · JVM RE2 · rg -i)" \
  'grep -q "Re2.compile(q.pattern, Re2.CASE_INSENSITIVE)" server/src/main/kotlin/io/wikilens/service/JvmGrepEngine.kt \
   && grep -q "ignoreCase = true" server/src/main/kotlin/io/wikilens/service/JvmGrepEngine.kt \
   && ! grep -n "Grep(" plugin/local/skills/search/SKILL.md | grep -qv -- "-i=true"'

# 모델에게 지시하는 파일 다섯(스킬 2 · 커맨드 2 · 레퍼런스 1)은 독자가 같으므로 문체도
# 같아야 한다. 한때 스킬만 존댓말이고 커맨드·레퍼런스는 반말이었다 — 나눌 근거가 없었다.
# 더 중요한 건 **지시가 평서형으로 새는 것**이다: "말합니다 / 답합니다" 는 일어나는 일에
# 대한 서술이지 지시가 아니라서, 하필 환각 방지 규칙이 가장 약한 문형으로 적혀 있었다.
check "모델용 지시 문서가 명령형 존댓말로 통일됨 (평서형은 지시로 안 읽힌다)" \
  '! grep -qnE "^[0-9]+\. .*(말합니다|답합니다|제안합니다)$" plugin/local/skills/search/SKILL.md plugin/client/skills/search/SKILL.md \
   && ! grep -qnE "(말 것\.|한다\.$|않는다\.)" plugin/local/commands/setup.md plugin/local/commands/sync.md plugin/local/references/setup.md'


# **공개판(`oss`)이면 그 판의 파일이 조직판으로 덮이지 않았는지 본다.**
#
# 두 판은 merge 로 잇지 못한다(공개 쪽 이력을 세탁해 공통 조상이 없다). 그래서
# 동기화가 `git checkout master -- .` 뒤 **oss 전용 파일만 손으로 되돌리는** 방식인데,
# 목록에서 하나만 빠지면 조직 정보가 그대로 덮여 들어온다 — 실측(2026-08-20): 실험
# 기록의 익명화와 계약 주석이 각각 한 번씩 되살아났고, **둘 다 커밋까지 갔다.**
#
# D25 에 절차를 적었지만 **적는 것으로는 안 막힌다.** 그래서 계약이 본다.
#
# 판별을 브랜치 이름으로 하지 않는다 — detached HEAD·CI 에서 깨진다. `README.md` 의
# 마켓플레이스 URL 이 그 판의 정체이므로 그것으로 가른다.
#
# **`queries.py` 의 판별 기준이 바뀌었다**(2026-08-23). 예전에는 자리표시자
# `000000001` 이 남아 있는지로 봤는데, 공개판이 **재현 가능한 공개 코퍼스**(ONAP)로
# 채워지면서 그 표식이 사라졌다. 검사하려던 것은 "자리표시자가 있나" 가 아니라
# **"조직판 질의로 덮이지 않았나"** 이므로, 공개 코퍼스의 표식이 있고 조직 고유
# 낱말이 없는지를 본다. 표식 하나만 보면 양쪽이 다 없는 빈 파일도 통과한다.
check "공개판이면 oss 전용 파일이 조직판으로 안 덮임 (동기화가 되돌릴 목록을 빠뜨림)" \
  'if grep -q "github.com/mukansei/wikilens" README.md; then         # 조직판이면 해당 없음
     grep -q "lf-onap.atlassian.net" bench/queries.py \
       && ! grep -q "mOrder\|ACUPI\|디지털세일즈" bench/queries.py \
       && grep -q "같은 제목" docs/experiment-2026-08-14-answer.md \
       && grep -q "emptyList<C>" server/src/test/kotlin/io/wikilens/index/Bm25LengthNormTest.kt \
       && ! grep -q "CowaySDK" docs/report-2026-08-21-learning-effect.md
   fi'

# **커밋 저자도 조직 정보다.** git 은 브랜치별 `user.email` 설정이 없어서(전역/로컬
# 하나뿐), 공개판에서 커밋하면 조직 계정이 그대로 박힌다 — 실측(2026-08-20): 304커밋
# 전부가 조직 이름·이메일이었고 `filter-repo --mailmap` 으로 고쳐야 했다.
#
# 되돌리기 어려운 쪽이라 **커밋 전에** 잡는 것이 요점이다. 공개판에서 작업할 때는
# `git config user.email` 을 개인 값으로 바꾸고, 조직판으로 돌아갈 때 되돌린다.
# **커밋 메시지도 조직 정보를 나른다 — 세탁이 파일 내용만 했다.**
#
# `filter-repo --replace-text` 는 **메시지를 안 건드린다.** 실측(2026-08-27):
# 공개판 커밋 메시지 19줄에 `Coway`·`wiki.coway.com` 이 남아 있었고, 파일 계약과
# 저자 계약이 둘 다 통과했다 — 검사 범위 밖이었다.
#
# 호스트명도 함께 본다. 차단 목록이 제품·직군명 위주라 **도메인이 빠져 있었다.**
#
# **GitHub 조직 슬러그도 뒤늦게 더했다**(2026-08-27). `cw-smart-catalogue` 는
# 회사명을 안 담고 있어 `coway` grep 에도, 차단 목록에도 안 걸렸다 — 세탁 네 번과
# 전수 조사 한 번을 전부 통과했고 공개판 트리에 4곳이 살아 있었다.
# **차단 목록은 "무엇이 조직을 가리키나" 가 아니라 "무엇이 회사명인가" 로 자라 왔다.**
check "공개판 커밋 메시지·파일에 조직 도메인·사번이 없음 (세탁은 파일 내용만 한다)" \
  'if grep -q "github.com/mukansei/wikilens" README.md; then
     [ -z "$(git log oss --format="%s%n%b" | grep -iE "coway|메가존|megazone|t2512624|cw-smart-catalogue")" ] \
     && [ -z "$(git grep -ilE "coway\.com|t2512624|@partner|cw-smart-catalogue" -- . ":!contract/shared_contract.sh")" ]
   fi'

check "공개판 이력에 조직 계정이 없음 (git 은 브랜치별 user 설정이 없다)" \
  'if grep -q "github.com/mukansei/wikilens" README.md; then
     [ -z "$(git log --format="%an <%ae>%n%cn <%ce>" -50 | grep -iE "coway|메가존")" ]
   fi'

# **태그도 조직 정보를 나른다 — `git log` 로는 안 보인다.**
#
# `git tag -a` 는 태그 객체에 **tagger 를 박는다.** 워크트리별 `user.email` 을
# 나눠 뒀어도 태그를 다른 트리에서 만들면 그쪽 설정이 들어간다 — 실측(2026-08-26):
# 공개 저장소의 `v0.18.1` tagger 가 조직 계정이었고, 커밋 317개는 전부 깨끗해서
# 위 계약이 통과했다. GitHub 태그 페이지에 그 이름이 그대로 보인다.
#
# **태그 객체 안의 이름도 본다.** `refs/tags/A:refs/tags/B` 로 밀면 겉이름만
# 바뀌고 객체 안 `tag A` 는 남는다 — 내부 구분용 이름이 그대로 공개된다.
check "공개판 태그에 조직 계정·내부 이름이 없음 (tagger 는 git log 에 안 보인다)" \
  'if grep -q "github.com/mukansei/wikilens" README.md; then
     bad=0
     for t in $(git tag -l "v*"); do
       o=$(git cat-file -p "$t" 2>/dev/null) || continue
       printf "%s" "$o" | grep -q "^tagger" || continue        # 경량 태그는 tagger 가 없다
       printf "%s" "$o" | grep -qiE "^tagger.*(coway|메가존)" && bad=1
       printf "%s" "$o" | grep -qE "^tag $t\$" || bad=1       # 객체 안 이름이 ref 와 달라짐
     done
     [ "$bad" = "0" ]
   fi'

# **그리고 실제로 조직 정보가 샜는지 직접 본다.** 위 검사는 "그 판의 파일인가" 만 보고,
# 파일 안에 새 문장이 들어오는 것은 못 잡는다 — 실측으로 겪은 것이 정확히 그 모양이다
# (`DECISIONS.md` 는 oss 전용 파일이 아닌데 익명화가 풀렸다).
check "공개판에 조직 문서 제목·식별자가 없음 (전 파일)" \
  'if grep -q "github.com/mukansei/wikilens" README.md; then
     [ -z "$(git grep -lE "CowaySDK|Admin BE-|국내DT영업|코디 신청|ACUPI Task|CDMC" \
             -- . ":!contract/shared_contract.sh")" ]
   fi'

# 이 도구는 Cloud·Server/DC 어느 조직 인스턴스에도 붙는다. 그런데 개발 코퍼스가 한
# 회사 것이라 그 이름이 **배포물로 새기 쉽다** — 실제로 `setup` 이 만들어 주는
# `~/.wikilens/env.sh` 템플릿에 "Coway(wiki.coway.com)라면" 이 들어가 있었다. 남의
# 회사 사람이 설치하면 자기 자격증명 파일에서 그 이름을 보게 된다.
# 검사 대상은 **사용자가 통째로 받는 플러그인**이다. `cli/wikilens/layout.py` 의
# "측정한 것 (Coway 2,377건)" 같은 주석은 남긴다 — 문서의 같은 표기와 마찬가지로
# 수치의 출처를 밝히는 라벨이고, 지우면 그 수가 어디서 나왔는지 알 수 없게 된다.
#
# **회사명만으로는 부족하다 — 목록이 좁아서 실제로 샜다**(2026-08-20 전수 검수).
# 공개판을 분리하면서 `coway` 로만 훑었는데, 이 계약의 목록이 정본처럼 보였기 때문이다.
# 그때 배포물 넷에 남아 있던 것: 스킬·README 의 검색 예시에 든 **직군명**, 테스트
# 픽스처의 **실제 문서 제목+ID 쌍**, 그리고 사내 **시스템명** 셋.
#
# (아래 목록은 막을 낱말이라 여기 적힐 수밖에 없다. 그래서 이 파일은 검사 범위 밖이다 —
#  `plugin/local plugin/client` 만 본다.)
#
# 그래서 **제품·직군·조직 단위까지** 막는다. 회사명 하나만 막는 가드는 "가드가 있다"
# 는 사실만 주고 넓이는 안 준다.
check "설치되는 플러그인에 회사 고유값이 없음 (측정 라벨은 주석·문서에만)" \
  '! grep -rniE "coway|cwdomesticdt|megazone|메가존|코디|코닥|국내DT|영업DT|ACUPI|mOrder|통합회원|서비스매니저|디지털세일즈" plugin/local plugin/client'


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
" server/src/main/kotlin/io/wikilens/config/WikiLensProperties.kt server/src/main/kotlin/io/wikilens/config/LearnProps.kt server/src/main/resources/application.yml\'


# 자격증명 파일 경로를 아는 곳은 **파이썬 둘뿐**이어야 한다 — CLI(`credentials.py`)와
# 진단(`vault_status.py`). 갈리면 CLI 는 읽는데 진단은 "없다" 고 한다.
#
# **래퍼는 조립하지 않고 물어본다.** 예전에는 `$HOME/.wikilens/env.sh` 를 직접 만들었는데,
# 셸의 `$HOME` 과 파이썬의 `Path.home()` 은 같은 자리가 아닐 수 있다 — Windows 에서
# 파이썬은 `USERPROFILE` 을 보고 Git Bash 의 `HOME` 은 홈 드라이브로 잡혀 있을 수 있다.
# 갈리면 래퍼가 소싱한 파일과 CLI 가 읽는 파일이 달라 **자격증명이 있는데 없다고 죽는다**
# (macOS JDK 의 `user.home` vs `HOME` 과 같은 실패 — 조용히 실패 20번).
# **교대(`A|B`)로 쓰면 안 된다.** 예전에는 `^(ENV_PATH = CONFIG_DIR|CONFIG_DIR = …)`
# 였는데, 앞쪽이 파일명을 안 봐서 `env.sh` → `env2.sh` 로 바꿔도 통과했다(실측).
# 한쪽만 강하게 검사하면 그 계약은 **약한 쪽만큼만** 강하다.
check "자격증명 경로 해석처가 파이썬 둘뿐 (래퍼는 조립하지 않고 물어본다)" \
  'grep -q "^CONFIG_DIR = Path.home() / \".wikilens\"" cli/wikilens/credentials.py \
   && grep -q "^ENV_PATH = CONFIG_DIR / \"env.sh\"" cli/wikilens/credentials.py \
   && grep -q "^ENV_PATH = CONFIG_DIR / \"env.sh\"" plugin/local/scripts/vault_status.py \
   && grep -q "\-\-env-path" plugin/local/scripts/vault_status.py \
   && grep -q "vault_status.py\" --env-path" plugin/local/scripts/wikilens_cli.sh \
   && ! grep -q "HOME/.wikilens/env.sh" plugin/local/scripts/wikilens_cli.sh'

# `~/.wikilens/config.json` 의 볼트 키를 이제 **세 언어가** 읽는다 — Python 진단·설정
# (`vault_status.py`), Python setup(`setup_vault.py`), Kotlin 서버(`UserConfig.kt`).
# 서버가 읽는 이유는 심링크를 손으로 만드는 단계를 없애기 위해서다. 문자열로만
# 이어져 있어 키가 갈리면 **예외 없이 폴백만 조용히 멈추고**, 증상은 "볼트가 비었다"로
# 나타나 원인이 설정 키에 있다는 걸 알 방법이 없다.
check "볼트 설정 키가 세 곳에서 같음 (config.json 의 \"vault\")" \
  'grep -q "cfg.get(\"vault\")" plugin/local/scripts/vault_status.py \
   && grep -q "cfg\[\"vault\"\] = str(vault)" plugin/local/scripts/setup_vault.py \
   && grep -q "VAULT_KEY = \"vault\"" server/src/main/kotlin/io/wikilens/config/UserConfig.kt'

# 폴백이 걸리는지 판단하려면 "사용자가 값을 줬는가"를 알아야 하는데 Spring 은 기본값과
# 명시값을 구분해주지 않는다. 상수와 yml 이 갈리면 **명시로 준 기본 경로가 폴백을 타서**
# 오타를 조용히 덮는다 — 명시가 이긴다는 규칙이 뒤집힌다.
# 색인(`IndexingService`)과 읽기(`ContentService`)가 각자 볼트를 풀던 시절, 후자는
# `toAbsolutePath()` 조차 안 걸어 실행 디렉터리에 매달려 있었다. 폴백이 들어오자 갈림이
# 결정적이 됐다 — 실측: 문서 3건 색인·검색 정상인데 **read 는 전부 404**.
check "볼트 경로 해석처가 한 곳 (VaultLocator — 갈리면 검색은 되고 읽기만 404)" \
  '[ "$(grep -rl "props\.vaultRoot" server/src/main/kotlin | wc -l | tr -d " ")" = "1" ] \
   && grep -q "props.vaultRoot" server/src/main/kotlin/io/wikilens/vault/VaultLocator.kt \
   && code_has server/src/main/kotlin/io/wikilens/service/ContentService.kt "locator.root" \
   && grep -q "locator.root" server/src/main/kotlin/io/wikilens/service/IndexingService.kt'

check "서버 볼트 기본값이 상수와 application.yml 에서 같음 (폴백 판정 근거)" \
  'python3 -c "
import re, pathlib, sys
kt = pathlib.Path(\"server/src/main/kotlin/io/wikilens/config/WikiLensProperties.kt\").read_text()
yml = pathlib.Path(\"server/src/main/resources/application.yml\").read_text()
m = re.search(r\"DEFAULT_VAULT_ROOT = .(\\S+).\", kt)
y = re.search(r\"^  vault-root: (\\S+)$\", yml, re.M)
sys.exit(0 if m and y and m.group(1) == y.group(1) else 1)
"'

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
# 불가), 서버판은 모듈 최상단이라 **프록시가 기동 중 죽어 도구 전부가 사라진다.**
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
  '[ -f server/src/test/kotlin/io/wikilens/service/GrepEngineParityTest.kt ] \
   && grep -q "두 엔진이 같은 매치를 낸다" server/src/test/kotlin/io/wikilens/service/GrepEngineParityTest.kt \
   && grep -q "대소문자를 무시한다" server/src/test/kotlin/io/wikilens/service/GrepEngineParityTest.kt \
   && grep -q "목록 밖을 내보내지 않는다" server/src/test/kotlin/io/wikilens/service/GrepEngineParityTest.kt'

# **엔진은 ACL 을 몰라야 한다.** 권한 해석이 엔진마다 갈리면 한쪽이 조용히 더 보여준다 —
# `AclRegistry` 에 스위치를 한 곳만 둔 것과 같은 이유다. 거르는 것은 ContentService 다.
check "grep 엔진이 ACL 을 직접 보지 않음 (호출부가 이미 거른 목록만 받는다)" \
  '! grep -lE "AclRegistry|canSee|tokensFor" \
      server/src/main/kotlin/io/wikilens/service/JvmGrepEngine.kt \
      server/src/main/kotlin/io/wikilens/service/RipgrepEngine.kt'

# `--no-config` 이 없으면 운영자의 `~/.ripgreprc` 가 플래그를 얹어 **같은 질의가 머신마다
# 다른 답**을 낸다. `-i` 는 두 판이 함께 지키는 대소문자 계약이다.
check "ripgrep 이 사용자 환경을 안 받고 대소문자를 무시함 (--no-config · -i)" \
  '[ "$(grep -cE "^ *add\(\"--no-config\"\)" server/src/main/kotlin/io/wikilens/service/RipgrepEngine.kt)" = "1" ] \
   && [ "$(grep -cE "^ *add\(\"--no-ignore\"\)" server/src/main/kotlin/io/wikilens/service/RipgrepEngine.kt)" = "1" ] \
   && [ "$(grep -cE "^ *add\(\"-i\"\)" server/src/main/kotlin/io/wikilens/service/RipgrepEngine.kt)" = "1" ]'

# 관리 API 가 열려 있으면 서버에 닿는 누구나 `acl/user` 로 **스스로 권한을 부여**한다 —
# 권한을 아무리 정확히 수집해도 이게 열려 있으면 의미가 없다. 기본이 "열림" 이면
# 조용히 열린 채 배포되므로 **잠김이 기본**이어야 하고, 거부는 404 여야 한다(403 은
# 엔드포인트의 존재를 알린다 — `read` 와 같은 규칙).
check "관리 API 가 기본 잠김이고 경로로 잠김 (엔드포인트마다 세지 않는다)" \
  'grep -q "val adminToken: String = \"\"" server/src/main/kotlin/io/wikilens/config/WikiLensProperties.kt \
   && grep -q "^  admin-token: \"\"$" server/src/main/resources/application.yml \
   && grep -q "ADMIN_PATHS = \"/api/admin/\*\*\"" server/src/main/kotlin/io/wikilens/api/AdminGuardConfig.kt \
   && grep -q "addInterceptor(guard).addPathPatterns(ADMIN_PATHS)" server/src/main/kotlin/io/wikilens/api/AdminGuardConfig.kt \
   && ! grep -rq "guard.check" server/src/main/kotlin/io/wikilens/api/Controller.kt'

# `mirror/acl/acl.json` 은 CLI 가 쓰고 Kotlin 이 읽는 **파일로만 이어진 계약**이다.
# 갈리면 서버가 파일을 못 읽어 전 페이지가 `@public` 폴백이 된다 — 조용한 과다 노출.
check "ACL 파일 경로·형식이 Python 과 Kotlin 에서 같음 (mirror/acl/*.json)" \
  'grep -q "root / \"mirror\" / \"acl\"" cli/wikilens/acl.py \
   && grep -q "resolve(\"mirror\").resolve(\"acl\")" server/src/main/kotlin/io/wikilens/vault/VaultReader.kt'

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
  '[ -f server/src/main/kotlin/io/wikilens/acl/UserStore.kt ] \
   && code_has server/src/main/kotlin/io/wikilens/acl/UserStore.kt "ATOMIC_MOVE" \
   && grep -q "store?.save(byUser)" server/src/main/kotlin/io/wikilens/acl/AclRegistry.kt'

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
  '! git grep -lE "isEnforced|aclEnforced" -- server/src/main/kotlin/io/wikilens/service \
        server/src/main/kotlin/io/wikilens/index server/src/main/kotlin/io/wikilens/vault'

# **기본값이 꺼짐으로 바뀌었다**(2026-08-11). 지금의 시행은 `sync` 가 권한을 안 가져와
# 실질적으로 사용자 허용목록이라, 얻는 것 없이 전원이 빈손이 되는 함정이었다.
#
# 그래서 "켜짐인가" 는 더는 검사할 것이 아니다. **남는 불변식은 둘이 일치하는가**다 —
# 코드 상수와 `application.yml` 이 갈리면 운영자가 한쪽을 고치고 다른 쪽이 조용히
# 이긴다. 그리고 꺼짐이 기본이므로 **"꺼진 것이 보이는가"(아래 계약)가 이 기본값의
# 전제**가 된다. 다시 켬으로 되돌린다면 두 곳을 함께 고쳐야 이 검사가 통과한다.
check "ACL 시행 기본값이 상수와 application.yml 에서 같음" \
  'python3 -c "
import re, pathlib, sys
kt = pathlib.Path(\"server/src/main/kotlin/io/wikilens/config/WikiLensProperties.kt\").read_text()
yml = pathlib.Path(\"server/src/main/resources/application.yml\").read_text()
m = re.search(r\"val aclEnforced: Boolean = (true|false)\", kt)
y = re.search(r\"^  acl-enforced: (true|false)$\", yml, re.M)
sys.exit(0 if m and y and m.group(1) == y.group(1) else 1)
"'

# 꺼두면 계속 말해야 한다 — 기동 로그 한 번으로는 재기동 뒤 아무도 모른다.
check "ACL 시행이 꺼진 것이 기동·stats·--status 세 곳에서 보임" \
  'grep -q "ACL 시행이 꺼져 있습니다" server/src/main/kotlin/io/wikilens/WikiLensApplication.kt \
   && grep -q "aclEnforced" server/src/main/kotlin/io/wikilens/api/Controller.kt \
   && grep -q "ACL_ENFORCED" plugin/client/mcp/wikilens_mcp.py'

# 권한이 좁은 사용자는 상위 후보가 전부 안 보일 때 **힌트가 통째로 0** 이 된다 —
# 볼 수 있는 후보가 더 아래에 있어도 슬롯을 이미 뺏겼기 때문이다. `SearchService` 가
# 어휘 결과에서 이미 겪은 실패다(조용히 실패 8번: "take 를 필터 뒤로"). 지금은 전 페이지가
# @public 이라 안 보이고 **ACL 수집이 들어오는 순간** 나타난다.
check "서빙 못 할 힌트를 자르기 전에 거름 (권한 + 존재)" \
  'grep -q "visible: (String) -> Boolean" server/src/main/kotlin/io/wikilens/learn/TrajectoryStore.kt \
   && grep -q "if (!visible(pid))" server/src/main/kotlin/io/wikilens/learn/TrajectoryStore.kt \
   && grep -qE "store\.hints\(.*limit\) \{ pid ->" server/src/main/kotlin/io/wikilens/service/SearchService.kt \
   && grep -q "acl.canSee(tokens, pid) && index.metaOf(pid) != null" server/src/main/kotlin/io/wikilens/service/SearchService.kt'

# 궤적에 남기는 것은 권한 **범위**(토큰 해시)이지 신원이 아니다. userKey 가 들어가면
# "누가 무엇을 검색했나" 가 영구 기록으로 남는데 그건 이 도구가 지금 안 하는 일이고,
# 해결하려는 문제(권한 폭에 따른 학습 오염)는 범위만 알면 풀린다.
check "궤적이 신원이 아니라 권한 범위를 남김 (userKey 필드 없음)" \
  'grep -q "val scope: String" server/src/main/kotlin/io/wikilens/learn/Trajectory.kt \
   && ! grep -q "userKey" server/src/main/kotlin/io/wikilens/learn/Trajectory.kt \
   && grep -q "MessageDigest" server/src/main/kotlin/io/wikilens/acl/AclRegistry.kt'

# Lucene write.lock 은 재색인 동안만 잡힌다. 그 밖의 시간에 둘째 프로세스가 붙으면
# 각자 다른 포스팅을 들고 같은 궤적 로그에 쓴다 — 갈림이 재기동 전까지 안 드러난다.
check "상태 디렉터리 단일 쓰기 보증 (락 + 읽을 수 있는 기동 실패)" \
  '[ -f server/src/main/kotlin/io/wikilens/learn/StateDirLock.kt ] \
   && grep -q "stateDirLock" server/src/main/kotlin/io/wikilens/WikiLensApplication.kt \
   && grep -q "FailureAnalyzer" server/src/main/resources/META-INF/spring.factories'

# 로그 쓰기가 실패해도 메모리 학습은 계속되므로, 갈라지고 있다는 사실 자체를 밖으로
# 내야 한다. 예전에는 WARN 한 줄이 전부라 재기동 때까지 아무도 몰랐다.
check "궤적 로그 상태가 stats 와 --status 에 드러남 (쓰기 실패·재생 누락·증가)" \
  'grep -q "fun status()" server/src/main/kotlin/io/wikilens/learn/FileTrajectorySink.kt \
   && grep -q "trajectoryLog" server/src/main/kotlin/io/wikilens/api/Controller.kt \
   && grep -q "trajectoryLog" plugin/client/mcp/wikilens_mcp.py \
   && grep -q "writeFailures" plugin/client/mcp/wikilens_mcp.py \
   && grep -q "replaySkipped" plugin/client/mcp/wikilens_mcp.py'
# 로그는 append-only 라 줄지 않는다. 압축은 넣지 않았지만(실측: 100만 건 = 210MB·5.3초,
# 20명 팀이면 7년치) 아무도 안 보면 기동이 조용히 느려진다 — 임계에서 알리는 것이
# 그것을 대신한다. 근거는 DECISIONS.md D17.
check "궤적 로그 증가가 임계에서 경고됨 (압축 대신 관측)" \
  'grep -q "const val SLOW_REPLAY_MILLIS" server/src/main/kotlin/io/wikilens/learn/FileTrajectorySink.kt \
   && grep -q "replayMillis > SLOW_REPLAY_MILLIS" server/src/main/kotlin/io/wikilens/learn/FileTrajectorySink.kt \
   && grep -q "log.warn" server/src/main/kotlin/io/wikilens/learn/FileTrajectorySink.kt'

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
  'grep -q "Map<String, List<String>>?" server/src/main/kotlin/io/wikilens/vault/VaultReader.kt \
   && grep -q "aclByPage == null -> listOf(PUBLIC)" server/src/main/kotlin/io/wikilens/vault/VaultReader.kt \
   && grep -q "emptyList<String>().also { unresolved++ }" server/src/main/kotlin/io/wikilens/vault/VaultReader.kt \
   && grep -q "unresolved" cli/wikilens/acl.py'

# 본문 스캔 경로가 둘이다. 어느 쪽으로 처리됐는지가 밖에서 안 보이면 답이 왜 다른지
# 물을 수도 없다 — 기동 로그는 콘솔 전용이라 로그를 못 보는 운영자에게 안 닿는다.
check "어느 grep 엔진인지 stats 와 --status 에 드러남" \
  'grep -q "val engineName" server/src/main/kotlin/io/wikilens/service/ContentService.kt \
   && grep -q "\"grepEngine\" to content.engineName" server/src/main/kotlin/io/wikilens/api/Controller.kt \
   && grep -q "GREP_ENGINE=" plugin/client/mcp/wikilens_mcp.py'

# grep 은 죄고 있었는데 search 만 안 죄고 있었다. 500 두 경로(0 이하 · 곱셈 오버플로우)
# 보다 나쁜 것은 **서빙한 힌트가 궤적 로그에 영구히 남는다**는 것이다 — append-only 이고
# 유일한 복구 불가 자산이라 한 요청이 수천 개를 적어 넣을 수 있으면 안 된다.
check "클라이언트가 주는 limit 을 두 경로 모두 상한으로 죔 (search·grep)" \
  'grep -q "req.limit.coerceIn(1, MAX_LIMIT)" server/src/main/kotlin/io/wikilens/service/SearchService.kt \
   && grep -q "limit.coerceIn(1, MAX_LIMIT)" server/src/main/kotlin/io/wikilens/service/ContentService.kt'

# `acl` 은 페이지마다 낱개 조회를 해서 이 프로젝트에서 API 를 가장 세게 쓴다. 429 를
# 못 견디면 곧 "조회 실패" 이고, 전부 실패한 결과를 쓰면 서버가 그것을 **전 페이지
# 비공개**로 읽는다 — 못 읽은 것과 없는 것은 다르다.
check "429 백오프가 모든 GET 에 걸리고, 전부 실패하면 acl.json 을 안 씀" \
  'grep -q "if r.status_code != 429:" cli/wikilens/sync.py \
   && ! grep -q "if r.status_code == 429:" cli/wikilens/sync.py \
   && grep -q "rep.page_failed >= len(pages)" cli/wikilens/acl.py \
   && grep -q "rep.wrote" cli/wikilens/cli.py'

# 궤적 로그는 append-only 이고 유일한 복구 불가 자산이다. 거기로 흘러가는 것 — 항 목록·
# sessionId — 에 상한이 없으면 한 요청이 무한정 적어 넣는다. `limit` 과 `MAX_PATTERN` 만
# 죄고 이 둘은 안 죄던 것이 **같은 판단의 비대칭**이었다.
check "로그로 흘러가는 것에 상한이 있음 (질의·항·sessionId·세션 수)" \
  'grep -q "const val MAX_QUERY" server/src/main/kotlin/io/wikilens/service/SearchService.kt \
   && grep -q "keywords.take(MAX_KEYWORDS)" server/src/main/kotlin/io/wikilens/learn/TrajectoryStore.kt \
   && grep -q "sessionId.length > MAX_SESSION_ID" server/src/main/kotlin/io/wikilens/learn/TrajectoryStore.kt \
   && grep -q "sessions.size >= MAX_SESSIONS" server/src/main/kotlin/io/wikilens/learn/TrajectoryStore.kt'

# `LOCALIZATION 만 간선 생성` 은 계약으로 잠겨 있는데, 그 게이트가 실제로 무엇을 걸러내는지
# 밖에서 볼 방법이 없었다. UNKNOWN 이 거의 0 이면 게이트는 사실상 항등함수다.
check "게이트의 종류 분포가 stats 와 --status 에 드러남" \
  'grep -q "\"byKind\" to QueryKind.entries" server/src/main/kotlin/io/wikilens/learn/TrajectoryStore.kt \
   && grep -q "QUERY_KINDS=" plugin/client/mcp/wikilens_mcp.py'

# 문턱 판정을 cdf 1회로 바꿨다(실측 5~18배). 빠른 길과 정확한 길이 어긋나면 **서빙 여부가
# 조용히 달라진다** — 검색은 정상으로 보이고 힌트만 다르게 나온다. 커버리지 축이 특히
# 중요하다: 실제 판정은 `ebLower * c >= 문턱` 이라 페이지별 문턱이 `문턱 / c` 다.
check "빠른 문턱 판정이 이분법과 대조됨 (커버리지 축 포함)" \
  'grep -q "fun meetsThreshold" server/src/main/kotlin/io/wikilens/learn/Reliability.kt \
   && grep -q "Reliability.meetsThreshold" server/src/main/kotlin/io/wikilens/learn/TrajectoryStore.kt \
   && grep -q "for (c in listOf" server/src/test/kotlin/io/wikilens/learn/ReliabilityThresholdTest.kt'

# 거부된 질의는 검색이 아예 안 돈 것이라 관측할 것이 없다. 관측하면 세션 객체가 생기고
# `sinceStart` 의 원시 계측이 클라이언트 오류로 오염된다. 결과 0건과는 다르다 —
# 그건 진짜 시도이고 일부러 센다.
check "거부된 질의는 궤적으로 관측하지 않음 (0건과는 다르다)" \
  'grep -q "if (res.error != null) return res" server/src/main/kotlin/io/wikilens/api/Controller.kt'

# 성능 측정이 실코퍼스에 매달리면 두 가지가 무너진다: 그 머신 밖에서는 검증이 안 되고
# (테스트가 통째로 건너뛴다), 나온 값이 소프트웨어가 아니라 그 위키에 대한 사실이 된다.
# 실제로 그렇게 적힌 상수 하나가 2배 틀린 채로 설계 결정의 근거가 돼 있었다.
check "성능 측정이 합성 볼트로 재현됨 (실코퍼스 없이도 돈다)" \
  '[ -f server/src/test/kotlin/io/wikilens/SyntheticVault.kt ] \
   && grep -q "SyntheticVault" server/src/test/kotlin/io/wikilens/service/RipgrepBudgetTest.kt \
   && grep -q "SyntheticVault" server/src/test/kotlin/io/wikilens/service/GrepScaleTest.kt \
   && ! grep -q "System.getProperty(\"user.home\")" server/src/test/kotlin/io/wikilens/service/RipgrepBudgetTest.kt'

# `canSee` 는 조건이 둘인데(등록됐나 + 토큰이 겹치나) 진단이 첫째만 보고 있었다.
# 둘째는 `wikilens acl` 을 처음 돌리면 **반드시** 걸린다 — 페이지 토큰이 @public 에서
# @space:<KEY> 로 바뀌면서 기존 등록이 전부 안 맞게 된다. 등록·색인은 멀쩡하다.
check "토큰이 안 겹치는 상태가 stats 와 --status 에 드러남" \
  'grep -q "fun tokenOverlap" server/src/main/kotlin/io/wikilens/acl/AclRegistry.kt \
   && grep -q "\"aclTokenOverlap\" to acl.tokenOverlap()" server/src/main/kotlin/io/wikilens/api/Controller.kt \
   && grep -q "aclTokenOverlap" plugin/client/mcp/wikilens_mcp.py'

# Windows 는 인터프리터 이름이 python·py 라 python3 를 박으면 서버판 사용자가 전혀 못
# 쓴다. 그리고 `${VAR}` 를 **기본값 없이** 쓰면 미설정일 때 리터럴 문자열이 전달돼
# 죽는다(조용히 실패 25번) — 둘을 함께 검사한다.
check "MCP 인터프리터가 이름 고정이 아니고 기본값을 가짐 (Windows)" \
  'grep -q "WIKILENS_PYTHON:-python3" plugin/client/.mcp.json \
   && ! grep -qE "\"command\": \"python3?\"" plugin/client/.mcp.json'

# Git for Windows 의 기본값이 `core.autocrlf=true` 라, `.gitattributes` 가 없으면
# Windows 에서 clone 할 때 `.sh` 가 CRLF 로 나온다. bash 는 첫 줄부터
# `$'\r': command not found` 로 죽고, 증상이 문법 오류처럼 보여 원인을 찾기 어렵다.
check "셸 스크립트가 CRLF 로 체크아웃되지 않음 (.gitattributes)" \
  '[ -f .gitattributes ] && grep -q "^\*\.sh text eol=lf" .gitattributes'

# 파이썬 이름은 플랫폼마다 다르다(python3 · python · py). 래퍼가 하나를 박으면
# Windows 에서 볼트를 만드는 모든 경로가 통째로 안 돈다.
check "래퍼가 파이썬 이름을 고정하지 않음 (Windows)" \
  'grep -q "for _c in \"\${WIKILENS_PYTHON:-}\" python3 python" plugin/local/scripts/wikilens_cli.sh \
   && ! grep -qE "^[^#]*python3 \"\$" plugin/local/scripts/wikilens_cli.sh'

# DTO 필드 이름이 곧 JSON 키이고 MCP 프록시가 그 키로 읽는다. 문자열로만 이어져 있어
# 한쪽만 바꾸면 컴파일도 테스트도 통과하는데 런타임에 그 자리가 빈다. 정본을 두고
# 양쪽이 그것에 맞는지 본다 — Kotlin 은 직렬화 결과로, 프록시 테스트는 가짜 서버가
# 내는 키로. (실측: 정본을 넣자마자 가짜 서버의 tree 응답에 `truncated` 가 빠져 있던
# 것을 잡았다 — 실제 서버가 절대 안 내는 모양을 테스트하고 있었다.)
check "와이어 포맷 정본이 있고 양쪽이 그것을 검사함" \
  '[ -f contract/wire-format.json ] \
   && grep -q "wire-format.json" server/src/test/kotlin/io/wikilens/api/WireFormatTest.kt \
   && grep -q "wire-format.json" plugin/tests/test_mcp_proxy.py'

# 컨테이너가 비루트로 도는데 마운트 지점이 이미지에 없으면, Docker 가 새 named volume 을
# `root:root` 로 만들어 **서버가 기동조차 못 한다**(첫 쓰기가 `StateDirLock` 의
# `/state/.lock` 이다). `compose.yml` 이 문서화된 유일한 기동 경로라 이게 깨지면 아무도
# 못 띄운다. `docker run` + bind mount 로는 안 걸린다 — Docker Desktop for Mac 이 bind
# mount 만 권한을 재매핑하기 때문이다. 그래서 검증이 통과해도 이 계약이 따로 필요하다.
#
# `mkdir`·`chown` 이 `USER` **앞**이어야 한다. 뒤면 비루트라 chown 이 못 돈다.
# **경로를 하드코딩하지 않는다** — `ENV HOME` 을 옮기면(2026-08-26: `/home/wikilens`
# → `/data`) 이 계약만 옛 자리를 봐서 깨진다. `HOME` 값을 읽어 그 아래를 검사한다.
check "컨테이너가 마운트 지점을 소유함 (없으면 compose 기동 실패)" \
  'home=$(grep -oE "^ENV HOME=\S+" server/Dockerfile | cut -d= -f2)
   [ -n "$home" ] || exit 1
   grep -q "chown -R wikilens:wikilens $home" server/Dockerfile || exit 1
   grep -q "mkdir -p $home/.wikilens/vault" server/Dockerfile || exit 1
   c=$(grep -n "chown -R wikilens:wikilens $home" server/Dockerfile | head -1 | cut -d: -f1)
   u=$(grep -n "^USER wikilens" server/Dockerfile | head -1 | cut -d: -f1)
   [ "$c" -lt "$u" ]'

# README 최상단 배지는 빌드 파일의 버전을 **손으로 복제**한 값이다(저장소가 비공개라
# shields.io 가 아무것도 못 읽는다). 어긋나면 배지만 옛 버전을 말한다 — 근거와
# 판정 규칙은 `badge_versions.py` 의 독스트링에.
check "README 배지 버전이 빌드 파일과 같음 (배지는 손으로 복제한 값이다)" \
  'python3 contract/badge_versions.py'

echo
if [ "$fail" -eq 0 ]; then
  echo "계약 ${total}개 모두 유지됨."
else
  printf '%s' "$broken"
  echo "$fail/$total 개 계약이 깨졌습니다. CLAUDE.md 의 '절대 깨면 안 되는 계약'을 확인하세요."
fi
exit "$fail"
