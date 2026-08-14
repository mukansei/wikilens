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

    /**
     * **진술 뒤에 연 문서가 미스를 안 먹던 자리.**
     *
     * `passedOver` 가 `reads.dropLast(1) - {dest}` 였다. 추정 시절에는
     * `dest == reads.last()` 라 그 둘이 같은 집합이었는데, 진술이 들어오면서
     * 갈라졌다 — **답을 말한 뒤에 확인용으로 연 문서**가 `dropLast(1)` 에 잘려
     * 벌점을 피한다. 하필 진술 설계가 겨냥한 바로 그 문서다.
     */
    @Test
    fun `진술보다 나중에 읽은 것도 지나침으로 센다`() {
        // 문턱 0 — 여기서 보려는 것은 서빙 여부가 아니라 **미스가 걸렸나**다.
        val s = TrajectoryStore(sink = { }, serveThreshold = 0.0)
        // 미스는 **이미 배운 페이지**에만 걸리므로(`byPage[pid] ?: continue`) 먼저 배운다.
        s.onQuery("a", "질의", listOf("가"))
        s.onServed("a", hinted = emptyList(), ranked = listOf("111"))
        s.onRead("a", "111")
        s.onEnd("a")
        val before = s.hints(listOf("가")).single { it.pageId == "111" }

        // 3위가 답이었고, 1위는 그 뒤에 확인용으로 열었다.
        s.onQuery("b", "질의", listOf("가"))
        s.onServed("b", hinted = emptyList(), ranked = listOf("111", "222", "333"))
        s.onRead("b", "333")
        s.onRead("b", "111")
        s.onAnswer("b", "333")
        s.onEnd("b")
        val after = s.hints(listOf("가")).single { it.pageId == "111" }
        assertEquals(before.misses + 1, after.misses,
                     "진술 뒤에 연 111 이 지나침으로 안 걸렸다")
    }

    /**
     * **추정의 오류율을 데이터가 내게 한다.**
     *
     * 진술이 있는 궤적에서는 정답(모델 기준)과 폴백(`reads.last()`)을 나란히 볼 수
     * 있으므로, 추가 관측 없이 폴백이 얼마나 틀리는지 계산된다. 손으로 잰 n=3 을
     * 대체하는 자리다.
     *
     * **읽기 1건은 분모에서 뺀다** — 두 값이 정의상 같아서, 세면 비율이 낙관적으로
     * 부풀고 "폴백은 대체로 맞다" 는 틀린 결론이 나온다.
     */
    @Test
    fun `폴백이 진술과 갈리는 비율을 센다`() {
        val (s, _) = capture()

        // ① 읽기 1건 — 자명한 일치라 분모에 안 들어간다
        s.onQuery("a", "질의", listOf("가")); s.onRead("a", "1"); s.onAnswer("a", "1"); s.onEnd("a")
        // ② 읽기 2건 · 마지막이 답 — 폴백 일치
        s.onQuery("b", "질의", listOf("가")); s.onRead("b", "1"); s.onRead("b", "2")
        s.onAnswer("b", "2"); s.onEnd("b")
        // ③ 읽기 2건 · 앞엣것이 답 — 폴백 불일치
        s.onQuery("c", "질의", listOf("가")); s.onRead("c", "1"); s.onRead("c", "2")
        s.onAnswer("c", "1"); s.onEnd("c")
        // ④ 진술 없음 — 대조할 정답이 없어 분모 밖
        s.onQuery("d", "질의", listOf("가")); s.onRead("d", "1"); s.onRead("d", "2"); s.onEnd("d")

        val st = s.stats()
        assertEquals(3, st["declaredDest"], "진술은 셋")
        assertEquals(2, st["fallbackChecked"], "읽기 2건 이상인 진술만 분모")
        assertEquals(1, st["fallbackAgreed"])
        assertEquals(0.5, st["fallbackAgreeRate"])
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
