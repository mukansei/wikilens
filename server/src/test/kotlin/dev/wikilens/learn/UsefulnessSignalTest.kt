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
        assertEquals(1, s.stats()["misses"], "읽기가 없으면 세션도 실패다")
    }

    /**
     * 그 페이지가 힌트로 **서빙되는지**. 포스팅 내부를 들여다보는 대신 동작으로 본다 —
     * `hints()` 는 임계값 아래를 걸러내므로, 미스가 쌓인 페이지는 여기서 사라진다.
     */
    private fun served(s: TrajectoryStore, term: String, pid: String) =
        s.hints(listOf(term), priors = mapOf(pid to 1.0), limit = 50).find { it.pageId == pid }

    // ---------------------------------------------------------- 읽기 개수

    @Test
    fun `열어보고 지나친 페이지는 미스가 된다`() {
        // `dest = reads.last()` 라는 전제를 따르면 앞서 읽은 것들은 지나친 것이다.
        // 1건만 읽으면 확신, 여러 건을 헤매면 그만큼 약한 증거가 된다.
        val s = store()
        s.onQuery("s1", "점유인증 정책 어디", listOf("점유인증"))
        s.onRead("s1", "111"); s.onRead("s1", "222"); s.onRead("s1", "333")
        s.onEnd("s1")

        // 셋 다 포스팅에는 들어갔지만(지나친 것도 기록된다)
        assertEquals(3, s.stats()["termPagePairs"], "지나친 페이지가 기록되지 않았다")
        // 지나친 것은 벌점 때문에 힌트로 안 나가고, 마지막 읽기만 나간다
        assertNull(served(s, "점유인증", "111"), "지나친 페이지가 힌트로 서빙된다")
        assertNull(served(s, "점유인증", "222"))
        assertNotNull(served(s, "점유인증", "333"), "마지막 읽기가 답이다")
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
}
