package io.wikilens.vault

import com.fasterxml.jackson.databind.ObjectMapper
import io.wikilens.acl.AclRegistry
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.createDirectories
import kotlin.io.path.writeText
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * **acl.json 에 없는 페이지를 무엇으로 볼 것인가.**
 *
 * Python `collect` 은 권한을 확정하지 못한 페이지를 **일부러 생략한다**(fail-closed) —
 * 조회 실패든 조상 미확정이든, 못 읽었다고 공개로 바뀌면 안 되기 때문이다. 그런데
 * 서버가 없는 항목을 `@public` 으로 채우면 **그 fail-closed 가 fail-open 으로 뒤집힌다.**
 * 파일로만 연결된 두 판이 각자 합리적인 기본값을 고르다 정반대가 되는 자리다.
 *
 * 구별해야 하는 것은 "수집한 적 없음" 과 "수집했는데 이 페이지가 없음" 이다.
 * 전자는 옛 볼트라 전부 `@public` 이 맞고, 후자는 확정 실패라 아무도 못 봐야 한다.
 */
class AclFallbackTest {

    private fun vault(dir: Path, acl: String?): Path {
        dir.resolve("mirror").createDirectories()
        dir.resolve("mirror/.sync-state.json").writeText(
            """{"pages":{"11":{"title":"확정됨","space":"ENG","ancestors":[]},
                         "22":{"title":"미확정","space":"ENG","ancestors":[]}}}""")
        if (acl != null) {
            dir.resolve("mirror/acl").createDirectories()
            dir.resolve("mirror/acl/acl.json").writeText(acl)
        }
        return dir
    }

    private fun read(root: Path): Pair<AclRegistry, Map<String, List<String>>> {
        val reg = AclRegistry()
        val pages = VaultReader(ObjectMapper()).read(root, reg)
        return reg to pages.associate { it.id to it.aclTokens }
    }

    @Test
    fun `수집한 적 없으면 전부 공개다 - 옛 볼트 동작을 지킨다`() {
        val root = vault(Files.createTempDirectory("v1"), null)
        val (reg, tokens) = read(root)
        assertEquals(listOf("@public"), tokens["11"])
        assertEquals(listOf("@public"), tokens["22"])
        assertTrue(reg.canSee(setOf("@public"), "22"))
    }

    @Test
    fun `수집했는데 없는 페이지는 아무에게도 안 보인다`() {
        val root = vault(Files.createTempDirectory("v2"), """{"11":["@space:ENG"]}""")
        val (reg, tokens) = read(root)

        assertEquals(listOf("@space:ENG"), tokens["11"])
        assertEquals(emptyList(), tokens["22"], "Python 이 일부러 생략한 것을 공개로 바꿨다")
        assertFalse(reg.canSee(setOf("@public"), "22"))
        assertFalse(reg.canSee(setOf("@space:ENG"), "22"))
    }

    @Test
    fun `깨진 acl 파일이 전 페이지를 공개로 만들지 않는다`() {
        val root = vault(Files.createTempDirectory("v3"), """{"11": [ 깨짐""")
        val (reg, tokens) = read(root)

        assertEquals(emptyList(), tokens["11"], "못 읽은 것을 '권한 없던 볼트' 로 취급했다")
        assertFalse(reg.canSee(setOf("@public"), "11"))
    }

    @Test
    fun `볼트에서 사라진 페이지는 권한 맵에서도 사라진다`() {
        val dir = Files.createTempDirectory("v4")
        val root = vault(dir, """{"11":["@public"],"22":["@public"]}""")
        val reg = AclRegistry()
        val reader = VaultReader(ObjectMapper())
        reader.read(root, reg)
        assertEquals(2, reg.pageCount())

        // 22 가 삭제된 볼트로 다시 읽는다
        dir.resolve("mirror/.sync-state.json").writeText(
            """{"pages":{"11":{"title":"확정됨","space":"ENG","ancestors":[]}}}""")
        reader.read(root, reg)

        assertEquals(1, reg.pageCount(), "사라진 페이지가 권한 맵에 남았다")
        assertFalse(reg.canSee(setOf("@public"), "22"))
    }

    @Test
    fun `빈 볼트는 권한 맵을 지우지 않는다`() {
        val dir = Files.createTempDirectory("v5")
        val root = vault(dir, """{"11":["@public"],"22":["@public"]}""")
        val reg = AclRegistry()
        val reader = VaultReader(ObjectMapper())
        reader.read(root, reg)
        assertEquals(2, reg.pageCount())

        dir.resolve("mirror/.sync-state.json").writeText("""{"pages":{}}""")
        reader.read(root, reg)

        assertEquals(2, reg.pageCount(), "빈 볼트가 권한을 지웠다 — 읽기가 전부 404 가 된다")
    }
}
