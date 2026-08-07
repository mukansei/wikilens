package dev.wikilens.service

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.UserConfig
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultLocator
import dev.wikilens.vault.VaultReader
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

/**
 * 적재는 두 곳에서 불린다 — 기동(`vaultBootstrap`)과 `/admin/reindex`.
 *
 * 예전엔 각자 `vault.read` + `index.rebuild` 를 직접 불렀고, 그래서 기동 쪽에만
 * "빈 볼트면 건너뛴다" 방어가 들어갔다. 엔드포인트는 그대로 남아서 **볼트 경로가
 * 틀린 상태로 `/admin/reindex` 를 부르면 살아 있던 색인이 지워졌다**(실측 2,383 → 0).
 */
class IndexingServiceTest {

    private val fixtureRoot: Path = Path.of("..", "contract", "shared-fixture")

    private fun svc(vaultRoot: String, index: LuceneIndex, acl: AclRegistry): IndexingService {
        val props = WikiLensProperties(vaultRoot = vaultRoot)
        return IndexingService(VaultReader(ObjectMapper()), index, acl, VaultLocator(props))
    }

    @Test
    fun `정상 볼트는 색인을 채운다`(@TempDir tmp: Path) {
        LuceneIndex(tmp.resolve("index")).use { index ->
            val r = svc(fixtureRoot.toString(), index, AclRegistry()).reload()
            assertEquals(3, r.indexed, "픽스처 볼트는 3페이지다")
            assertEquals(r.indexed, r.aclPages, "색인과 ACL 이 어긋나면 read 가 깨진다")
            assertFalse(r.skipped)
        }
    }

    @Test
    fun `빈 볼트는 멀쩡한 색인을 지우지 않는다`(@TempDir tmp: Path) {
        val acl = AclRegistry()
        LuceneIndex(tmp.resolve("index")).use { index ->
            svc(fixtureRoot.toString(), index, acl).reload()
            val before = index.docCount
            assertTrue(before > 0, "선행 조건: 색인이 있어야 한다")

            val r = svc(tmp.resolve("없는볼트").toString(), index, acl).reload()

            assertTrue(r.skipped, "건너뛰었음을 호출부에 알려야 한다")
            assertEquals(before, index.docCount, "색인이 지워졌다 — 재싱크해야 복구된다")
            assertEquals(before, r.indexed, "보고도 남아 있는 색인 기준이어야 한다")
        }
    }

    @Test
    fun `상대경로 볼트를 절대경로로 푼다`(@TempDir tmp: Path) {
        LuceneIndex(tmp.resolve("index")).use { index ->
            val s = svc(tmp.resolve("어딘가").toString(), index, AclRegistry())
            assertTrue(s.vaultRoot.isAbsolute, "로그와 오류 메시지가 어디를 봤는지 말해야 한다")
        }
    }

    /**
     * 기본 자리가 비면 `~/.wikilens/config.json` 으로 폴백한다 — 심링크를 손으로
     * 만드는 단계가 이것 때문에 없어졌다.
     */
    @Test
    fun `기본 경로가 없으면 사용자 설정의 볼트를 쓴다`(@TempDir tmp: Path) {
        val vault = Files.createDirectories(tmp.resolve("wiki"))
        Files.createDirectories(tmp.resolve(".wikilens"))
        Files.writeString(tmp.resolve(".wikilens/config.json"), """{"vault": "$vault"}""")

        UserConfig.homeOverride = tmp
        try {
            LuceneIndex(tmp.resolve("index")).use { index ->
                val fallback = svc(WikiLensProperties.DEFAULT_VAULT_ROOT, index, AclRegistry())
                assertEquals(vault.toAbsolutePath().normalize(), fallback.vaultRoot)

                // 명시로 준 경로는 폴백하지 않는다 — 오타를 조용히 덮으면 안 된다.
                val explicit = svc(tmp.resolve("내가준경로").toString(), index, AclRegistry())
                assertEquals(tmp.resolve("내가준경로").normalize(), explicit.vaultRoot)
            }
        } finally {
            UserConfig.homeOverride = null
        }
    }

    /**
     * **색인이 읽은 볼트와 read·grep 이 보는 볼트가 같아야 한다.**
     *
     * 예전에는 둘이 각자 `props.vaultRoot` 를 풀었고 `ContentService` 는 절대화조차
     * 안 했다. 폴백이 들어오자 갈림이 결정적이 됐다 — 실측: 폴백으로 기동한 서버가
     * 문서 3건을 색인하고 **검색은 되는데 read 는 전부 404** 였다. 이 저장소가 이미
     * 한 번 겪은 실패 모양이다(`CLAUDE.md` 조용히 실패 12번).
     */
    @Test
    fun `색인과 읽기가 같은 볼트를 본다`(@TempDir tmp: Path) {
        val vault = Files.createDirectories(tmp.resolve("wiki"))
        Files.createDirectories(tmp.resolve(".wikilens"))
        Files.writeString(tmp.resolve(".wikilens/config.json"), """{"vault": "$vault"}""")

        UserConfig.homeOverride = tmp
        try {
            val props = WikiLensProperties(vaultRoot = WikiLensProperties.DEFAULT_VAULT_ROOT)
            val locator = VaultLocator(props)
            LuceneIndex(tmp.resolve("index")).use { index ->
                val indexing = IndexingService(VaultReader(ObjectMapper()), index, AclRegistry(), locator)
                assertEquals(indexing.vaultRoot, locator.root, "해석처가 둘이면 여기서 갈린다")
                assertEquals(vault.toAbsolutePath().normalize(), locator.root)
            }
        } finally {
            UserConfig.homeOverride = null
        }
    }
}
