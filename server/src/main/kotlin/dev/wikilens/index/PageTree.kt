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

/**
 * [TreeRenderer] 결과. [truncated] 는 깊이 제한으로 잘린 가지가 있었는지 —
 * 마크다운 본문의 문구를 파싱하지 않고도 논-LLM 소비자가 확인할 수 있는 구조화된
 * 신호다 (`GrepResponse.truncated` 와 같은 패턴).
 */
data class RenderedTree(val markdown: String, val truncated: Boolean)

/**
 * 계층을 들여쓰기 마크다운으로 렌더링한다. 페이지마다 [canSee] 로 ACL 을 확인한다.
 *
 * **부모가 안 보여도 자식은 숨기지 않는다** — 대신 부모가 없었던 것처럼 그 깊이에서
 * 이어 그린다. 존재를 숨기는 것과 접근을 거부하는 것은 다르다(권한 없음은 404 원칙과
 * 같은 결). `rootId` 로 진입할 때도 같은 규칙이다 — 별도 분기로 "rootId 안 보이면
 * 통째로 빈 응답"을 하면, 전체 트리 조회에서는 보이던 자식이 그 자식의 rootId 로
 * 콕 집어 들어가는 순간 사라지는 비일관성이 생긴다.
 */
class TreeRenderer(
    private val tree: TreeIndex,
    private val meta: Map<String, PageMeta>,
) {
    companion object {
        /**
         * 잘린 가지의 하위 개수를 셀 때 **가지 하나당** 훑는 노드 수 상한.
         * 요청 전체가 아니라 가지별이어야 한다 — 공유하면 첫 큰 가지가 예산을 소진해
         * 뒤따르는 작은 가지까지 "N개 이상"으로 어림되고, 정확도가 트리 순서에 의존한다.
         */
        const val DESCENDANT_BUDGET = 1000
    }

    /**
     * [rootId] 를 주면 그 서브트리만, [maxDepth] > 0 이면 그 깊이까지만 그린다.
     * 잘린 가지는 하위 개수와 rootId 를 요약 라인으로 남겨 이어서 조회할 수 있다.
     */
    fun render(canSee: (String) -> Boolean, rootId: String? = null, maxDepth: Int = 0): RenderedTree {
        val sb = StringBuilder()
        var truncated = false
        // 순환 방어. Confluence 가 순환 ancestors 를 주는 일은 없어야 하지만,
        // 손상된 .sync-state.json 하나로 서버가 StackOverflow 로 죽으면 안 된다.
        val seen = HashSet<String>()

        fun render(pid: String, depth: Int) {
            if (!seen.add(pid)) return
            val title = meta[pid]?.title
            val visible = title != null && canSee(pid)
            if (visible) {
                sb.append("  ".repeat(depth)).append("- ").append(title)
                    .append(" — ").append(pid).append('\n')
                if (maxDepth > 0 && depth + 1 >= maxDepth) {
                    val (below, capped) = countVisibleBelow(pid, canSee)
                    if (below > 0 || capped) {
                        truncated = true
                        val count = if (capped) "${below}개 이상" else "${below}개"
                        sb.append("  ".repeat(depth + 1))
                            .append("… (+").append(count)
                            .append(" 하위, rootId=").append(pid).append("로 조회)\n")
                    }
                    return
                }
            }
            // 안 보이는 노드는 줄을 차지하지 않으므로 자식이 그 깊이를 이어받는다.
            val nextDepth = if (visible) depth + 1 else depth
            tree.children[pid].orEmpty().forEach { render(it, nextDepth) }
        }

        if (rootId != null) render(rootId, 0) else tree.roots.forEach { render(it, 0) }
        return RenderedTree(sb.toString(), truncated)
    }

    /**
     * (보이는 하위 개수, 예산 초과로 어림했는지).
     *
     * 개수도 **보이는** 하위만 센다 — 개수가 숨긴 문서의 존재를 새면 안 된다.
     * 예산을 넘기면 그 자리서 멈춘다. 전체 서브트리를 다 훑으면 depth 로 응답을
     * 가볍게 하려는 취지가 큰 가지에서 무너진다.
     */
    private fun countVisibleBelow(pid: String, canSee: (String) -> Boolean): Pair<Int, Boolean> {
        var budget = DESCENDANT_BUDGET
        var capped = false
        var n = 0
        val seen = HashSet<String>()

        fun walk(node: String) {
            for (c in tree.children[node].orEmpty()) {
                if (capped) return
                if (!seen.add(c)) continue
                if (budget-- <= 0) { capped = true; return }
                if (meta[c]?.title != null && canSee(c)) n++
                walk(c)
            }
        }
        walk(pid)
        return n to capped
    }
}
