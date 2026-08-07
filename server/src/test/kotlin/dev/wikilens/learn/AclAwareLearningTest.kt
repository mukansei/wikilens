package dev.wikilens.learn

import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * ACL 이 들어왔을 때 학습 레이어가 어떻게 되는가.
 *
 * 지금은 전 페이지가 `@public` 이라 이 검사들이 전부 자명해 보이지만, **ACL 수집이
 * 들어오는 순간 갈리는 자리**들이다. 그때 발견하면 이미 학습이 오염된 뒤다.
 */
class AclAwareLearningTest {

    private fun store() = TrajectoryStore(sink = { }, serveThreshold = 0.0)

    private fun learn(s: TrajectoryStore, session: String, term: String, page: String,
                      scope: String = "") {
        s.onQuery(session, term, listOf(term), scope)
        s.onRead(session, page)
        s.onEnd(session)
    }

    /**
     * **자르기 전에 걸러야 한다.** 호출부에서 `take` 뒤에 거르면, 권한이 좁은 사용자는
     * 상위 limit 개가 전부 안 보이는 문서일 때 **힌트가 통째로 0** 이 된다 — 볼 수 있는
     * 후보가 더 아래에 있어도 슬롯을 이미 뺏겼기 때문이다.
     * `SearchService` 가 어휘 결과에서 이미 한 번 겪은 실패다(조용히 실패 8번).
     */
    @Test
    fun `안 보이는 후보가 limit 슬롯을 먹지 않는다`() {
        val s = store()
        // 같은 항으로 네 페이지를 배운다. HIDDEN* 이 관측이 많아 상위에 온다.
        repeat(4) { learn(s, "s-h1-$it", "배포", "HIDDEN1") }
        repeat(3) { learn(s, "s-h2-$it", "배포", "HIDDEN2") }
        repeat(2) { learn(s, "s-v1-$it", "배포", "VISIBLE1") }
        learn(s, "s-v2", "배포", "VISIBLE2")

        // 필터 없이 상위 2개를 받으면 둘 다 안 보이는 것이어야 한다 (선행 조건).
        val unfiltered = s.hints(listOf("배포"), limit = 2).map { it.pageId }
        assertEquals(listOf("HIDDEN1", "HIDDEN2"), unfiltered, "선행 조건이 깨졌다: $unfiltered")

        // 같은 limit 으로 권한 필터를 주면 **볼 수 있는 것으로 채워져야** 한다.
        val visible = s.hints(listOf("배포"), limit = 2) { it.startsWith("VISIBLE") }
        assertEquals(listOf("VISIBLE1", "VISIBLE2"), visible.map { it.pageId },
            "안 보이는 후보가 슬롯을 먹었다 — take 가 필터보다 앞이다")
    }

    /** 술어를 안 주면 예전 그대로여야 한다 — 기본 인자가 동작을 바꾸면 안 된다. */
    @Test
    fun `술어를 안 주면 전부 통과한다`() {
        val s = store()
        learn(s, "s1", "배포", "P1")
        assertEquals(1, s.hints(listOf("배포")).size)
    }

    /**
     * 권한 **범위**는 남기고 신원은 안 남긴다. 로그가 커지기 전에 자리를 잡아둬야
     * 나중에 학습 오염을 다룰 수 있다 — 나중에 넣으면 그전 궤적에는 영영 없다.
     */
    @Test
    fun `궤적에 권한 범위가 실리고 서로 다른 범위가 집계된다`() {
        val recorded = ArrayList<Trajectory>()
        val s = TrajectoryStore(sink = { recorded.add(it) }, serveThreshold = 0.0)

        learn(s, "wide", "배포", "P1", scope = "aaaaaaaaaaaa")
        learn(s, "narrow", "배포", "P2", scope = "bbbbbbbbbbbb")
        learn(s, "wide2", "배포", "P1", scope = "aaaaaaaaaaaa")

        assertEquals(listOf("aaaaaaaaaaaa", "bbbbbbbbbbbb", "aaaaaaaaaaaa"), recorded.map { it.scope })
        // 2 이면 권한 폭이 다른 관측이 한 포스팅에 섞이는 중이라는 신호다.
        assertEquals(2, s.stats()["permissionScopes"])
    }

    /** 범위를 안 주면 안 센다 — 지금(전부 @public)의 상태가 1 이 아니라 0 이어야 한다. */
    @Test
    fun `범위가 없으면 집계하지 않는다`() {
        val s = store()
        learn(s, "s1", "배포", "P1")
        assertEquals(0, s.stats()["permissionScopes"])
    }
}
