package io.wikilens

import com.fasterxml.jackson.databind.ObjectMapper
import io.wikilens.acl.AclRegistry
import io.wikilens.config.WikiLensProperties
import io.wikilens.index.LuceneIndex
import io.wikilens.service.IndexingService
import io.wikilens.vault.VaultLocator
import io.wikilens.vault.VaultReader
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Path

/**
 * 기동 적재 검증.
 *
 * 없으면 서버가 **검색은 되고 읽기는 전부 404** 인 반쪽 상태로 뜬다. Lucene 색인은
 * 디스크에서 되살아나는데 ACL 페이지 맵은 순수 힙이라 비어 있고, `search` 와 `read` 가
 * 서로 다른 ACL 경로를 쓰기 때문이다(2026-08-06 실측).
 *
 * 스프링 컨텍스트를 띄우지 않는다 — 이 저장소의 Kotlin 테스트는 전부 순수 단위
 * 테스트이고, 검증하려는 것은 배선이 아니라 **빈 메서드가 실제로 적재하는가** 다.
 */
class VaultBootstrapTest {

    private val fixtureRoot: Path = Path.of("..", "contract", "shared-fixture")

    private fun indexing(vaultRoot: String, index: LuceneIndex, acl: AclRegistry) =
        WikiLensProperties(vaultRoot = vaultRoot).let {
            IndexingService(VaultReader(ObjectMapper()), index, acl, VaultLocator(it), it)
        }

    @Test
    fun `기동 적재가 색인과 ACL 을 함께 채운다`(@TempDir tmp: Path) {
        val acl = AclRegistry()
        val index = LuceneIndex(tmp.resolve("index"))

        // 적재 전에는 ACL 이 비어 있다 — 이 상태로 뜨면 read 가 전부 404 다.
        assertEquals(0, acl.pageCount())

        val got = WikiLensApplication().vaultBootstrap(
            WikiLensProperties(vaultRoot = fixtureRoot.toString()),
            indexing(fixtureRoot.toString(), index, acl),
        )

        assertEquals(3, got.indexed, "픽스처 볼트는 3페이지다")
        assertEquals(got.indexed, got.aclPages, "색인 문서 수와 ACL 페이지 수가 어긋나면 read 가 깨진다")
        assertTrue(acl.pageCount() > 0, "ACL 이 실제로 채워져야 한다")
    }

    @Test
    fun `볼트를 못 읽어도 기동은 계속된다`(@TempDir tmp: Path) {
        // 설정이 틀렸다고 서버가 안 뜨면 --status 로 진단할 길까지 사라진다.
        // (색인을 지우지 않는다는 것 자체는 `IndexingServiceTest` 가 잠근다.)
        val got = WikiLensApplication().vaultBootstrap(
            WikiLensProperties(vaultRoot = tmp.resolve("없는볼트").toString()),
            indexing(tmp.resolve("없는볼트").toString(), LuceneIndex(tmp.resolve("index")), AclRegistry()),
        )

        assertEquals(0, got.indexed, "빈 채로 시작하고 --status 가 INDEXED_DOCS=0 으로 잡는다")
        assertEquals(0, got.aclPages)
    }

    /**
     * `sweep` 을 실제로 부르는 코드가 존재하는지.
     *
     * 예전에는 `/api/admin/sweep` 하나뿐이었고 아무도 안 불렀다. 세션 종료는 MCP
     * 프록시의 `atexit` 에만 걸려 있어 프로세스가 SIGKILL 되면 `onEnd` 가 영영 안 오고,
     * 그 세션의 궤적이 **확정되지 않아 학습에 안 들어갔다**(맵 누수보다 이쪽이 더 나쁘다 —
     * 조용하다). 실측으로 확인했다: idle 2초·주기 3초로 띄우니 `activeSessions` 1→0,
     * `trajectories` 38→39.
     */
    @Test
    fun `버려진 세션을 스케줄러가 거둔다`() {
        val sunk = ArrayList<io.wikilens.learn.Trajectory>()
        val store = io.wikilens.learn.TrajectoryStore(sink = { sunk.add(it) })
        store.onQuery("버려진세션", "점유인증 어디", listOf("점유인증"))
        store.onRead("버려진세션", "p1")

        assertEquals(1, store.stats()["activeSessions"], "세션이 살아 있다")
        assertTrue(sunk.isEmpty(), "onEnd 를 안 불렀으므로 아직 확정 전이다")

        // 스케줄러가 부르는 것과 같은 호출. idle 을 음수로 주는 이유는 `sweep` 이
        // `now - lastTouch > idleMillis` 로 비교하기 때문이다 — 0 을 주면 같은 밀리초
        // 안에서 만든 세션이 안 걸려 테스트가 시계에 의존한다.
        SessionSweeper(store, WikiLensProperties(learn = io.wikilens.config.LearnProps(sessionIdleMillis = -1))).sweep()

        assertEquals(0, store.stats()["activeSessions"], "거둬가야 맵이 안 샌다")
        assertEquals(1, sunk.size, "궤적이 확정돼 학습에 들어가야 한다")
    }
}
