package dev.wikilens.index

import kotlin.io.path.createTempDirectory
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * `renderTree`의 ACL·depth·rootId 동작을 잠근다.
 *
 * 코드 리뷰에서 발견된 버그 둘의 회귀 테스트다:
 * 1. rootId로 진입할 때도 "부모가 안 보이면 자식을 승격한다"는 규칙이 지켜져야
 *    한다 — 이전에는 rootId 자신이 안 보이면 무조건 빈 응답이라, 전체 트리
 *    조회에서는 보이던 자식이 그 자식의 rootId로 들어가는 순간 사라졌다.
 * 2. depth 로 잘린 가지의 "+N개 하위" 요약이 실제로 순회량을 줄여야 한다 —
 *    이전에는 요약 하나를 만들려고 잘린 서브트리 전체를 다시 훑었다.
 */
class LuceneIndexTreeTest {

    private lateinit var index: LuceneIndex

    @BeforeTest
    fun setUp() {
        index = LuceneIndex(createTempDirectory("lucene-tree-test"))
    }

    @AfterTest
    fun tearDown() {
        index.close()
    }

    private fun page(id: String, title: String, parentId: String? = null) = IndexedPage(
        id = id, title = title, space = "DOCS", path = "mirror/pages/00/0$id.md",
        body = "", anchors = emptyList(), aclTokens = listOf("@public"),
        ancestors = if (parentId != null) listOf(Ancestor(parentId, "")) else emptyList(),
    )

    @Test
    fun `보이지 않는 부모의 rootId로 들어가도 보이는 자식은 승격되어 보인다`() {
        index.rebuild(listOf(
            page("1", "부서 D"),
            page("2", "공개 문서 C1", parentId = "1"),
            page("3", "공개 문서 C2", parentId = "1"),
        ))
        val canSee = { pid: String -> pid != "1" }   // 부서 D만 안 보임

        val full = index.renderTree(canSee)
        assertTrue("공개 문서 C1" in full.markdown, "전체 트리 조회에서는 자식이 승격되어 보여야 한다")
        assertTrue("공개 문서 C2" in full.markdown)
        assertFalse("부서 D" in full.markdown)

        val scoped = index.renderTree(canSee, rootId = "1")
        assertTrue("공개 문서 C1" in scoped.markdown, "rootId로 콕 집어 들어가도 같은 승격 규칙이 적용돼야 한다")
        assertTrue("공개 문서 C2" in scoped.markdown)
        assertFalse("부서 D" in scoped.markdown)
    }

    @Test
    fun `rootId 자체가 보이면 그대로 렌더링된다`() {
        index.rebuild(listOf(page("1", "루트"), page("2", "자식", parentId = "1")))
        val result = index.renderTree({ true }, rootId = "1")
        assertEquals("- 루트 — 1\n  - 자식 — 2\n", result.markdown)
        assertFalse(result.truncated)
    }

    @Test
    fun `존재하지 않거나 안 보이는 rootId는 빈 트리 (존재 비노출)`() {
        index.rebuild(listOf(page("1", "루트")))
        assertEquals("", index.renderTree({ true }, rootId = "없는id").markdown)
        assertEquals("", index.renderTree({ false }, rootId = "1").markdown)
    }

    @Test
    fun `depth 로 잘린 가지는 요약 라인과 truncated=true 를 남긴다`() {
        index.rebuild(listOf(
            page("1", "루트"),
            page("2", "자식", parentId = "1"),
            page("3", "손자", parentId = "2"),
        ))
        val result = index.renderTree({ true }, maxDepth = 1)
        assertTrue(result.truncated)
        assertTrue("… (+2개 하위, rootId=1로 조회)" in result.markdown)
        assertFalse("자식" in result.markdown, "depth=1 이면 루트 아래는 요약으로만 남아야 한다")
    }

    @Test
    fun `잘리지 않으면 truncated=false`() {
        index.rebuild(listOf(page("1", "루트")))
        assertFalse(index.renderTree({ true }, maxDepth = 5).truncated)
        assertFalse(index.renderTree({ true }, maxDepth = 0).truncated)
    }
}
