package dev.wikilens.index

import dev.wikilens.acl.AclRegistry
import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlin.test.assertFailsWith

/**
 * 색인 시점에 분석기를 고르는 것.
 *
 * **이 선택이 색인과 질의에서 갈리면 예외가 아니라 조용히 0건이 된다.** 이 프로젝트가
 * 겪은 실패 1번이 정확히 그 모양이었다(클라이언트와 서버가 각자 토큰화). 그래서
 * 선택을 색인 커밋 데이터에 기록하고 기동 시 대조한다.
 */
class AnalyzerChoiceTest {

    private fun page(id: String, body: String) =
        IndexedPage(id, "제목 $id", "S", "p/$id.md", body, emptyList(), listOf("@public"))

    @Test
    fun `영어 분석기는 굴절을 넘어 찾고 한국어 분석기는 못 찾는다`() {
        // 문서는 복수형, 질의는 단수형. **한 낱말만 던진다** — 파서 기본이 OR 이라
        // 여러 낱말을 주면 다른 낱말이 맞아서 굴절 실패가 가려진다.
        val body = "we run production servers behind the gateway"
        val acl = listOf("@public")

        LuceneIndex(createTempDirectory("ko"), AnalyzerKind.KOREAN).use { ko ->
            ko.rebuild(listOf(page("1", body)))
            assertEquals(1, ko.search("servers", acl, 5).size, "그대로 치면 찾는다")
            assertTrue(
                ko.search("server", acl, 5).isEmpty(),
                "Nori 는 servers→server 를 못 줄인다 — 영어 코퍼스에서의 한계다",
            )
        }

        LuceneIndex(createTempDirectory("en"), AnalyzerKind.ENGLISH).use { en ->
            en.rebuild(listOf(page("1", body)))
            assertEquals(1, en.search("server", acl, 5).size, "어간 추출로 단수형도 찾는다")
        }
    }

    @Test
    fun `한국어 분석기는 조사를 뗀다 — 영어 분석기로는 그것을 잃는다`() {
        val body = "배포 파이프라인을 구성했습니다"
        val acl = listOf("@public")

        LuceneIndex(createTempDirectory("ko2"), AnalyzerKind.KOREAN).use { ko ->
            ko.rebuild(listOf(page("1", body)))
            assertEquals(1, ko.search("파이프라인", acl, 5).size, "조사를 떼고 찾아야 한다")
        }
        LuceneIndex(createTempDirectory("en2"), AnalyzerKind.ENGLISH).use { en ->
            en.rebuild(listOf(page("1", body)))
            assertTrue(
                en.search("파이프라인", acl, 5).isEmpty(),
                "대칭인 실패다 — 영어 분석기는 '파이프라인을' 을 통째로 들고 있다",
            )
        }
    }

    @Test
    fun `색인이 어떤 분석기로 지어졌는지 기록한다`() {
        val dir = createTempDirectory("mark")
        LuceneIndex(dir, AnalyzerKind.ENGLISH).use { it.rebuild(listOf(page("1", "hello"))) }

        // 다른 분석기로 같은 디렉터리를 열어도 **디스크에 적힌 것**이 나와야 한다.
        LuceneIndex(dir, AnalyzerKind.KOREAN).use { reopened ->
            reopened.openIfExists()
            assertEquals("english", reopened.builtWith(), "색인을 지은 분석기가 기록돼야 한다")
            assertNotEquals(reopened.kind.key, reopened.builtWith(), "이 상태가 곧 불일치다")
        }
    }

    @Test
    fun `불일치는 조용한 0건으로 나타난다 — 그래서 기록이 필요하다`() {
        // 한국어 코퍼스를 실수로 english 로 색인한 경우 — 이 프로젝트에서 현실적인 사고다.
        val dir = createTempDirectory("mismatch")
        val acl = listOf("@public")
        LuceneIndex(dir, AnalyzerKind.ENGLISH).use {
            it.rebuild(listOf(page("1", "배포 파이프라인을 구성했습니다")))
        }
        LuceneIndex(dir, AnalyzerKind.KOREAN).use { wrong ->
            wrong.openIfExists()
            // 색인에는 `파이프라인을` 이 통째로 들어가 있는데 질의는 `파이프라인` 으로 온다.
            // 예외도 경고도 없이 그냥 안 나온다 — 사용자 눈에는 "문서가 없다" 로 보인다.
            assertTrue(
                wrong.search("파이프라인", acl, 5).isEmpty(),
                "이것이 조용한 0건이다 — 잡아주는 것은 기록 대조뿐이다",
            )
            assertNotEquals(wrong.kind.key, wrong.builtWith(), "대조로만 잡을 수 있다")
        }
    }

    @Test
    fun `분석기 이름 오타는 기동 시 죽는다`() {
        // 조용히 기본값으로 떨어지면 그게 "검색이 0건" 의 원인이 되고,
        // 그때는 설정이 아니라 색인을 의심하게 된다.
        val e = assertFailsWith<IllegalArgumentException> { AnalyzerKind.of("korea") }
        assertTrue("korean" in e.message!!, "가능한 값을 알려줘야 한다: ${e.message}")
        assertEquals(AnalyzerKind.KOREAN, AnalyzerKind.of(" Korean "), "공백·대소문자는 관대하게")
    }

    @Test
    fun `분석기 기록 이전 색인은 null 이고 경고하지 않는다`() {
        LuceneIndex(createTempDirectory("empty"), AnalyzerKind.KOREAN).use {
            it.openIfExists()
            assertNull(it.builtWith(), "색인이 없으면 대조할 것도 없다")
        }
    }
}
