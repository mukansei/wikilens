package dev.wikilens.service

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultReader
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

    private fun svc(vaultRoot: String, index: LuceneIndex, acl: AclRegistry) =
        IndexingService(
            WikiLensProperties(vaultRoot = vaultRoot),
            VaultReader(ObjectMapper()), index, acl,
        )

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
            val s = svc("./mirror-root", index, AclRegistry())
            assertTrue(s.vaultRoot.isAbsolute, "로그와 오류 메시지가 어디를 봤는지 말해야 한다")
        }
    }
}
