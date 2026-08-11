package io.wikilens.index

import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * **재색인 중에도 검색이 성공해야 한다.**
 *
 * `swap` 이 스냅샷을 원자 교체한 뒤 옛 것을 바로 닫으면, 그 스냅샷을 이미 잡고 검색
 * 중이던 요청이 닫힌 reader 를 만난다. `AlreadyClosedException` 이 나고 예외 핸들러가
 * 없으므로 그대로 **HTTP 500** 이다 — cron 이 `sync && reindex` 를 돌 때마다 창이 열린다.
 *
 * 실측(수정 전): 재색인 30회 × 검색 4스레드 → 성공 58,583 · **실패 110**.
 *
 * 되돌려 확인할 것: `LuceneIndex.withSnapshot` 의 `tryIncRef`/`decRef` 를 빼고
 * `snapshotRef.get()` 을 직접 쓰면 이 테스트가 빨개진다.
 */
class ReindexRaceTest {

    private fun pages(n: Int) = (1..n).map {
        IndexedPage(
            id = "$it", title = "제목 $it 로그인", space = "SP",
            path = "p/$it.md", body = "본문 로그인 인증 $it",
            anchors = listOf("별칭$it"), aclTokens = listOf("@public"), ancestors = emptyList(),
        )
    }

    @Test
    fun `재색인 중 검색이 예외를 내지 않는다`() {
        val dir = Files.createTempDirectory("reindex-race")
        LuceneIndex(dir).use { idx ->
            idx.rebuild(pages(300))

            val stop = AtomicBoolean(false)
            val ok = AtomicInteger()
            val failures = ConcurrentHashMap<String, AtomicInteger>()

            val readers = (1..4).map {
                Thread {
                    while (!stop.get()) {
                        try {
                            idx.analyzeAndSearch("로그인", listOf("@public"), 10)
                            ok.incrementAndGet()
                        } catch (e: Throwable) {
                            failures.computeIfAbsent(e::class.java.name) { AtomicInteger() }
                                .incrementAndGet()
                        }
                    }
                }.apply { isDaemon = true; start() }
            }

            repeat(30) { idx.rebuild(pages(300)) }
            stop.set(true)
            readers.forEach { it.join(10_000) }

            // 검색이 실제로 돌았는지부터 — 0 이면 이 테스트가 아무것도 안 지킨다.
            assertTrue(ok.get() > 1_000, "검색이 충분히 돌지 않았다: ${ok.get()}")
            assertEquals(
                emptyMap(), failures.mapValues { it.value.get() },
                "재색인 중 검색이 실패했다 (성공 ${ok.get()})",
            )
        }
    }
}
