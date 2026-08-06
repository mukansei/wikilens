# WikiLens 서버 (Spring Boot + Lucene/Nori)

Confluence 미러를 **서버측에서 색인**하고, 탐색 궤적을 축적해 랭킹을 보강합니다.
읽기는 여전히 클라이언트 로컬입니다 — 서버는 좌표만 반환합니다.

## 검증 상태 (먼저 읽으세요)

| 레이어 | 검증 |
|---|---|
| `learn/` (게이트·EB·궤적·포스팅) | JUnit 12개 통과, Python/scipy와 1e-6 일치 |
| `index/` `api/` `vault/` `acl/` (Lucene·Spring 배선) | 빌드·`bootRun`·재색인 검증됨 (Coway 실데이터 2,378건, 2026-08) |
| JUnit 전체 | 51개 통과 |
| 검색 랭킹 **품질** | **미검증.** 배선이 도는 것과 랭킹이 좋은 것은 다른 문제다 |

```bash
./gradlew test       # JUnit 전체
./gradlew bootRun    # 개발용 기동 (작업 디렉터리가 server/ 로 고정된다)
```

## 배포할 때는 경로를 절대경로로 주세요

기본값이 **상대경로**입니다(`./mirror-root`, `./.wikilens/index`, `./.wikilens/state`).
`bootRun` 은 Gradle 이 작업 디렉터리를 `server/` 로 고정해서 늘 같은 자리를 쓰지만,
**jar 로 띄우면 실행한 디렉터리 기준**이 됩니다.

엉뚱한 디렉터리에서 띄우면 **에러 없이 정상 기동합니다** — 빈 볼트를 보고 문서 0개로
뜨고, `state/` 도 새로 만들어 **학습 궤적이 분기됩니다**(실측 2026-08-06).
궤적은 유일한 복구 불가 자산입니다.

```bash
java -jar wikilens-server.jar \
  --wikilens.vault-root=/srv/wikilens/mirror \
  --wikilens.index-dir=/srv/wikilens/index \
  --wikilens.state-dir=/srv/wikilens/state
```

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
api/         검색 융합 · 관측 · 배포 매니페스트
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

그리고 **사용자를 등록해야 아무거나 보입니다.** ACL 이 fail-closed 라, 등록 전에는
색인이 멀쩡해도 모든 검색이 빈손입니다 — 실측: 같은 질의가 등록 전 0건, `@public`
등록 후 3건(색인 2,377건 상태에서).

```bash
curl -XPOST 'localhost:8787/api/admin/acl/user?userKey=alice@corp' \
  -H 'Content-Type: application/json' -d '["@public"]'
```

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

**질의 원문이 서버로 갑니다.** 콘텐츠는 아니지만 질의어 자체가 민감할 수 있습니다.
