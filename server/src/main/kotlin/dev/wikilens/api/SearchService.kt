package dev.wikilens.api

import dev.wikilens.acl.AclRegistry
import dev.wikilens.index.LuceneIndex
import dev.wikilens.learn.TrajectoryStore
import org.springframework.stereotype.Service

/**
 * 어휘 랭킹(Lucene) + 학습 힌트(궤적) 융합.
 *
 * 서버가 질의를 토큰화한다. 클라이언트는 원문만 보낸다 — 토크나이저 정본이 하나여야
 * 항이 어긋나지 않는다. 양쪽이 각자 토큰화했다가 조용히 0건이 되는 버그를 겪었다.
 *
 * 학습 힌트는 순위가 아니라 **신뢰도로 가중**한다. EB 하한은 이미 확률이므로
 * 순위로 뭉개면 보정된 정보를 버리게 된다.
 */
@Service
class SearchService(
    private val index: LuceneIndex,
    private val store: TrajectoryStore,
    private val acl: AclRegistry,
) {
    companion object {
        private const val RRF_K = 60.0
        private const val LEARNED_WEIGHT = 1.6
    }

    fun search(req: SearchRequest): SearchResponse {
        val tokens = acl.tokensFor(req.userKey)
        val terms = index.analyze(req.query)

        // 권한 토큰이 없으면 어휘 결과도 힌트도 내지 않는다.
        if (tokens.isEmpty() || terms.isEmpty()) {
            return SearchResponse(req.query, terms, 0, 0, emptyList())
        }

        val lexical = index.search(req.query, tokens, req.limit * 3)

        // Lucene 점수를 [0,1]로 정규화해 EB 사전분포로 넘긴다.
        val top = lexical.firstOrNull()?.score?.toDouble()?.takeIf { it > 0.0 } ?: 1.0
        val priors = lexical.associate { it.id to (it.score / top).toDouble().coerceIn(0.0, 1.0) }

        val hints = store.hints(terms, priors, req.limit)

        data class Acc(var score: Double, var source: String, var rel: Double?)
        val acc = LinkedHashMap<String, Acc>()
        val meta = lexical.associateBy { it.id }

        lexical.forEachIndexed { rank, s ->
            acc[s.id] = Acc(1.0 / (RRF_K + rank + 1), "lexical", null)
        }
        hints.forEachIndexed { rank, h ->
            // 힌트 대상도 ACL 을 통과해야 한다. 학습 레이어는 권한을 모르므로
            // 여기서 반드시 다시 거른다 — 이중 방어선이다.
            if (!acl.canSee(req.userKey, h.pageId)) return@forEachIndexed
            val boost = LEARNED_WEIGHT * h.reliability / (RRF_K + rank + 1)
            val cur = acc[h.pageId]
            if (cur != null) {
                cur.score += boost; cur.source = "both"; cur.rel = h.reliability
            } else {
                acc[h.pageId] = Acc(boost, "learned", h.reliability)
            }
        }

        val hits = acc.entries
            .sortedByDescending { it.value.score }
            .take(req.limit)
            .mapNotNull { (pid, a) ->
                val m = meta[pid] ?: return@mapNotNull null   // 색인에 없으면 경로를 모른다
                SearchHit(pid, m.title, m.space, a.score, a.source, a.rel)
            }

        return SearchResponse(req.query, terms, lexical.size, hints.size, hits)
    }
}
