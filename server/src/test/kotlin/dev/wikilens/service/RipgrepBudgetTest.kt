package dev.wikilens.service

import com.fasterxml.jackson.databind.ObjectMapper
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * rg 경로의 **예산과 완주 보고**를 재는 장치. `SessionRaceTest` 와 같은 성격이다 —
 * 단정보다 측정이 목적이고, 되돌리면 여기서 먼저 빨개진다.
 *
 * **실제 코퍼스가 있어야 의미가 있어 없으면 건너뛴다.** 작은 픽스처에서는 rg 가
 * 어차피 몇 ms 에 끝나 예산이 걸리는지 아닌지를 구별하지 못한다 — 통과해도 아무것도
 * 증명하지 않는 테스트를 두는 것보다 건너뛰는 편이 정직하다.
 *
 * 고친 결함 둘(실측 2026-08-09, 13,921건):
 *
 *   - 예산이 **읽는 동안 안 걸렸다.** rg 는 매치가 있을 때만 줄을 내므로 매치 없는
 *     스캔은 EOF 까지 블록됐다 — 예산 1ms 에 실제 738ms(JVM 경로는 79ms 에 끊었다).
 *   - **완주한 스캔이 잘림으로 보고됐다.** 남은 예산으로 프로세스를 거두다 만료되면
 *     `truncated=true` 였다. 전량을 훑고 매치 0건이 확정인데 "잘렸다" 로 보였다.
 *     감시 스레드가 들어온 뒤로는 `truncated` 가 "우리가 정말 끊었다" 는 뜻이 됐다.
 *
 * **회귀를 잡는 것은 첫 테스트뿐이다.** 되돌려 확인했다 — 나머지 둘은 고치기 전 코드에서도
 * 통과한다(예산이 넉넉하면 옛 코드도 완주를 완주라고 했다). 그 둘은 회귀 감지가 아니라
 * 정상 경로를 잠그는 쪽이다: 완주를 잘림으로 바꾸거나 거두는 여유를 다 기다리게 되면 빨개진다.
 */
class RipgrepBudgetTest {

    private val vault = Path.of(System.getProperty("user.home"), ".wikilens", "vault")
    private val engine = RipgrepEngine(ObjectMapper())

    private fun pages(): List<PageRef>? {
        if (!engine.isAvailable() || !Files.isDirectory(vault.resolve("mirror/pages"))) return null
        val all = Files.walk(vault.resolve("mirror/pages")).use { s ->
            s.filter { it.toString().endsWith(".md") }
                .map { PageRef(it.fileName.toString().removeSuffix(".md"), "t",
                               vault.relativize(it).toString()) }
                .toList()
        }
        return all.takeIf { it.size >= 5_000 }      // 작으면 구별력이 없다
    }

    /** 없는 문자열이라 rg 가 줄을 하나도 안 낸다 — 예산을 거는 장치가 없으면 완주해버린다. */
    private val noMatch = "존재하지않는문자열zzqqxx9999"

    @Test
    fun `매치가 없어도 예산이 걸린다`() {
        val pages = pages() ?: return
        val q = GrepQuery(vault, pages, noMatch, regex = false, cap = 200,
                          budgetNanos = 1_000_000L)                       // 1ms
        val t0 = System.nanoTime()
        val out = engine.search(q)
        val elapsedMs = (System.nanoTime() - t0) / 1_000_000

        assertTrue(out.truncated, "마감으로 끊었으면 잘렸다고 해야 한다")
        // 고치기 전 실측이 527~738ms 였다. 프로세스 기동·종료가 있어 1ms 로는 못 끝내지만
        // **완주 시간에 매달리지 않는다**는 것이 확인 대상이다.
        assertTrue(elapsedMs < 300, "예산 1ms 인데 ${elapsedMs}ms 걸렸다 — 마감이 안 걸린다")
    }

    @Test
    fun `완주하면 잘렸다고 하지 않고 전량을 훑었다고 보고한다`() {
        val pages = pages() ?: return
        val q = GrepQuery(vault, pages, noMatch, regex = false, cap = 200,
                          budgetNanos = 30_000_000_000L)                  // 30초
        val out = engine.search(q)

        assertFalse(out.truncated, "끝까지 갔으면 잘린 것이 아니다")
        assertEquals(pages.size, out.scanned, "완주했으면 대상 전량이다")
        assertTrue(out.matches.isEmpty())
    }

    @Test
    fun `cap 이 차면 rg 를 기다리지 않고 바로 끝낸다`() {
        val pages = pages() ?: return
        val q = GrepQuery(vault, pages, "e", regex = false, cap = 5,
                          budgetNanos = 30_000_000_000L)
        val t0 = System.nanoTime()
        val out = engine.search(q)
        val elapsedMs = (System.nanoTime() - t0) / 1_000_000

        assertTrue(out.truncated)
        assertEquals(5, out.matches.size)
        // 파이프를 닫으면 rg 가 EPIPE 로 죽는다. 거두는 여유(2초)를 다 쓰면 안 된다.
        assertTrue(elapsedMs < 1_000, "cap 이 찼는데 ${elapsedMs}ms 를 기다렸다")
    }
}
