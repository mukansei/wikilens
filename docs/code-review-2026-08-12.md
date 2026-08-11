# 아키텍처 · 코드 품질 · 최적화 리뷰

**작성 2026-08-12 · 대상 커밋 `b75d9ec` (master, 워킹트리 클린)**

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

## 내일 작업 순서 제안

| | 무엇 | 규모 | 검증 |
|---|---|---|---|
| **1** | 재색인 중 검색 500 (①) | `LuceneIndex` refcount + Directory 단일화 | 프로브로 실패 0, **정식 테스트로 승격** |
| **2** | 랭킹 가중 잠그기 (③) | 테스트 하나 | 1:1 로 바꿔 빨개지는지 확인 |
| **3** | `readBody` 를 `VaultText` 로 (②) | 한 줄 + 실패 카운터 | 깨진 파일로 search·grep·read 대조 |
| **4** | 싱크 커서를 시작 시각으로 (④) | 한 줄 | 싱크 중 수정 후 다음 싱크가 받는지 |

1번만 사용자에게 보이는 고장이고 나머지 셋은 **조용한 것들**이다. 그래서 순서가
심각도순이 아니라 1 → 3 → 2 → 4 가 되어도 된다(2·3은 각각 몇 분이다).

**전부 `./check.sh` 를 통과시킨 뒤 커밋할 것.** 그리고 이 저장소의 규율대로,
고친 것을 **되돌려 빨개지는지 확인한 뒤에만** "잠갔다" 고 적을 것.
