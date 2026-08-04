package dev.wikilens.index

import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * 계층 트리 구축·렌더링 테스트.
 *
 * `TreeIndex`/`TreeRenderer` 는 순수 자료구조라 Lucene 없이 검증한다 —
 * 예전엔 트리 로직 하나 보려고 실제 색인 디렉터리를 만들어야 했다.
 *
 * 코드 리뷰에서 나온 결함들의 회귀 테스트가 포함돼 있다:
 * rootId 로 진입할 때 ACL 승격 규칙이 우회되던 것, 순회 예산이 요청 전체에서
 * 공유돼 정확도가 트리 순서에 의존하던 것.
 */
class PageTreeTest {

    private fun page(id: String, title: String, parentId: String? = null) = IndexedPage(
        id = id, title = title, space = "DOCS", path = "mirror/pages/00/0$id.md",
        body = "", anchors = emptyList(), aclTokens = listOf("@public"),
        ancestors = if (parentId != null) listOf(Ancestor(parentId, "")) else emptyList(),
    )

    private fun renderer(vararg pages: IndexedPage) = TreeRenderer(
        TreeIndex.build(pages.toList()),
        pages.associate { it.id to PageMeta(it.id, it.title, it.space) },
    )

    private val all = { _: String -> true }

    // ------------------------------------------------------------ 구축

    @Test
    fun `부모-자식 관계를 세운다`() {
        val t = TreeIndex.build(listOf(page("1", "루트"), page("2", "자식", parentId = "1")))
        assertEquals(listOf("1"), t.roots)
        assertEquals(listOf("2"), t.children["1"])
    }

    @Test
    fun `싱크 범위 밖 부모를 가진 페이지는 루트로 승격된다`() {
        val t = TreeIndex.build(listOf(page("5", "고아", parentId = "없는부모")))
        assertEquals(listOf("5"), t.roots, "부모가 범위 밖이면 최상위로 취급한다")
    }

    @Test
    fun `자식과 루트가 제목순으로 미리 정렬된다`() {
        val t = TreeIndex.build(listOf(
            page("1", "루트"),
            page("2", "나중", parentId = "1"),
            page("3", "가장먼저", parentId = "1"),
        ))
        assertEquals(listOf("3", "2"), t.children["1"], "렌더링마다 재정렬하지 않으려면 여기서 정렬돼야 한다")
    }

    // ------------------------------------------------------------ ACL

    @Test
    fun `안 보이는 부모의 보이는 자식은 승격되어 보인다`() {
        val r = renderer(page("1", "부서D"), page("2", "공개C1", parentId = "1"))
        val canSee = { pid: String -> pid != "1" }

        val full = r.render(canSee)
        assertTrue("공개C1" in full.markdown)
        assertFalse("부서D" in full.markdown)

        // rootId 로 콕 집어 들어가도 같은 규칙이어야 한다. 예전엔 별도 분기가
        // "rootId 안 보이면 빈 응답"으로 처리해, 전체 조회에선 보이던 자식이 사라졌다.
        val scoped = r.render(canSee, rootId = "1")
        assertTrue("공개C1" in scoped.markdown, "rootId 경로가 승격 규칙을 우회했다")
        assertFalse("부서D" in scoped.markdown)
    }

    @Test
    fun `존재하지 않는 rootId 는 빈 트리다`() {
        assertEquals("", renderer(page("1", "루트")).render(all, rootId = "없는id").markdown)
    }

    @Test
    fun `아무것도 안 보이면 빈 트리다`() {
        val r = renderer(page("1", "루트"), page("2", "자식", parentId = "1"))
        assertEquals("", r.render({ false }).markdown)
    }

    // ------------------------------------------------------------ depth

    @Test
    fun `rootId 서브트리만 그린다`() {
        val r = renderer(page("1", "루트"), page("2", "자식", parentId = "1"))
        assertEquals("- 루트 — 1\n  - 자식 — 2\n", r.render(all, rootId = "1").markdown)
    }

    @Test
    fun `depth 로 잘린 가지는 요약 라인과 truncated 를 남긴다`() {
        val r = renderer(
            page("1", "루트"), page("2", "자식", parentId = "1"), page("3", "손자", parentId = "2"),
        )
        val res = r.render(all, maxDepth = 1)
        assertTrue(res.truncated)
        assertTrue("… (+2개 하위, rootId=1로 조회)" in res.markdown)
        assertFalse("자식" in res.markdown, "depth=1 이면 루트 아래는 요약으로만 남는다")
    }

    @Test
    fun `잘리지 않으면 truncated 가 거짓이다`() {
        val r = renderer(page("1", "루트"))
        assertFalse(r.render(all, maxDepth = 5).truncated)
        assertFalse(r.render(all, maxDepth = 0).truncated)
    }

    @Test
    fun `요약 개수는 보이는 하위만 센다`() {
        val r = renderer(
            page("1", "루트"),
            page("2", "공개", parentId = "1"),
            page("3", "비공개", parentId = "1"),
        )
        val res = r.render({ it != "3" }, maxDepth = 1)
        assertTrue("+1개" in res.markdown, "숨긴 문서가 개수로 새면 안 된다: ${res.markdown}")
    }

    // ------------------------------------------------------------ 견고성

    @Test
    fun `순환 ancestors 에도 무한 재귀하지 않는다`() {
        // 손상된 .sync-state.json 하나로 서버가 StackOverflow 로 죽으면 안 된다.
        val r = renderer(page("1", "A", parentId = "2"), page("2", "B", parentId = "1"))
        val res = r.render(all, rootId = "1")
        assertTrue("A" in res.markdown && "B" in res.markdown)
    }

    @Test
    fun `순회 예산은 가지마다 따로다`() {
        // 예전엔 요청 전체가 예산을 공유해, 첫 큰 가지가 소진하면 뒤따르는 작은
        // 가지까지 "N개 이상"으로 어림됐다 — 정확도가 트리 순서에 의존했다.
        val pages = mutableListOf(page("big", "AAA 큰가지"), page("small", "ZZZ 작은가지"))
        repeat(TreeRenderer.DESCENDANT_BUDGET + 100) { pages += page("b$it", "child$it", parentId = "big") }
        pages += page("s1", "s1", parentId = "small")
        pages += page("s2", "s2", parentId = "small")

        val md = renderer(*pages.toTypedArray()).render(all, maxDepth = 1).markdown

        assertTrue("rootId=big" in md && "개 이상" in md, "큰 가지는 어림돼야 한다")
        assertTrue("+2개 하위, rootId=small" in md, "작은 가지는 정확해야 한다: $md")
    }

    // ------------------------------------------------------------ 배선

    @Test
    fun `LuceneIndex 재구축이 트리 스냅샷을 함께 교체한다`() {
        LuceneIndex(createTempDirectory("tree-wiring")).use { index ->
            index.rebuild(listOf(page("1", "루트"), page("2", "자식", parentId = "1")))
            assertEquals("- 루트 — 1\n  - 자식 — 2\n", index.renderTree(all).markdown)

            index.rebuild(listOf(page("9", "새 루트")))
            assertEquals("- 새 루트 — 9\n", index.renderTree(all).markdown, "옛 트리가 남아 있으면 안 된다")
        }
    }
}
