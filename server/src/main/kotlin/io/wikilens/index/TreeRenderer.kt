package io.wikilens.index

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
         * 잘린 가지의 하위 개수를 셀 때 **가지 하나당** 상한. 요청 전체로 공유하면 첫 큰
         * 가지가 예산을 소진해 정확도가 트리 순서에 의존한다.
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
     * (보이는 하위 개수, 예산 초과로 어림했는지). **보이는** 것만 센다 — 개수가 숨긴
     * 문서의 존재를 새면 안 된다.
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
