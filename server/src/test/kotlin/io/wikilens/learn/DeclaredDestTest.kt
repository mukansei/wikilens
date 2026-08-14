package io.wikilens.learn

import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * 모델이 답을 진술하면 `dest` 가 그것이 된다. **안 부르면 지금 동작 그대로다.**
 *
 * 추정(`reads.last()`)이 틀리는 빈도는 실측했다 — 읽기 2건 이상에서 3건 중 2건
 * (2026-08-14, `docs/declared-answer-design.md`). 한 번 틀리면 셋이 함께 틀린다:
 * 간선·미스(`passedOver`)·깊이 가중(`rank`).
 */
class DeclaredDestTest {

    private fun capture(): Pair<TrajectoryStore, MutableList<Trajectory>> {
        val out = mutableListOf<Trajectory>()
        return TrajectoryStore(sink = { out.add(it) }) to out
    }

    @Test
    fun `진술이 없으면 마지막 읽기가 답이다`() {
        val (s, out) = capture()
        s.onQuery("s", "질의", listOf("가"))
        s.onRead("s", "111")
        s.onRead("s", "222")
        s.onEnd("s")
        val t = out.single()
        assertEquals("222", t.dest, "폴백이 깨졌다 — 안 부르는 클라이언트가 퇴행한다")
        assertFalse(t.declared)
    }

    @Test
    fun `진술이 있으면 그것이 답이다`() {
        val (s, out) = capture()
        s.onQuery("s", "질의", listOf("가"))
        s.onRead("s", "111")
        s.onRead("s", "222")
        // 실측의 G09 q0 모양 — 답은 첫 문서인데 나중에 다른 것을 열었다
        assertTrue(s.onAnswer("s", "111"))
        s.onEnd("s")
        val t = out.single()
        assertEquals("111", t.dest)
        assertTrue(t.declared, "진술 여부가 기록돼야 옛 궤적과 구별된다")
    }

    @Test
    fun `안 읽은 페이지는 거부한다`() {
        val (s, out) = capture()
        s.onQuery("s", "질의", listOf("가"))
        s.onRead("s", "111")
        assertFalse(s.onAnswer("s", "999"), "읽지 않은 페이지를 받아들였다")
        s.onEnd("s")
        // 거부하면 폴백으로 남는다 — 손실이 없다
        assertEquals("111", out.single().dest)
    }

    @Test
    fun `없는 세션과 열린 스팬 없음은 조용히 버린다`() {
        val (s, _) = capture()
        assertFalse(s.onAnswer("없음", "111"))
        s.onQuery("s", "질의", listOf("가"))
        s.onEnd("s")                                  // 스팬을 닫는다
        assertFalse(s.onAnswer("s", "111"))
    }

    @Test
    fun `마지막 진술이 이긴다`() {
        val (s, out) = capture()
        s.onQuery("s", "질의", listOf("가"))
        s.onRead("s", "111")
        s.onRead("s", "222")
        s.onAnswer("s", "111")
        s.onAnswer("s", "222")                        // 답을 고쳐 말한다
        s.onEnd("s")
        assertEquals("222", out.single().dest)
    }

    @Test
    fun `진술이 passedOver 와 rank 도 함께 옮긴다`() {
        val (s, out) = capture()
        s.onQuery("s", "질의", listOf("가"))
        s.onServed("s", hinted = emptyList(), ranked = listOf("111", "222", "333"))
        s.onRead("s", "333")                          // 어휘 3위를 먼저 열고
        s.onRead("s", "111")                          // 1위를 나중에 확인
        s.onAnswer("s", "333")                        // 답은 3위였다
        s.onEnd("s")
        val t = out.single()
        assertEquals("333", t.dest)
        // rank 가 진술을 따라간다 — 깊이 가중이 여기 걸려 있다(1위 ×1 · 그 아래 ×3)
        assertEquals(2, t.rank, "rank 가 추정된 dest 를 보고 있다")
    }

    @Test
    fun `재생해도 진술 수가 복구된다`() {
        val (s, out) = capture()
        s.onQuery("s", "질의", listOf("가"))
        s.onRead("s", "111")
        s.onAnswer("s", "111")
        s.onEnd("s")
        assertEquals(1, s.stats()["declaredDest"])

        // **재기동 흉내** — 카운트를 `finalize` 에서 세면 여기서 0 이 된다.
        val (fresh, _) = capture()
        out.forEach { fresh.replay(it) }
        assertEquals(1, fresh.stats()["declaredDest"],
            "재생에서 복구되지 않는다 — apply 가 아니라 finalize 에서 세고 있다")
    }
}
