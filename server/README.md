# WikiLens 서버 (Spring Boot + Lucene/Nori)

Confluence 미러를 **서버측에서 색인**하고, 탐색 궤적을 축적해 랭킹을 보강합니다.
읽기는 여전히 클라이언트 로컬입니다 — 서버는 좌표만 반환합니다.

## 검증 상태 (먼저 읽으세요)

| 레이어 | 검증 |
|---|---|
| `learn/` (게이트·EB·궤적·포스팅) | JUnit 통과. 기대값 6개가 `scoring_reference.py` 산출과 1e-6 일치 |
| `index/` `service/` `api/` `vault/` `acl/` (Lucene·Spring 배선) | 빌드·`bootRun`·재색인 검증됨 (Coway 실데이터 2,378건, 2026-08) |
| JUnit 전체 | 통과 (`./gradlew test`) |
| 검색 랭킹 **품질** | **미검증.** 배선이 도는 것과 랭킹이 좋은 것은 다른 문제다 |

```bash
./gradlew test       # JUnit 전체
./gradlew bootRun    # 개발용 기동 (작업 디렉터리가 server/ 로 고정된다)
```

## 처음 clone 했다면 볼트부터 연결하세요

`bootRun` 은 기본값 `./mirror-root` 를 보는데, **그건 저장소에 없습니다**(gitignore).
그대로 띄우면 이렇게 됩니다:

```
ERROR  볼트에서 문서를 하나도 못 읽었습니다: …/server/mirror-root
```

기동은 되지만 검색이 전부 빕니다. 이미 로컬판으로 만든 볼트가 있으면 심링크 하나면
됩니다 — 사본을 만들지 마세요. 볼트가 둘이 되면 어느 쪽을 색인했는지 알 수 없습니다:

```bash
ln -s ~/wiki server/mirror-root      # 로컬판이 쓰는 볼트를 그대로
```

볼트가 아직 없으면 먼저 만듭니다(`cli/README.md` 참고):

```bash
wikilens --root ~/wiki sync --space <KEY>
```

`--wikilens.vault-root=/절대/경로` 로 매번 주는 것도 됩니다. 심링크를 권하는 이유는
`bootRun` 인자를 IntelliJ 실행 구성과 CLI 양쪽에 중복해 적지 않아도 되기 때문입니다.

### IntelliJ 에서는 **`1. 서버 실행 (bootRun)`** 구성을 쓰세요

`fun main` 옆의 초록 화살표로 띄우면 IntelliJ 가 구성을 즉석에서 만드는데, 그때
**작업 디렉터리가 저장소 루트**가 됩니다. 기본값이 상대경로라 이렇게 갈립니다:

| | 볼트 | 상태(궤적) |
|---|---|---|
| `bootRun` (체크인된 구성) | `server/mirror-root` ✓ | `server/.wikilens/state` ✓ |
| main 에서 바로 실행 | `<루트>/mirror-root` ✗ | `<루트>/.wikilens/state` ✗ |

볼트가 없는 건 ERROR 로 바로 보이지만, **상태 디렉터리는 조용히 새로 만들어집니다** —
그러면 옛 궤적을 못 읽은 채 두 갈래로 쌓이고, 궤적은 유일한 복구 불가 자산입니다.
그래서 기존 로그가 없으면 이렇게 경고합니다:

```
WARN  기존 궤적 로그가 없어 새로 시작합니다: … — 처음이면 정상이지만,
      재기동인데 이 줄이 보이면 작업 디렉터리가 달라져 **옛 궤적과 갈라진 것**입니다.
```

이미 갈렸다면 두 `trajectories.jsonl` 을 이어붙이면 복구됩니다 — append-only 라
순서만 맞으면 되고, 포스팅은 재생으로 다시 만들어집니다.

## 배포할 때는 경로를 절대경로로 주세요

기본값이 **상대경로**입니다(`./mirror-root`, `./.wikilens/index`, `./.wikilens/state`).
`bootRun` 은 Gradle 이 작업 디렉터리를 `server/` 로 고정해서 늘 같은 자리를 쓰지만,
**jar 로 띄우면 실행한 디렉터리 기준**이 됩니다.

엉뚱한 디렉터리에서 띄우면 **에러 없이 정상 기동합니다** — 빈 볼트를 보고 문서 0개로
뜨고, `state/` 도 새로 만들어 **학습 궤적이 분기됩니다**(실측 2026-08-06).
궤적은 유일한 복구 불가 자산입니다.

```bash
java --enable-native-access=ALL-UNNAMED -jar wikilens-server.jar \
  --wikilens.vault-root=/srv/wikilens/mirror \
  --wikilens.index-dir=/srv/wikilens/index \
  --wikilens.state-dir=/srv/wikilens/state
```

`--enable-native-access` 는 **로그를 위한 것이지 성능을 위한 것이 아닙니다.** Lucene
`MMapDirectory` 가 `posix_madvise` 를 FFM API 로 부르는데(커널 readahead 힌트), Java 22
부터 네이티브 호출이 "restricted" 라 승인하지 않으면 기동마다 경고 네 줄이 찍힙니다.
빼도 동작합니다 — 실측으로 `--illegal-native-access=deny`(미래 JDK 동작) 에서도
색인 2,383건이 정상이고 시간도 같습니다(2,279ms 대 2,171ms, 노이즈 수준). Lucene 이
madvise 를 못 쓰면 그냥 안 씁니다.

그런데 **이 로그가 진단 도구입니다** — 볼트·색인·상태 경로, `궤적 N건 재생`,
`기동 적재 완료` 를 눈으로 확인하는 구조라, 무해한 경고가 그 사이를 가리면 곤란합니다.
`bootRun` 과 `test` 는 `build.gradle.kts` 에서 이미 켜져 있습니다.

기동 로그 첫 줄이 **실제로 쓰는 절대경로**를 찍습니다. systemd·cron 처럼 작업
디렉터리를 못 믿는 환경에서는 이 줄부터 확인하세요:

```
볼트 /srv/wikilens/mirror · 색인 /srv/wikilens/index · 상태 /srv/wikilens/state
기동 적재 완료: 문서 2377 · ACL 페이지 2377
```

문서가 0개면 `기동 적재 완료` 대신 **ERROR** 가 찍힙니다.

`learn/` 에는 Spring·Lucene import 가 없다 — EB·게이트·궤적은 순수 알고리즘이라
프레임워크와 섞이면 단위 테스트가 통합 테스트로 변질된다. `shared_contract.sh` 가 강제한다.

## 왜 서버가 색인을 갖는가

클라이언트 분산 색인을 철회했습니다. 대가가 컸습니다:

| | 클라이언트 분산 | 서버 색인 |
|---|---|---|
| Confluence API 부하 | 사용자 수에 비례 (**200명이면 200배**) | 1배 |
| dense 임베딩 | 사용자마다 중복 계산 | 1회 |
| IDF | 권한 좁은 사용자는 추정 붕괴 | 전역, 안정 |
| **랭킹 척도** | **사용자마다 다름** | 동일 |
| 보고서(고아·누락 링크) | 개인 뷰라 무의미 | 전역 그래프 |

넷째 줄이 결정적입니다. 척도가 다르면 EB 사전분포로 들어오는 점수가 서로 달라
**이질적인 랭커에서 나온 관측을 하나의 카운터에 섞게** 됩니다. 학습 레이어의 전제가 깨집니다.

대가는 질의 시점 ACL 시행입니다. 이색적인 요구가 아니라 사내 검색의 표준이고,
공유 배포를 하는 이상 어차피 풀어야 하는 문제입니다.

## 왜 JVM인가

**한국어 형태소 분석 하나 때문입니다.** 교착어라 조사가 붙습니다 —
'로그인을/로그인은/로그인이'. 형태소 분석 없이는 BM25가 무너지고,
Lucene Nori가 그 문제의 프로덕션 검증된 해답입니다.

서버가 색인을 갖지 않던 설계에서는 이 근거가 없었고, 실제로 Python으로 만들었습니다.
색인이 돌아오면서 근거도 함께 돌아왔습니다.

## 구성

```
learn/       게이트 · EB · 궤적 · 항 단위 포스팅   ← 프레임워크 의존성 없음
index/       Lucene + Nori, 필드 가중, ACL 필터
vault/       Python 싱크가 만든 미러 읽기
acl/         페이지·사용자 권한 토큰
service/     검색 융합(RRF) · 콘텐츠 서빙(read·grep)
api/         HTTP 표면 — Controller · Dto(= 와이어 포맷)
```

`learn/`이 Spring도 Lucene도 참조하지 않는 것은 의도입니다.
알고리즘 핵심을 프레임워크 없이 컴파일·검증할 수 있어야 하기 때문입니다.

## 싱크는 Python 그대로

서버가 Confluence를 직접 크롤하지 않습니다. 로컬판의 `wikilens sync`를
**서비스 계정으로 1회** 돌려 미러를 만들고, 서버는 그것을 읽습니다.
이미 동작하고 테스트된 것을 다시 만들 이유가 없습니다.

```bash
CONFLUENCE_TOKEN=<서비스계정> wikilens --root ./mirror-root sync --space PLATFORM \
  && curl -XPOST localhost:8787/api/admin/reindex
```

**`&&` 를 빼지 마세요.** 싱크가 실패했는데 재색인이 돌면 절반만 반영됩니다.

`/admin/reindex` 는 **볼트가 비면 색인을 건드리지 않고** `{"skipped":true}` 를 돌려줍니다 —
경로를 잘못 준 재색인이 마지막으로 성공한 색인을 지우면 안 되기 때문입니다. 기동 적재와
**같은 코드**(`IndexingService`)를 씁니다. 예전엔 둘이 따로였고, 그래서 기동 쪽에만
방어가 들어간 동안 이 엔드포인트가 색인 2,383건을 0으로 지웠습니다(실측).

### cron 으로 자동화할 때

CLI 는 자격증명을 **환경변수 → `~/.wikilens/env.sh`** 순으로 읽습니다. cron 은 환경이
최소라 `export` 가 없으므로, 서비스 계정 자격증명을 그 파일에 두면 crontab 이 깨끗해집니다:

```bash
mkdir -p ~/.wikilens && chmod 700 ~/.wikilens
cat > ~/.wikilens/env.sh <<'EOF'
export CONFLUENCE_URL=https://wiki.mycompany.com
export CONFLUENCE_TOKEN=<서비스 계정 PAT>
EOF
chmod 600 ~/.wikilens/env.sh
```
```cron
0 4 * * *  wikilens --root /srv/wikilens/mirror sync --space PLATFORM \
             && curl -sf -XPOST localhost:8787/api/admin/reindex
```

**예전에는 이게 조용히 실패했습니다** — CLI 가 환경변수만 읽어서 cron 에서는
`CONFLUENCE_URL 환경변수가 필요합니다` 로 죽었습니다(실측: `env -i` 로 재현).
`&&` 덕에 절반 반영은 막혔지만 **볼트가 낡아가는 것을 알 방법이 없었습니다.**
로컬판은 래퍼 스크립트로 막아뒀는데 서버판만 빠져 있었습니다.

crontab 에 직접 `CONFLUENCE_TOKEN=...` 을 쓰는 것도 됩니다(환경변수가 파일보다
우선합니다). 다만 `crontab -l` 에 토큰이 그대로 보입니다.

그리고 **사용자를 등록해야 아무거나 보입니다.** ACL 이 fail-closed 라, 등록 전에는
색인이 멀쩡해도 모든 검색이 빈손입니다 — 실측: 같은 질의가 등록 전 0건, `@public`
등록 후 3건(색인 2,377건 상태에서).

```bash
curl -XPOST 'localhost:8787/api/admin/acl/user?userKey=alice@corp' \
  -H 'Content-Type: application/json' -d '["@public"]'
```

> **`/api/admin/*` 에는 인증이 없습니다.** 위 명령은 서버에 닿는 누구나 자기 자신에게
> 실행할 수 있고, 그러면 **스스로 권한을 부여**하는 것이 됩니다. `/admin/reindex` 도
> 마찬가지입니다. ACL 수집(CLAUDE.md 우선순위 1)과 **같은 묶음의 문제**입니다 —
> 권한을 아무리 정확히 수집해도 이 문이 열려 있으면 무의미합니다.
> 그때까지는 **신뢰 경계 안**(사내망 · 리버스 프록시 뒤 · 루프백)에만 띄우세요.

배포 직후 상태 확인은 클라이언트 플러그인의 진단이 가장 빠릅니다 —
주소·식별자·도달·색인 크기·등록 사용자 수를 한 번에 보여줍니다:

```bash
python3 plugin/client/mcp/wikilens_mcp.py --status
```

## API

```bash
# 검색 — 어휘 랭킹 + 학습 힌트 융합, ACL 필터링
curl -XPOST localhost:8787/api/search -H 'Content-Type: application/json' \
  -d '{"query":"로그인 붙이는 법","userKey":"alice@corp"}'


# 본문 읽기 — 매 요청 ACL 확인. 권한 없으면 404 (403은 존재를 알려주므로 유출)
curl -XPOST localhost:8787/api/read -H 'Content-Type: application/json' \
  -d '{"pageId":"200000001","userKey":"alice@corp","sessionId":"s1"}'

# 리터럴 검색 — 형태소 분석을 거치지 않는 정확 일치
curl -XPOST localhost:8787/api/grep -H 'Content-Type: application/json' \
  -d '{"pattern":"DEPLOY_TOKEN","userKey":"alice@corp"}'

curl localhost:8787/api/stats
```

`grep` 의 `regex=true` 는 **RE2 문법**입니다 — ripgrep 과 같은 엔진 계열입니다.
`java.util.regex` 를 쓰던 동안 사용자 패턴 하나가 서버 스레드를 영구히 묶거나
(`(.+)+@@@@`) 스택을 넘겨 HTTP 500 을 냈습니다(`(a|aa)+c`). RE2 는 유한 오토마타라
입력에 선형이고 재귀하지 않아 둘 다 원리적으로 없습니다.

**대소문자는 `regex` 와 무관하게 구분하지 않습니다.** 리터럴 경로가 `ignoreCase` 인데
RE2 기본은 구분이라, 맞춰주지 않으면 이 플래그가 문법뿐 아니라 민감도까지 바꿉니다
(실측: 본문 `Coway` 에 `coway` 가 리터럴 1건 · 정규식 0건). rg 프로세스를 붙인다면
`-i` 를 함께 넘겨야 답이 같습니다.

대가는 **역참조(`\1`)와 전방탐색(`(?=)`)** 을 못 쓰는 것입니다. 쓰면 조용히 0건이
되는 대신 `error` 필드로 이유가 옵니다. 속도를 위해 rg 프로세스를 붙이는 것은
아직 안 했고, 붙여도 문법이 같아 답이 갈리지 않습니다 — `DECISIONS.md` D12.

## ACL — 클라이언트에 배포하지 않는 이유

배포된 사본은 **회수할 수 없습니다.** 사용자가 팀에서 나가도 그 노트북의 볼트에는
전체 사본이 남습니다. 서버가 콘텐츠를 서빙하면 매 요청마다 ACL을 확인하므로
권한 취소가 즉시 반영됩니다.

시행 지점은 셋입니다:
1. `search` — Lucene 질의에 ACL 필터가 `MUST` 절로 결합
2. 학습 힌트 — 서버의 학습 레이어는 권한을 모르므로 융합 시 다시 검사
3. `read` / `grep` — 매 요청 확인. 권한 없으면 **404** (403은 존재를 알려주므로 유출)

권한 토큰이 없으면 빈 결과를 냅니다. 실수로 전체가 노출되는 것보다 낫습니다.

## 훅이 없는 이유

읽기가 서버를 거치므로 **서버가 궤적을 직접 관측합니다.** MCP 프록시 프로세스
하나가 세션 하나이고, `sessionId`가 그 경계입니다.

클라이언트 훅·버퍼링·핫패스 비용·세션 조립이 전부 사라집니다.

> **주의:** 권한 변경은 `lastModified`를 건드리지 않습니다. 콘텐츠 증분 싱크로는
> 잡히지 않으므로 ACL 싱크를 분리해 더 자주 돌려야 합니다.

## 분석기는 색인 시점에 고른다

기본은 `korean`(Nori). 영어가 주된 코퍼스면 `english`, 다국어가 섞여 주 언어를
못 고르겠으면 `standard` 다.

설정하는 방법은 넷이고 **전부 실측으로 확인했습니다**(아래로 갈수록 우선순위가 높습니다):

```bash
# ① application.yml — 기본값이 여기 적혀 있습니다
wikilens:
  analyzer: english

# ② 환경변수 — systemd·Docker 에서 가장 흔합니다
WIKILENS_ANALYZER=english java -jar wikilens-server.jar

# ③ 시스템 속성
java -Dwikilens.analyzer=english -jar wikilens-server.jar

# ④ 앱 인자 — 일회성으로 바꿔볼 때
java --enable-native-access=ALL-UNNAMED -jar wikilens-server.jar --wikilens.analyzer=english
```

jar 안의 `application.yml` 을 고치려면 다시 빌드해야 하므로, 배포에서는 **②나 ④**를
쓰거나 jar 옆에 `application.yml` 을 두세요(그쪽이 jar 안의 것을 덮습니다).

이름을 틀리면 조용히 기본값으로 떨어지지 않고 **기동이 실패합니다** —
`알 수 없는 분석기 'korea'. 가능한 값: korean·english·standard`.

**설정은 "무엇으로 지을까"이지 "무엇으로 질의할까"가 아닙니다.** 질의는 항상 디스크
색인이 실제로 지어진 분석기를 씁니다 — 선택이 Lucene 커밋 데이터에 기록되고(색인과
원자적이라 따로 놀 수 없습니다) 서버가 그것을 읽어 씁니다. 그래서 설정을 바꿔도
재색인 전까지는 **옛 분석기로 정상 동작**하고, 둘은 재색인에서 만납니다:

```
색인은 'korean' 로 지어졌고 설정은 'english' 입니다. 검색은 'korean' 로 정상 동작합니다 —
설정을 적용하려면 POST /api/admin/reindex 로 다시 지으세요.
```

기록을 안 쓰고 설정을 따라가면 그 사이가 **에러 없이 0건**입니다. 색인에 답이 적혀
있는데 그것을 안 쓰고 어긋남을 재고만 있는 셈이라, 경고 대신 **불일치가 성립하지 않게**
바꿨습니다. 실측: 볼트 경로를 틀리게 주고 `--wikilens.analyzer=english` 로 띄워도
색인 2,383건이 그대로 살아 검색 8건이 나옵니다.

**설정을 바꾸는 절차는 재기동 하나입니다.** 값은 빈 생성 시점에 한 번 읽히므로 —
실행 중인 서버는 `application.yml` 을 고쳐도 모릅니다 — 재기동해야 하고, 재기동하면
기동 적재가 곧 재색인이라 **그 자리에서 적용됩니다**(포트가 열리기 전에 끝납니다):

```
색인은 'korean' 로 지어졌고 설정은 'english' 입니다…     ← 순간적으로 찍힌다
색인 재구축 2383건 · 분석기 english                       ← 곧바로 적용
```

수동 `POST /api/admin/reindex` 가 필요한 경우는 하나뿐입니다 — **볼트를 못 읽어 기동
적재가 건너뛰어졌을 때.** 그때만 불일치가 지속되고, 그 상태는 밖에서도 보입니다:

```bash
curl localhost:8787/api/stats     # analyzer · analyzerConfigured
python3 plugin/client/mcp/wikilens_mcp.py --status
#   ANALYZER=korean (설정은 english)
```

같으면 `--status` 가 한 줄만 찍고, 다를 때만 무엇을 해야 하는지 알려줍니다.


> **학습 궤적도 영향을 받습니다.** `trajectories.jsonl` 에는 분석된 항이 저장돼 있어
> 분석기를 바꾸면 옛 항과 새 질의가 안 맞습니다. 궤적은 복구 불가 자산이라 지우지
> 않지만, 그만큼 학습이 무효가 된다는 것은 알고 있어야 합니다.

## 토크나이저 정본은 서버

클라이언트는 질의 원문만 보내고 서버가 Nori로 토큰화합니다.
양쪽이 각자 토큰화하면 규칙이 달라졌을 때 **에러 없이 조용히 0건**이 되는데,
Python 프로토타입에서 실제로 겪은 버그입니다.

## 여전히 미해결

**"유용했다"의 판정.** 서버는 무엇을 읽었는지만 보고 그게 답이었는지는 모릅니다.
신호 셋을 씁니다 — 마지막으로 읽은 페이지가 답일 확률이 높다, 키워드가 겹치는 질의가
뒤따르면 앞 시도는 실패였다, 그리고 **서빙한 힌트를 끝내 안 읽으면 그 힌트가 틀렸다**
(2026-08-06 추가). 셋째가 유일하게 학습을 **되돌리는** 신호라, 나쁜 간선이 증거로
밀려납니다.

`pWrong` = 거부된 힌트 / 서빙한 힌트. 손익분기 `p_hit > p_wrong/(n−1)` 의 그 값입니다.
세션이 실패한 비율은 `sessionFailureRate` 로 따로 봅니다 — 다른 값입니다.

읽기 개수와 순위도 씁니다. 마지막 읽기 앞의 것들은 "열어보고 지나친" 페이지이므로
미스로 charge 하고(1건이면 미스 0, 6건이면 5), `dest` 의 검색 순위가 깊을수록 강한
증거로 셉니다(1위 ×1, 2~3위 ×2, 그 아래 ×3). 위를 지나쳐 아래를 골랐다는 것은
어휘 랭킹이 틀렸다는 뜻이고, 학습 레이어가 고치라고 있는 경우가 그것입니다.

**가중치 문턱은 손으로 정했습니다** — 깊을수록 강하다는 방향만 확립된 것이고
(웹 검색 클릭 모델의 position bias), 3·6 이라는 경계와 3배 상한은 이 코퍼스에서
측정한 값이 아닙니다. `pWrong` 과 함께 모니터링하세요.

여전히 약한 곳: `dest = reads.last()` 라는 전제 자체입니다.

### 원시 계측 (`/api/stats` 의 `sinceStart`)

**궤적 수로는 검색 횟수를 역산할 수 없습니다.** 읽기도 서빙도 없는 검색은 궤적을 아예
안 남기는데, 그게 정확히 "결과가 시원찮아 다시 치는" 경우의 첫 시도라 **재검색이
체계적으로 과소 계상**됩니다. 웹 검색 로그 연구는 질의 자체를 남기므로 이 편향이
없습니다 — 세션의 약 40%가 질의 2개 이상이라는 수치가 그렇게 나옵니다
([Chen et al., WWW '21](https://dl.acm.org/doi/10.1145/3442381.3450127)).

```json
"sinceStart": {
  "queries": 3, "sessions": 2, "multiQuerySessions": 1,
  "queriesPerSession": 1.5, "multiQueryRate": 0.5, "unrecordedQueries": 3
}
```

**이 블록만 재기동에서 0으로 돌아갑니다.** 위쪽 값들은 궤적 로그에서 재생되지만 이건
로그에 없습니다 — 기록되지 않는 것을 세려는 지표라서요. 섞어서 빼면 재기동 직후
음수가 납니다.

`unrecordedQueries` 는 "빗나간 검색 수"가 아닙니다. **아직 안 끝난 세션의 진행 중인
스팬**도 포함하므로, `activeSessions` 가 0일 때만 그 뜻이 됩니다.

### 클릭 없는 종료를 실패로 세지 않습니다

에이전트가 검색 결과 제목만 보고 답하면 `reads` 가 빕니다. 예전에는 그것을 무조건
실패로 셌는데, IR 에서 **good abandonment** 로 알려진 오독입니다
([Li et al., SIGIR '09](https://dl.acm.org/doi/10.1145/1571941.1571951) ·
[Williams et al., WWW '16](https://www.microsoft.com/en-us/research/wp-content/uploads/2017/05/williams_www2016_good_abandonment.pdf)).
이제 `abandonedWithHints` 로 따로 세고 `sessionFailureRate` 의 분모에서 뺍니다.

**판정을 바꾸지는 않았습니다** — 만족인지 아닌지 구별할 신호가 아직 없습니다.
힌트 단위 벌점(`rejected`)은 그대로입니다. 그 힌트가 안 읽혔다는 사실은 확실하고,
`pWrong` 이 재려던 것이 그것이기 때문입니다.

**질의 원문이 서버로 갑니다.** 콘텐츠는 아니지만 질의어 자체가 민감할 수 있습니다.

## 버려진 세션은 백그라운드로 거둡니다

세션 종료는 MCP 프록시의 `atexit` 에 걸려 있어 프로세스가 SIGKILL 되거나 크래시하면
`/api/session/end` 가 안 옵니다. 그러면 그 세션의 궤적이 **확정되지 않아 학습에
들어가지 않습니다** — 사용자는 정상적으로 검색하고 읽었는데 배운 게 없습니다.

`SessionSweeper` 가 주기적으로 돌면서 조용해진 세션을 확정합니다. 기본은 5분마다,
30분 넘게 조용한 세션이 대상입니다:

```bash
--wikilens.learn.sweep-interval-millis=300000
--wikilens.learn.session-idle-millis=1800000
```

거둘 때 `버려진 세션에서 궤적 N건을 확정했습니다` 를 로그에 남깁니다.
즉시 돌리려면 `POST /api/admin/sweep` 입니다.
