package dev.wikilens.learn

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.registerKotlinModule
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import org.junit.jupiter.api.io.TempDir

class ReplayCompatTest {
    private val mapper = ObjectMapper().registerKotlinModule()

    @Test
    fun `필드가 없는 옛 로그도 재생된다`(@TempDir tmp: Path) {
        val dir = tmp.resolve("state")
        Files.createDirectories(dir)
        Files.writeString(dir.resolve("trajectories.jsonl"), listOf(
            // 가장 오래된 형태 — served·rank·scope 가 전부 없다
            """{"ts":1,"session":"s1","keywords":["배포"],"kind":"LOCALIZATION","reads":["P1"],"dest":"P1","success":true}""",
            // served·rank 는 있고 scope 만 없는 형태 (이번 변경 직전)
            """{"ts":2,"session":"s2","keywords":["배포"],"kind":"LOCALIZATION","reads":["P2"],"dest":"P2","success":true,"served":[],"rank":0}""",
        ).joinToString("\n") + "\n")

        val sink = FileTrajectorySink(dir, mapper)
        val store = TrajectoryStore(sink = sink, serveThreshold = 0.0)
        assertEquals(2, sink.replayInto(store), "옛 로그가 조용히 버려졌다")
        assertEquals(2, store.stats()["trajectories"])
    }

    /**
     * 파싱 실패를 삼키면 **스키마를 바꾼 순간 옛 궤적이 통째로 사라지는데 로그는
     * `궤적 0건 재생` 이라고만 한다** — 첫 배포와 구별되지 않는다. 스키마는 실제로
     * 두 번 바뀌었다(`served`·`rank`, 그리고 `scope`).
     */
    @Test
    fun `읽지 못한 줄은 세고 성공한 줄만 반영한다`(@TempDir tmp: Path) {
        val dir = tmp.resolve("state")
        Files.createDirectories(dir)
        Files.writeString(dir.resolve("trajectories.jsonl"), listOf(
            """{"ts":1,"session":"s1","keywords":["배포"],"kind":"LOCALIZATION","reads":["P1"],"dest":"P1","success":true}""",
            "{이건 JSON 이 아니다",
            """{"ts":3,"kind":"LOCALIZATION"}""",              // 필수 필드가 없다
        ).joinToString("\n") + "\n")

        val sink = FileTrajectorySink(dir, mapper)
        val store = TrajectoryStore(sink = sink, serveThreshold = 0.0)
        assertEquals(1, sink.replayInto(store), "성공한 줄만 세야 한다")
        assertEquals(1, store.stats()["trajectories"], "실패한 줄이 반영되면 안 된다")
        // 재기동을 지켜보지 않은 운영자도 알아야 한다 — 로그만으로는 안 닿는다.
        assertEquals(2, sink.status()["replaySkipped"], "버려진 줄이 밖으로 안 나간다")
    }

    /**
     * 쓰기가 실패해도 메모리 학습은 계속된다(그 편이 낫다 — 로그가 막혔다고 검색까지
     * 나빠질 이유는 없다). 그래서 **갈라지고 있다는 사실 자체**를 세야 한다.
     * 예전에는 WARN 한 줄이 전부라 재기동 때까지 아무도 몰랐다.
     */
    @Test
    fun `쓰기가 실패하면 세고 학습은 계속한다`(@TempDir tmp: Path) {
        val dir = tmp.resolve("state")
        Files.createDirectories(dir)
        // 로그 파일 자리를 **디렉터리**로 막는다 — append 가 반드시 실패한다.
        Files.createDirectories(dir.resolve("trajectories.jsonl"))

        val sink = FileTrajectorySink(dir, mapper)
        val store = TrajectoryStore(sink = sink, serveThreshold = 0.0)
        store.onQuery("s1", "배포", listOf("배포"))
        store.onRead("s1", "P1")
        store.onEnd("s1")

        assertEquals(1, sink.failures, "실패를 세지 않았다")
        assertEquals(1, sink.status()["writeFailures"], "밖으로 나가지 않으면 아무도 모른다")
        assertEquals(1, store.stats()["trajectories"], "쓰기 실패로 학습까지 멈추면 안 된다")
    }
}
