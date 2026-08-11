# 아키텍처 · 코드 품질 · 최적화 리뷰

**작성 2026-08-12 · 대상 커밋 `b75d9ec` (master, 워킹트리 클린)**

> **네 항목 모두 처리됨 (2026-08-12).** 아래 분석과 수치는 발견 당시 그대로 두고
> 각 절 머리에 결과만 붙였다 — 수치가 이 문서의 값어치이고, 고쳤다고 지우면 다음에
> 같은 자리를 의심할 근거가 사라진다.
>
> | | 커밋 | 되돌려 확인 |
> |---|---|---|
> | ① 재색인 중 검색 500 | `9bf3e88` | 참조 계수 제거 → 빨감 |
> | ③ 랭킹 가중 잠금 | `b84e950` | 되돌림 3종 전부 잡음 |
> | ② 디코더 갈림 | `ab01464` | `git stash` → 빨감 |
> | ④ 싱크 커서 | `ecf89a7` | `git stash` → 빨감 |
>
> pytest 175 → 177, JUnit 테스트 3개 추가. **고치면서 이 문서에 없던 것 셋이
> 나왔고** 아래 [작업 결과](#작업-결과--리뷰에-없던-것-셋)에 적었다.

앞선 리뷰([`code-review-2026-08-10.md`](code-review-2026-08-10.md))와 겹치지 않는다 —
그쪽은 효용성과 군더더기를 봤고, 이쪽은 **동시성 · 경계 조건 · 잠겨 있지 않은 상수**를
본다. 아래 판단의 근거가 된 실행은 전부 이 커밋에서 이루어졌다:

- `./check.sh` → **넷 모두 통과** (계약 79 · pytest 175 · MCP 75 · JUnit)
- Kotlin main 주요 파일 정독(`LuceneIndex`·`TrajectoryStore`·`SearchService`·
  `ContentService`·`Controller`·`AclRegistry`·`VaultReader`·`VaultText`), `cli/wikilens/sync.py`
- 결함 셋은 **임시 JUnit 프로브로 실측**했고 되돌려서 확인했다(측정 후 삭제, 클린 확인)

**"고쳤다" 가 아니라 "이렇게 하면 재현된다" 를 적었다.** 각 항목에 재현 명령과
제안하는 고침이 함께 있다.

---

## 총평

아키텍처는 이 규모에서 드물게 잘 잡혀 있다. 경계가 실제 실패에서 유도됐고
(`api/` ↔ `service/` 분리, `learn/` 프레임워크 무의존, ACL 스위치 단일화), 되돌리면
안 되는 것마다 근거가 붙어 있다.

**결함은 아키텍처가 아니라 "가드가 있다고 믿는 자리" 에 있다.** 셋 다 겉으로는 정상이고
테스트도 초록이다.

---

## ① 재색인 중 검색이 HTTP 500 — 우선순위 1

> **해결 `9bf3e88`.** reader 참조 계수 + 디렉터리 단일화 + **분석기 분리**(아래 참고).
> `ReindexRaceTest` 로 잠갔다.

**증상.** `/api/admin/reindex` 가 도는 동안 들어온 `/api/search` 가
`AlreadyClosedException` 으로 500 이 된다. cron 이 `sync && reindex` 를 돌 때마다
창이 열린다.

**실측(프로브).** 재색인 30회 × 동시 검색 4스레드:

```
성공 58,583 · 실패 110   — 전부 org.apache.lucene.store.AlreadyClosedException
```

**원인.** `index/LuceneIndex.kt:173`

```kotlin
val old = snapshotRef.getAndSet(Snapshot(...))
old.close()          // ← reader 를 즉시 닫는다
```

교체는 원자적인데 **수명 관리가 없다.** `analyzeAndSearch` 는 235행에서
`snapshotRef.get()` 으로 스냅샷을 잡고 그 뒤로 searcher 를 쓰는데, 그 사이에
`rebuild` 가 끝나면 자기가 들고 있는 reader 가 닫힌다.

`@ExceptionHandler` 도 `@ControllerAdvice` 도 없어 그대로 500 이 나간다.

**노출 범위는 `search` 뿐이다.** `metaOf`·`renderTree` 는 불변 맵을 보므로 안전하고,
`read`·`grep` 은 searcher 를 안 쓴다.

**제안.** Lucene 이 이걸 위해 refcount 를 제공한다. 스냅샷 획득을 감싼다:

```kotlin
private inline fun <T> withSnapshot(f: (Snapshot) -> T): T {
    while (true) {
        val s = snapshotRef.get()
        val r = s.reader ?: return f(s)          // 색인 없음 — 닫힐 것이 없다
        if (r.tryIncRef()) {
            try { return f(s) } finally { r.decRef() }
        }
        // 이미 닫힌 스냅샷을 잡았다 — 새것으로 다시
    }
}
```

**`MMapDirectory` 를 교체마다 새로 여는 것도 불필요하다**(151행) — `dir` 경로는
불변이다. 하나로 유지하면 `Snapshot.close()` 가 reader 만 다루면 되고, 디렉터리를
쓰는 중에 닫는 문제도 같이 사라진다.

**재현.** `src/test/kotlin/io/wikilens/index/` 에 프로브를 두고
`rebuild(300건)` 30회를 돌리면서 4스레드로 `analyzeAndSearch` 를 친다.
고친 뒤 같은 프로브로 실패 0 을 확인하고, **그 프로브를 정식 테스트로 남길 것** —
동시성 테스트가 지금 `SessionRaceTest` 하나뿐이고 이 공백에서 나온 결함이다.

---

## ② 깨진 UTF-8 이 검색에서만 사라진다 — 우선순위 3

> **해결 `ab01464`.** `readBody` 가 `VaultText` 를 쓰고, 못 읽은 페이지를 세어 WARN 한다.
> `VaultText` KDoc 의 "세 소비자"를 넷으로 고쳤다. `MalformedBodyTest` 로 잠갔다.

**증상.** 잘못된 바이트가 든 페이지가 `grep` 으로는 찾히고 `read` 로는 서빙되는데
**`search` 로만 도달 불가**다. 로그도 없다.

**실측.** 같은 파일에 두 디코더를 붙였다:

```
Files.readString    → MalformedInputException
VaultText.reader    → 성공 31자
```

**원인.** `vault/VaultReader.kt:130`

```kotlin
private fun readBody(root: Path, pid: String): String {
    val f = root.resolve(VaultLayout.relPagePath(pid))
    return if (Files.exists(f)) runCatching { Files.readString(f) }.getOrDefault("") else ""
}
```

`VaultText` 의 KDoc 이 경계를 정확히 서술해 뒀다:

> **세 소비자가 같은 디코더를 써야 한다** — `read`(ContentService) · JVM 스캔 · rg 의 stdout.

**그런데 넷째가 있다.** 색인 경로다. 목록이 낡았고 주석은 아무것도 안 막았다.

**제안.** `VaultText.reader(f).use { it.readText() }` 로 바꾼다. 한 줄이다.
`runCatching` 은 남기되 **실패를 세어 로그로 낸다** — 지금은 빈 본문이 조용히
색인되므로 "본문 없는 페이지" 와 구별되지 않는다.

KDoc 의 "세 소비자" 를 넷으로 고칠 것. **그리고 계약이 이것을 검사하게 할 것** —
`Files.readString` 이 볼트 경로에 다시 나타나면 걸리도록.

---

## ③ 랭킹 가중이 잠겨 있지 않다 — 우선순위 2

> **해결 `b84e950`.** `FieldBoostTest` — 점수로 단언한다(순서만 보면 1:1:1 에서도
> docId 순으로 우연히 통과한다). 다른 세 상수(`RRF_K`·`LEARNED_WEIGHT`·`serveThreshold`)는
> **여전히 안 잠겨 있다.**

**증상.** 앵커 텍스트 색인이 이 프로젝트의 핵심 주장인데, 그 메커니즘을 무력화해도
**아무것도 빨개지지 않는다.**

**실측.** `index/FieldBoost.kt` 의 `ANCHOR = 4.0f` · `TITLE = 3.0f` 를 각각 `1.0f` 로
바꾸고 전체 테스트:

```
./gradlew test  → 통과
계약 79개       → 통과
```

**문서에는 있다.** `README.md:270` 과 `docs/architecture.md:47` 에
`앵커4 : 제목3 : 본문1` 로 두 번 적혀 있다. **검사는 0곳이다.**

**같은 상태인 상수들:**

| 상수 | 위치 | 하는 일 |
|---|---|---|
| `ANCHOR 4.0 · TITLE 3.0 · BODY 1.0` | `index/FieldBoost.kt:5-7` | BM25 필드 가중 |
| `RRF_K = 60.0` | `service/SearchService.kt` | 융합 상수 |
| `LEARNED_WEIGHT = 1.6` | 같음 | 학습 힌트 배수 |
| `serveThreshold = 0.45` | `learn/TrajectoryStore.kt` | abstention 문턱 |

**제안.** 순위가 실제로 바뀌는지 보는 테스트 하나면 된다 — 같은 어휘를 제목에만 가진
문서와 앵커에만 가진 문서를 넣고 **앵커 쪽이 위에 오는지** 본다. 가중을 1:1 로 만들면
빨개져야 한다(그것까지 확인한 뒤에 "잠갔다" 고 적을 것).

계약에 상수 문자열을 grep 으로 박는 방법도 있지만 그건 값을 지킬 뿐 **동작을 안 지킨다.**
문서 두 곳과 함께 검사하려면 grep 이 맞고, 랭킹을 지키려면 테스트가 맞다.

---

## ④ 증분 싱크가 자기 실행 시간만큼을 흘린다 — 우선순위 4

> **해결 `ecf89a7`. 미검증이었는데 재현했고 맞았다.** 재현하려면 가짜 서버가
> `lastModified >` 를 실제로 걸러야 했다 — 안 걸러서 이 결함이 여태 안 잡혔다.

**미검증이다.** 코드를 읽고 낸 판단이고 실행으로 재현하지 않았다.

`cli/wikilens/sync.py`:

```python
447:  started = time.time()
448:  cursor = state.get("cursor")             # 이 값으로 lastModified > cursor
      ...                                       # 13,933건 순회 (수십 분)
501:  state["cursor"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
```

커서를 **끝에서** 잡는다. 싱크가 도는 동안 수정된 페이지는 이미 지나갔거나 아직
안 온 상태이고, 다음 실행은 `lastModified > 끝시각` 으로 물으므로 **그 수정을 영영
안 받는다**(그 페이지가 또 수정될 때까지).

분 단위 절삭이라 최대 1분은 안전한 쪽으로 넓어지지만, 싱크 소요 시간만큼의 창은 남는다.

**제안.** 커서를 **시작 시각**으로 잡는다. 재수신은 멱등이므로(`_ingest` 가 version
비교로 `unchanged` 처리) 겹치는 쪽 손해가 없다.

**검증 방법.** 싱크 중에 페이지를 하나 고치고, 다음 싱크가 그것을 받는지 본다.
지금 구조로는 안 받아야 한다.

---

## 최적화 — 재봤고 문제 아님

`hints()` 는 질의의 항마다 그 항이 가리키는 페이지를 **전부 순회**한다. 포스팅은
줄지 않으므로(append-only · 감쇠 없음) 비용이 단조증가한다. 재봤다.

```
스캔    300건 →   190us          고정비 약 150us
      3,000건 →   965us          한계비용 약 0.06us/건
     30,000건 →  4,180us
     80,000건 →  5,529us
```

**D17 의 분포로 환산하면 문제가 아니다.** 궤적 100만 건에서도 항당 평균 150페이지라
3항 질의면 450건 스캔 ≈ 190us 이고, 실제 검색 지연이 9~14ms 이므로 **2%** 다.

**상한은 코퍼스 크기다** — `dest` 가 실제 페이지라 항 하나가 가리킬 수 있는 페이지 수는
문서 수를 못 넘는다. 이 코퍼스 최악은 3항 × 13,933 = 41,799건 ≈ 4ms, 지연의 30% 선이고
그게 천장이다.

> **다만 D17 이 이 축을 안 쟀다.** 재생 시간 · 힙 · 디스크만 봤고 **정상 상태 질의
> 지연**은 표에 없다. 결론("압축 불필요")은 안 바뀌지만 빠진 열이고, 코퍼스가 10만으로
> 가면 천장이 7배가 되므로 그때 함께 볼 자리다.

**참고 — 실서버 검색 지연**(13,933건 색인 · 포스팅 11쌍):

```
"배치 스케줄"       0.091s   (콜드)
"<시스템> 가동 주기"   0.014s
"로그인 인증 토큰"  0.009s
```

---

## 품질 관찰

**좋은 것**

- 주석이 **회귀 방지 장치**로 실제 기능한다. "되돌리면 X가 빨개진다" 가 맞는 곳이 많다
- `learn/` 순수성이 계약으로 강제되고 지켜지고 있다
- ACL 스위치가 `AclRegistry` 한 곳 — 소비자가 각자 분기하지 않는다
- 실패 모드가 **구별 가능하게** 설계돼 있다 (404 vs 403, `usable` vs 빈 결과, `truncated`)

**약한 것**

- **동시성 테스트가 하나뿐**(`SessionRaceTest`, 세션 전용). 스냅샷 교체 ·
  `replacePages` · `putUser` 는 전부 동시 접근을 받는데 안 재봤다. ①이 그 공백이다
- **랭킹 상수가 문서에만 있다**(③)
- `hints()` 의 `cover` 가 **미스만 쌓인 항도 올린다**(`TrajectoryStore.kt:328`).
  `best` 가 최댓값을 취하므로 신뢰도 자체는 낮게 나오지만 `c` 는 부풀어 있다.
  포스팅이 얕아 아직 안 드러난다 — `pWrong` 이 오르기 시작하면 볼 자리

**테스트에서 이름이 한 번도 안 나오는 main 클래스** (DTO 제외):
`VaultText` · `FieldBoost` · `VaultBootstrap` · `StateDirLockFailureAnalyzer`.
앞의 둘이 ②③의 무대다.

---

## 작업 결과 — 리뷰에 없던 것 셋

제안한 순서(① → ③ → ② → ④)대로 처리했다. **고치면서 이 문서가 못 본 것이 셋 나왔다.**
전부 "고치는 과정에서만 보이는" 종류라 적어둔다.

### ⓐ 분석기도 같은 경쟁을 갖고 있었다 (①)

reader 만 참조 계수로 고쳤으면 **같은 실패가 자리만 옮겨 남았다.** `Snapshot.close()` 가
분석기도 닫는데 분석기는 참조 계수를 안 따르므로, reader 를 잡은 스레드가 **닫힌
분석기로 토큰화**하게 된다. 종류당 하나를 인스턴스가 들고 살게 바꿨다.

교훈: **한 객체의 수명을 고치면 그 객체와 함께 닫히던 것들을 전부 봐야 한다.**

### ⓑ 순서 단언만으로는 가중을 못 지킨다 (③)

되돌림을 셋으로 시험했다:

```
전부 1.0          앵커(0.4458) = 제목(0.4458)     순서 단언이 잡음
앵커만 2.0        앵커(0.8917) < 제목(1.3375)     순서 단언이 잡음
1.2 : 1.1 : 1.0   순서는 맞고 배수 1.20            배수 단언이 잡음
```

셋째가 **순서는 맞는데 가중이 사실상 꺼진** 상태다. 배수 단언이 없으면 통과한다.

그리고 1:1:1 에서 세 점수가 `0.44583148` 로 **완전히 같은 것**을 확인했다 — 필드 통계를
같게 맞춘 설계가 실제로 성립한다는 뜻이고, 그래서 점수비가 곧 가중비다.

### ⓒ 기존 테스트가 가짜 서버의 느슨함을 재고 있었다 (④)

④를 재현하려면 가짜 서버가 `lastModified >` 를 실제로 걸러야 했다. **안 걸러서 이
결함이 여태 안 잡혔다** — 커서를 어떻게 잡든 전부 다시 받으므로 늘 통과했다.

가짜를 실물에 맞추자 `test_second_sync_skips_unchanged` 가 깨졌다. 그 테스트의
`unchanged == 5` 는 **서버가 전부 돌려주기 때문에** 성립하던 값이고 실물은 애초에
안 돌려준다. `fetched == 0` 으로 바꾸고, `_ingest` 의 unchanged 분기는 별도 테스트로
덮었다(커서 분 단위 절삭 때문에 경계 페이지가 실제로 다시 넘어오는 경로다).

교훈: **가짜가 실물보다 느슨하면 그 차이만큼이 사각지대다.** 이번엔 그 사각지대에
실제 결함이 있었다.

---

## 아직 안 한 것

| | 왜 안 했나 |
|---|---|
| `RRF_K` · `LEARNED_WEIGHT` · `serveThreshold` 잠그기 | ③과 같은 상태다. `FieldBoostTest` 가 본을 만들었으니 같은 방식으로 가능하다 |
| `hints()` 의 `cover` 가 미스만 쌓인 항도 올림 | 포스팅이 얕아 아직 안 드러난다. `pWrong` 이 오르면 볼 자리 |
| 계약에 `Files.readString` 금지 추가 | ②의 재발 방지. 지금은 테스트가 색인 경로만 잠근다 |
| D17 에 질의 지연 열 추가 | 결론은 안 바뀌지만 근거표가 불완전하다 |
| `replacePages` · `putUser` 동시성 | ①과 같은 공백. 안 재봤다 |
