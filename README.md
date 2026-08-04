# WikiLens

Confluence 위키를 로컬 마크다운으로 미러링하고, **다른 문서들이 각 페이지를 실제로
부르는 이름**(앵커 텍스트)을 색인해 어휘 격차를 메웁니다.
그 위에 에이전트 탐색 궤적을 축적하는 학습 레이어를 선택적으로 얹습니다.

```console
$ grep "로그인" ALIASES.md
OAuth 2.0 인가 코드 흐름 | 로그인 붙이는 법 · 인증 붙이기 | 2 | mirror/pages/20/00/200000001.md
```

본문만 뒤졌다면 **엉뚱한 문서**가 나옵니다 — 그 표현으로 *링크한* 온보딩 페이지지,
찾으려는 문서가 아닙니다. 이 차이가 이 프로젝트의 출발점입니다.

---

## 구성

| 디렉터리 | 내용 | 언어 | 상태 |
|---|---|---|---|
| **`cli/`** | Confluence 싱크 · 앵커 전치 · 로컬판 | Python | **65 테스트 통과** |
| **`server/`** | Lucene/Nori 색인 · 학습 레이어 | Kotlin | 핵심 32 통과 / 배선 검증됨(Coway 실데이터) |
| **`plugin/local/`** | 스킬만 (로컬판) | — | 형식 검증 |
| **`plugin/server/`** | MCP 도구 4개 + 스킬 (서버판) | Python | **21 테스트 통과** |
| `marketplace/` | 조직 배포용 | — | — |
| `bench/` | 설계 검증 시뮬레이션 | Python | 실행 가능 |
| `docs/` | 아키텍처 · 다이어그램 · 원 페이저 | — | — |

---

## 두 판은 증분이 아니라 전환입니다

| | 로컬판 | 서버판 |
|---|---|---|
| 코퍼스 | 클라이언트 (~1GB) | **서버** |
| 클라이언트 저장소 | 볼트 전체 | **0** |
| 검색 | grep + 별칭 색인 | BM25 + Nori + 앵커 + 학습 |
| 인터페이스 | 스킬 | **MCP 도구 4개** |
| ACL | 개인 토큰 (자동) | 질의 시점 (**취소 가능**) |
| 궤적 관측 | 없음 | 서버가 직접 |
| 오프라인 | 가능 | 불가 |

서버판이 볼트를 배포하지 않는 이유는 **배포된 사본을 회수할 수 없기** 때문입니다.
사용자가 팀에서 나가도 노트북의 볼트에는 전체 사본이 남습니다.
서버가 서빙하면 권한 취소가 즉시 반영되고, 부수적으로 훅도 불필요해집니다 —
읽기가 서버를 거치므로 서버가 궤적을 직접 관측합니다.

1인 사용은 로컬판, 팀 배포는 서버판. **업그레이드 경로가 아니라 다른 배포 형태입니다.**

## 단계별 경로

의도적으로 **실패해도 산출물이 남는** 순서입니다.

### 1단계 — 로컬판 (며칠)

서버 없음. 파일과 grep만 씁니다.

```bash
cd cli && pip install -e .
export CONFLUENCE_URL=https://confluence.mycompany.com

# 인증은 환경에 따라 넷 중 하나 (자동 판별)
export CONFLUENCE_TOKEN=<PAT>                    # Server/DC — SSO 써도 대개 이게 동작
# export CONFLUENCE_EMAIL=me@corp                #   + Cloud API 토큰이면 이메일 추가
# export IAM_TOKEN_URL=... IAM_CLIENT_ID=...     #   사내 IAM OAuth2
# export CONFLUENCE_HEADERS='X-Forwarded-User: me@corp'   # 리버스 프록시

wikilens doctor                            # 먼저 이것부터
wikilens --root ~/wiki sync --space PLATFORM
wikilens --root ~/wiki stats
```

`doctor`가 배포 형태(Cloud/Server·경로 접두사), 인증 방식, 접근 가능한 스페이스,
본문 확장 가능 여부를 실행 전에 확인합니다. 여기서 막히면 `sync`는 어차피 실패합니다.

**SSO 환경이면** — 대부분의 Confluence는 SSO(브라우저 로그인)와 별개로 토큰 인증을
허용합니다. Server/DC의 PAT를 **먼저 시도해 보세요.** 그것이 막혀 있을 때만
IAM OAuth2가 필요합니다. 인증 계층은 `cli/wikilens/auth.py` 한 곳에 격리돼 있어
새 방식이 필요하면 거기만 고치면 됩니다 — `build`와 서버는 Confluence를 모릅니다.

**`doctor`가 배포 형태를 잘못 판별하면** — `detect_prefix()`는 `/rest/api/space`가
열려있는지로 Cloud(`/wiki`)와 Server/DC(빈 접두사)를 자동 판별합니다. 실제로
사내 리버스 프록시가 `/space`만 허용하고 그 아래 다른 엔드포인트는 로그인
페이지로 리다이렉트하는 구성을 겪은 적이 있어, 한 엔드포인트만 더 검증하도록
고쳤습니다. 그래도 회사마다 게이트웨이 구성이 다 달라서 자동판별이 또 속을
수 있습니다 — 그럴 땐 직접 지정하세요:
```bash
export CONFLUENCE_PREFIX=""      # Server/DC 강제 (자동판별 건너뜀)
# export CONFLUENCE_PREFIX="/wiki"   # Cloud 강제
```

**여기서 판단하세요.** `stats`가 "제목과 다른 별칭을 가진 페이지" 비율을 냅니다.
낮으면 어휘 격차가 없다는 뜻이고 **이 프로젝트 전체가 값어치가 없습니다.**

플러그인은 스킬만 설치하면 됩니다 (MCP도 훅도 불필요):

```bash
mkdir -p ~/wiki/.claude/skills/wikilens
cp plugin/local/skills/wikilens/SKILL.md ~/wiki/.claude/skills/wikilens/
```

### 2단계 — 궤적 관측만 (몇 주)

랭킹 결과에 반영하기 전에, 실제 서버를 로컬에서 혼자 띄워 궤적이 실제로
쌓이는지만 먼저 봅니다. 3단계와 같은 서버지만 팀 배포(`marketplace` 설치) 없이
혼자 써보는 단계입니다:

```bash
cd server && ./verify.sh      # 순수 로직만, kotlinc면 충분
./gradlew bootRun
curl -XPOST localhost:8787/api/admin/reindex
```

`curl -X POST localhost:8787/api/search -d '{"query":"...", "userKey":"me", "sessionId":"t1"}'`
로 직접 질의해보면서 `/api/stats`의 `trajectories`·`termPagePairs`가 느는지 확인하세요.

측정할 것: **비링크 도달 비율**과 질의 중복률. 낮으면 3단계로 가지 마세요.

### 3단계 — 프로덕션 서버 (몇 주)

```bash
# 서비스 계정으로 1회 싱크 (사용자별 싱크 없음 → Confluence 부하 1배)
CONFLUENCE_TOKEN=<서비스계정> wikilens --root ./mirror-root sync --space PLATFORM

cd server && ./verify.sh      # 순수 로직만, kotlinc면 충분
./gradlew bootRun
curl -XPOST localhost:8787/api/admin/reindex
```

사용자는 볼트를 받지 않습니다. MCP 플러그인만 설치하면 됩니다:

```bash
export WIKILENS_SERVER=http://wikilens.corp:8787
export WIKILENS_USER=alice@corp
/plugin marketplace add ./marketplace
/plugin install wikilens@wikilens-tools
```

---

## 설계 결정 요약

**앵커 텍스트가 가치의 대부분입니다.** 벤치마크에서 정답까지 읽는 페이지 수가
6.04 → 1.81로 줄었고, 그 개선이 어휘 격차 페이지에 집중됩니다(51.8% 대 13.9%).
신규 기법이 아니라(Brin & Page 1998) 신뢰도가 높습니다.

**경로 의존 질의는 캐싱하지 않습니다.** "이 데이터가 어떻게 흐르나"에 목적지만
주면 답한 게 아니라 답을 지운 겁니다. `LOCALIZATION`만 통과시킵니다.

**압축이 아니라 가산입니다.** 원본 궤적을 보존합니다. 무효화·폴백·출처 추적이
전부 여기 의존합니다.

**위키에 쓰지 않습니다.** 쓰면 사람 링크와 기계 링크가 섞여 정답 신호가 영구히
오염됩니다. 읽기 전용은 규율이 아니라 설계로 얻는 보장입니다.

**서버가 색인을 갖습니다.** 클라이언트 분산 색인은 Confluence API 부하가 사용자 수에
비례하고(200명이면 200배), 무엇보다 사용자마다 랭킹 척도가 달라져 학습 레이어에
이질적 관측이 섞입니다. 대가는 질의 시점 ACL 시행입니다.

**한국어 때문에 서버가 JVM입니다.** 교착어라 형태소 분석 없이는 BM25가 무너지고,
Lucene Nori가 그 문제의 프로덕션 해답입니다. 색인이 서버에 없던 설계에서는
이 근거가 없었고 실제로 Python이었습니다.

자세한 근거는 [`docs/architecture.md`](docs/architecture.md),
[`bench/RESULTS.md`](bench/RESULTS.md) 참조.

---

## 측정 지표 (우선순위 순)

1. **제목과 다른 별칭 비율** — 낮으면 여기서 중단
2. **비링크 도달 비율** — 낮으면 학습 레이어가 링크 그래프의 복사본으로 퇴화
3. **`pWrong`** — 손익분기 `p_hit > p_wrong/(n−1)`의 분자. 적중률보다 이게 기준
4. 적중당 절약 대 미스당 검증 오버헤드

---

## 검증 상태

정직하게 나누면:

| | 검증 |
|---|---|
| Python CLI · 앵커 전치 · 파서 | 23개 테스트 통과 (골든 픽스처 포함) |
| Confluence 클라이언트 | 17개 통과 (가짜 서버 — Cloud/Server·429·페이지네이션·재개·`--follow-refs`) |
| 인증 계층 (SSO/IAM) | 12개 통과 (가짜 IAM — OAuth2·만료 갱신·401 재시도) |
| Python 서버 스코어링 (`server/scoring.py`) | 13개 통과. Kotlin `Scoring.kt`와 나란히 유지되는 정본 — 공유 Python 서버 자체(구 훅 기반 설계)는 제거됨 |
| MCP 프록시 | 21개 테스트 통과 (핸드셰이크·도구 4개·세션·404) |
| Kotlin 학습 레이어 (`learn/`) | 컴파일·실행 32/32, Python/scipy와 1e-6 일치 |
| Kotlin Lucene/Spring 배선 | 빌드·bootRun·재색인 검증됨 (Coway 실데이터 2,378건). 검색 랭킹 품질은 별도 미검증 |
| 벤치마크 | 실행 가능. 단 **합성 코퍼스**이므로 절대 수치는 무의미 |

벤치마크는 효용을 측정하지 않습니다. 파라미터를 제가 정했으므로 유효한 것은
공식의 참/거짓, 방향성, 민감도뿐입니다.

---

## 미해결

**"유용했다"의 판정.** 훅은 무엇을 읽었는지만 보여줍니다. 마지막 읽기와 질의 재구성
두 가지 약한 신호에 의존하고, 노이즈 크기는 `pWrong`으로만 알 수 있습니다.
학습 레이어 전체가 이 레이블에 걸려 있습니다.

**ACL 싱크 주기.** 권한 변경은 `lastModified`를 건드리지 않아 콘텐츠 증분 싱크가
놓칩니다. 공유 서버에서는 그 창이 곧 유출 창입니다.

**질의 원문이 서버로 갑니다.** 콘텐츠는 아니지만 질의어 자체가 민감할 수 있습니다.
