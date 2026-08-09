package dev.wikilens.vault

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.acl.AclRegistry
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.copyToRecursively
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

/**
 * `mirror/acl/acl.json` 은 CLI 의 `wikilens acl` 이 쓰고 서버가 읽는다 —
 * **파일로만 이어진 계약**이라 형식이 갈리면 조용히 전 페이지가 `@public` 이 된다.
 *
 * 골든 픽스처를 **복사해서** 쓴다. 원본에 acl 을 넣으면 "acl 이 없으면 공개 기본값"
 * 을 잠그는 `VaultReaderTest` 가 깨진다 — 픽스처는 계약 자산이라 건드리지 않는다.
 */
@OptIn(kotlin.io.path.ExperimentalPathApi::class)
class AclFileTest {

    private fun vaultWithAcl(tmp: Path, json: String): Path {
        val root = tmp.resolve("vault")
        Path.of("..", "contract", "shared-fixture").copyToRecursively(root, followLinks = false)
        val dir = Files.createDirectories(root.resolve("mirror").resolve("acl"))
        Files.writeString(dir.resolve("acl.json"), json)
        return root
    }

    @Test
    fun `볼트의 acl 파일이 페이지 토큰이 된다`(@TempDir tmp: Path) {
        val root = vaultWithAcl(tmp, """{"100":["@space:DOCS"],"200":["group:secret"]}""")
        val acl = AclRegistry()
        VaultReader(ObjectMapper()).read(root, acl)

        assertEquals(setOf("@space:DOCS"), acl.tokensOf("100"))
        assertEquals(setOf("group:secret"), acl.tokensOf("200"))
        // 파일에 **없는** 페이지는 공개가 아니다. Python `collect` 은 권한을 확정하지
        // 못한 페이지를 일부러 생략하므로, 여기서 공개로 채우면 그쪽 fail-closed 가
        // 뒤집힌다. "파일이 아예 없을 때"(= 수집 전 볼트)와는 다른 상황이다.
        assertEquals(emptySet(), acl.tokensOf("300"))
    }

    @Test
    fun `토큰이 다르면 못 본다`(@TempDir tmp: Path) {
        val root = vaultWithAcl(tmp, """{"100":["@space:DOCS"],"200":["group:secret"]}""")
        val acl = AclRegistry()
        VaultReader(ObjectMapper()).read(root, acl)
        acl.putUser("alice", listOf("@space:DOCS"))

        assertTrue(acl.canSee("alice", "100"))
        assertFalse(acl.canSee("alice", "200"), "제한된 문서가 보였다")
    }

    /**
     * 깨진 acl 파일이 **전 페이지를 공개로** 만들면 안 된다 — 폴백이 곧 노출이다.
     *
     * **예전에는 이 설명 바로 밑에서 정반대를 단정하고 있었다**(`assertEquals(PUBLIC, …)`).
     * 주석은 구멍을 막겠다고 하는데 단정은 그 구멍을 잠그고 있었으니, 테스트가 있다는
     * 사실이 오히려 안심시켰다. 파일이 있는데 못 읽은 것은 "권한 정보가 없던 볼트" 가
     * 아니다.
     */
    @Test
    fun `깨진 acl 파일은 공개로 떨어지지 않는다`(@TempDir tmp: Path) {
        val root = vaultWithAcl(tmp, "{이건 JSON 이 아니다")
        val acl = AclRegistry()
        VaultReader(ObjectMapper()).read(root, acl)
        assertEquals(emptySet(), acl.tokensOf("200"))
        assertFalse(acl.canSee(setOf(VaultReader.PUBLIC), "200"))
    }
}
