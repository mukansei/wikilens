package dev.wikilens.learn

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger


class TrajectoryStore(
    private val sink: TrajectorySink,
    private val serveThreshold: Double = 0.45,
    /** 앞 질의와 키워드가 이만큼 겹치면 앞 시도가 실패한 것으로 본다. */
    private val reformulationOverlap: Double = 0.5,
) {
    private val sessions = ConcurrentHashMap<String, Session>()

    /**
     * 항 단위 포스팅: term -> pageId -> intArrayOf(hits, misses)
     *
     * 키워드 집합 전체를 키로 쓰면 "로그인 붙이는 법 어디"와 "로그인 붙이는 법"이
     * 다른 키가 되어 카운트가 흩어진다. 자연어 질의는 매번 표현이 달라지므로
     * 정확 집합 일치는 성립하지 않는다.
     *
     * 페이지가 단일 값이 아니라 **분포**인 것도 중요하다. 같은 항에 목적지가 여럿인 건
     * 경쟁 상태가 아니라 질의가 모호한 것이므로, 벌주지 않고 분포로 기록한다.
     */
    private val postings = ConcurrentHashMap<String, ConcurrentHashMap<String, IntArray>>()

    /** 궤적 단위 카운터. 포스팅은 항마다 증가하므로 궤적 수와 다르다. */
    private val trajHits = AtomicInteger()
    private val trajMisses = AtomicInteger()
    private val trajCount = AtomicInteger()

    // 서빙한 힌트와 그중 거부된 것. `pWrong` 은 **이 비율**이어야 한다 —
    // 손익분기 `p_hit > p_wrong/(n−1)` 의 p_wrong 은 "캐시가 틀린 지름길을 줘서
    // n 대신 1+n 을 읽게 될 확률"이지, 세션이 실패할 확률이 아니다.
    private val servedCount = AtomicInteger()
    private val rejectedCount = AtomicInteger()

    /**
     * **원시 질의 수.** 궤적 수로 역산할 수 없다 — 읽기도 서빙도 없는 검색은 궤적을
     * 안 남기기 때문이다(`finalize` 의 첫 줄). 그런데 그게 정확히 "결과가 시원찮아
     * 다시 치는" 경우의 첫 시도라, 궤적만 세면 **재검색이 체계적으로 과소 계상**된다.
     *
     * 웹 검색 로그 연구는 질의 자체는 남기므로 이 편향이 없다 — 세션의 약 40%가
     * 질의 2개 이상이라는 수치가 그렇게 나온다(Chen et al., WWW '21). 우리는 그
     * 비교조차 못 하고 있었다.
     */
    private val queryCount = AtomicInteger()

    /** 시작된 세션 수. `queries / sessions` 가 세션당 질의 수다. */
    private val sessionCount = AtomicInteger()

    /** 질의를 2회 이상 받은 세션 수. 비율이 곧 "재검색이 얼마나 흔한가"다. */
    private val multiQuerySessions = AtomicInteger()

    /** 이번 기동에서 실제로 기록된 스팬 수. `queries` 와 같은 축이라 빼도 말이 된다. */
    private val recordedSinceStart = AtomicInteger()

    /**
     * 힌트는 서빙했는데 **아무것도 안 읽고 끝난** 스팬.
     *
     * 클릭 없는 종료를 곧바로 실패로 세면 안 된다는 것이 IR 에서 **good abandonment**
     * 로 알려진 문제다(Li·Huffman·Tokuda SIGIR '09, Williams et al. WWW '16) —
     * 결과 목록만 보고 답을 얻었을 수 있다. 여기서는 에이전트가 제목만 보고 답한
     * 경우가 그것이고, 예전엔 `success = success && reads.isNotEmpty()` 때문에
     * 무조건 실패로 셌다.
     *
     * **판정을 바꾸지는 않았다** — 어느 쪽인지 구별할 신호가 아직 없다.
     * 대신 세어서 `sessionFailureRate` 에서 빼고 따로 보고한다.
     */
    private val abandonedCount = AtomicInteger()

    /**
     * 지금까지 본 **권한 범위**들(`AclRegistry.scopeOf`). 신원이 아니다.
     *
     * 이 수가 1 이면 전원이 같은 권한이라 학습이 균질하다. 2 이상이면 **권한 폭이 다른
     * 사람들의 관측이 한 포스팅에 섞이고 있다**는 뜻이고, 그때부터 `rank` 가중과
     * 목적지 분포가 사람마다 다른 의미를 갖는다. 그 상태인지 아닌지를 아는 것이
     * 먼저라 세기부터 한다 — 지금은 전 페이지가 `@public` 이라 늘 1 이다.
     */
    private val scopesSeen = ConcurrentHashMap.newKeySet<String>()

    // ---------------------------------------------------------- 관측

    /**
     * `computeIfAbsent` 와 `synchronized` 사이에 `SessionSweeper`(다른 스레드)가
     * 이 세션을 거둘 수 있다. 그러면 맵에서 빠진 객체에 스팬이 쌓이고 그 궤적은
     * 확정되지 않는다 — **그런데 재봤더니 잡을 값어치가 없었다.**
     *
     * 락 안에서 "맵의 주인이 아직 나인가"를 확인하고 아니면 다시 잡는 형태를 만들어
     * 대조했다(2026-08-08, 2,000 라운드 × 5회, 스위퍼가 `idleMillis=0` 으로 계속 도는
     * 최악 조건): **재확인 있음 평균 1967 · 없음 평균 1977** — 줄이기는커녕 노이즈
     * 안에서 조금 나빴다. 이유는 창이 좁아서가 아니라 **손실의 주범이 다른 것**이라서다:
     *
     *   - 스위퍼가 락을 먼저 잡으면 기존 스팬을 확정하고, 그 뒤 `onQuery` 가 고아에
     *     스팬을 하나 더한다 → 그 하나가 사라진다. 다음 질의는 `computeIfAbsent` 가
     *     새로 만들므로 **세션이 영구히 죽지는 않는다.**
     *   - `onQuery` 가 먼저 잡으면 스위퍼의 `onEnd` 가 **모든 스팬**을 도므로 새 스팬도
     *     함께 확정된다 — 애초에 잃지 않는다.
     *   - 실제 손실 대부분은 스위퍼가 `onQuery` 와 `onRead` **사이**에 세션을 끝내는
     *     경우인데, 그때 빈 스팬을 버리는 것은 **정당한 동작**이다("배울 것이 없다").
     *     어떤 재확인으로도 줄지 않는다.
     *
     * 그래서 원래 형태로 둔다. 되살리려면 위 대조부터 다시 할 것 —
     * `SessionRaceTest` 가 그 측정 장치다.
     */
    fun onQuery(sessionId: String, query: String, keywords: List<String>, scope: String = "") {
        val s = sessions.computeIfAbsent(sessionId) { sessionCount.incrementAndGet(); Session(it) }
        queryCount.incrementAndGet()
        synchronized(s) {
            // 세션 단위로 고정이다. 권한이 세션 도중에 바뀌면 첫 값을 유지한다 —
            // 한 세션의 궤적이 두 범위로 갈리는 것보다 낫다.
            if (s.scope.isEmpty()) s.scope = scope
            val prev = s.current
            if (prev != null) {
                // 이 세션이 두 번째 질의를 받는 순간. 세 번째부터는 다시 안 센다.
                if (s.spans.size == 1) multiQuerySessions.incrementAndGet()
                val reformulated = overlap(prev.keywords, keywords) >= reformulationOverlap
                finalize(sessionId, prev, success = !reformulated, scope = s.scope)
            }
            s.spans.add(QuerySpan(normalize(keywords), Gate.classify(query)))
            s.lastTouch = System.currentTimeMillis()
        }
    }

    /**
     * 학습 레이어가 이 질의에 서빙한 힌트를 기록한다. `onQuery` 직후에 부른다.
     *
     * 이걸 알아야 **틀린 힌트를 미스로 되돌릴 수 있다.** 그전에는 미스가 나는 경로가
     * "무언가 읽고 나서 비슷한 말로 다시 검색" 하나뿐이라, 힌트가 아무리 엉뚱해도
     * 세션이 그냥 끝나면 아무 벌점이 없었다 — 실측 `pWrong` 이 계속 0.0 이던 이유다.
     */
    fun onServed(sessionId: String, hinted: List<String>, ranked: List<String> = emptyList()) {
        val s = sessions[sessionId] ?: return
        synchronized(s) {
            s.current?.let {
                it.served = hinted.toList()
                it.ranked = ranked.toList()
            }
        }
    }

    fun onRead(sessionId: String, pageId: String) {
        val s = sessions[sessionId] ?: return
        synchronized(s) {
            // 질의 없는 읽기는 궤적이 아니다 — 훅이 잡은 무관한 파일 읽기를 거른다
            s.current?.addRead(pageId) ?: return
            s.lastTouch = System.currentTimeMillis()
        }
    }

    fun onEnd(sessionId: String): Int {
        val s = sessions.remove(sessionId) ?: return 0
        synchronized(s) {
            var n = 0
            for (span in s.spans) {
                // 읽기가 없어도 서빙한 힌트가 있으면 확정한다 — 거부된 힌트가 곧
                // 실패 신호다. 판정은 `finalize` 가 한다(읽기 없음 → success=false).
                if ((span.reads.isNotEmpty() || span.served.isNotEmpty()) && !span.finalized) {
                    finalize(sessionId, span, success = true, scope = s.scope)
                    n++
                }
            }
            return n
        }
    }

    /**
     * '유용했다' 판정이 이 설계의 최대 미해결 지점이다. 훅은 무엇을 읽었는지만
     * 보여주고 그게 답이었는지는 알려주지 않는다. 두 가지 약한 신호를 쓴다:
     *   - 마지막으로 읽은 페이지가 답일 확률이 높다 (탐색은 성공에서 멈춘다)
     *   - 키워드가 겹치는 질의가 뒤따르면 앞 시도는 실패였다 (재구성 신호)
     *
     * 웹 검색의 abandonment 신호와 같은 구조이고 마찬가지로 노이즈가 있다.
     * pWrong 이 그 노이즈의 크기다.
     */
    private fun finalize(sessionId: String, span: QuerySpan, success: Boolean, scope: String = "") {
        // 읽기가 없어도 **서빙한 힌트가 있으면** 기록한다 — 그 힌트가 거부됐다는 뜻이라
        // 가장 강한 실패 신호다. 둘 다 없으면 배울 것이 없다.
        if ((span.reads.isEmpty() && span.served.isEmpty()) || span.finalized) return
        span.finalized = true
        recordedSinceStart.incrementAndGet()
        val t = Trajectory(
            ts = System.currentTimeMillis(),
            session = sessionId,
            keywords = span.keywords,
            kind = span.kind,
            reads = span.reads.toList(),
            dest = span.reads.lastOrNull().orEmpty(),
            success = success && span.reads.isNotEmpty(),
            served = span.served,
            rank = span.reads.lastOrNull()?.let { span.ranked.indexOf(it) } ?: -1,
            scope = scope,
        )
        sink.append(t)
        apply(t)
    }

    /** 재기동 시 로그 재생용. 간선은 궤적의 함수다. */
    fun replay(t: Trajectory) = apply(t)

    private fun apply(t: Trajectory) {
        trajCount.incrementAndGet()
        if (t.scope.isNotEmpty()) scopesSeen.add(t.scope)
        // 셋으로 나눈다. 읽은 게 없는데 힌트만 서빙된 것은 **실패가 아니라 미판정**이다
        // (good abandonment). 실패로 세면 `sessionFailureRate` 가 부풀고, 그러면
        // 학습이 잘 되고 있는지를 그 수로 판단할 수 없게 된다.
        when {
            t.success -> trajHits.incrementAndGet()
            t.reads.isEmpty() && t.served.isNotEmpty() -> abandonedCount.incrementAndGet()
            else -> trajMisses.incrementAndGet()
        }
        if (!t.kind.cacheable) return   // 경로 의존 질의는 간선을 만들지 않는다

        // 서빙했는데 **끝내 안 읽힌** 힌트 = 틀린 힌트. dest 와 겹치지 않게 뺀다.
        val rejected = t.served.toSet() - t.reads.toSet()
        servedCount.addAndGet(t.served.size)
        rejectedCount.addAndGet(rejected.size)

        // 읽었지만 답이 아니었던 것들. `dest = reads.last()` 라는 이 모델의 전제
        // ("탐색은 성공에서 멈춘다")를 그대로 따르면, 앞서 읽은 것들은 **열어보고
        // 지나친** 페이지다. 읽기 개수가 곧 확신도가 된다 — 1건이면 미스 0,
        // 6건이면 미스 5. 카운터 구조를 안 바꾸고 비율로 드러난다.
        val passedOver = if (t.success) t.reads.dropLast(1).toSet() - setOf(t.dest) else emptySet()

        val weight = hitWeight(t.rank)
        for (term in normalize(t.keywords)) {
            val byPage = postings.computeIfAbsent(term) { ConcurrentHashMap() }
            if (t.dest.isNotEmpty()) {
                val slot = byPage.computeIfAbsent(t.dest) { IntArray(2) }
                synchronized(slot) {
                    // 히트만 가중한다. 깊은 순위로 배운 간선(×3)은 미스 3번이라야
                    // 상쇄된다 — 강한 증거에는 강한 반증이 필요하다는 뜻이라 의도한
                    // 비대칭이다. 다만 "1위로 서빙한 힌트를 거부한 것"이 "5위로 서빙한
                    // 것을 거부한 것"보다 강한 반증일 여지는 남는다 — 미스도 순위로
                    // 가중할지는 `pWrong` 을 보고 판단할 것.
                    if (t.success) slot[0] += weight else slot[1]++
                }
            }
            // **이미 배운 페이지만** 끌어내린다. 미스의 목적은 서빙될 것을 밀어내는
            // 것인데, 항목이 없는 페이지는 애초에 서빙되지 않으므로 만들어봐야 스캔
            // 비용만 는다. `computeIfAbsent` 를 쓰면 한 번 스쳐본 페이지가 전부 영구히
            // 남는다 — 실측(100세션·5읽기)에서 항-페이지쌍이 200 → 1,200 으로 6배였고
            // `hints()` 가 질의당 2.3ms 였다.
            for (pid in rejected + passedOver) {
                val slot = byPage[pid] ?: continue
                synchronized(slot) { slot[1]++ }
            }
        }
    }

    /**
     * 순위가 깊을수록 강한 증거로 센다.
     *
     * 1위를 읽는 건 기본 행동이라 정보가 적다 — 어휘 랭킹이 이미 맞혔으므로 간선을
     * 만들어도 새로 얻는 게 없다. 반대로 7위를 읽었다면 앞의 여섯을 지나쳤다는 뜻이고,
     * **어휘 랭킹이 틀렸는데 그 페이지가 답**이라는 강한 신호다. 학습 레이어가 고치라고
     * 있는 경우가 정확히 이것이다.
     *
     * **경계값은 손으로 정했다** — position bias 의 방향(깊을수록 강함)은 웹 검색
     * 클릭 모델에서 확립됐지만, 3·6 이라는 문턱과 3배 상한은 이 코퍼스에서 측정한
     * 값이 아니다. `pWrong` 과 함께 모니터링할 것.
     */
    private fun hitWeight(rank: Int): Int = when {
        rank < 0 -> 1     // 검색 결과에 없던 페이지를 직접 읽음 — 순위 정보 없음
        rank == 0 -> 1    // 어휘가 이미 1위로 줬다
        rank < 3 -> 2
        else -> 3         // 깊은 곳에서 건져 올림
    }

    // ---------------------------------------------------------- 조회

    /**
     * 항 단위 포스팅을 조회해 후보를 모으고 커버리지로 가중한다.
     *
     * 한 궤적이 자기 키워드 전부를 증가시키므로 항별 카운트를 더하면 중복 계산이 된다.
     * 대표값(순이득 최대)을 쓰고, 대신 몇 개 항이 이 페이지를 가리켰는지를 커버리지로 반영한다.
     *
     * [priors]는 Lucene 랭킹 점수를 정규화한 값이다. EB 사전분포로 쓴다.
     *
     * [visible]로 **자르기 전에** 거른다. 이 순서가 핵심이다 — 호출부에서 `take` 뒤에
     * 거르면, 권한이 좁은 사용자는 상위 [limit]개가 전부 안 보이는 문서일 때 **힌트가
     * 통째로 0개**가 된다. 볼 수 있는 후보가 더 아래에 있어도 슬롯을 이미 뺏겼기
     * 때문이다. 지금은 전 페이지가 `@public` 이라 안 보이고 **ACL 수집이 들어오는
     * 순간 나타난다.** 같은 실패를 `SearchService` 가 어휘 결과에서 이미 한 번 겪었다
     * (`CLAUDE.md` 조용히 실패 8번 — "take 를 필터 뒤로").
     *
     * 술어는 순수 함수라 `learn/` 의 프레임워크 무의존 계약을 건드리지 않는다 —
     * 이 패키지는 ACL 이 무엇인지 몰라도 된다.
     */
    fun hints(keywords: List<String>, priors: Map<String, Double> = emptyMap(),
              limit: Int = 5, visible: (String) -> Boolean = { true }): List<Hint> {
        val terms = normalize(keywords)
        if (terms.isEmpty()) return emptyList()

        val best = HashMap<String, IntArray>()
        val cover = HashMap<String, Int>()
        for (t in terms) {
            val byPage = postings[t] ?: continue
            for ((pid, hm) in byPage) {
                val snap = synchronized(hm) { intArrayOf(hm[0], hm[1]) }
                cover[pid] = (cover[pid] ?: 0) + 1
                val cur = best[pid]
                if (cur == null || (snap[0] - snap[1]) > (cur[0] - cur[1])) best[pid] = snap
            }
        }

        return best.mapNotNull { (pid, hm) ->
            if (!visible(pid)) return@mapNotNull null      // **take 보다 먼저** — 위 주석 참고
            val c = (cover[pid] ?: 0).toDouble() / terms.size
            val rel = Reliability.ebLower(hm[0], hm[1], priors[pid] ?: 0.3) * c
            if (rel >= serveThreshold) Hint(pid, hm[0], hm[1], rel) else null
        }.sortedByDescending { it.reliability }.take(limit)
    }

    fun stats(): Map<String, Any?> {
        val h = trajHits.get(); val m = trajMisses.get(); val n = h + m
        return linkedMapOf(
            "terms" to postings.size,
            "termPagePairs" to postings.values.sumOf { it.size },
            "ambiguousTerms" to postings.values.count { it.size > 1 },
            // 궤적(세션) 단위 성공률. 사람이 원하는 것을 찾았는가.
            "hits" to h,
            "misses" to m,
            // 힌트 단위. 서빙한 지름길 중 몇이 거부됐나 — 손익분기 공식의 p_wrong 이다.
            // 예전엔 pWrong 을 궤적 성공률로 계산했는데 그건 다른 값이다.
            "served" to servedCount.get(),
            "rejected" to rejectedCount.get(),
            "pWrong" to servedCount.get().let { s ->
                if (s > 0) rejectedCount.get().toDouble() / s else null
            },
            // 읽은 것이 있는 세션 중 실패한 비율. good abandonment 는 분모에서 뺀다 —
            // 클릭 없는 종료를 실패로 세면 안 된다는 것이 IR 의 확립된 지적이다.
            "sessionFailureRate" to if (n > 0) m.toDouble() / n else null,
            "abandonedWithHints" to abandonedCount.get(),
            "trajectories" to trajCount.get(),
            // 1 이면 학습이 균질하다. 2 이상이면 권한 폭이 다른 관측이 섞이는 중이다.
            "permissionScopes" to scopesSeen.size,
            // 궤적 로그 쓰기 실패. **0 이 아니면 메모리 학습만 앞서가고 있다** —
            // 재기동하면 그만큼이 사라진다. 유일한 복구 불가 자산이라 따로 낸다.
            "logWriteFailures" to sink.failures,
            // **이 아래는 이번 기동에서만 센 값이다.** 위의 것들은 로그에서 재생되므로
            // 누적이고, 아래 것들은 로그에 없어서 재생이 불가능하다 — 소득 없는 검색은
            // 애초에 기록되지 않는다는 것이 이 값들을 만든 이유이기 때문이다.
            // 섞어서 빼면(예: queries - trajectories) 재기동 직후 음수가 나온다.
            "sinceStart" to linkedMapOf<String, Any?>(
                "queries" to queryCount.get(),
                "sessions" to sessionCount.get(),
                "multiQuerySessions" to multiQuerySessions.get(),
                "queriesPerSession" to sessionCount.get().let { s ->
                    if (s > 0) queryCount.get().toDouble() / s else null
                },
                // 질의를 2회 이상 받은 세션 비율. 웹 검색은 약 40% 다(Chen et al., WWW '21) —
                // 사람과 에이전트가 다르므로 참고점이지 기대값은 아니다.
                "multiQueryRate" to sessionCount.get().let { s ->
                    if (s > 0) multiQuerySessions.get().toDouble() / s else null
                },
                // 질의 대비 **아직 기록되지 않은** 수. 둘로 나뉜다:
                //   - 소득 없이 끝난 검색 (읽기도 서빙도 없어 `finalize` 가 버린 것)
                //   - **아직 안 끝난 세션의 진행 중인 스팬** — 곧 확정될 수도 있다
                // 그래서 이 값 하나로 "빗나간 검색 수"를 읽으면 안 된다. `activeSessions`
                // 가 0일 때만 온전히 전자를 뜻한다.
                "unrecordedQueries" to (queryCount.get() - recordedSinceStart.get()).coerceAtLeast(0),
            ),
            "activeSessions" to sessions.size,
        )
    }

    /** 세션 종료 훅을 못 받은 경우 대비. */
    fun sweep(idleMillis: Long = 1_800_000): Int {
        val now = System.currentTimeMillis()
        return sessions.filterValues { now - it.lastTouch > idleMillis }
            .keys.toList().sumOf { onEnd(it) }
    }

    private fun normalize(keywords: List<String>): List<String> =
        keywords.mapNotNull { it.trim().lowercase().ifEmpty { null } }.distinct().sorted()

    private fun overlap(a: List<String>, b: List<String>): Double {
        val sa = a.map { it.lowercase() }.toSet()
        val sb = b.map { it.lowercase() }.toSet()
        if (sa.isEmpty() || sb.isEmpty()) return 0.0
        return sa.intersect(sb).size.toDouble() / minOf(sa.size, sb.size)
    }
}
