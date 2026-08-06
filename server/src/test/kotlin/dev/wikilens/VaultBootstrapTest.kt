package dev.wikilens

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultReader
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

    private fun props(vaultRoot: String, indexDir: Path) =
        WikiLensProperties(vaultRoot = vaultRoot, indexDir = indexDir.toString())

    @Test
    fun `기동 적재가 색인과 ACL 을 함께 채운다`(@TempDir tmp: Path) {
        val acl = AclRegistry()
        val index = LuceneIndex(tmp.resolve("index"))

        // 적재 전에는 ACL 이 비어 있다 — 이 상태로 뜨면 read 가 전부 404 다.
        assertEquals(0, acl.pageCount())

        val got = WikiLensApplication().vaultBootstrap(
            props(fixtureRoot.toString(), tmp.resolve("index")), VaultReader(ObjectMapper()), index, acl)

        assertEquals(3, got.indexed, "픽스처 볼트는 3페이지다")
        assertEquals(got.indexed, got.aclPages, "색인 문서 수와 ACL 페이지 수가 어긋나면 read 가 깨진다")
        assertTrue(acl.pageCount() > 0, "ACL 이 실제로 채워져야 한다")
    }

    @Test
    fun `상대경로 볼트를 절대경로로 푼다`(@TempDir tmp: Path) {
        // 기본값이 `./mirror-root` 라 실제 위치가 **실행 디렉터리에 달려 있다.**
        // jar 로 다른 디렉터리에서 띄우면 빈 볼트를 보고 문서 0개로 정상 기동한다
        // (2026-08-06 실측). 절대경로로 풀어야 로그가 어디를 봤는지 말할 수 있다.
        val got = WikiLensApplication().vaultBootstrap(
            props("./없는볼트", tmp.resolve("index")),
            VaultReader(ObjectMapper()), LuceneIndex(tmp.resolve("index")), AclRegistry())
        assertEquals(0, got.indexed)
    }

    @Test
    fun `볼트를 못 읽어도 기동은 계속된다`(@TempDir tmp: Path) {
        // 설정이 틀렸다고 서버가 안 뜨면 --status 로 진단할 길까지 사라진다.
        val got = WikiLensApplication().vaultBootstrap(
            props(tmp.resolve("없는볼트").toString(), tmp.resolve("index")),
            VaultReader(ObjectMapper()), LuceneIndex(tmp.resolve("index")), AclRegistry())

        assertEquals(0, got.indexed, "빈 채로 시작하고 --status 가 INDEXED_DOCS=0 으로 잡는다")
        assertEquals(0, got.aclPages)
    }
}
