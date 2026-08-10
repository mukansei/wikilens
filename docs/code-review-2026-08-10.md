# WikiLens 코드 분석 · 효용성 · 최적화 리포트

**작성 2026-08-10 · 대상 커밋 `96a551a` (master, 워킹트리 클린)**

이 문서는 다른 세션에 넘기기 위한 인수인계본입니다. 아래 판단의 근거가 된 실행은
전부 이 커밋에서 이루어졌습니다:

- `./check.sh` → **넷 모두 통과** (계약 68 · pytest 170 · MCP 54 · JUnit)
- 서버 Kotlin main 전량 정독, `cli/wikilens` 핵심 모듈, `plugin/client/mcp/wikilens_mcp.py`
- 학습 레이어는 임시 JUnit 프로브를 붙여 **실측** (측정 후 삭제, 워킹트리 클린 확인)

`§5 ⑥` 의 수치는 가정이 아니라 이 저장소에서 실제로 잰 값입니다. 되돌리거나 반박하려면
같은 프로브를 다시 만들어 재는 것이 맞습니다 — 만드는 법은 그 절에 적어뒀습니다.

---

## 0. 한 줄 판정

**엔지니어링 품질은 이 규모에서 보기 드물게 높고, 그 품질이 집중된 곳(학습 레이어)의
효용만 아직 미검증이다.** 지금 필요한 것은 코드가 아니라 **데이터**이고, 코드 쪽에서
손댈 값어치가 있는 것은 실측으로 확인된 핫스팟 하나와 상한 누락 두 곳이다.

## 1. 규모

| | 줄수 | 비고 |
|---|---|---|
| Kotlin main | 3,440 (59파일) | 한 파일 한 선언 |
| Kotlin test | 2,696 (25파일) | main 대비 78% |
| Python (cli+plugin) | ~6,550 | `cli/build/lib/` 사본 2,179줄 제외 |
| 계약 | 561줄 / 68항목 | grep + 실동작 픽스처 2중 |

테스트가 없는 main 클래스를 전수로 뽑아보니 **23개 전부가 DTO·data class·인터페이스**
였습니다. 로직을 가진 클래스 중 테스트 없는 것은 0건입니다.

## 2. 설계에서 실제로 잘 된 것

칭찬이 아니라, **되돌리면 안 되는 근거**로 적습니다.

- **`LuceneIndex.Snapshot` (LuceneIndex.kt:54)** — searcher·reader·dir·meta·tree·**analyzer**
  를 한 덩어리로 원자 교체. 재색인 중 "새 트리 + 옛 메타" 같은 혼합 상태가 **타입 수준에서
  성립하지 않습니다.** `analyzeAndSearch`(:259)가 스냅샷을 한 번만 읽어 항 추출과 검색을
  같은 세대에서 하는 것도 같은 원리입니다.
- **디스크가 정본 (LuceneIndex.kt:176)** — 질의 분석기를 설정이 아니라 커밋 데이터에서
  읽습니다. 이건 "불일치를 감지한다"가 아니라 **불일치가 성립할 수 없게 만든 것**이라
  질적으로 다릅니다.
- **해석처 단일화** — `VaultLocator`(볼트), `AclRegistry`(시행 스위치),
  `IndexingService`(적재), `credentials.py`(자격증명). 각각 "두 곳이 갈려서 실제로 터진"
  이력이 주석에 남아 있고, 합친 뒤에 딸려온 비용(`VaultLocator` 해석이 공짜가 아님, :46)
  까지 적어뒀습니다.
- **`AdminGuardConfig`가 경로로 잠근다 (:17)** — 핸들러마다 `check()` 를 부르던 것을
  인터셉터로 바꿨습니다. "계약이 `@PostMapping` 만 세서 `@GetMapping` 우회를 못 잡았다"는
  실측이 주석에 있습니다. 세는 방어를 **구조적 방어로 교체한** 정확한 사례입니다.
- **`SessionRaceTest`** — 경쟁을 고치지 않고 **재보고 그만둔** 기록(재확인 1967 vs 없음
  1977). 손실의 주범이 다른 것이었다는 진단까지 남겼습니다. 이런 음성 결과가 남아 있는
  저장소는 드뭅니다.

## 3. 효용성 평가

가치 주장이 두 층인데, **검증 상태가 다릅니다.**

### (A) 앵커 텍스트 색인 — 구조는 타당, 이 코퍼스에서의 이득은 작게 측정됨

`build.py:transpose` 가 "A가 T로 B를 링크"를 "B는 T로 불린다"로 뒤집는 것이 핵심이고,
구현은 견고합니다(원본 XHTML에서 추출, space-key 생략 규칙 처리, 동명이인 미해석).
다만 저장소 자신의 기록상 **앵커가 제목 대비 주는 부가 어휘는 2% 수준**입니다. 즉 이
층의 실제 값은 "어휘 격차 해소"보다 **`ALIASES.md`·`TREE.md` 라는 grep 가능한 산출물**과
Nori 형태소 검색 쪽에서 나오고 있습니다.

### (B) 궤적 학습 레이어 — 저장소에서 가장 정교한데, 효용이 한 번도 측정된 적 없음

`learn/` + 그 테스트가 약 1,400줄로 저장소에서 가장 공들인 부분입니다. EB 하한,
position bias 가중(×1/×2/×3), good abandonment 분리, 재구성 신호 — 전부 IR 문헌에 근거가
있고 Python 참조 구현과 1e-6 일치까지 계약으로 잠겨 있습니다.

그런데 **손익분기 `p_hit > p_wrong/(n−1)` 을 판정할 실사용 값이 아직 없습니다.**
`pWrong` 은 "서빙 대비 거부율"로 정의를 고쳤고 자기교정도 실측으로 확인했지만
(`rel=0.57 → 0`), 그건 인위적 시나리오였습니다. `CLAUDE.md` 의 표가
"실사용 적중률: **측정된 바 없음**" 이라고 정직하게 적고 있습니다.

**여기가 이 프로젝트의 진짜 다음 관문입니다.** 그리고 좋은 소식은 그것을 재기 위한
계측이 이미 다 있다는 것입니다 — `sinceStart.queries`·`multiQueryRate`·`served`·
`rejected`·`abandonedWithHints`. 실사용 궤적 2~4주면 "학습 레이어를 유지할 것인가"에
답이 나옵니다.

### 다만 그 계측에 한 칸이 비어 있습니다 — `kind` 분포

`Gate.classify`(Gate.kt:46)의 `LOCALIZATION` 마커가 **매우 넓습니다**:
`문서`·`찾아`·`알려줘`·`보여줘`·`있나`·`있어`·`뭐야`·`어느`. 여기에 안 걸려도
**8토큰 이하면 LOCALIZATION** 입니다(:51). 실질적으로 거의 모든 한국어 질의가
`cacheable` 로 떨어집니다.

`LOCALIZATION만 간선 생성` 은 계약으로 잠겨 있는 항목인데, **그 게이트가 실제로 무엇을
걸러내고 있는지 밖에서 볼 방법이 없습니다.** `stats()`(TrajectoryStore.kt:328)에 `kind`
분포가 없습니다. `Trajectory.kind` 는 이미 로그에 있으므로 집계만 추가하면 됩니다 —
**가장 값싼 개선**이고, (B)의 판정에도 직접 필요합니다.

> `hits`/`misses` 옆에 `byKind: {LOCALIZATION: n, RATIONALE: n, TRACING: n, UNKNOWN: n}` 한 줄.
> UNKNOWN 이 5% 미만으로 나오면 게이트는 사실상 항등함수이고, 그건 지금 아무도 모릅니다.

## 4. 발견 — 심각도 순

### ① 검색 질의와 sessionId에 길이 상한이 없다 (궤적 로그가 유일한 복구 불가 자산인데)

`SearchService.kt:44` 주석이 `limit` 을 죄는 이유를 명시합니다 — *"서빙한 힌트는 궤적
로그에 `served` 로 영구히 남는다. 로그는 append-only 이고 유일한 복구 불가 자산이라,
한 요청이 수천 개를 적어 넣을 수 있으면 안 된다."*

같은 논리가 `query` 와 `sessionId` 에는 적용돼 있지 않습니다. `SearchRequest.query`(:5)는
상한이 없고, 분석된 항이 `QuerySpan.keywords` → `Trajectory.keywords` 로 로그에 그대로
들어갑니다. `sessionId` 는 클라이언트가 주는 임의 문자열이 그대로 맵 키이자 로그의
`session` 필드입니다.

`grep` 은 `pattern.length > MAX_PATTERN`(ContentService.kt:134)으로 정확히 이 형태를 막고
있어서, **같은 판단이 한쪽에만 적용된 비대칭**입니다.

### ② 인증 없는 `POST /api/search` 가 30분 사는 세션 객체를 무제한 만든다

`Controller.search`(:41) → `store.onQuery` → `sessions.computeIfAbsent`
(TrajectoryStore.kt:107). 세션은 `SessionSweeper` 가 **30분 유휴 뒤에야** 거둡니다
(`sessionIdleMillis: 1800000`). 매 요청 새 `sessionId` 를 주면 30분치 요청량만큼
`Session` 객체가 힙에 살아 있습니다. 등록 안 된 `userKey` 여도(결과는 빈손) 세션은
만들어집니다 — ACL 은 결과를 막지만 관측은 안 막습니다.

`sessions.size` 는 `stats()` 에 `activeSessions` 로 **나와 있지만 상한은 없습니다.**
신뢰 경계 안의 소규모 팀 배포라 실무 위험은 낮고, `limit` 을 죈 것과 정확히 같은 부류의
자원 상한이 빠진 자리입니다.

### ③ `/api/stats` 가 인증 없이 `aclEnforced` 를 알려준다

`stats` 는 `/api/admin` 밖이라 `AdminGuard` 가 안 걸립니다. `--status`
(wikilens_mcp.py:150)가 이걸 써야 하므로 **의도된 설계**이고, 콘텐츠는 새지 않습니다.

다만 `aclEnforced: false` 인 배포에서는, 포트에 닿은 누구나 인증 없이 "이 서버는 권한을
안 본다"를 확인하고 아무 `userKey` 로 전 문서를 읽을 수 있다는 것까지 **한 번의 GET으로
알게 됩니다.** 설정 상태가 발견 가능해집니다. 심각도는 낮지만(끄는 것 자체가 신뢰 경계
안 전용), `AclRegistry` 의 긴 주석이 "끄는 것이 정당한 경우는 좁다"고 못 박은 것과 짝을
이루어 한 줄 적어둘 값어치는 있습니다.

### ④ `cli/build/lib/` 에 소스와 다른 CLI 사본이 남아 있다

`acl.py` 가 137줄(사본) vs 187줄(소스) — ACL 429 백오프와 fail-closed 수정 이전 판입니다.
gitignore 대상이고 `pytest.ini` 의 `testpaths` 가 수집하지 않으므로 **동작 위험은
없습니다.**

적는 이유는 이 저장소가 *"설치는 버전별 캐시로 복사되므로 설치본이 조용히 구버전으로
돈다"*(조용히 실패 11번)에 계약까지 두고 있기 때문입니다. 같은 모양의 사본이 하나 남아
있고, 다음 사람이 grep 으로 여기 먼저 닿을 수 있습니다. `rm -rf cli/build` 한 줄이면
끝납니다.

### ⑤ `check.sh` 의 JUnit PASS 요약이 "무엇을 검증했나"를 안 말한다

이번 실행 결과가 `PASS JUnit — 5 actionable tasks: 5 up-to-date` 였습니다. Gradle 의
up-to-date 판정은 건전하지만, `tail -1` 이 집는 줄이 **테스트가 이번에 안 돌았다**는
사실을 PASS 라벨 아래 감춥니다. `check.sh` 가 스스로 *"성공만 라벨이라 실패한 채로
커밋한 적이 있다"* 를 근거로 만들어진 스크립트라, 요약 문구가 검증량을 말하지 않는 것은
같은 결의 약점입니다. (판정 자체는 옳으므로 `--rerun-tasks` 는 과합니다 — 요약을
`tail -3` 에서 테스트 수를 집게 하는 정도.)

## 5. 최적화 여지 — 실측

### ⑥ `hints()` 가 후보 수에 선형이고, 후보 수는 단조증가한다 【최대 건】

임시 프로브를 붙여 실제로 쟀습니다(측정 후 삭제, `git status` 클린):

```
ebLower 한 번        = 4.71 us
hints(후보    100)   = 0.45 ms/질의
hints(후보  1,000)   = 4.03 ms/질의
hints(후보 10,000)   = 39.9 ms/질의
```

**이 비용은 검색 핫패스에 있고**(`SearchService.kt:80`, 모든 검색이 지나감), 후보 수 =
한 항에 대해 지금까지 관측된 서로 다른 목적지 수입니다. 포스팅은 **절대 줄지 않습니다** —
궤적 로그가 append-only 이고 재기동마다 전량 재생되니까요. 즉 `문서`·`가이드` 같은 흔한
항은 시간에 비례해 후보가 쌓입니다.

저장소는 궤적 로그의 **크기와 재생 시간**은 `stats` 로 감시하면서(`trajectoryLog`), 그
로그가 만드는 **질의 시점 비용**은 감시하지 않습니다. `termPagePairs` 가 있지만 그것이
지연으로 얼마나 번역되는지는 어디에도 없습니다. 지금은 값이 작아 안 보이는, 정확히
"조용히 느려지는 자리"입니다.

**고칠 곳은 하나입니다.** `ebLower` 는 이분법(`betaPpf`, Reliability.kt:100)으로 1e-9까지
수렴시키는데, `hints()` 가 그 값으로 하는 일은 **임계값 비교**가 대부분입니다(:324).
`betaCdf` 는 단조증가이므로:

```
betaPpf(q, a, b) ≥ T   ⟺   betaCdf(T, a, b) ≤ q
```

즉 **cdf 한 번**이면 판정이 끝나고, 정확한 값은 **통과한 소수(≤limit)에만** 구하면
됩니다. 같은 프로브로 확인했습니다:

```
betaCdf 1회 = 0.200 us  →  ebLower 대비 23.6배  (리플렉션 포함이라 실제 이득은 더 큼)
등가성 검사: (hits 0..12) × (misses 0..12) × (prior .05/.30/.60/.85) = 676조합 전부 일치, 불일치 0
```

10,000 후보 기준 **약 40ms → 2ms 수준**입니다. 덤으로 `hints()` 의
`intArrayOf(hm[0], hm[1])`(:313)이 (항×페이지)마다 배열을 하나씩 할당하는데
(3항 × 1만 = 3만 할당/질의), 락 안에서 int 둘을 지역변수로 읽으면 그것도 사라집니다.

#### 재현 방법

`server/src/test/kotlin/dev/wikilens/learn/` 에 임시 JUnit 클래스를 만들어
`./gradlew test --tests '*<이름>*' -i` 로 돌리고 `println` 을 grep 합니다. 프로브가 한 일:

1. `Reliability.ebLower(i%7, i%3, 0.3)` 을 10만 회 (워밍 2만 회 뒤)
2. `Reliability::class.java.getDeclaredMethod("betaCdf", …)` 를 리플렉션으로 열어 10만 회
3. `TrajectoryStore(sink={})` 에 항 3개 × 목적지 N개를 `replay` 로 심고 `hints()` 200회
4. 등가성은 (hits, misses, prior) 격자에서 `ebLower(...) >= 0.45` 와
   `betaCdf(0.45, a, b) <= 0.05` 를 대조 — `a = pm*5 + hits`, `b = (1-pm)*5 + misses`

**측정 뒤 반드시 지울 것.** 이 저장소의 테스트는 회귀 방어이지 벤치가 아닙니다.

### ⑦ `grep` 이 ripgrep 경로에서 쓰지 않는 경로 문자열을 문서마다 만든다

`ContentService.kt:149` 가 매 요청
`index.allMeta().filter{canSee}.map{ PageRef(id, title, relPagePath(id)) }` 를 만듭니다.
그런데 `RipgrepEngine` 은 **디렉터리를 통째로 rg 에 주고 결과를 거르는** 방식이라(:75)
`relPath` 를 한 번도 안 씁니다 — `visible[id]` 조회용 id/title 만 필요합니다.

D20 이 "두 엔진이 ACL 목록 고정비를 똑같이 낸다"고 적은 그 고정비의 일부가 **rg 경로에서는
순수 낭비**입니다. 14k 문서면 문자열 조립 14k회/요청. `PageRef.relPath` 를 `by lazy` 로
돌리거나 엔진에 따라 안 만들면 됩니다. 크지는 않지만 "요청당 한 번만 풀라"고
`VaultLocator` 에서 66ms 를 잡아낸 것과 같은 성격입니다.

### ⑧ (관찰) `allTokens` 는 페이지가 사라져도 줄지 않는다

`AclRegistry.replacePages`(:72)가 `byPage` 는 `retainAll` 로 정리하지만 `allTokens`(:59)는
누적만 합니다. `enforced=false` 일 때 `tokensFor` 가 돌려주는 집합이 삭제된 스페이스의
토큰까지 들고 있게 됩니다. 상위집합이라 **유출은 아니고**(`byPage` 가 이미 비었으므로
`canSee` 는 false), 크기가 그룹 수에 묶여 있어 비용도 없습니다. 되돌리기 전 확인용으로만
적습니다.

또한 `tokensFor` 가 내부 가변 셋을 그대로 반환합니다(:92). 아무 호출부도 안 건드리지만,
ACL 클래스가 내부 상태 참조를 내주는 것은 이 저장소의 나머지 규율과 어긋납니다.

## 6. 권장 순서

| | 무엇 | 왜 | 비용 |
|---|---|---|---|
| 1 | `stats()` 에 `byKind` 분포 추가 | 게이트가 실제로 무엇을 거르는지 **지금 아무도 모른다.** 학습 효용 판정의 선결 | 10줄 |
| 2 | `query`·`sessionId` 길이 상한 | `limit` 을 죈 것과 **같은 이유**가 적용 안 된 자리. 로그는 복구 불가 | 5줄 |
| 3 | `hints()` 임계 판정을 `betaCdf` 1회로 | 실측 23.6배 · 등가성 676/676 확인. 조용히 느려지는 유일한 핫패스 | 20줄 |
| 4 | 실사용 궤적 2~4주 수집 → `pWrong` 판독 | **(B) 학습 레이어의 존재 이유를 처음으로 재는 일** | 코드 0 |
| 5 | `activeSessions` 상한 | 인증 없는 무제한 할당. 심각도 낮음(신뢰 경계 안) | 5줄 |
| 6 | `rm -rf cli/build` · `PageRef.relPath` 지연 | 청소 | 각 1~3줄 |

1·2·3·5 는 바로 구현 가능합니다. **4번이 실질적으로 가장 중요한 항목이고, 그건 코드로
대신할 수 없는 일입니다** — 다만 1번이 없으면 4번의 결과를 해석할 수 없으니 순서는
이대로가 맞습니다.

---

## 인수인계 메모

- 이 리포트는 **진단만** 했습니다. 코드는 한 줄도 안 고쳤습니다.
- `§4` 는 전부 "실패하는 것"이 아니라 "상한·계측이 빠진 것"입니다. 지금 깨져 있는 것은
  없고 `./check.sh` 는 통과합니다.
- `§5 ⑥` 을 구현한다면 `LearnLayerTest.kt` 의 EB 기대값 6개가 그대로 통과해야 합니다 —
  그 값들은 `cli/wikilens/scoring_reference.py` 산출이고 계약이 1e-6 으로 대조합니다.
  임계 판정만 바꾸고 **통과한 후보의 최종 `reliability` 값은 기존 `ebLower` 로 구할 것.**
- `§6` 1번(`byKind`)을 넣을 때 `stats()` 의 `sinceStart` 안에 넣지 마세요. 그건 재생
  불가능한 값들의 자리이고, `kind` 는 로그에 있어 **재생되는 누적값**입니다.

---
---

# 후속: 96a551a → f592443 (23커밋) 분석

**작성 2026-08-10 (같은 날 저녁) · 대상 `f592443`**

위 리포트를 받은 다른 세션이 23개 커밋을 쌓았습니다. 그 결과를 검토하고, 새로 찾은
결함 둘을 고친 기록입니다. 아래 판정의 근거가 된 실행:

- `./check.sh` → 넷 모두 통과 (계약 **68 → 79개**, MCP 54 → 63건, pytest 170)
- `SearchServiceTest` 를 두 가지로 변형해 실행 (§B)
- Docker 런타임 단계를 떼어 재현 + 실제 `docker compose up` (§A)

## 1. 위 리포트 6항목의 처리

| | 항목 | 결과 |
|---|---|---|
| 1 | `byKind` 분포 | 구현. `sinceStart` **밖**에 배치 — 인수인계 메모의 구분을 지켰습니다 |
| 2 | `query`·`sessionId` 상한 | 구현. 제안 이상 — `MAX_QUERY=500`(자르지 않고 **거부**), `MAX_SESSION_ID=128`(자르면 두 세션이 합쳐지므로 **버림**), `MAX_KEYWORDS=32`, `droppedSessions` 계측 |
| 3 | `hints()` cdf 1회 | 구현. **제안보다 정확함** — 아래 |
| 4 | 실사용 궤적 수집 | 미착수 (예정대로) |
| 5 | `activeSessions` 상한 | 구현. 맵이 차면 **새 세션만** 거절, 기존 세션은 계속 관측 |
| 6 | `cli/build` 삭제 · `relPath` 지연 | 전자 완료. **후자는 넣었다가 되돌림** — 아래 |

**3번이 제안보다 정확한 지점.** 리포트는 `ebLower >= threshold` 를 `betaCdf` 비교로
바꾸라고만 적었는데, 실제 판정은 `rel = ebLower × c >= serveThreshold`(c = 커버리지)라
**페이지마다 문턱이 `serveThreshold / c`** 다. 구현은 그것을 반영하고 `threshold >= 1.0`
경계(항을 일부만 덮은 후보)까지 처리했다 — 제안대로 짰으면 커버리지가 조용히 무시됐다.
수치도 리포트의 23.6배는 `ebLower` **단가**였고 구현 쪽 5.4~17.6배는 `hints()` **전체**다.
후자가 실제로 얻는 값이므로 **구현 쪽 수치가 맞다.**

**6번을 되돌린 것도 옳다.** `relPath` 는 문서당 최대 한 번 읽히는데 `by lazy` 는
인스턴스마다 홀더와 락을 만든다. 순서를 뒤집어 재니 lazy 가 오히려 느렸다
(0.56~0.66 vs 0.42~0.48ms). **리포트 ⑦은 근거 없는 제안이었다.**

## 2. 그쪽이 스스로 찾은 것

- **`Gate` 의 ㄹ 불규칙** — `어떻게 흐르` 만 있어서 실제로 치는 말인 "어떻게 흘러가나"가
  TRACING 을 놓치고 길이 폴백으로 LOCALIZATION 이 됐다. 경로 의존 질의가 **간선을
  만드는 쪽으로** 새는 거라 방향이 나쁘다. 위 리포트는 마커 목록을 읽고도 못 봤다.
- **자기가 방금 넣은 것의 2차 결함** — 질의 거부를 넣었더니 `Controller` 가 거부된
  질의도 그대로 `onQuery` 했다. 검색이 안 돌았는데 세션이 생기고 `sinceStart` 가
  클라이언트 오류로 오염되는 상태. "결과 0건과는 다르다"는 구분까지 붙였다.
- **D20 인과 오류** — ACL 고정비는 전체의 0.18% 라 비를 안 바꾼다. 진짜 원인은 워밍 유무.
- **와이어 포맷 계약** (`contract/wire-format.json` + `WireFormatTest`) — `Dto.kt` 주석에
  있던 사실을 실행되는 가드로 옮겼고, 정본을 넣자마자 불일치를 하나 잡았다.
- **`GrepScaleTest` + `SyntheticVault`** — 성능 측정을 실코퍼스에서 떼어내며 기록된
  상수가 2배 틀린 것을 찾았다.

## 3. 새로 찾은 결함 둘 — 고쳤음

### A. `docker compose up` 이 기동하지 못했다 【실측 · 고침】

`server/Dockerfile` 이 `USER wikilens`(uid 10001)로 도는데 `/state`·`/index` 를 이미지에
만들지 않았고, `compose.yml` 은 그 경로에 named volume 을 붙인다. Docker 는 **이미지에
없는 경로**의 새 볼륨을 `root:root 0755` 로 만든다. 2단계를 그대로 떼어 재현:

```
uid=10001(wikilens) gid=10001(wikilens)
drwxr-xr-x 2 root root /index
drwxr-xr-x 2 root root /state
touch: cannot touch '/state/.lock': Permission denied
```

`/state/.lock` 이 `StateDirLock` 의 첫 쓰기다. 거기서 나는 것은 `AccessDeniedException`
이라 `OverlappingFileLockException` catch 를 안 지나가고, `StateDirLockFailureAnalyzer`
도 `AlreadyRunning` 만 다루므로 **만들어 둔 진단 대신 생 스택트레이스**가 난다.

**왜 그쪽 검증("재빌드 후 끝까지 재확인 … health = healthy")이 통과했나** — Docker
Desktop for Mac 은 **bind mount 만 권한을 재매핑**한다. `docker run` + bind mount 로는
안 걸리고 `docker compose up`(named volume)에서 걸린다. Linux 호스트는 둘 다 걸린다.

**고친 것:** `USER` 앞에 한 줄.

```dockerfile
RUN mkdir -p /vault /state /index \
 && chown wikilens:wikilens /vault /state /index
```

새 named volume 이 이미지 쪽 디렉터리의 소유권을 물려받는다. **실제 `compose up` 으로
확인**: `/state/.lock` 이 `wikilens wikilens` 소유로 생성, `/api/health` 200,
`/api/stats` 정상(`byKind` 노출 확인).

계약을 하나 더했다 — `chown` 이 있는지와 그것이 `USER` **앞**인지 둘 다 본다(뒤면
비루트라 chown 이 못 돈다). 두 방향 모두 되돌려 빨개지는 것을 확인했다.

### B. "삭제된 페이지" 재현이 프로덕션 상태가 아니었다 【실측 · 기록 정정】

`a6df831` 이 `hints()` 의 술어에 존재 조건(`index.metaOf(pid) != null`)을 더하며
*"위키에서 문서를 지우면 그 간선이 살아있는 것을 밀어낸다"* 를 실측과 함께 적었다.

가드 자체는 옳고, 되돌리면 테스트가 실제로 빨개진다:

```
A) 존재 가드 제거 → SearchServiceTest > … FAILED
```

그런데 **그 상태를 만드는 것은 테스트 헬퍼다.** `load` 는 `acl.putPage`(추가 전용)를
쓰는데 프로덕션의 `VaultReader.read` 는 `acl.replacePages` 를 부르고 그것이 `retainAll`
로 사라진 페이지를 지운다 — **삭제 방향은 권한 술어가 이미 거른다.** 헬퍼를 프로덕션과
같게 바꾸고 가드를 뺀 채 돌리면:

```
B) load 를 replacePages 로 + 가드 제거 → BUILD SUCCESSFUL
```

원인은 헬퍼의 KDoc 이 낡은 것이었다 — *"프로덕션에서 `VaultReader.read()` 가
`acl.putPage()` 를 하고"* 라고 적혀 있는데 `replacePages` 도입 때 사실이 아니게 됐고,
그 문장이 재현을 프로덕션처럼 보이게 만들었다.

**가드는 남겼다** (스냅샷 맵 조회 한 번이라 사실상 공짜이고, 같은 술어를 자리만 바꿔
세 번 놓친 이력이 있다). 고친 것은 **기록**이다:

- `SearchService` 의 필터 주석 — "고친 버그" 가 아니라 **아직 안 일어난 것을 막는 것**
- `SearchServiceTest.load` KDoc — 프로덕션과 다르다는 사실과 **왜 일부러 그렇게 두는지**
- 테스트 이름 — `삭제된 페이지의…` → `갈린 상태에서도 색인에 없는 간선이 슬롯을 먹지 않는다`
- `DECISIONS.md` D16 말미에 정정

프로덕션에서 두 맵이 갈리는 자리는 `IndexingService.reload()` 의
`replacePages`(볼트 읽기 안) → `rebuild`(그 뒤) 사이 창 하나뿐이다.

**교훈은 "가드가 있다는 사실은 증거가 아니다" 의 한 단계 안쪽이다.** 되돌려 빨개지는
것을 확인했고 그것은 옳았지만, 그것이 증명하는 것은 **가드가 배선됐다**는 사실이지
**그 상태가 일어난다**는 사실이 아니다. 재현 장치가 프로덕션과 다른 API 를 쓰면
빨간불은 자기 자신을 가리킨다 — 재현을 만들 때 **프로덕션이 부르는 것과 같은 함수를
부르는지** 먼저 볼 것.

## 4. 이번에 바꾼 파일

```
server/Dockerfile                        A 수정 + 근거
contract/shared_contract.sh              계약 1개 추가 (79개)
server/.../service/SearchService.kt      B 주석 정정
server/.../service/SearchServiceTest.kt  B 헬퍼 KDoc · 테스트 이름·주석
DECISIONS.md                             D16 말미에 B 정정
```

`./check.sh` 통과 — 계약 79 · pytest 170 · MCP 63 · JUnit(4 executed).
커밋은 하지 않았다.

## 5. 다음 세션에

- 위 리포트 `§6` 의 **4번(실사용 궤적 수집)이 유일하게 남은 큰 항목**이다. 1번(`byKind`)이
  들어갔으니 이제 해석할 수 있다 — UNKNOWN 비율이 5% 미만이면 게이트는 항등함수다.
- Docker 는 **Linux 호스트에서 한 번 돌려볼 것.** 이번 확인은 전부 Docker Desktop for
  Mac 이고, bind mount 권한 재매핑이라는 플랫폼 차이가 실제로 판정을 뒤집었다.
- `StateDirLockFailureAnalyzer` 가 `AlreadyRunning` 만 다룬다. 상태 디렉터리에 못 쓰는
  경우(`AccessDeniedException`)도 같은 자리에서 사람 말로 안내할 수 있다 — A 를 고쳐서
  당장 급하지는 않지만, 마운트를 손으로 주는 배포에서 같은 모양으로 다시 난다.

---
---

# 후속 2: 군더더기 걷어내기 (주석 압축 + 죽은 코드 제거)

**작성 2026-08-11 · `f592443` 기준 · `./check.sh` 통과(계약 79 · pytest 170 · MCP 63 · JUnit)**

## 왜

Kotlin main 이 **코드 1,839줄에 주석 1,524줄(45%)** 이었다. 이 밀도는 사고가 아니라
규율의 결과다 — 이 저장소는 주석을 회귀 방지 장치로 쓴다. 그런데 그 규율이 **주석
하나씩** 적용됐지 저장소 전체로 적용된 적이 없어서 세 가지가 쌓였다: 한 KDoc 안에서의
되풀이, 세션 시점 서술("방금"·"어제"), 그리고 증명 가능한 죽은 것.

**문서와의 문자 그대로 중복은 5% 뿐이었다** — 부피의 원인은 중복이 아니라 부연이다.
그래서 "CLAUDE.md 에 있으니 지운다" 는 접근은 애초에 성립하지 않았다.

## 원칙

> **이 문장이 없으면 누군가 이 코드를 되돌리거나 잘못 고칠까?**

**예 → 남긴다**(수치, 되돌림의 결과, 측정 조건, 기각된 대안).
**아니오 → 지운다**(정황, 되풀이, 코드를 그대로 옮긴 줄).
그리고 **무시간형으로** 쓴다 — 누가 언제 했는지는 `git log` 가 안다.

## 결과

| | 전 | 후 |
|---|---|---|
| Kotlin main 주석 | 1,524줄 (45%) | **1,147줄 (39%)** |
| Python 주석 | 812줄 (27%) | 787줄 (26%) |
| 전체 diff | | 35파일 · +690 / −914 |

## 작업 1 — 증명 가능한 제거

| 대상 | 근거 |
|---|---|
| `WikiLensProperties` 의 `LuceneIndex.checkAnalyzerMatches` 참조 | **그 함수가 없다**(실제는 `reportAnalyzer`) |
| `Reliability.wilsonLower` 삭제 | main·test 통틀어 호출처 0 |
| `VaultText` 신설, `lenientReader` 통합 | 같은 디코더가 **세 벌**이었다 — `ContentService`·`JvmGrepEngine`(동일 본문) + `RipgrepEngine`(스트림판) |
| `SearchService` 융합 루프의 `acl.canSee(req.userKey, …)` 삭제 | `hints()` 가 이미 **정의상 더 강한 술어**를 적용 — 발동할 수 없는 분기 |
| `LearnProps.sweepIntervalMillis` 삭제 | `@Scheduled` 가 프로퍼티 문자열로 받아 이 필드는 안 읽힌다 |
| `models.jsonl_line` 인라인 | `canonical_json` 을 그대로 부르는 별칭, 호출 1곳 |
| `LuceneIndex.search()` → `internal` | 테스트에서만 호출 |
| `UserConfig` 의 고아 KDoc | `defaultHome()` 설명이 `homeOverride` 앞에 붙어 KDoc 두 개가 겹쳐 있었다 |

**ACL 검사를 지운 것이 가장 놀라워 보이는 변경**이라, 왜 안전한지를 술어가 사는 자리에
남겼다 — `canSee(userKey, p)` 는 정의상 `canSee(tokensFor(userKey), p)` 이므로
`hints()` 의 술어에 포함된다. "옮기거나 지우지 말 것" 도 함께 적었다.

**`layout.py` 의 `SHARD_DEPTH` 는 건드리지 않았다** — `SHARD_DEPTH=1` 이라 루프가 한 번만
돌지만 계약이 그 문자열을 세 파일에서 함께 검사한다.

## 작업 2 — 주석 압축

밀도순이되 `learn/`·`acl/` 을 먼저 했다(되돌리면 안 되는 것이 가장 많은 곳에서 판정
기준을 먼저 벼려야 한다). 지운 것을 유형별로:

- **되풀이** — 한 KDoc 이 같은 말을 두세 번. `AclRegistry` 의 시행 스위치 근거가 클래스
  KDoc·`tokensFor`·`application.yml` 셋에 있었다(→ 클래스 KDoc 한 벌 + 한 줄 참조).
- **정황** — "그동안은 운영자가 재색인을 부르는 것으로 우연히 가려져 있었다" 류.
  무엇이 터졌는지는 남기고 그때의 사정은 지웠다.
- **코드 복창** — `// 맵을 갈아끼운다` 바로 아래 `replacePages(...)` 같은 줄.
- **세션 시점** — "방금 넣은"·"한때" 를 무시간형으로. 30건 → 0건.

## 지운 수치 감사 — 둘을 되살렸다

주석 816줄을 지웠으므로 **수치가 사라졌는지 기계로 감사**했다(지워진 줄의 측정값이
새 텍스트에 남아 있는지 대조). 21개가 걸렸고 각각을 원칙으로 판정해 **둘을 복원**했다:

- **`layout.py` 의 나열 컬럼**(0.1ms → 25.3ms) — 요약문에 "직접 열기" 수치만 남겼는데,
  **"샤딩의 이유는 나열하는 쪽" 이라는 결론을 지탱하는 것은 나열 컬럼**이다. 표를 되살렸다.
- **기동 전체 재구축의 근거와 비용**(2,377건 6.7초) — "ACL 만 채우지 않고 왜 전체
  재구축인가" 는 되돌릴 수 있는 설계 결정이다.

나머지 19개는 유지 판정: 파생값(1.3% = 465쌍 중 6쌍), 더 강한 형태로 대체된 것
(`2,377건 → 24개, 10만 → 1,000개` 대신 `N/100` 공식), 그리고 **저장소가 이미 스스로
철회한 코퍼스 상수**(`2,383 문서 0.64초`·`10만이면 27초` — `GREP_BUDGET_NANOS` 가
문서당 비용 모델로 대체했다).

## 딸려온 것

`plugin/local/scripts/vault_status.py` 를 고치자 **계약 하나가 깨졌다** — "설치된
플러그인이 소스와 같은 내용". 설치는 버전별 캐시로 복사되므로 소스만 고치면 설치본이
조용히 구버전으로 돈다(조용히 실패 11번). 규율대로 **패치 버전을 올렸다**
(`wikilens-local` 0.17.0 → 0.17.1, `plugin.json` 과 `marketplace.json` 함께).

## 남긴 것과 그 이유

주석 비율이 여전히 높은 파일들은 **문서가 본체인 파일**이다 — `AnalyzerKind`(65%)는
분석기 셋의 선택 근거가 곧 내용이고, `WikiLensProperties`(61%)는 설정 필드의 계약이
곧 내용이다. 원칙이 남기라고 하면 남겼다.

애매하면 남기는 쪽을 택했다. 이 저장소에서 **잘못 지운 주석의 비용이 잘못 남긴 주석의
비용보다 크다** — 전자는 조용히 되돌려지고 후자는 그냥 길 뿐이다.
