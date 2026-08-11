package io.wikilens.service

import com.fasterxml.jackson.databind.ObjectMapper
import io.wikilens.SyntheticVault
import io.wikilens.vault.VaultLayout
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * rg 경로의 **예산과 완주 보고**.
 *
 * ### 왜 합성 볼트인가
 *
 * 예전에는 `~/.wikilens/vault`(Coway 위키 13,921건)로 재고, 없으면 **통째로 건너뛰었다.**
 * 즉 이 머신 밖에서는 아무것도 검증하지 않았고, 통과했을 때 그 값은 WikiLens 가 아니라
 * 그 위키에 대한 사실이었다. 지금은 [SyntheticVault] 로 만들어 어디서나 돈다.
 *
 * rg 가 없는 머신에서는 여전히 건너뛴다 — 그건 코퍼스가 아니라 **도구**의 부재라
 * 대신할 방법이 없다.
 *
 * ### 고친 결함 둘
 *
 *   - **예산이 읽는 동안 안 걸렸다.** rg 는 매치가 있을 때만 줄을 내므로 매치 없는
 *     스캔은 EOF 까지 블록되고 그동안 마감 검사가 한 번도 안 돈다. 이건 코퍼스 크기와
 *     무관한 **구조적** 결함이다 — 코퍼스는 증상을 크게 보이게 했을 뿐이다.
 *   - **완주한 스캔이 잘림으로 보고됐다.** 남은 예산으로 프로세스를 거두다 만료되면
 *     `truncated=true` 였다. 매치 0건이 확정인데 "잘렸다" 로 보였다.
 *
 * **회귀를 잡는 것은 첫 테스트뿐이다.** 나머지는 정상 경로를 잠근다.
 */
class RipgrepBudgetTest {

    private val engine = RipgrepEngine(ObjectMapper())

    /**
     * 문서 수는 "예산이 안 걸리면 눈에 띄게 오래 걸리는" 정도면 된다. 크게 잡을수록
     * 구별력은 오르지만 테스트가 느려진다 — 회귀 방어이지 벤치가 아니다.
     */
    private val docs = 3_000

    private class Corpus(val root: Path, val pages: List<PageRef>)

    private fun corpus(): Corpus? {
        if (!engine.isAvailable()) return null
        val root = SyntheticVault.create(Files.createTempDirectory("rg-budget"), docs)
        val pages = (0 until docs).map { PageRef((100_000 + it).toString(), "문서") }
        // 레이아웃이 갈리면 엔진이 파일을 못 찾는다 — 전제를 여기서 한 번 확인한다.
        assertTrue(Files.exists(root.resolve(VaultLayout.relPagePath(pages[0].id))))
        return Corpus(root, pages)
    }

    private fun query(c: Corpus, pattern: String, cap: Int, budgetNanos: Long) =
        GrepQuery(c.root, c.pages, pattern, regex = false, cap = cap, budgetNanos = budgetNanos)

    @Test
    fun `매치가 없어도 예산이 걸린다`() {
        val c = corpus() ?: return

        // 먼저 예산을 넉넉히 줘서 **완주에 얼마나 걸리는지** 잰다. 이 값이 기준선이라
        // 머신 속도에 안 매달린다 — 코퍼스 크기를 상수로 박던 것을 대신하는 부분이다.
        val t0 = System.nanoTime()
        engine.search(query(c, SyntheticVault.NO_MATCH, 200, 30_000_000_000L))
        val fullScanMs = (System.nanoTime() - t0) / 1_000_000

        val t1 = System.nanoTime()
        val out = engine.search(query(c, SyntheticVault.NO_MATCH, 200, 1_000_000L))  // 1ms
        val elapsedMs = (System.nanoTime() - t1) / 1_000_000

        assertTrue(out.truncated, "마감으로 끊었으면 잘렸다고 해야 한다")
        // 마감이 안 걸리면 완주 시간만큼 걸린다. 절대 ms 가 아니라 **완주 대비**로 본다.
        assertTrue(
            elapsedMs < fullScanMs || fullScanMs < 30,
            "예산 1ms 인데 ${elapsedMs}ms — 완주(${fullScanMs}ms)에 매달려 있다",
        )
    }

    @Test
    fun `완주하면 잘렸다고 하지 않고 전량을 훑었다고 보고한다`() {
        val c = corpus() ?: return
        val out = engine.search(query(c, SyntheticVault.NO_MATCH, 200, 30_000_000_000L))

        assertFalse(out.truncated, "끝까지 갔으면 잘린 것이 아니다")
        assertEquals(docs, out.scanned, "완주했으면 대상 전량이다")
        assertTrue(out.matches.isEmpty())
    }

    @Test
    fun `cap 이 차면 rg 를 기다리지 않고 바로 끝낸다`() {
        val c = corpus() ?: return
        val t0 = System.nanoTime()
        val out = engine.search(query(c, SyntheticVault.IN_EVERY_DOC, 5, 30_000_000_000L))
        val elapsedMs = (System.nanoTime() - t0) / 1_000_000

        assertTrue(out.truncated)
        assertEquals(5, out.matches.size)
        // 파이프를 닫으면 rg 가 EPIPE 로 죽는다. 거두는 여유(2초)를 다 쓰면 안 된다.
        assertTrue(elapsedMs < 1_000, "cap 이 찼는데 ${elapsedMs}ms 를 기다렸다")
    }
}
