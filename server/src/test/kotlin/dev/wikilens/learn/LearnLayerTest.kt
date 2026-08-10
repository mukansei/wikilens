package dev.wikilens.learn

import kotlin.math.abs
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * 학습 레이어 테스트.
 *
 * 여기 있는 것 대부분은 Python 프로토타입 개발 중 실제로 겪은 버그다.
 * "동작한다"가 아니라 "이 방식으로 깨졌었다"를 잠근다.
 *
 * 기준값은 Python 구현(scipy와 1e-9 일치 검증)에서 자동 생성했다.
 * 손으로 적었다가 두 번 틀렸으므로 생성된 값만 쓴다.
 */
class LearnLayerTest {

    private fun store(threshold: Double = 0.45): Pair<TrajectoryStore, MutableList<Trajectory>> {
        val log = mutableListOf<Trajectory>()
        return TrajectoryStore({ log.add(it) }, serveThreshold = threshold) to log
    }

    private fun session(s: TrajectoryStore, sid: String, q: String,
                        kw: List<String>, reads: List<String>) {
        s.onQuery(sid, q, kw); reads.forEach { s.onRead(sid, it) }; s.onEnd(sid)
    }

    // ------------------------------------------------------ EB

    @Test
    fun `EB 가 Python 대조 구현과 일치한다`() {
        val cases = listOf(
            Triple(4, 3, 0.30) to 0.234549444,
            Triple(4, 3, 0.50) to 0.309662748,
            Triple(4, 3, 0.70) to 0.391761128,
            Triple(5, 0, 0.30) to 0.396043431,
            Triple(1, 0, 0.85) to 0.615658063,
            Triple(15, 0, 0.85) to 0.877932582,
        )
        for ((args, expected) in cases) {
            val (h, m, p) = args
            assertTrue(abs(Reliability.ebLower(h, m, p) - expected) < 1e-6,
                "EB($h,$m,$p)=${Reliability.ebLower(h, m, p)} expected=$expected")
        }
    }

    @Test
    fun `사전확률이 1 이어도 한 번 관측에 확신하지 않는다`() {
        // 겪은 버그: 사전확률 1.0 -> Beta 모수 0 -> 1관측에 신뢰도 1.0
        assertTrue(Reliability.ebLower(1, 0, 1.0) < 0.75)
        assertEquals(Reliability.ebLower(1, 0, Reliability.PRIOR_CEIL),
            Reliability.ebLower(1, 0, 1.0), 1e-12)
    }

    @Test
    fun `표본이 쌓이면 사전확률의 영향이 사라진다`() {
        val small = Reliability.ebLower(4, 3, 0.85) - Reliability.ebLower(4, 3, 0.05)
        val large = Reliability.ebLower(400, 300, 0.85) - Reliability.ebLower(400, 300, 0.05)
        assertTrue(large < small / 10, "small=$small large=$large")
    }

    // ------------------------------------------------------ 게이트

    @Test
    fun `게이트가 찾기 표현만 잡고 도메인 명사에는 안 걸린다`() {
        // 겪은 버그: '파이프라인'을 흐름 마커로 써서 조회 질의가 TRACING으로 오탐
        assertEquals(QueryKind.LOCALIZATION, Gate.classify("배포 파이프라인 문서 어디"))
        // 겪은 버그: 위치 명사만 있고 조회 동사가 없어 UNKNOWN으로 누락
        assertEquals(QueryKind.LOCALIZATION, Gate.classify("로그인 붙이는 법 알려줘"))
        assertEquals(QueryKind.TRACING, Gate.classify("토큰이 어떻게 흐르나"))
        assertEquals(QueryKind.RATIONALE, Gate.classify("왜 이 정책이지"))
    }

    @Test
    fun `마커 없는 자연어 질의를 8토큰까지 폴백이 받는다`() {
        // 실측 실패 사례: 마커 없는 7토큰 자연어 질의가 예전엔 UNKNOWN으로 빠져 학습 간선이 안 생겼다.
        assertEquals(QueryKind.LOCALIZATION, Gate.classify("컨텐츠 노출 권한 필터링에 대한 3가지 방법"))
        // 경계값 고정: 정확히 8토큰(마커 없음) -> LOCALIZATION, 9토큰(마커 없음) -> UNKNOWN
        assertEquals(QueryKind.LOCALIZATION, Gate.classify("컨텐츠 노출 권한 필터링에 대한 세가지 처리 절차"))
        assertEquals(QueryKind.UNKNOWN, Gate.classify("컨텐츠 노출 권한 필터링에 대한 세가지 처리 절차 정리"))
    }

    @Test
    fun `RATIONALE 마커가 긴 질의를 길이 폴백에서 지킨다`() {
        // 임계값을 8로 올려도, "배경" 마커가 길이와 무관하게 먼저 걸려야 한다 —
        // 안 그러면 경로의존 질의가 LOCALIZATION으로 잘못 캐싱된다.
        assertEquals(QueryKind.RATIONALE, Gate.classify("이 기능을 이렇게 구현한 배경이 궁금해"))
    }

    @Test
    fun `경로 의존 질의는 간선을 만들지 않는다`() {
        val (s, log) = store()
        repeat(8) { session(s, "t$it", "토큰이 어떻게 흐르나", listOf("토큰", "흐르"), listOf("A", "B")) }
        assertEquals(8, log.size, "궤적은 분석용으로 남아야 함")
        assertTrue(s.hints(listOf("토큰", "흐르")).isEmpty())
        assertEquals(0, s.stats()["terms"])
    }

    // ------------------------------------------------------ 포스팅

    @Test
    fun `표현이 달라도 같은 항으로 카운트가 모인다`() {
        // 겪은 버그: 키워드 집합 전체를 키로 써서 표현이 달라지면 카운트가 흩어짐
        val (s, _) = store()
        listOf(
            listOf("로그인", "붙이는", "문서", "어디"),
            listOf("로그인", "붙이는", "알려줘"),
            listOf("로그인", "붙이는"),
            listOf("로그인", "붙이는", "페이지"),
            listOf("로그인", "붙이는", "가이드"),
        ).forEachIndexed { i, kw -> session(s, "s$i", "로그인 붙이는 법 어디", kw, listOf("P1")) }

        val h = s.hints(listOf("로그인", "붙이는"), mapOf("P1" to 0.75))
        assertEquals(1, h.size)
        assertEquals(5, h[0].hits)
    }

    @Test
    fun `모호한 질의는 실패가 아니라 분포다`() {
        val (s, _) = store()
        repeat(6) { session(s, "a$it", "설정 문서 어디", listOf("설정", "문서"), listOf("P1")) }
        repeat(3) { session(s, "b$it", "설정 문서 어디", listOf("설정", "문서"), listOf("P2")) }
        assertEquals(0, s.stats()["misses"], "모호함을 실패로 기록하면 안 됨")
        assertTrue((s.stats()["ambiguousTerms"] as Int) > 0)
    }

    // ------------------------------------------------------ 세션 조립

    @Test
    fun `질의 재구성은 앞 시도를 정확히 한 번만 실패로 표시한다`() {
        // 겪은 버그: onQuery와 onEnd가 같은 스팬을 이중 확정
        val (s, log) = store()
        s.onQuery("r1", "배포 파이프라인 어디", listOf("배포", "파이프라인", "어디"))
        s.onRead("r1", "WRONG")
        s.onQuery("r1", "배포 파이프라인 문서", listOf("배포", "파이프라인", "문서"))
        s.onRead("r1", "RIGHT")
        s.onEnd("r1")

        assertEquals(2, log.size, "3건이면 이중 확정")
        assertTrue(!log[0].success && log[0].dest == "WRONG")
        assertTrue(log[1].success && log[1].dest == "RIGHT")
        assertEquals(1, s.stats()["misses"])
    }

    @Test
    fun `질의 없는 읽기는 궤적이 아니다`() {
        val (s, log) = store()
        s.onRead("z1", "X"); s.onEnd("z1")
        assertTrue(log.isEmpty(), "훅이 잡은 무관한 파일 읽기는 궤적이 아님")
    }

    @Test
    fun `재생이 포스팅을 그대로 복원한다`() {
        val (a, log) = store()
        repeat(6) { session(a, "p$it", "온보딩 문서 어디", listOf("온보딩", "문서"), listOf("ONB")) }
        val before = a.hints(listOf("온보딩", "문서"), mapOf("ONB" to 0.75))
        val (b, _) = store()
        log.forEach { b.replay(it) }
        assertEquals(before, b.hints(listOf("온보딩", "문서"), mapOf("ONB" to 0.75)))
    }

    /**
     * **`pWrong` 은 종류를 가리지 않는다.**
     *
     * 힌트는 `Gate` 분류와 무관하게 서빙된다 — `hints()` 는 항만 받는다. 그러니 거부된
     * 사실도 종류와 무관하게 세야 "서빙한 것 중 틀린 비율" 이 된다. 예전에는 이 집계가
     * `if (!kind.cacheable) return` **뒤에** 있어서 캐시 가능한 질의만 셌다.
     *
     * UNKNOWN 이 통째로 빠지는 게 문제였다 — 마커에 안 걸리는 자연어 질의가 전부
     * 거기로 떨어지고(조용히 실패 7번), 하필 LOCALIZATION 에서 배운 간선을 다른 종류의
     * 질의에 서빙하는 경우라 **가장 자주 틀릴 만한 쪽**이다. 즉 과소 보고 방향이었다.
     *
     * 같은 궤적을 종류만 바꿔 넣어 확인한다. 간선을 만드는지(`postings`)는 그대로
     * 종류를 따른다 — 그건 `Gate` 의 일이고 여기서 바꾸는 것이 아니다.
     */
    @Test
    fun `서빙 거부는 캐시 불가 질의에서도 세어진다`() {
        for (kind in QueryKind.entries) {
            val store = TrajectoryStore({ })
            store.replay(
                Trajectory(
                    ts = 1, session = "s", keywords = listOf("배포"), kind = kind,
                    reads = emptyList(), dest = "", success = false,
                    served = listOf("A", "B"),
                ),
            )
            val st = store.stats()
            assertEquals(2, st["served"], "$kind 에서 서빙이 안 세어졌다")
            assertEquals(2, st["rejected"], "$kind 에서 거부가 안 세어졌다")
            assertEquals(1.0, st["pWrong"], "$kind 의 pWrong")
        }
    }
}
