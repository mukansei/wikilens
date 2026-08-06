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

    // ---------------------------------------------------------- ReDoS 방어
    //
    // `regex=true` 는 사용자 정규식을 그대로 실행한다. JVM 정규식은 백트래킹이라
    // `(.+)+@@@@` 하나로 CPU 를 무한히 태운다 — 실측으로 요청이 20초를 넘겨도 안
    // 끝났고, ACL 이 fail-closed 라도 등록된 사용자면 누구나 스레드를 묶을 수 있었다.

    @Test
    fun `폭발적 정규식이 예산 안에서 끊긴다`() {
        // **위협은 한 줄이 아니라 누적이다.** 실볼트 실측: `(.+)+@@@@` 는 줄 하나에는
        // 0.2 초면 끝나지만, 2,383 문서를 훑으면 721 번째 파일에서 이미 20 초를 넘겼다.
        // 그래서 픽스처도 "여러 문서 × 여러 줄" 이어야 재현된다.
        repeat(20) { i ->
            val body = (1..40).joinToString("\n") { "a".repeat(600) }
            write("doc$i", "문서 $i", body + "\n", listOf("@public"))
        }
        acl.putUser(user, listOf("@public"))

        // 예산을 200ms 로 줄여 확인한다 — 기본 3초를 그대로 쓰면 테스트가 그만큼 느려진다.
        val t0 = System.nanoTime()
        val r = svc.grep("(.+)+@@@@", user, limit = 5, regex = true, budgetNanos = 200_000_000)
        val elapsedSec = (System.nanoTime() - t0) / 1e9

        assertTrue(elapsedSec < 5, "예산을 넘겨 %.1f초 걸렸다".format(elapsedSec))
        assertTrue(r.matches.isEmpty(), "매치가 있을 리 없다")
        assertTrue(r.truncated, "예산으로 끊었으면 truncated 로 알려야 한다")
    }

    @Test
    fun `스택을 넘기는 정규식이 500 이 되지 않는다`() {
        // JVM 정규식은 재귀 백트래킹이라 깊이가 스택을 넘기면 **Error** 가 난다.
        // 예외가 아니라 Error 라 그대로 위로 던져져 HTTP 500 이 됐다(실측).
        // 시간 예산으로는 못 막는다 — 터지는 데 0.02초면 된다.
        //
        // 재현 조건이 좁다: **매치에 실패하는** 긴 줄이어야 한다. 일찍 매치되면
        // 백트래킹이 깊어지기 전에 끝나서 실볼트에서는 안 났다.
        write("1", "긴 줄", "a".repeat(5_000) + "\n", listOf("@public"))
        acl.putUser(user, listOf("@public"))

        val r = svc.grep("(a|aa)+c", user, limit = 5, regex = true)

        assertTrue(r.matches.isEmpty(), "매치될 리 없다")
        assertEquals(1, r.scanned, "그 줄만 건너뛰고 문서 스캔은 계속해야 한다")
    }

    @Test
    fun `스택을 넘긴 줄 뒤의 문서도 계속 스캔한다`() {
        write("1", "덫", "a".repeat(5_000) + "\n", listOf("@public"))
        write("2", "정상", "점유인증 정책\n", listOf("@public"))
        acl.putUser(user, listOf("@public"))

        // 덫에 걸린 뒤에도 2번 문서에서 찾아야 한다
        val r = svc.grep("(a|aa)+c|점유인증", user, limit = 5, regex = true)
        assertEquals(2, r.scanned)
        assertEquals(1, r.matches.size, "덫 때문에 나머지를 버리면 안 된다")
        assertEquals("2", r.matches[0].pageId)
    }

    @Test
    fun `너무 긴 패턴은 거부한다`() {
        write("1", "문서", "hello\n", listOf("@public"))
        acl.putUser(user, listOf("@public"))

        val r = svc.grep("x".repeat(ContentService.MAX_PATTERN + 1), user, 5, regex = false)
        assertEquals(0, r.scanned, "긴 패턴은 스캔조차 하지 않는다")
        assertTrue(r.matches.isEmpty())
    }

    @Test
    fun `정상 질의는 방어에 걸리지 않는다`() {
        write("1", "문서", "점유인증 정책 강화\nDEPLOY_TOKEN=abc\n", listOf("@public"))
        acl.putUser(user, listOf("@public"))

        assertEquals(1, svc.grep("점유인증", user, 5, regex = false).matches.size)
        assertEquals(1, svc.grep("DEPLOY_[A-Z]+", user, 5, regex = true).matches.size)
    }

    @Test
    fun `긴 줄은 잘라서 검사한다`() {
        // 앞부분은 잘려도 매칭되고, 상한 너머는 안 보인다
        val head = "찾을말" + "z".repeat(ContentService.MAX_LINE)
        write("1", "긴 줄", head + "숨은말\n", listOf("@public"))
        acl.putUser(user, listOf("@public"))

        assertEquals(1, svc.grep("찾을말", user, 5, regex = false).matches.size)
        assertTrue(svc.grep("숨은말", user, 5, regex = false).matches.isEmpty(),
            "상한 너머까지 검사하면 긴 줄에서 백트래킹이 폭발한다")
    }
}
