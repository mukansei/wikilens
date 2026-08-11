package io.wikilens.learn

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.registerKotlinModule
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

/**
 * 궤적 로그는 **유일하게 복구 불가능한 자산**이다. 포스팅은 이 로그의 함수라
 * 언제든 재집계되지만, 로그 자체가 갈라지면 되돌릴 방법이 없다.
 */
class TrajectorySinkTest {

    private val mapper = ObjectMapper().registerKotlinModule()

    private fun traj(session: String) = Trajectory(
        ts = 1, session = session, keywords = listOf("점유인증"),
        kind = QueryKind.LOCALIZATION, reads = listOf("p1"), dest = "p1", success = true,
    )

    @Test
    fun `기록한 궤적이 재생으로 그대로 돌아온다`(@TempDir tmp: Path) {
        val state = tmp.resolve("state")
        FileTrajectorySink(state, mapper).append(traj("s1"))

        val store = TrajectoryStore(sink = {})
        assertEquals(1, FileTrajectorySink(state, mapper).replayInto(store))
        assertEquals(1, store.stats()["trajectories"], "포스팅이 로그에서 복구돼야 한다")
    }

    @Test
    fun `상태 디렉터리가 바뀌면 옛 궤적을 못 읽는다`(@TempDir tmp: Path) {
        // 작업 디렉터리가 달라졌을 때 실제로 벌어지는 일. 기본값이 상대경로라
        // IntelliJ 가 main 에서 바로 띄우면 저장소 루트를, gradle bootRun 은
        // server/ 를 쓴다 — 같은 서버인데 궤적이 두 갈래가 된다(실측).
        FileTrajectorySink(tmp.resolve("server/.wikilens/state"), mapper).append(traj("s1"))

        val store = TrajectoryStore(sink = {})
        val n = FileTrajectorySink(tmp.resolve(".wikilens/state"), mapper).replayInto(store)

        assertEquals(0, n, "다른 자리라 한 건도 못 읽는다 — 그래서 경고가 필요하다")
        assertTrue(
            Files.exists(tmp.resolve("server/.wikilens/state/trajectories.jsonl")),
            "옛 로그는 그대로 남아 있다 — 사라지는 게 아니라 **갈라지는** 것이 문제다",
        )
    }
}
