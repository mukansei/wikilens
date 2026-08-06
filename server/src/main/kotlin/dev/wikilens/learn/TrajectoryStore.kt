package dev.wikilens.learn

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * 궤적 저장소. Spring도 Lucene도 참조하지 않는다.
 *
 * 서버가 보관하는 것:
 *     term -> pageId -> (hits, misses)
 *     trajectory(session, keywords, reads, dest, success)
 *
 * 색인은 별도 레이어(Lucene)에 있고, 이 레이어는 **관측과 신뢰도만** 다룬다.
 */

data class Hint(
    val pageId: String,
    val hits: Int,
    val misses: Int,
    val reliability: Double,
)

data class Trajectory(
    val ts: Long,
    val session: String,
    val keywords: List<String>,
    val kind: QueryKind,
    val reads: List<String>,
    val dest: String,
    val success: Boolean,
    /**
     * 학습 레이어가 이 질의에 **서빙한** 힌트 페이지들. 그중 세션이 끝까지 읽지 않은
     * 것은 **틀린 힌트**이므로 미스로 charge 한다 — `pWrong` 이 원래 재려던 값이다.
     *
     * 옛 로그에는 이 필드가 없다. 기본값이 빈 목록이라 재생이 그대로 통과한다.
     */
    val served: List<String> = emptyList(),
)

/** 한 질의와 그 뒤에 이어진 읽기들. */
class QuerySpan(val keywords: List<String>, val kind: QueryKind) {
    val reads = ArrayList<String>()
    /** 이 질의에 학습 레이어가 서빙한 힌트 페이지들 (`onServed` 가 채운다). */
    var served: List<String> = emptyList()
    /** on_query와 on_end가 같은 스팬을 두 번 확정하는 것을 막는다. */
    var finalized = false

    fun addRead(pageId: String) {
        // 같은 페이지를 연속으로 읽으면 한 번으로 센다
        if (reads.isEmpty() || reads.last() != pageId) reads.add(pageId)
    }
}

class Session(val id: String) {
    val spans = ArrayList<QuerySpan>()
    @Volatile var lastTouch: Long = System.currentTimeMillis()
    val current: QuerySpan? get() = spans.lastOrNull()
}

/**
 * 궤적 로그를 받는 싱크. 파일이든 DB든 이 인터페이스만 만족하면 된다.
 * 순수 로직을 I/O에서 떼어내기 위한 경계.
 */
fun interface TrajectorySink {
    fun append(t: Trajectory)
}

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

    // ---------------------------------------------------------- 관측

    fun onQuery(sessionId: String, query: String, keywords: List<String>) {
        val s = sessions.computeIfAbsent(sessionId) { Session(it) }
        synchronized(s) {
            val prev = s.current
            if (prev != null) {
                val reformulated = overlap(prev.keywords, keywords) >= reformulationOverlap
                finalize(sessionId, prev, success = !reformulated)
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
    fun onServed(sessionId: String, pageIds: List<String>) {
        val s = sessions[sessionId] ?: return
        synchronized(s) { s.current?.served = pageIds.toList() }
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
                    finalize(sessionId, span, success = true)
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
    private fun finalize(sessionId: String, span: QuerySpan, success: Boolean) {
        // 읽기가 없어도 **서빙한 힌트가 있으면** 기록한다 — 그 힌트가 거부됐다는 뜻이라
        // 가장 강한 실패 신호다. 둘 다 없으면 배울 것이 없다.
        if ((span.reads.isEmpty() && span.served.isEmpty()) || span.finalized) return
        span.finalized = true
        val t = Trajectory(
            ts = System.currentTimeMillis(),
            session = sessionId,
            keywords = span.keywords,
            kind = span.kind,
            reads = span.reads.toList(),
            dest = span.reads.lastOrNull().orEmpty(),
            success = success && span.reads.isNotEmpty(),
            served = span.served,
        )
        sink.append(t)
        apply(t)
    }

    /** 재기동 시 로그 재생용. 간선은 궤적의 함수다. */
    fun replay(t: Trajectory) = apply(t)

    private fun apply(t: Trajectory) {
        trajCount.incrementAndGet()
        if (t.success) trajHits.incrementAndGet() else trajMisses.incrementAndGet()
        if (!t.kind.cacheable) return   // 경로 의존 질의는 간선을 만들지 않는다

        // 서빙했는데 **끝내 안 읽힌** 힌트 = 틀린 힌트. dest 와 겹치지 않게 뺀다.
        val rejected = t.served.toSet() - t.reads.toSet()
        servedCount.addAndGet(t.served.size)
        rejectedCount.addAndGet(rejected.size)

        for (term in normalize(t.keywords)) {
            val byPage = postings.computeIfAbsent(term) { ConcurrentHashMap() }
            if (t.dest.isNotEmpty()) {
                val slot = byPage.computeIfAbsent(t.dest) { IntArray(2) }
                synchronized(slot) { slot[if (t.success) 0 else 1]++ }
            }
            for (pid in rejected) {
                val slot = byPage.computeIfAbsent(pid) { IntArray(2) }
                synchronized(slot) { slot[1]++ }
            }
        }
    }

    // ---------------------------------------------------------- 조회

    /**
     * 항 단위 포스팅을 조회해 후보를 모으고 커버리지로 가중한다.
     *
     * 한 궤적이 자기 키워드 전부를 증가시키므로 항별 카운트를 더하면 중복 계산이 된다.
     * 대표값(순이득 최대)을 쓰고, 대신 몇 개 항이 이 페이지를 가리켰는지를 커버리지로 반영한다.
     *
     * [priors]는 Lucene 랭킹 점수를 정규화한 값이다. EB 사전분포로 쓴다.
     */
    fun hints(keywords: List<String>, priors: Map<String, Double> = emptyMap(),
              limit: Int = 5): List<Hint> {
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
            "sessionFailureRate" to if (n > 0) m.toDouble() / n else null,
            "trajectories" to trajCount.get(),
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
