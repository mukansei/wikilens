package dev.wikilens.service

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.SyntheticVault
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * grep 예산의 한계를 **코퍼스 없이 유도한다.**
 *
 * ### 무엇을 대신하나
 *
 * 저장소는 오래 `JVM 스캔은 13,921건에 2.44초 → 약 17,000건에서 상시 잘림` 을 용량
 * 상수처럼 적어왔다. 그건 **Acme 위키에 대한 사실**이다 — 문서 크기 분포·언어·디스크가
 * 다르면 전부 달라지고, 그 코퍼스가 없는 사람은 검증도 못 한다.
 *
 * 코퍼스와 무관한 형태는 이것이다:
 *
 *     한계 문서 수 = 예산 / 문서당 스캔 비용
 *
 * 오른쪽 둘은 각자의 배포에서 잴 수 있다. 이 테스트가 **문서당 비용을 재고 한계를 찍는다.**
 * 절대 시간은 머신마다 다르므로 단정하지 않는다.
 *
 * ### 단정하는 것은 선형성이다
 *
 * 위 나눗셈은 스캔 비용이 문서 수에 **선형**일 때만 성립한다. 규모를 3배로 올렸는데
 * 문서당 비용이 크게 달라지면 그 모델 자체가 틀린 것이고, 그때는 예산을 환산하는 방식을
 * 바꿔야 한다. 그것이 이 테스트가 실제로 잡는 회귀다 — 시간 단정이 아니라 **모델 단정**이라
 * 머신 속도에 안 매달린다.
 *
 * (증분 색인을 안 하는 근거도 같은 성질에 기대고 있다 — `DECISIONS.md` D12 의
 * "문서당 비용이 규모에 따라 줄어든다" 는 관찰이 여기서 반대 방향으로 확인된다.)
 */
class GrepScaleTest {

    private val jvm = JvmGrepEngine()

    private fun vault(dir: Path, docs: Int): Pair<Path, List<PageRef>> {
        val root = SyntheticVault.create(dir, docs)
        return root to (0 until docs).map { PageRef((100_000 + it).toString(), "문서") }
    }

    /** 문서당 마이크로초. 매치가 없어야 전량을 훑는다. */
    private fun perDocMicros(engine: GrepEngine, root: Path, pages: List<PageRef>): Double {
        val q = GrepQuery(root, pages, SyntheticVault.NO_MATCH, regex = false,
                          cap = 200, budgetNanos = 60_000_000_000L)
        engine.search(q)                                   // 워밍 (JIT · 페이지 캐시)
        val t0 = System.nanoTime()
        val out = engine.search(q)
        val nanos = System.nanoTime() - t0
        assertTrue(!out.truncated, "예산 안에 완주하지 못했다 — 측정이 무의미하다")
        return nanos / 1000.0 / pages.size
    }

    @Test
    fun `문서당 스캔 비용이 규모에 매달리지 않는다`() {
        val small = vault(Files.createTempDirectory("scale-s"), 1_000)
        val large = vault(Files.createTempDirectory("scale-l"), 3_000)

        val cSmall = perDocMicros(jvm, small.first, small.second)
        val cLarge = perDocMicros(jvm, large.first, large.second)
        val ratio = cLarge / cSmall

        val budgetMicros = ContentService.GREP_BUDGET_NANOS / 1000.0
        println("  JVM 문서당 %.1f us (1k) · %.1f us (3k) · 비 %.2f".format(cSmall, cLarge, ratio))
        println("  → 예산 %.0fs 에서 한계 약 %,.0f 건 (이 머신·이 문서 크기 기준)"
            .format(ContentService.GREP_BUDGET_NANOS / 1e9, budgetMicros / cLarge))

        val rg = RipgrepEngine(ObjectMapper())
        if (rg.isAvailable()) {
            val r = perDocMicros(rg, large.first, large.second)
            println("  rg 문서당 %.1f us → JVM 대비 %.1f배 · 한계 약 %,.0f 건"
                .format(r, cLarge / r, budgetMicros / r))
        }

        // **모델 단정.** 시간이 아니라 "문서당 비용이 규모에 안 매달린다" 를 본다.
        // 선형이 깨지면 `예산 / 문서당 비용` 이라는 환산 자체가 틀린 것이 된다.
        assertTrue(ratio in 0.4..2.5, "문서당 비용이 규모에 매달린다 (비 %.2f)".format(ratio))
    }

    /**
     * **한계는 문서 *수* 가 아니라 바이트에 붙는다.**
     *
     * 이것이 "약 17,000건" 같은 상수가 왜 무의미한지의 핵심이다. 같은 코드로 이 합성
     * 코퍼스(작은 문서)를 재면 한계가 5만 건대로 나온다 — 소프트웨어가 달라진 게 아니라
     * **문서가 작을 뿐**이다. 문서 수로 적힌 한계는 그 코퍼스의 평균 문서 크기를
     * 숨긴 채로 옮겨진다.
     */
    @Test
    fun `문서당 비용은 문서 크기를 따라간다`() {
        val thin = vault(Files.createTempDirectory("scale-thin"), 1_000)      // 기본 40줄
        val fatDir = Files.createTempDirectory("scale-fat")
        SyntheticVault.create(fatDir, 1_000, lines = 160)
        val fat = fatDir to (0 until 1_000).map { PageRef((100_000 + it).toString(), "문서") }

        val cThin = perDocMicros(jvm, thin.first, thin.second)
        val cFat = perDocMicros(jvm, fat.first, fat.second)
        val bytesThin = bytesOf(thin.first)
        val bytesFat = bytesOf(fat.first)

        println("  40줄  문서당 %.1f us · %,d B → %.2f us/KB".format(
            cThin, bytesThin / 1_000, cThin / (bytesThin / 1_000.0 / 1_000)))
        println("  160줄 문서당 %.1f us · %,d B → %.2f us/KB".format(
            cFat, bytesFat / 1_000, cFat / (bytesFat / 1_000.0 / 1_000)))

        // 문서를 4배로 키웠으면 문서당 비용도 커져야 한다. 안 커지면 비용이 파일 개수에만
        // 붙는다는 뜻이고, 그러면 "문서 수 한계" 가 오히려 맞는 모델이 된다.
        assertTrue(cFat > cThin, "문서를 4배로 키웠는데 문서당 비용이 안 늘었다")
    }

    private fun bytesOf(root: Path): Long =
        Files.walk(root.resolve("mirror").resolve("pages")).use { s ->
            s.filter { Files.isRegularFile(it) }.mapToLong { Files.size(it) }.sum()
        }
}
