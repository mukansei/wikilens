package io.wikilens.learn

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger


class TrajectoryStore(
    private val sink: TrajectorySink,
    private val serveThreshold: Double = 0.45,
    /** 앞 질의와 키워드가 이만큼 겹치면 앞 시도가 실패한 것으로 본다. */
    private val reformulationOverlap: Double = 0.5,
) {
    private val sessions = ConcurrentHashMap<String, Session>()

    companion object {
        /** `sessionId` 길이 상한. MCP 프록시가 만드는 것은 30자 안쪽이다. */
        const val MAX_SESSION_ID = 128

        /**
         * 동시에 살아 있는 세션 수 상한. 유휴 30분 뒤에야 거둬지므로 그 사이의
         * 요청량만큼 쌓인다 — 인증이 없는 경로라 상한이 필요하다.
         */
        const val MAX_SESSIONS = 10_000

        /** 한 질의에서 로그로 흘려보낼 항 수 상한. 자연어 질의는 여기 근처에도 안 온다. */
        const val MAX_KEYWORDS = 32
    }

    /**
     * 항 단위 포스팅: term -> pageId -> intArrayOf(hits, misses)
     *
     * **키워드 집합 전체를 키로 쓰면 안 된다** — "로그인 붙이는 법 어디" 와 "로그인 붙이는
     * 법" 이 다른 키가 되어 카운트가 흩어진다. 자연어 질의는 매번 표현이 달라진다.
     *
     * 페이지가 단일 값이 아니라 **분포**인 것도 의도다. 한 항에 목적지가 여럿인 건 경쟁이
     * 아니라 질의가 모호한 것이므로 벌주지 않는다.
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
     * **원시 질의 수.** 궤적 수로 역산할 수 없다 — 읽기도 서빙도 없는 검색은 궤적을 안
     * 남기는데([finalize] 첫 줄), 그게 정확히 "결과가 시원찮아 다시 치는" 경우의 첫
     * 시도다. 궤적만 세면 **재검색이 체계적으로 과소 계상된다.**
     *
     * 웹 검색 로그 연구는 질의 자체를 남겨 이 편향이 없다 — 세션의 약 40%가 질의 2개
     * 이상이라는 수치가 그렇게 나온다(Chen et al., WWW '21).
     */
    private val queryCount = AtomicInteger()

    /** 시작된 세션 수. `queries / sessions` 가 세션당 질의 수다. */
    private val sessionCount = AtomicInteger()

    /**
     * 종류별 궤적 수. `LOCALIZATION 만 간선 생성` 이 계약인데, 마커가 넓고 8토큰 이하는
     * 전부 LOCALIZATION 이라 **게이트가 항등함수인지 아닌지를 알 방법이 없었다.**
     * UNKNOWN 비율이 그 답이다.
     *
     * `kind` 는 로그에 있어 재생되는 누적값이다 — `sinceStart` 에 넣지 않는다.
     */
    private val byKind = ConcurrentHashMap<QueryKind, AtomicInteger>()

    /**
     * 세션 맵이 가득 차 **관측을 버린** 횟수. 상한이 필요한 이유는 세션이 유휴 30분
     * 뒤에야 거둬지고 이 경로에 인증이 없어서다. 넘친 것이 조용하면 학습이 왜 안 되는지
     * 알 수 없다.
     */
    private val droppedSessions = AtomicInteger()

    /** 질의를 2회 이상 받은 세션 수. 비율이 곧 "재검색이 얼마나 흔한가"다. */
    private val multiQuerySessions = AtomicInteger()

    /** 이번 기동에서 실제로 기록된 스팬 수. `queries` 와 같은 축이라 빼도 말이 된다. */
    private val recordedSinceStart = AtomicInteger()

    /**
     * 힌트는 서빙했는데 **아무것도 안 읽고 끝난** 스팬.
     *
     * 클릭 없는 종료를 실패로 세면 안 된다는 것이 IR 의 **good abandonment**
     * (Li·Huffman·Tokuda SIGIR '09, Williams et al. WWW '16) — 결과 목록만 보고 답을
     * 얻었을 수 있다. 여기서는 에이전트가 제목만 보고 답한 경우다.
     *
     * **판정은 안 바꾼다** — 어느 쪽인지 구별할 신호가 없다. 세어서 `sessionFailureRate`
     * 분모에서 빼고 따로 보고한다.
     */
    private val abandonedCount = AtomicInteger()

    /**
     * `dest` 를 **모델이 말해준** 궤적 수. 나머지는 `reads.last()` 추정이다.
     *
     * **이 값이 진술 설계의 성립 조건이다** — 0 에 가까우면 모델이 `answer` 를 안
     * 부르는 것이고, 그러면 폴백만 돌아 아무것도 안 바뀐다. 스킬 지시가 먹히는지를
     * 이 비율로만 알 수 있어서 `/api/stats` 가 낸다.
     */
    private val declaredCount = AtomicInteger()

    /**
     * 진술된 궤적 중 **폴백이 같은 답을 골랐을** 수. 나머지는 폴백이 틀렸다는 뜻이다.
     *
     * **이것이 `dest = reads.last()` 추정의 오류율을 직접 잰다.** 모델이 답을 말한
     * 궤적에서는 정답(모델 기준)과 추정을 나란히 볼 수 있으므로, 추가 관측 없이
     * 공짜로 계산된다 — 진술이 오는 만큼 표본이 저절로 쌓인다.
     *
     * 손으로 잰 값은 n=3 이었다(읽기 2건 이상에서 3건 중 2건 어긋남,
     * `docs/declared-answer-design.md`). 그 수를 **데이터가 대신 내게** 하는 자리다.
     *
     * **읽기가 1건이면 자명하게 일치한다** — 그것까지 세면 비율이 낙관적으로 부풀므로
     * 2건 이상만 센다(`fallbackChecked`).
     */
    private val fallbackAgreed = AtomicInteger()

    /** 위 비교의 분모. 진술이 있고 **읽기가 2건 이상**인 궤적만 센다. */
    private val fallbackChecked = AtomicInteger()

    /**
     * 지금까지 본 **권한 범위**들(`AclRegistry.scopeOf`). 신원이 아니다.
     *
     * 1 이면 학습이 균질하다. 2 이상이면 권한 폭이 다른 관측이 한 포스팅에 섞이는 중이고,
     * 그때부터 `rank` 가중과 목적지 분포가 사람마다 다른 의미를 갖는다. 그 상태인지
     * 아는 것이 먼저라 세기만 한다.
     */
    private val scopesSeen = ConcurrentHashMap.newKeySet<String>()

    // ---------------------------------------------------------- 관측

    /**
     * `computeIfAbsent` 와 `synchronized` 사이에 `SessionSweeper` 가 세션을 거둘 수 있다.
     * **재봤더니 잡을 값어치가 없었다** — 락 안에서 "맵의 주인이 아직 나인가" 를
     * 재확인하는 형태와 대조: 2,000 라운드 × 5회, `idleMillis=0` 최악 조건에서
     * **재확인 있음 1967 · 없음 1977** 로 오히려 조금 나빴다. 손실의 주범이 다른 것이기
     * 때문이다 — 스위퍼가 `onQuery` 와 `onRead` **사이**에 끝내는 경우이고, 그때 빈 스팬을
     * 버리는 것은 정당한 동작이다. 되살리려면 `SessionRaceTest` 에서 먼저 잴 것.
     */
    fun onQuery(sessionId: String, query: String, keywords: List<String>, scope: String = "") {
        // 로그로 흘러가는 것에는 상한이 있어야 한다. **자르지 않고 버린다** — 자르면
        // 서로 다른 두 세션이 같은 키로 합쳐져 궤적이 섞인다.
        if (sessionId.length > MAX_SESSION_ID) {
            droppedSessions.incrementAndGet()
            return
        }
        // 맵이 가득 차면 **새 세션만** 거절한다. 이미 있는 세션은 계속 관측된다.
        val existing = sessions[sessionId]
        if (existing == null && sessions.size >= MAX_SESSIONS) {
            droppedSessions.incrementAndGet()
            return
        }
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
            // 항은 반대로 **자른다** — 버리면 그 질의를 통째로 못 배운다.
            s.spans.add(QuerySpan(normalize(keywords.take(MAX_KEYWORDS)), Gate.classify(query)))
            s.lastTouch = System.currentTimeMillis()
        }
    }

    /**
     * 이 질의에 서빙한 힌트를 기록한다. `onQuery` 직후에 부른다.
     *
     * 이걸 알아야 **틀린 힌트를 미스로 되돌릴 수 있다.** 없으면 미스가 나는 경로가
     * "읽고 나서 비슷한 말로 재검색" 하나뿐이라, 엉뚱한 힌트도 세션이 그냥 끝나면
     * 벌점이 없다 — `pWrong` 이 계속 0.0 이던 이유다.
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

    /**
     * 모델이 **답이라고 말한다.** 안 부르면 `reads.last()` 폴백이라 퇴행은 없다.
     *
     * **읽은 것 중에서만 받는다.** 안 읽은 페이지를 답이라고 하면 `reads` 와 `dest` 가
     * 어긋나 `passedOver = reads - {dest}` 가 무의미해진다(읽은 것 전부가 지나침이
     * 된다). 거부하면 그 궤적은 폴백으로 남으므로 손실이 없다.
     *
     * **마지막 진술이 이긴다** — 모델이 답을 고쳐 말할 수 있고, 그때 나중 것이 맞다.
     *
     * 받아들였는지를 반환한다 — 조용히 버려지는 경로가 셋(없는 세션·열린 스팬 없음·
     * 안 읽은 페이지)이라, 클라이언트가 그 사실을 알 방법이 이것뿐이다.
     */
    fun onAnswer(sessionId: String, pageId: String): Boolean {
        val s = sessions[sessionId] ?: return false
        synchronized(s) {
            val span = s.current ?: return false
            if (pageId !in span.reads) return false
            span.declaredDest = pageId
            s.lastTouch = System.currentTimeMillis()
            return true
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
        // **모델이 답을 말했으면 그것, 아니면 마지막 읽기로 추정한다.**
        //
        // 폴백이 계약이다 — 안 부르는 클라이언트(옛 프록시·로컬판)는 지금 동작
        // 그대로여야 한다. 그리고 **진술은 읽은 것 중에서만 받는다**(`onAnswer` 가
        // 검사한다) — 안 읽은 페이지를 답이라고 하면 `reads` 와 `dest` 가 어긋나
        // `passedOver` 계산이 무의미해진다.
        //
        // 추정이 틀리는 빈도는 `QuerySpan.declaredDest` 의 KDoc 에 실측이 있다.
        val declared = span.declaredDest?.takeIf { it.isNotEmpty() }
        val dest = declared ?: span.reads.lastOrNull().orEmpty()
        val t = Trajectory(
            ts = System.currentTimeMillis(),
            session = sessionId,
            keywords = span.keywords,
            kind = span.kind,
            reads = span.reads.toList(),
            dest = dest,
            success = success && span.reads.isNotEmpty(),
            served = span.served,
            rank = span.ranked.indexOf(dest),
            scope = scope,
            readTs = span.readTs.toList(),
            declared = declared != null,
        )
        sink.append(t)
        apply(t)
    }

    /** 재기동 시 로그 재생용. 간선은 궤적의 함수다. */
    fun replay(t: Trajectory) = apply(t)

    private fun apply(t: Trajectory) {
        trajCount.incrementAndGet()
        // **여기서 센다 — `finalize` 가 아니다.** 재생(`replay`)도 이 경로를 지나므로
        // 재기동 뒤에도 값이 복구된다. `finalize` 에서 세면 그때마다 0 부터 시작한다.
        if (t.declared) {
            declaredCount.incrementAndGet()
            // 진술이 있으면 폴백이 무엇을 골랐을지 알 수 있다 — 그 둘을 대조한다.
            // 읽기 1건은 두 값이 정의상 같으므로 분모에서 뺀다.
            if (t.reads.size >= 2) {
                fallbackChecked.incrementAndGet()
                if (t.reads.last() == t.dest) fallbackAgreed.incrementAndGet()
            }
        }
        byKind.computeIfAbsent(t.kind) { AtomicInteger() }.incrementAndGet()
        if (t.scope.isNotEmpty()) scopesSeen.add(t.scope)
        // 셋으로 나눈다 — 읽은 게 없는데 힌트만 서빙된 것은 **실패가 아니라 미판정**이다
        // (good abandonment). 실패로 세면 `sessionFailureRate` 가 부풀어 못 쓰게 된다.
        when {
            t.success -> trajHits.incrementAndGet()
            t.reads.isEmpty() && t.served.isNotEmpty() -> abandonedCount.incrementAndGet()
            else -> trajMisses.incrementAndGet()
        }
        // 서빙했는데 **끝내 안 읽힌** 힌트 = 틀린 힌트.
        //
        // **아래 `return` 보다 위여야 한다.** 힌트는 `Gate` 분류와 무관하게 서빙되므로
        // (`hints()` 는 항만 받는다) 거부도 종류를 안 가리고 세야 `pWrong` 이 "서빙한 것
        // 중 틀린 비율" 이 된다. `return` 뒤에 두면 UNKNOWN 이 통째로 빠지는데 마커에 안
        // 걸리는 자연어 질의가 전부 거기라, 하필 **과소 보고 방향**이다.
        val rejected = t.served.toSet() - t.reads.toSet()
        servedCount.addAndGet(t.served.size)
        rejectedCount.addAndGet(rejected.size)

        if (!t.kind.cacheable) return   // 경로 의존 질의는 간선을 만들지 않는다

        // 읽었지만 답이 아니었던 것들 — 열어보고 **지나친** 페이지다. 읽기 개수가 곧
        // 확신도가 된다(1건이면 미스 0, 6건이면 5).
        //
        // **`dest` 를 빼는 것이지 마지막을 빼는 것이 아니다.** 한때
        // `reads.dropLast(1)` 였는데, `dest = reads.last()` 추정 시절에는 두 집합이
        // 같아서 티가 안 났다. 진술(`onAnswer`)이 들어오면서 갈라진다 — 답을 말한
        // **뒤에** 확인용으로 연 문서가 `dropLast(1)` 에 잘려 벌점을 피했고, 하필
        // 진술 설계가 겨냥한 바로 그 문서다. `DeclaredDestTest` 가 잠근다.
        val passedOver = if (t.success) t.reads.toSet() - setOf(t.dest) else emptySet()

        val weight = hitWeight(t.rank)
        for (term in normalize(t.keywords)) {
            val byPage = postings.computeIfAbsent(term) { ConcurrentHashMap() }
            if (t.dest.isNotEmpty()) {
                val slot = byPage.computeIfAbsent(t.dest) { IntArray(2) }
                synchronized(slot) {
                    // **히트만 가중한다** — 의도한 비대칭이다. 깊은 순위로 배운 간선(×3)은
                    // 미스 3번이라야 상쇄된다(강한 증거에는 강한 반증이). 미스도 순위로
                    // 가중할지는 `pWrong` 을 보고 판단할 것.
                    if (t.success) slot[0] += weight else slot[1]++
                }
            }
            // **이미 배운 페이지만** 끌어내린다. 미스의 목적은 서빙될 것을 밀어내는
            // 것이라, 항목이 없는 페이지는 만들어봐야 스캔 비용만 는다 —
            // `computeIfAbsent` 로 하면 실측(100세션·5읽기) 항-페이지쌍이 200 → 1,200 으로
            // 6배가 되고 `hints()` 가 질의당 2.3ms 였다.
            for (pid in rejected + passedOver) {
                val slot = byPage[pid] ?: continue
                synchronized(slot) { slot[1]++ }
            }
        }
    }

    /**
     * 순위가 깊을수록 강한 증거로 센다 — 1위를 읽는 건 기본 행동이라 어휘 랭킹이 이미
     * 맞힌 것이고, 7위를 읽었다면 **어휘가 틀렸는데 그 페이지가 답**이라는 신호다.
     *
     * **경계값은 손으로 정했다** — 방향은 웹 검색 클릭 모델에서 확립됐지만 문턱 3·6 과
     * 3배 상한은 이 코퍼스에서 측정한 값이 아니다.
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
     * [visible]로 **자르기 전에** 거른다 — 호출부에서 `take` 뒤에 거르면 서빙 못 할
     * 후보가 [limit] 슬롯을 먹어, 서빙 가능한 것이 아래에 있어도 안 나온다
     * (`CLAUDE.md` 조용히 실패 8·22번). 술어를 밖에서 받으므로 이 패키지는 ACL 이
     * 무엇인지 몰라도 된다 — `learn/` 의 프레임워크 무의존 계약이 유지된다.
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
                // 락 안에서는 int 둘만 읽는다. 여기서 배열을 만들면 (항×페이지)마다 하나씩
                // 이라 3항 × 1만 후보면 질의당 3만 할당이다 — 개선됐을 때만 만든다.
                val h: Int
                val m: Int
                synchronized(hm) { h = hm[0]; m = hm[1] }
                cover[pid] = (cover[pid] ?: 0) + 1
                val cur = best[pid]
                if (cur == null || (h - m) > (cur[0] - cur[1])) best[pid] = intArrayOf(h, m)
            }
        }

        return best.mapNotNull { (pid, hm) ->
            if (!visible(pid)) return@mapNotNull null      // **take 보다 먼저** — 위 주석 참고
            val c = (cover[pid] ?: 0).toDouble() / terms.size
            val prior = priors[pid] ?: 0.3
            // **문턱 판정은 cdf 한 번.** 이 루프는 모든 검색이 지나는 핫패스인데 후보 수는
            // 포스팅이 쌓일수록 단조증가한다. `rel = ebLower * c >= serveThreshold` 이므로
            // 페이지별 문턱이 `serveThreshold / c` 다(c 가 작으면 1 을 넘어 자명 기각).
            if (!Reliability.meetsThreshold(hm[0], hm[1], prior, serveThreshold / c)) {
                return@mapNotNull null
            }
            // 통과한 소수에만 정확한 값을 구한다 — 이 값이 랭킹 키라 근사하면 안 된다.
            Hint(pid, hm[0], hm[1], Reliability.ebLower(hm[0], hm[1], prior) * c)
        }.sortedByDescending { it.reliability }.take(limit)
    }

    fun stats(): Map<String, Any?> {
        val h = trajHits.get(); val m = trajMisses.get(); val n = h + m
        return linkedMapOf(
            "terms" to postings.size,
            // UNKNOWN 이 아주 낮으면 게이트는 사실상 항등함수다.
            "byKind" to QueryKind.entries.associate { it.name to (byKind[it]?.get() ?: 0) },
            "termPagePairs" to postings.values.sumOf { it.size },
            "ambiguousTerms" to postings.values.count { it.size > 1 },
            // 궤적(세션) 단위 성공률. 사람이 원하는 것을 찾았는가.
            "hits" to h,
            "misses" to m,
            // 힌트 단위. **손익분기 공식의 p_wrong 은 이것이지 궤적 성공률이 아니다.**
            "served" to servedCount.get(),
            "rejected" to rejectedCount.get(),
            "pWrong" to servedCount.get().let { s ->
                if (s > 0) rejectedCount.get().toDouble() / s else null
            },
            // 읽은 것이 있는 세션 중 실패 비율. good abandonment 는 분모에서 뺀다.
            "sessionFailureRate" to if (n > 0) m.toDouble() / n else null,
            "abandonedWithHints" to abandonedCount.get(),
            // **진술 설계의 성립 조건.** 0 이면 모델이 `answer` 를 안 부르는 것이고
            // 폴백만 돌아 아무것도 안 바뀐다 — 그 사실이 여기서만 보인다.
            "declaredDest" to declaredCount.get(),
            // **추정의 오류율.** 진술이 있는 궤적에서 `reads.last()` 폴백이 같은 답을
            // 골랐는가. 읽기 2건 이상만 센다(1건은 자명하게 일치한다).
            // 낮으면 진술 없는 궤적의 `dest` 를 그만큼 못 믿는다는 뜻이다.
            "fallbackChecked" to fallbackChecked.get(),
            "fallbackAgreed" to fallbackAgreed.get(),
            "fallbackAgreeRate" to fallbackChecked.get().let {
                if (it > 0) fallbackAgreed.get().toDouble() / it else null
            },
            // 0 이 아니면 세션 상한이나 sessionId 길이 상한에 걸려 **관측을 버리는 중**이다.
            "droppedSessions" to droppedSessions.get(),
            "trajectories" to trajCount.get(),
            // 1 이면 학습이 균질하다. 2 이상이면 권한 폭이 다른 관측이 섞이는 중이다.
            "permissionScopes" to scopesSeen.size,
            // **이 아래는 이번 기동에서만 센 값이다** — 소득 없는 검색은 로그에 없어
            // 재생이 불가능하고, 그게 이 값들을 만든 이유다. 위(누적)와 섞어서 빼면
            // 재기동 직후 음수가 나온다.
            "sinceStart" to linkedMapOf<String, Any?>(
                "queries" to queryCount.get(),
                "sessions" to sessionCount.get(),
                "multiQuerySessions" to multiQuerySessions.get(),
                "queriesPerSession" to sessionCount.get().let { s ->
                    if (s > 0) queryCount.get().toDouble() / s else null
                },
                // 웹 검색은 약 40% 다(Chen et al., WWW '21) — 에이전트는 다르므로 참고점.
                "multiQueryRate" to sessionCount.get().let { s ->
                    if (s > 0) multiQuerySessions.get().toDouble() / s else null
                },
                // 아직 기록되지 않은 질의 수. 소득 없이 끝난 검색 **과** 아직 안 끝난
                // 세션의 진행 중 스팬이 섞여 있다 — `activeSessions` 가 0 일 때만
                // "빗나간 검색 수" 로 읽을 수 있다.
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
