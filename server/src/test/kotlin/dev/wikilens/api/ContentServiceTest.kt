package dev.wikilens.api

import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.IndexedPage
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultLayout
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.createTempDirectory
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * 콘텐츠 서빙(`read`/`grep`) 테스트.
 *
 * `read` 가 권한 없을 때 **null 을 반환**하는 것이 404 계약의 실현부다 —
 * Controller 가 그걸 받아 404 로 바꾼다. 403 이면 "존재하지만 못 본다"를 알려주므로 유출이다.
 */
class ContentServiceTest {

    private lateinit var vault: Path
    private lateinit var index: LuceneIndex
    private lateinit var acl: AclRegistry
    private lateinit var svc: ContentService

    private val user = "tester"

    @BeforeTest
    fun setUp() {
        vault = createTempDirectory("content-svc-vault")
        index = LuceneIndex(createTempDirectory("content-svc-idx"))
        acl = AclRegistry()
        svc = ContentService(acl, index, WikiLensProperties(vaultRoot = vault.toString()))

        write("1", "공개 문서", "첫 줄 APPLE\n둘째 줄\n셋째 줄 APPLE\n", listOf("@public"))
        write("2", "기밀 문서", "APPLE 이 여기도 있다\n", listOf("@secret"))
        acl.putUser(user, listOf("@public"))
    }

    @AfterTest
    fun tearDown() = index.close()

    private val pages = mutableListOf<IndexedPage>()

    private fun write(id: String, title: String, body: String, tokens: List<String>) {
        val f = vault.resolve(VaultLayout.relPagePath(id))
        Files.createDirectories(f.parent)
        Files.writeString(f, body)
        acl.putPage(id, tokens)
        pages += IndexedPage(id, title, "DOCS", VaultLayout.relPagePath(id), body, emptyList(), tokens)
        index.rebuild(pages.toList())
    }

    // ----------------------------------------------------------------- read

    @Test
    fun `권한 있는 문서는 본문을 준다`() {
        val r = svc.read("1", user)
        assertEquals("공개 문서", r?.title)
        assertTrue(r!!.markdown.contains("APPLE"))
    }

    @Test
    fun `권한 없으면 null 이다 (Controller 가 404 로 바꾼다)`() {
        assertNull(svc.read("2", user), "볼 수 없는 문서의 존재가 새면 안 된다")
        assertNull(svc.read("1", "미등록"))
        assertNull(svc.read("1", null))
    }

    @Test
    fun `색인에 없는 id 는 null 이다`() {
        assertNull(svc.read("없는id", user))
    }

    // ----------------------------------------------------------------- grep

    @Test
    fun `권한 있는 문서만 스캔한다`() {
        val r = svc.grep("APPLE", user, limit = 40, regex = false)
        assertEquals(2, r.matches.size, "공개 문서의 두 줄만 나와야 한다")
        assertTrue(r.matches.all { it.pageId == "1" }, "기밀 문서가 새어 나왔다")
        assertEquals(1, r.scanned)
    }

    @Test
    fun `limit 에 걸리면 truncated 가 참이다`() {
        val r = svc.grep("APPLE", user, limit = 1, regex = false)
        assertEquals(1, r.matches.size)
        assertTrue(r.truncated, "잘렸는데 truncated=false 로 나가면 소비자가 더 있는 줄 모른다")
    }

    @Test
    fun `잘리지 않으면 truncated 가 거짓이다`() {
        assertFalse(svc.grep("APPLE", user, limit = 40, regex = false).truncated)
    }

    @Test
    fun `권한 토큰이 없으면 빈 결과다`() {
        val r = svc.grep("APPLE", "미등록", limit = 40, regex = false)
        assertTrue(r.matches.isEmpty())
        assertEquals(0, r.scanned)
    }

    @Test
    fun `잘못된 정규식은 예외 대신 빈 결과다`() {
        val r = svc.grep("[unclosed", user, limit = 40, regex = true)
        assertTrue(r.matches.isEmpty())
    }

    @Test
    fun `정규식 모드가 동작한다`() {
        val r = svc.grep("^첫.*APPLE$", user, limit = 40, regex = true)
        assertEquals(1, r.matches.size)
        assertEquals(1, r.matches[0].line, "줄 번호는 1부터")
    }
}
