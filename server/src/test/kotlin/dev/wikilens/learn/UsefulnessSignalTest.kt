package dev.wikilens.learn

import org.junit.jupiter.api.Assertions.assertEquals
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

    @Test
    fun `힌트도 읽기도 없으면 아무것도 기록하지 않는다`() {
        val s = store()
        s.onQuery("s1", "아무거나", listOf("아무거나"))
        s.onEnd("s1")
        assertEquals(0, s.stats()["trajectories"], "배울 게 없는데 궤적이 생겼다")
    }
}
