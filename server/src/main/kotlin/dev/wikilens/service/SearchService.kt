package dev.wikilens.service

/*
 * `api/` 에서 분리했다. 거기엔 HTTP 표면(`Controller`·`Dto`)만 남는다 —
 * 한 패키지가 "라우팅"과 "무엇을 하는가"를 함께 갖고 있으면, 검색 랭킹을 고치려는
 * 사람과 엔드포인트를 추가하려는 사람이 같은 자리를 연다.
 */

import dev.wikilens.api.SearchHit
import dev.wikilens.api.SearchRequest
import dev.wikilens.api.SearchResponse

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

        // **항과 검색을 한 번에 받는다.** 따로 부르면 그 사이 재색인이 끝났을 때
        // 항은 옛 분석기 것이고 결과는 새 색인 것이 된다 — 그 항이 학습 포스팅의
        // 키라서, 같은 질의가 그 순간에만 다른 키로 기록된다.
        val analyzed = index.analyzeAndSearch(req.query, tokens, req.limit * 3)
        val terms = analyzed.terms

        // 권한 토큰이 없으면 어휘 결과도 힌트도 내지 않는다.
        if (tokens.isEmpty() || terms.isEmpty()) {
            return SearchResponse(req.query, terms, 0, 0, emptyList())
        }

        val lexical = analyzed.hits

        // Lucene 점수를 [0,1]로 정규화해 EB 사전분포로 넘긴다.
        val top = lexical.firstOrNull()?.score?.toDouble()?.takeIf { it > 0.0 } ?: 1.0
        val priors = lexical.associate { it.id to (it.score / top).coerceIn(0.0, 1.0) }

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

        // 어휘 결과에 없는 후보(학습 힌트로만 발견된 페이지)는 메타데이터 캐시에서 채운다.
        // 예전엔 여기서 조용히 버려져 `source="learned"` 가 도달 불가능한 분기였다 —
        // 어휘 검색이 못 찾는 문서를 찾아주는 것이 학습 레이어의 존재 이유인데 그게 죽어 있었다.
        // take 는 필터 **뒤에** 와야 한다. 앞에 두면 버려질 후보가 limit 슬롯을 먹는다.
        val hits = acc.entries
            .sortedByDescending { it.value.score }
            .mapNotNull { (pid, a) ->
                val title: String
                val space: String
                val scored = meta[pid]
                if (scored != null) {
                    title = scored.title; space = scored.space
                } else {
                    val pm = index.metaOf(pid) ?: return@mapNotNull null  // 색인에도 없으면 폐기된 ID
                    title = pm.title; space = pm.space
                }
                SearchHit(pid, title, space, a.score, a.source, a.rel)
            }
            .take(req.limit)

        return SearchResponse(req.query, terms, lexical.size, hints.size, hits)
    }
}
