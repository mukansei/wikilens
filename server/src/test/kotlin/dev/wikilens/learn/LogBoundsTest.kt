package dev.wikilens.learn

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * **궤적 로그로 흘러가는 것에는 상한이 있어야 한다.**
 *
 * `SearchService` 가 `limit` 을 죌 때 근거로 댄 것이 "서빙한 힌트는 `served` 로 로그에
 * 영구히 남는다 — append-only 이고 유일한 복구 불가 자산이다" 였다. 같은 논리가
 * `sessionId`(로그의 `session`)와 항 목록(`keywords`)에는 적용돼 있지 않았다.
 * `grep` 은 `MAX_PATTERN` 으로 정확히 이 형태를 막고 있어서 **한쪽에만 적용된
 * 비대칭**이었다.
 *
 * 세션 맵 자체도 같은 부류다 — 인증 없는 `POST /api/search` 가 30분 사는 객체를
 * 무제한 만들 수 있었다.
 */
class LogBoundsTest {

    private fun store(): Pair<TrajectoryStore, MutableList<Trajectory>> {
        val log = mutableListOf<Trajectory>()
        return TrajectoryStore({ log.add(it) }) to log
    }

    @Test
    fun `긴 sessionId 는 자르지 않고 버린다`() {
        val (s, log) = store()
        val huge = "x".repeat(TrajectoryStore.MAX_SESSION_ID + 1)
        s.onQuery(huge, "배포 가이드", listOf("배포"))
        s.onRead(huge, "P1")
        s.onEnd(huge)

        assertTrue(log.isEmpty(), "상한을 넘은 sessionId 가 로그에 들어갔다")
        assertEquals(1, s.stats()["droppedSessions"])
    }

    @Test
    fun `상한 안의 sessionId 는 그대로 관측된다`() {
        val (s, log) = store()
        s.onQuery("normal-session", "배포 가이드", listOf("배포"))
        s.onRead("normal-session", "P1")
        s.onEnd("normal-session")

        assertEquals(1, log.size)
        assertEquals(0, s.stats()["droppedSessions"])
    }

    @Test
    fun `항 목록은 상한까지만 로그에 들어간다`() {
        val (s, log) = store()
        val many = (1..TrajectoryStore.MAX_KEYWORDS * 3).map { "낱말$it" }
        s.onQuery("sess", "질의", many)
        s.onRead("sess", "P1")
        s.onEnd("sess")

        assertEquals(1, log.size)
        assertTrue(log[0].keywords.size <= TrajectoryStore.MAX_KEYWORDS,
                   "항 ${log[0].keywords.size}개가 로그에 들어갔다")
    }

    @Test
    fun `세션 맵이 차면 새 세션만 거절하고 기존 세션은 계속 관측한다`() {
        val (s, log) = store()
        repeat(TrajectoryStore.MAX_SESSIONS) { s.onQuery("s$it", "배포", listOf("배포")) }

        s.onQuery("새로운세션", "배포", listOf("배포"))
        assertEquals(1, s.stats()["droppedSessions"], "가득 찬 뒤 새 세션이 만들어졌다")

        // 이미 있는 세션은 영향받지 않는다 — 상한이 진행 중인 학습을 끊으면 안 된다.
        s.onRead("s0", "P1")
        s.onEnd("s0")
        assertEquals(1, log.size)
    }

    /**
     * 게이트가 실제로 무엇을 거르는지 밖에서 볼 수 있어야 한다. UNKNOWN 이 아주 낮으면
     * `LOCALIZATION 만 간선 생성` 은 사실상 항등함수이고, 그건 지금 아무도 모른다.
     */
    @Test
    fun `종류 분포가 stats 에 나온다`() {
        val (s, _) = store()
        for ((kind, q) in listOf(
            QueryKind.LOCALIZATION to "배포 문서 어디",
            QueryKind.RATIONALE to "왜 이렇게 했는지 배경",
            QueryKind.TRACING to "배포 흐름 단계별로",
        )) {
            assertEquals(kind, Gate.classify(q), "픽스처 전제가 깨졌다: $q")
            s.onQuery("s-$kind", q, listOf("항$kind"))
            s.onRead("s-$kind", "P")
            s.onEnd("s-$kind")
        }

        @Suppress("UNCHECKED_CAST")
        val dist = s.stats()["byKind"] as Map<String, Int>
        assertEquals(1, dist["LOCALIZATION"])
        assertEquals(1, dist["RATIONALE"])
        assertEquals(1, dist["TRACING"])
        assertEquals(0, dist["UNKNOWN"], "관측 안 된 종류도 0 으로 나와야 한다")
    }
}
