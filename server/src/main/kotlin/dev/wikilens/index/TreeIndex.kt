package dev.wikilens.index

/**
 * 부모-자식 계층. 앵커 색인(어휘)과 완전히 분리된 신호다 — "이 문서를 뭐라고 부르나"가
 * 아니라 "이 문서가 어디 분류에 속하나"를 답한다. 로컬판 TREE.md와 같은 데이터다.
 *
 * Lucene 과 분리해 둔 이유는 순수 자료구조라 색인 없이 단위 테스트할 수 있어야 하기
 * 때문이다. `LuceneIndex` 는 이걸 스냅샷의 일부로 들고만 있는다.
 */
data class TreeIndex(
    /** 부모 → 자식들. **제목순으로 미리 정렬돼 있다** (렌더링마다 재정렬하지 않으려고). */
    val children: Map<String, List<String>>,
    /** 부모가 없거나 부모가 싱크 범위 밖인 노드들. 역시 제목순 정렬. */
    val roots: List<String>,
) {
    companion object {
        val EMPTY = TreeIndex(emptyMap(), emptyList())

        fun build(pages: Collection<IndexedPage>): TreeIndex {
            val ids = pages.mapTo(HashSet()) { it.id }
            val titleOf = pages.associate { it.id to it.title }
            val children = HashMap<String, MutableList<String>>()
            val roots = mutableListOf<String>()

            for (p in pages) {
                val parent = p.ancestors.lastOrNull()?.id
                // 부모가 싱크 범위 밖이면 루트로 승격한다 — 로컬판 build.py 와 같은 규칙.
                if (parent != null && parent in ids) {
                    children.getOrPut(parent) { mutableListOf() }.add(p.id)
                } else {
                    roots.add(p.id)
                }
            }

            val cmp = compareBy<String> { titleOf[it].orEmpty() }
            return TreeIndex(
                children = children.mapValues { (_, v) -> v.sortedWith(cmp) },
                roots = roots.sortedWith(cmp),
            )
        }
    }
}
