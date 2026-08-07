package dev.wikilens.learn

import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

/**
 * 같은 상태 디렉터리에 두 프로세스가 붙으면 각자 다른 포스팅을 들고 같은 궤적 로그에
 * 쓰게 되어 **학습이 조용히 갈린다.** Lucene 의 `write.lock` 은 재색인 동안만 잡히므로
 * 그 밖의 시간에는 아무 방어가 없었다.
 */
class StateDirLockTest {

    @Test
    fun `첫 번째는 잡고 두 번째는 거부된다`(@TempDir tmp: Path) {
        val dir = tmp.resolve("state")
        StateDirLock(dir)
        val e = assertFailsWith<StateDirLock.AlreadyRunning> { StateDirLock(dir) }
        assertTrue("이미" in e.message!!, e.message!!)
        // 무엇을 해야 하는지까지 말해야 한다 — 기동을 막는 오류이므로.
        assertTrue("state-dir" in e.message!!, e.message!!)
    }

    @Test
    fun `다른 디렉터리는 서로 막지 않는다`(@TempDir tmp: Path) {
        StateDirLock(tmp.resolve("a"))
        StateDirLock(tmp.resolve("b"))   // 예외가 나면 실패
    }

    @Test
    fun `없는 디렉터리는 만들어서 잡는다`(@TempDir tmp: Path) {
        val dir = tmp.resolve("깊은/경로/state")
        StateDirLock(dir)
        assertTrue(Files.isRegularFile(dir.resolve(".lock")))
    }
}
