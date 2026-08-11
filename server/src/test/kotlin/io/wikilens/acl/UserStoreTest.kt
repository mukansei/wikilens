package io.wikilens.acl

import com.fasterxml.jackson.databind.ObjectMapper
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

/**
 * 사용자 등록이 재기동을 넘어야 한다.
 *
 * 예전에는 순수 힙이라 재기동마다 전원이 사라졌고, 그 상태가 "문서가 없다"와
 * 구별되지 않았다(`CLAUDE.md` 조용히 실패 10·12번).
 */
class UserStoreTest {
    private val mapper = ObjectMapper()

    @Test
    fun `등록이 재기동을 넘는다`(@TempDir tmp: Path) {
        val first = AclRegistry(store = UserStore(tmp, mapper))
        first.putPage("P1", listOf("team-a"))
        first.putUser("alice", listOf("team-a"))
        assertTrue(first.canSee("alice", "P1"))

        // 새 프로세스를 흉내낸다 — 힙은 비고 디스크만 남는다.
        val second = AclRegistry(store = UserStore(tmp, mapper))
        second.putPage("P1", listOf("team-a"))
        assertTrue(second.canSee("alice", "P1"), "재기동에서 등록이 사라졌다")
        assertEquals(1, second.userCount())
    }

    @Test
    fun `저장소가 없으면 예전처럼 메모리 전용이다`(@TempDir tmp: Path) {
        val a = AclRegistry()
        a.putUser("alice", listOf("team-a"))
        assertEquals(0, AclRegistry().userCount(), "저장소 없이 새는 상태가 생겼다")
    }

    /** 깨진 파일을 조용히 빈 맵으로 넘기면 이 파일이 막으려던 상태로 돌아간다. */
    @Test
    fun `깨진 파일에 죽지 않는다`(@TempDir tmp: Path) {
        Files.writeString(tmp.resolve("acl-users.json"), "{이건 JSON 이 아니다")
        assertEquals(0, AclRegistry(store = UserStore(tmp, mapper)).userCount())
    }

    /** 반쪽 파일이 남으면 다음 기동에 전원이 사라진다 — 원자 교체여야 한다. */
    @Test
    fun `저장은 원자적이다`(@TempDir tmp: Path) {
        val acl = AclRegistry(store = UserStore(tmp, mapper))
        repeat(50) { acl.putUser("u$it", listOf("team-$it")) }
        assertTrue(Files.list(tmp).use { s -> s.noneMatch { it.toString().endsWith(".tmp") } },
            "임시 파일이 남았다")
        assertEquals(50, AclRegistry(store = UserStore(tmp, mapper)).userCount())
    }
}
