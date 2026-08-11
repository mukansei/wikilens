package io.wikilens.vault

import com.fasterxml.jackson.databind.ObjectMapper
import io.wikilens.acl.AclRegistry
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.createDirectories
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * **깨진 UTF-8 이 색인에서만 사라지면 안 된다.**
 *
 * [VaultText] 의 KDoc 이 "세 소비자가 같은 디코더를 써야 한다" 고 못 박아 뒀는데
 * **넷째가 있었다** — 색인 경로([VaultReader.read])가 `Files.readString` 을 쓰고
 * 실패를 빈 문자열로 삼켰다. 결과는 갈린 상태였다(실측 2026-08-12):
 *
 *     grep    찾는다   (VaultText, 관대한 디코더)
 *     read    서빙한다 (VaultText)
 *     search  못 찾는다 (본문이 빈 채로 색인됨)
 *
 * 경고도 없어서 "본문 없는 페이지" 와 구별되지 않았다.
 *
 * 되돌려 확인할 것: `readBody` 를 `Files.readString` 으로 되돌리면 빨개진다.
 */
class MalformedBodyTest {

    /** 깨진 바이트 앞뒤로 성한 본문이 있는 볼트 하나. */
    private fun vaultWithBrokenPage(): Path {
        val root = Files.createTempDirectory("malformed-vault")
        val mirror = root.resolve("mirror").also { it.createDirectories() }
        mirror.resolve(".sync-state.json").toFile().writeText(
            """{"pages":{"100":{"title":"배포 문서","space":"SP","version":1,"ancestors":[]}}}""",
        )
        // `VaultLayout.relPagePath("100")` 과 같은 자리에 둔다.
        val page = root.resolve(VaultLayout.relPagePath("100"))
        page.parent.createDirectories()
        Files.write(
            page,
            "앞부분 DEPLOY_TOKEN ".toByteArray() +
                byteArrayOf(0xFF.toByte(), 0xFE.toByte()) +
                " 뒷부분 인증".toByteArray(),
        )
        return root
    }

    @Test
    fun `깨진 바이트가 있어도 성한 부분이 색인된다`() {
        val pages = VaultReader(ObjectMapper()).read(vaultWithBrokenPage(), AclRegistry())

        assertEquals(1, pages.size)
        val body = pages.single().body

        assertTrue(
            body.isNotEmpty(),
            "본문이 비었다 — 깨진 바이트 하나에 페이지 전체가 색인에서 사라진다",
        )
        // 깨진 지점 **앞뒤가 모두** 살아야 한다. 앞만 살면 잘라 읽은 것이다.
        assertTrue("DEPLOY_TOKEN" in body, "깨진 지점 앞이 유실됐다: $body")
        assertTrue("인증" in body, "깨진 지점 뒤가 유실됐다: $body")
    }
}
