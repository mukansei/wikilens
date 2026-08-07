package dev.wikilens.learn

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/**
 * "유용했다" 판정 검증.
 *
 * 예전에는 미스가 나는 경로가 **하나뿐**이었다 — 무언가 읽고 나서 비슷한 말로 다시
 * 검색. 그래서 힌트가 아무리 엉뚱해도 세션이 그냥 끝나면 벌점이 없었고, 실측
 * `pWrong` 이 계속 0.0 이었다. 서빙한 힌트를 세션이 끝내 안 읽으면 그것을 미스로
 * 되돌린다.
 */
class UsefulnessSignalTest {

    private fun store() = TrajectoryStore(sink = object : TrajectorySink {
        override fun append(t: Trajectory) {}
    })

    @Test
    fun `거부된 힌트가 미스로 기록된다`() {
        val s = store()
        s.onQuery("s1", "점유인증 정책 어디", listOf("점유인증", "정책"))
        s.onServed("s1", listOf("999"))          // 학습이 999 를 권했는데
        s.onRead("s1", "111")                    // 사용자는 111 을 읽었다
        s.onEnd("s1")

        val hints = s.hints(listOf("점유인증", "정책"), limit = 10)
        val h999 = hints.find { it.pageId == "999" }
        assertTrue(h999 == null || h999.reliability < 0.5,
            "거부된 힌트가 벌점 없이 남아 있다: $h999")
        // 세션 자체는 성공이다(사용자가 111 을 찾았다). 틀린 것은 **힌트**이므로
        // 궤적 카운터가 아니라 힌트 지표에 잡혀야 한다.
        assertEquals(1, s.stats()["rejected"], "거부된 힌트가 안 잡혔다")
        assertEquals(1.0, s.stats()["pWrong"], "서빙 1건이 전부 거부됐으면 pWrong=1.0")
        assertEquals(1, s.stats()["hits"], "세션은 성공이다")
    }

    @Test
    fun `읽힌 힌트는 벌점을 받지 않는다`() {
        val s = store()
        s.onQuery("s1", "점유인증 정책 어디", listOf("점유인증", "정책"))
        s.onServed("s1", listOf("111"))
        s.onRead("s1", "111")
        s.onEnd("s1")
        assertEquals(0, s.stats()["rejected"])
        assertEquals(0.0, s.stats()["pWrong"], "읽힌 힌트는 벌점이 없다")
    }

    @Test
    fun `아무것도 안 읽고 끝나도 서빙된 힌트는 벌점을 받는다`() {
        // 가장 강한 실패 신호인데 예전엔 통째로 버려졌다 (reads 가 비면 finalize 가 즉시 반환).
        val s = store()
        s.onQuery("s1", "점유인증 정책 어디", listOf("점유인증", "정책"))
        s.onServed("s1", listOf("999"))
        s.onEnd("s1")
        assertEquals(1, s.stats()["rejected"], "포기(abandonment)가 기록되지 않았다")

        // **힌트 단위로는 벌점, 세션 단위로는 미판정.** 둘은 다른 질문이다 —
        // 그 힌트가 틀렸다는 것(`rejected`)은 안 읽혔다는 사실에서 바로 나오지만,
        // 세션이 실패했는지는 알 수 없다. 결과 제목만 보고 답했을 수도 있다
        // (IR 의 good abandonment). 예전엔 이것을 `misses` 로 셌다.
        assertEquals(0, s.stats()["misses"], "읽은 것이 없으니 실패라고 단정할 수 없다")
        assertEquals(1, s.stats()["abandonedWithHints"], "미판정으로 따로 센다")
    }

    /**
     * 그 페이지가 힌트로 **서빙되는지**. 포스팅 내부를 들여다보는 대신 동작으로 본다 —
     * `hints()` 는 임계값 아래를 걸러내므로, 미스가 쌓인 페이지는 여기서 사라진다.
     */
    private fun served(s: TrajectoryStore, term: String, pid: String) =
        s.hints(listOf(term), priors = mapOf(pid to 1.0), limit = 50).find { it.pageId == pid }

    // ---------------------------------------------------------- 읽기 개수

    @Test
    fun `이미 배운 페이지를 지나치면 끌어내린다`() {
        // `dest = reads.last()` 라는 전제를 따르면 앞서 읽은 것들은 지나친 것이다.
        // 1건만 읽으면 확신, 여러 건을 헤매면 그만큼 약한 증거가 된다.
        val s = store()
        // 먼저 111 을 답으로 배운다
        s.onQuery("s0", "점유인증 정책 어디", listOf("점유인증"))
        s.onRead("s0", "111"); s.onEnd("s0")
        assertNotNull(served(s, "점유인증", "111"), "선행 학습이 안 됐다")

        // 다음 세션은 111 을 열어보고 지나쳐 222 로 갔다
        s.onQuery("s1", "점유인증 정책 어디", listOf("점유인증"))
        s.onRead("s1", "111"); s.onRead("s1", "222")
        s.onEnd("s1")

        assertEquals(1, served(s, "점유인증", "111")!!.misses, "지나친 페이지가 벌점을 안 받았다")
        assertNotNull(served(s, "점유인증", "222"), "마지막 읽기가 답이다")
    }

    @Test
    fun `배운 적 없는 페이지는 미스만으로 항목을 만들지 않는다`() {
        // 미스의 목적은 **서빙될 것을 끌어내리는 것**이다. 항목이 없는 페이지는 애초에
        // 서빙되지 않으므로 만들어봐야 스캔 비용만 는다 — 실측으로 항-페이지쌍이
        // 6배(200→1,200), `hints()` 가 2.3ms/질의였다.
        val s = store()
        s.onQuery("s1", "점유인증 정책 어디", listOf("점유인증"))
        s.onRead("s1", "111"); s.onRead("s1", "222"); s.onRead("s1", "333")
        s.onEnd("s1")
        assertEquals(1, s.stats()["termPagePairs"], "지나친 페이지가 항목을 만들었다")
    }

    @Test
    fun `한 건만 읽으면 미스가 없다`() {
        val s = store()
        s.onQuery("s1", "점유인증 정책 어디", listOf("점유인증"))
        s.onRead("s1", "111")
        s.onEnd("s1")
        assertEquals(0, served(s, "점유인증", "111")!!.misses)
    }

    // ---------------------------------------------------------- 순위

    @Test
    fun `깊은 순위에서 건진 것이 더 강한 증거다`() {
        // 1위를 읽는 건 기본 행동이고, 7위를 읽으려면 앞의 여섯을 지나쳐야 한다.
        val top = store()
        top.onQuery("s1", "점유인증 정책 어디", listOf("점유인증"))
        top.onServed("s1", emptyList(), ranked = listOf("111", "222", "333", "444"))
        top.onRead("s1", "111")          // 1위
        top.onEnd("s1")

        val deep = store()
        deep.onQuery("s1", "점유인증 정책 어디", listOf("점유인증"))
        deep.onServed("s1", emptyList(), ranked = listOf("111", "222", "333", "444"))
        deep.onRead("s1", "444")         // 4위
        deep.onEnd("s1")

        val h1 = served(top, "점유인증", "111")!!.hits
        val h4 = served(deep, "점유인증", "444")!!.hits
        assertTrue(h4 > h1, "깊은 순위(가중치 $h4)가 1위(가중치 $h1)보다 커야 한다")
    }

    @Test
    fun `순위를 모르면 기본 가중치를 쓴다`() {
        val s = store()
        s.onQuery("s1", "점유인증 정책 어디", listOf("점유인증"))
        s.onRead("s1", "999")            // 검색 결과에 없던 페이지를 직접 read
        s.onEnd("s1")
        assertEquals(1, served(s, "점유인증", "999")!!.hits)
    }

    @Test
    fun `힌트도 읽기도 없으면 아무것도 기록하지 않는다`() {
        val s = store()
        s.onQuery("s1", "아무거나", listOf("아무거나"))
        s.onEnd("s1")
        assertEquals(0, s.stats()["trajectories"], "배울 게 없는데 궤적이 생겼다")
    }

    // ------------------------------------------------- 원시 계측 (2026-08-07)

    @Suppress("UNCHECKED_CAST")
    private fun since(store: TrajectoryStore) =
        store.stats()["sinceStart"] as Map<String, Any?>

    @Test
    fun `소득 없는 검색은 궤적에 안 남지만 질의 수에는 남는다`() {
        // `finalize` 가 읽기도 서빙도 없는 스팬을 버린다. 그런데 그게 정확히
        // "결과가 시원찮아 다시 치는" 경우의 첫 시도라, 궤적만 세면 재검색이
        // 체계적으로 과소 계상된다. 원시 카운터가 필요한 이유가 이것이다.
        val store = TrajectoryStore(sink = {})
        store.onQuery("s1", "점유인증 정책", listOf("점유인증", "정책"))
        store.onEnd("s1")

        assertEquals(0, store.stats()["trajectories"], "아무 소득이 없으면 궤적은 안 남는다")
        assertEquals(1, since(store)["queries"], "그래도 질의는 있었다")
        // 세션을 끝냈으므로 이 값은 온전히 "소득 없이 끝난 검색" 이다.
        assertEquals(0, store.stats()["activeSessions"], "선행 조건: 진행 중인 스팬이 없다")
        assertEquals(1, since(store)["unrecordedQueries"])
    }

    @Test
    fun `세션당 질의 수와 재검색 비율을 센다`() {
        val store = TrajectoryStore(sink = {})
        // 한 번만 검색한 세션
        store.onQuery("one", "점유인증", listOf("점유인증"))
        store.onRead("one", "p1")
        // 두 번 검색한 세션 — 겹치는 키워드라 재구성으로 잡힌다
        store.onQuery("two", "점유인증 정책", listOf("점유인증", "정책"))
        store.onRead("two", "p1")
        store.onQuery("two", "점유인증 방식", listOf("점유인증", "방식"))
        store.onRead("two", "p2")
        // 세 번째 질의를 받아도 multiQuerySessions 는 한 번만 는다
        store.onQuery("two", "본인확인", listOf("본인확인"))

        val s = since(store)
        assertEquals(2, s["sessions"])
        assertEquals(4, s["queries"])
        assertEquals(1, s["multiQuerySessions"], "세션 단위로 한 번만 센다")
        assertEquals(2.0, s["queriesPerSession"])
        assertEquals(0.5, s["multiQueryRate"])
    }

    @Test
    fun `힌트만 받고 안 읽은 세션은 실패가 아니라 미판정이다`() {
        // IR 에서 good abandonment 로 알려진 문제 — 클릭 없는 종료가 곧 불만족은
        // 아니다. 에이전트가 결과 제목만 보고 답한 경우가 그것이다.
        val store = TrajectoryStore(sink = {})
        store.onQuery("s1", "점유인증 정책", listOf("점유인증"))
        store.onServed("s1", hinted = listOf("p9"), ranked = listOf("p9"))
        store.onEnd("s1")   // 아무것도 안 읽었다

        assertEquals(1, store.stats()["trajectories"], "서빙한 힌트가 있으니 기록은 된다")
        assertEquals(1, store.stats()["abandonedWithHints"], "미판정으로 따로 센다")
        assertEquals(0, store.stats()["misses"], "실패로 세면 sessionFailureRate 가 부푼다")
        assertNull(store.stats()["sessionFailureRate"], "판정된 궤적이 없으므로 비율도 없다")
    }

    @Test
    fun `읽고 나서 재검색한 것은 여전히 실패로 센다`() {
        // 미판정과 구별돼야 한다 — 이쪽은 읽었는데 답이 아니었다는 실제 증거다.
        val store = TrajectoryStore(sink = {})
        store.onQuery("s1", "점유인증 정책", listOf("점유인증", "정책"))
        store.onRead("s1", "p1")
        store.onQuery("s1", "점유인증 방식", listOf("점유인증", "방식"))

        assertEquals(1, store.stats()["misses"], "재구성은 앞 시도가 실패였다는 신호다")
        assertEquals(0, store.stats()["abandonedWithHints"])
    }
}
