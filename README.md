# WikiLens

Confluence 위키를 로컬 마크다운으로 미러링하고, **다른 문서들이 각 페이지를 실제로
부르는 이름**(앵커 텍스트)을 색인해 어휘 격차를 메웁니다.
그 위에 에이전트 탐색 궤적을 축적하는 학습 레이어를 선택적으로 얹습니다.

```console
$ grep "로그인" ALIASES.md
PLATFORM | OAuth 2.0 인가 코드 흐름 | 로그인 붙이는 법 · 인증 붙이기 | 2 | mirror/pages/01/200000001.md
```

본문만 뒤졌다면 **엉뚱한 문서**가 나옵니다 — 그 표현으로 *링크한* 온보딩 페이지지,
찾으려는 문서가 아닙니다. 이 차이가 이 프로젝트의 출발점입니다.

> **이 문서는 만드는 사람을 위한 것입니다.** 설치해서 쓰기만 한다면
> [로컬판 안내](plugin/local/README.md) 또는 [서버판 안내](plugin/client/README.md)를 보세요.

---

## 구성

| 경로 | 내용 | 언어 | 상태 |
|---|---|---|---|
| **`cli/`** | Confluence 싱크 · 앵커 전치 · 로컬판 | Python | pytest |
| **`server/`** | Lucene/Nori 색인 · 학습 레이어 | Kotlin | JUnit · 배선 검증됨(Coway 실데이터) |
| **`plugin/local/`** | 스킬 + 커맨드 2개 + [사용 안내](plugin/local/README.md) | Python | 테스트는 `plugin/tests/` (배포 밖) |
| **`plugin/client/`** | MCP 도구 4개 + 스킬 + [사용 안내](plugin/client/README.md) | Python | 프록시 테스트 (별도 실행) |
| `.claude-plugin/` | 마켓플레이스 매니페스트 (조직 배포용) | — | — |
| `docs/` | 아키텍처 · 임베딩 설계 제안 | — | — |
| `DECISIONS.md` | 뒤집힌 결정과 지우면 안 되는 것들 — 되돌리기 전에 읽으세요 | — | — |

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
```

**자격증명은 `~/.wikilens/env.sh`(권한 600)에 둡니다** — `export` 가 아니라. `export` 는
그 셸에서만 살아서, Claude Code 를 앱으로 띄우면 없는 것과 같습니다. 실제로 그것 때문에
**검색은 되는데 `sync` 만 조용히 죽는** 상태를 오래 겪었습니다(D10).

```bash
mkdir -p ~/.wikilens
cat > ~/.wikilens/env.sh <<'EOF'
export CONFLUENCE_URL=https://confluence.mycompany.com
export CONFLUENCE_TOKEN=<PAT>                  # Server/DC — SSO 써도 대개 이게 동작
# export CONFLUENCE_EMAIL=me@corp              #   + Cloud API 토큰이면 이메일 추가
# export IAM_TOKEN_URL=... IAM_CLIENT_ID=...   #   사내 IAM OAuth2
# export CONFLUENCE_HEADERS='X-Forwarded-User: me@corp'   # 리버스 프록시
EOF
chmod 600 ~/.wikilens/env.sh
source ~/.wikilens/env.sh

wikilens doctor                            # 먼저 이것부터
wikilens --root ~/wiki sync --space PLATFORM
wikilens --root ~/wiki stats
```

플러그인을 설치했다면 이 파일을 손으로 만들 필요가 없습니다 — `/wikilens-local:setup` 이
만들어 줍니다. 이미 셸에 `export` 해뒀다면
`plugin/local/scripts/setup_vault.py --capture-env` 가 값을 화면에 찍지 않고 그대로 옮깁니다.

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
수 있습니다 — 그럴 땐 직접 지정하세요(`env.sh` 에 넣거나 일회성으로):
```bash
export CONFLUENCE_PREFIX=""      # Server/DC 강제 (자동판별 건너뜀)
# export CONFLUENCE_PREFIX="/wiki"   # Cloud 강제
```
**첫 번째로 의심할 자리는 아닙니다.** 이중 검증을 넣은 뒤로는 강제 지정 없이 동작하는
것을 실측했습니다(2026-08-06, Coway DC).

**여기서 판단하세요.** `stats`가 "제목과 다른 별칭을 가진 페이지" 비율을 냅니다.
낮으면 어휘 격차가 없다는 뜻이고 **이 프로젝트 전체가 값어치가 없습니다.**

플러그인을 설치하면 **어느 프로젝트에서든** 볼트를 검색할 수 있습니다
(MCP도 훅도 불필요 — 파일 읽기와 grep만 씁니다):

```
/plugin marketplace add ./
/plugin install wikilens-local@wikilens
/wikilens-local:setup
```

`setup` 이 볼트 위치를 `~/.wikilens/config.json` 에 기록합니다. **이미 볼트가 있으면
옮길 필요 없이 그 경로를 등록**하면 됩니다. 갱신은 `/wikilens-local:sync`.

볼트는 프로젝트 밖에 있으므로, 등록하지 않으면 프로젝트마다 읽기 승인을 다시 받습니다.
`setup` 이 마지막에 `~/.claude/settings.json` 등록 여부를 묻습니다(전역 설정 변경이라
승낙해야만 씁니다).

### 2단계 — 궤적 관측만 (몇 주)

랭킹 결과에 반영하기 전에, 실제 서버를 로컬에서 혼자 띄워 궤적이 실제로
쌓이는지만 먼저 봅니다. 3단계와 같은 서버지만 팀 배포(플러그인 설치) 없이
혼자 써보는 단계입니다:

```bash
cd server && ./gradlew bootRun
curl -XPOST localhost:8787/api/admin/reindex
```

`curl -X POST localhost:8787/api/search -d '{"query":"...", "userKey":"me", "sessionId":"t1"}'`
로 직접 질의해보면서 `/api/stats`의 `trajectories`·`termPagePairs`가 느는지 확인하세요.

측정할 것: **비링크 도달 비율**과 질의 중복률. 낮으면 3단계로 가지 마세요.

### 3단계 — 프로덕션 서버 (몇 주)

```bash
# 서비스 계정으로 1회 싱크 (사용자별 싱크 없음 → Confluence 부하 1배)
CONFLUENCE_TOKEN=<서비스계정> wikilens --root ./mirror-root sync --space PLATFORM

cd server && ./gradlew bootRun
curl -XPOST localhost:8787/api/admin/reindex
```

사용자는 볼트를 받지 않습니다. MCP 플러그인만 설치하고 `~/.wikilens/config.json` 에
주소와 본인 식별자를 넣으면 됩니다:

```json
{ "server": "http://wikilens.corp:8787", "user": "alice@corp" }
```
```
/plugin marketplace add ./          # 또는 팀 배포 시 저장소의 git URL
/plugin install wikilens-client@wikilens
/reload-plugins
```

`WIKILENS_SERVER`·`WIKILENS_USER` 환경변수로도 되고 파일보다 우선하지만, **그 셸에서만
유지됩니다** — 앱으로 띄우면 비어서 전 결과가 빕니다. `user` 가 없으면 서버가 요청자를
식별하지 못해 **결과가 항상 빈손**인데, 그게 "문서가 없다"처럼 보입니다(D10).

**매니페스트는 반드시 저장소 루트의 `.claude-plugin/marketplace.json` 이어야 합니다.**
하위 디렉터리(`marketplace/.claude-plugin/…`)에 두고 그 경로를 `add` 하면 등록은 되지만
설치가 `"source type your Claude Code version does not support"` 로 실패합니다 —
플러그인 `source` 의 상대 경로가 기대와 다르게 해석되기 때문입니다(2026-08-04 실측,
Claude Code 2.1.220). 루트에 두면 `source` 가 `./plugin/client` 처럼 하위 경로여도
정상 동작합니다.

`source` 가 `plugin/client`·`plugin/local` 을 직접 가리키므로 **저장소 안에는** 사본이
없습니다. 다만 **설치는 버전별 캐시로 복사**되므로, 플러그인을 고쳐도 이미 설치된 것에는
자동 반영되지 않습니다(실측). 고친 뒤에는 재설치하거나 version 을 올려야 합니다.

---

## 설계 결정 요약

**앵커 텍스트가 가치의 대부분입니다.** 실제 Coway 데이터로 베이스라인(순수 MD 검색)과
비교했을 때 `ALIASES.md`(앵커 색인)가 정확도·토큰 사용량 둘 다 크게 앞섰다 — 특히
어휘 격차가 있는 문서에서 개선이 두드러졌다. 신규 기법이 아니라(Brin & Page 1998)
신뢰도가 높습니다.

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

자세한 근거는 [`docs/architecture.md`](docs/architecture.md), 기각된 안과 뒤집힌
결정의 이력은 [`DECISIONS.md`](DECISIONS.md) 참조.

---

## 측정 지표 (우선순위 순)

1. **제목과 다른 별칭 비율** — 낮으면 여기서 중단
2. **비링크 도달 비율** — 낮으면 학습 레이어가 링크 그래프의 복사본으로 퇴화
3. **`pWrong`** — 손익분기 `p_hit > p_wrong/(n−1)`의 분자. 적중률보다 이게 기준
4. 적중당 절약 대 미스당 검증 오버헤드

---

## IntelliJ 실행 구성

`.idea/runConfigurations/` 를 저장소에 포함해 두었습니다. 프로젝트를 열면 실행
드롭다운에 그대로 뜹니다(개인 설정인 `workspace.xml` 등은 계속 무시됩니다).

| 구성 | 하는 일 |
|---|---|
| 1. 서버 실행 (bootRun) | Spring Boot 기동 (`:8787`) |
| 2. 재색인 | `/admin/reindex` + 테스트 사용자 등록 + `stats` — **1번이 떠 있어야 함** |
| 3. 전체 검증 | 계약·Python·MCP·JUnit 넷을 순서대로. **변경 후 이것부터** |
| 4. 계약 검사만 | `contract/shared_contract.sh` |
| 5. Kotlin 테스트 | `gradlew test` |
| 6. Python 테스트 | `pytest` + MCP 프록시 |

3번을 통과시키는 것이 변경의 기본 조건입니다.

---

## 검증 상태

정직하게 나누면:

| | 검증 |
|---|---|
| Python CLI · 앵커 전치 · 파서 | 통과 — 공유 골든 픽스처로 Kotlin 과 같은 산출물 대조 |
| CLI 배선 (서브커맨드·진단 메시지) | 통과 |
| Confluence 클라이언트 | 통과 — 가짜 서버로 Cloud/Server·429·페이지네이션·재개·`--follow-refs` |
| 인증 계층 (SSO/IAM) | 통과 — 가짜 IAM 으로 OAuth2·만료 갱신·401 재시도 |
| Python 스코어링 대조 구현 (`cli/wikilens/scoring_reference.py`) | 통과. 런타임에 안 쓰이고 Kotlin `Scoring.kt` 의 짝으로만 존재 — `LearnLayerTest.kt` 의 기대값 6개가 여기서 나온다 (scipy 는 안 쓴다 — 뉴턴법 자체 구현) |
| MCP 프록시 (서버판) | 통과 — 핸드셰이크·도구 4개·세션·404·설정 해석 |
| 로컬판 플러그인 | 통과 — 경로 해석·상태 판정·스킬 정합성·포맷 드리프트 |
| Kotlin 학습 레이어 (`learn/`) | JUnit 통과. 기대값 6개가 `scoring_reference.py` 산출과 1e-6 일치 |
| Kotlin 서비스 계층 (search·content·acl·tree) | JUnit 통과 |
| Kotlin Lucene/Spring 배선 | 빌드·bootRun·재색인 검증됨 (Coway 실데이터 2,383건). 검색 랭킹 품질은 별도 미검증 |

**개수는 일부러 안 적습니다** — 늘 때마다 낡습니다. 실제 수는 위 네 스위트를 돌리면 나옵니다.

---

## 미해결

**"유용했다"의 판정.** 서버는 무엇을 읽었는지만 보고 그게 답이었는지는 모릅니다
(훅은 없습니다 — 읽기가 서버를 거치므로 서버가 직접 관측합니다). 신호 다섯을 씁니다:
마지막 읽기, 질의 재구성, **서빙했는데 끝내 안 읽힌 힌트**, 지나친 읽기, 그리고
`dest` 의 검색 순위. 셋째가 유일하게 학습을 **되돌리는** 신호입니다.
노이즈 크기는 `pWrong`(= 거부된 힌트 / 서빙한 힌트)으로만 알 수 있고, 학습 레이어
전체가 이 레이블에 걸려 있습니다. `dest = reads.last()` 라는 전제 자체는 그대로입니다.

**서버판 인증.** `sync` 가 Confluence 권한을 안 가져와 모든 페이지가 공개로 들어오고,
`/api/admin/*` 에는 인증이 아예 없어 서버에 닿는 누구나 스스로 권한을 부여할 수 있습니다.
게다가 권한 변경은 `lastModified`를 건드리지 않아 콘텐츠 증분 싱크가 놓칩니다 —
공유 서버에서는 그 창이 곧 유출 창입니다. **셋이 한 묶음이고, 다중 사용자 배포의
선결 조건입니다**(로컬판에는 해당 없음 — 개인 토큰이 곧 권한 범위).

**질의 원문이 서버로 갑니다.** 콘텐츠는 아니지만 질의어 자체가 민감할 수 있습니다.
