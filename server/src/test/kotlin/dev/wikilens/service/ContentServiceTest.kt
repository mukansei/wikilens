package dev.wikilens.service


import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.IndexedPage
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultLocator
import dev.wikilens.vault.VaultLayout
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.createTempDirectory
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
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

    /** setUp 이 쓰는 문서 중 tester 가 볼 수 있는 것. 나머지 하나는 @secret 이다. */
    private val pagesBefore = 1

    @BeforeTest
    fun setUp() {
        vault = createTempDirectory("content-svc-vault")
        index = LuceneIndex(createTempDirectory("content-svc-idx"))
        acl = AclRegistry()
        svc = ContentService(acl, index, VaultLocator(WikiLensProperties(vaultRoot = vault.toString())))

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

    // ------------------------------------------------------- 폭발적 정규식
    //
    // `regex=true` 는 사용자 정규식을 그대로 실행한다. `java.util.regex` 를 쓰던
    // 동안 `(.+)+@@@@` 하나로 CPU 가 무한히 탔다 — 실측으로 요청이 20초를 넘겨도
    // 안 끝났고, ACL 이 fail-closed 라도 등록된 사용자면 누구나 스레드를 묶었다.
    // 시간 예산으로 끊는 것이 그때의 대응이었다.
    //
    // 지금은 엔진이 RE2(유한 오토마타)라 **끊을 것이 없다.** 아래 테스트가 재는 것도
    // 바뀌었다 — "예산 안에 끊기는가" 가 아니라 "예산이 필요하지도 않은가" 다.

    @Test
    fun `폭발적 정규식이 예산을 쓰지도 않고 끝난다`() {
        // **위협은 한 줄이 아니라 누적이었다.** 실볼트 실측: `(.+)+@@@@` 는 줄 하나에는
        // 0.2 초면 끝나지만, 2,383 문서를 훑으면 721 번째 파일에서 이미 20 초를 넘겼다.
        // 그래서 픽스처가 "여러 문서 × 여러 줄" 이어야 그때의 실패가 재현된다.
        repeat(20) { i ->
            val body = (1..40).joinToString("\n") { "a".repeat(600) }
            write("doc$i", "문서 $i", body + "\n", listOf("@public"))
        }
        acl.putUser(user, listOf("@public"))

        // 예산을 200ms 로 **줄여** 준다. 백트래킹이 남아 있다면 여기서 잘려
        // truncated 가 서고, RE2 면 그 전에 끝나 안 선다.
        val t0 = System.nanoTime()
        val r = svc.grep("(.+)+@@@@", user, limit = 5, regex = true, budgetNanos = 200_000_000)
        val elapsedSec = (System.nanoTime() - t0) / 1e9

        assertTrue(elapsedSec < 5, "%.1f초 걸렸다".format(elapsedSec))
        assertTrue(r.matches.isEmpty(), "매치가 있을 리 없다")
        assertFalse(r.truncated, "200ms 예산에도 안 잘렸다 = 백트래킹이 없다")
        assertEquals(20 + pagesBefore, r.scanned, "잘리지 않았으므로 전 문서를 다 봤다")
    }

    @Test
    fun `스택을 넘기던 정규식이 500 이 되지 않는다`() {
        // `java.util.regex` 시절 이 조합은 재귀 깊이가 스택을 넘겨 **Error** 를 냈고,
        // 예외가 아니라 Error 라 그대로 HTTP 500 이 됐다(실측). 시간 예산으로는
        // 못 막았다 — 터지는 데 0.02초면 됐다.
        //
        // 재현 조건이 좁았다: **매치에 실패하는** 긴 줄이어야 한다. 일찍 매치되면
        // 깊어지기 전에 끝나서 실볼트에서는 안 났다.
        //
        // RE2 로 바꾼 뒤로는 재귀 자체가 없어 이 테스트가 엔진이 아니라 **성질**을
        // 잠근다 — 어떤 엔진을 쓰든 사용자 패턴이 서버를 죽이면 안 된다.
        write("1", "긴 줄", "a".repeat(5_000) + "\n", listOf("@public"))
        acl.putUser(user, listOf("@public"))

        val r = svc.grep("(a|aa)+c", user, limit = 5, regex = true)

        assertTrue(r.matches.isEmpty(), "매치될 리 없다")
        assertEquals(1, r.scanned, "그 줄만 건너뛰고 문서 스캔은 계속해야 한다")
    }

    @Test
    fun `역참조는 조용히 0건이 아니라 이유를 돌려준다`() {
        // RE2 에 없는 문법이다. error 가 없으면 사용자는 `\\1` 이 문제인 줄 모르고
        // "일치 없음" 만 본다 — 이 프로젝트가 가장 싫어하는 실패 방식이다.
        val r = svc.grep("(\\w+)\\s\\1", user, limit = 5, regex = true)

        assertTrue(r.matches.isEmpty())
        assertNotNull(r.error, "왜 안 되는지 말해야 한다")
        assertTrue("역참조" in r.error!!, "고칠 수 있는 말이어야 한다: ${r.error}")
    }

    @Test
    fun `전방탐색도 마찬가지다`() {
        val r = svc.grep("APPLE(?=조사)", user, limit = 5, regex = true)
        assertNotNull(r.error)
        assertTrue("전방탐색" in r.error!!)
    }

    @Test
    fun `regex 토글이 대소문자 민감도를 바꾸지 않는다`() {
        // 도구 설명은 이 플래그가 **문법만** 바꾼다고 말한다. 리터럴 경로는
        // ignoreCase 인데 RE2 기본은 대소문자 구분이라, 맞춰주지 않으면 같은 문자열이
        // 플래그 하나로 다른 답을 낸다 — 실측: 본문 `Acme` 에 `acme` 가 1건 대 0건.
        write("7", "영문", "Acme 스마트 카탈로그\n", listOf("@public"))

        for (p in listOf("ACME", "acme", "Acme")) {
            assertEquals(
                svc.grep(p, user, 5, regex = false).matches.size,
                svc.grep(p, user, 5, regex = true).matches.size,
                "'$p' 이 regex 토글로 답이 갈렸다",
            )
        }
    }

    @Test
    fun `정상 검색에는 error 가 없다`() {
        assertNull(svc.grep("APPLE", user, limit = 5, regex = false).error)
    }

    @Test
    fun `긴 줄 뒤쪽의 일치도 놓치지 않는다`() {
        // 예전에는 줄을 4,000자에서 잘라 봤다. 백트래킹 비용이 줄 길이에 비선형이라
        // 어쩔 수 없었는데, 그 대가로 표처럼 긴 줄의 뒤쪽 일치를 **조용히 놓쳤다.**
        // RE2 는 줄 길이에 선형이라 자를 이유가 없다.
        write("9", "긴 표", "x".repeat(10_000) + "찾을말\n", listOf("@public"))

        val r = svc.grep("찾을말", user, limit = 5, regex = false)

        assertEquals(1, r.matches.size, "4,000자 뒤에 있어도 찾아야 한다")
        assertEquals("9", r.matches[0].pageId)
    }

    @Test
    fun `스택을 넘기던 줄 뒤의 문서도 계속 스캔한다`() {
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
    fun `깨진 UTF-8 파일이 스캔 전체를 죽이지 않는다`() {
        // 볼트는 Python 싱크가 UTF-8 로 쓰지만, 디스크가 차거나 싱크가 도중에 죽으면
        // 멀티바이트 문자가 반토막 난 파일이 남는다. 기본 디코더는 REPORT 라
        // **읽는 중에** MalformedInputException 을 던지고, 그건 파일 열기를 감싼
        // runCatching 밖이라 그대로 500 이 됐다 — 파일 하나가 나머지 전부를 날렸다.
        val f = vault.resolve(VaultLayout.relPagePath("1"))
        Files.write(f, byteArrayOf(0x61, 0xFF.toByte(), 0xFE.toByte(), 0x0A))

        val r = svc.grep("APPLE", user, limit = 5, regex = false)

        assertEquals(1, r.scanned, "깨진 파일도 스캔한다 — 건너뛰는 게 아니라 대체 문자로 읽는다")
        assertTrue(r.matches.isEmpty(), "깨진 파일에는 APPLE 이 없다")
    }

    @Test
    fun `깨진 UTF-8 문서도 읽힌다`() {
        val f = vault.resolve(VaultLayout.relPagePath("1"))
        Files.write(f, "정상".toByteArray() + byteArrayOf(0xFF.toByte()) + "부분".toByteArray())

        val r = svc.read("1", user)

        assertNotNull(r, "깨진 바이트 하나로 문서 전체를 못 읽게 하면 안 된다")
        assertTrue(r.markdown.startsWith("정상"), "깨진 지점 앞은 온전해야 한다")
        assertTrue(r.markdown.endsWith("부분"), "깨진 지점 뒤도 살아야 한다")
    }

    @Test
    fun `limit 은 클라이언트가 무한히 키울 수 없다`() {
        // limit 은 요청 본문에서 온다. 상한이 없으면 매치 객체가 매치 줄 수만큼 쌓인다.
        val r = svc.grep("APPLE", user, limit = Int.MAX_VALUE, regex = false)
        assertTrue(r.matches.size <= ContentService.MAX_LIMIT)
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
}
