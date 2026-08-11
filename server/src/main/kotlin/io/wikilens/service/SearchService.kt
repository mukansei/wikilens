package io.wikilens.service

import io.wikilens.api.SearchHit
import io.wikilens.api.SearchRequest
import io.wikilens.api.SearchResponse

import io.wikilens.acl.AclRegistry
import io.wikilens.index.LuceneIndex
import io.wikilens.learn.TrajectoryStore
import org.springframework.stereotype.Service

/**
 * 어휘 랭킹(Lucene) + 학습 힌트(궤적) 융합.
 *
 * `api/` 에서 분리한 이유는 한 패키지가 "라우팅" 과 "무엇을 하는가" 를 함께 가지면 랭킹을
 * 고치려는 사람과 엔드포인트를 추가하려는 사람이 같은 자리를 열어서다.
 *
 * 학습 힌트는 순위가 아니라 **신뢰도로 가중**한다 — EB 하한은 이미 확률이라 순위로
 * 뭉개면 보정된 정보를 버린다.
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

        /** 최대 결과 수. `grep` 은 응답 크기 때문이고 **여기는 궤적 로그 때문**이다. */
        const val MAX_LIMIT = 100

        /**
         * 질의 길이 상한. 분석된 항이 `Trajectory.keywords` 로 **궤적 로그에 영구히**
         * 들어가므로 한 요청이 무한정 적어 넣을 수 있으면 안 된다. `grep` 의
         * `MAX_PATTERN` 과 같은 자리다.
         */
        const val MAX_QUERY = 500
    }

    fun search(req: SearchRequest): SearchResponse {
        // **상한이 없으면 셋이 깨진다**(전부 실측): `limit <= 0` 은 Lucene 예외로 HTTP 500,
        // 큰 값은 `limit * 3` 오버플로우로 또 500, 그리고 제일 나쁜 것 — **서빙한 힌트가
        // 궤적 로그에 영구히 남으므로** 한 요청이 수천 개를 적어 넣으면 안 된다.
        // 메모리는 Lucene 이 `maxDoc` 으로 죄어 안 터진다(limit 300만 → 3,941건 268ms).
        val limit = req.limit.coerceIn(1, MAX_LIMIT)
        // 자르지 않고 거부한다 — 자르면 사용자가 친 것과 다른 질의의 답을 주면서
        // 그 사실을 안 알린다. `grep` 이 패턴에 대해 하는 것과 같다.
        if (req.query.length > MAX_QUERY) {
            return SearchResponse(req.query.take(MAX_QUERY), emptyList(), 0, 0, emptyList(),
                                  error = "질의가 너무 깁니다 (최대 $MAX_QUERY 자)")
        }
        val tokens = acl.tokensFor(req.userKey)

        // **항과 검색을 한 번에 받는다** — 따로 부르면 그 사이 재색인이 끝났을 때 항은
        // 옛 분석기 것이고 결과는 새 색인 것이 된다. 그 항이 학습 포스팅의 키다.
        val analyzed = index.analyzeAndSearch(req.query, tokens, limit * 3)
        val terms = analyzed.terms

        // 권한 토큰이 없으면 어휘 결과도 힌트도 내지 않는다.
        if (tokens.isEmpty() || terms.isEmpty()) {
            return SearchResponse(req.query, terms, 0, 0, emptyList())
        }

        val lexical = analyzed.hits

        // Lucene 점수를 [0,1]로 정규화해 EB 사전분포로 넘긴다.
        val top = lexical.firstOrNull()?.score?.toDouble()?.takeIf { it > 0.0 } ?: 1.0
        val priors = lexical.associate { it.id to (it.score / top).coerceIn(0.0, 1.0) }

        // **서빙 못 할 후보는 자르기 전에 거른다.** `take` 뒤에 거르면 버려질 후보가
        // limit 슬롯을 먹어, 서빙 가능한 힌트가 아래에 있어도 안 나온다 — 같은 술어를
        // 자리만 바꿔 세 번 놓쳤다(`CLAUDE.md` 조용히 실패 8·22번).
        //
        //   - **권한** — 실제로 겪은 실패. 권한이 좁으면 힌트가 통째로 0 이 됐다.
        //   - **존재** — 포스팅은 한 번도 지워지지 않는다(궤적 로그가 정본이고
        //     append-only). 단 **도달 경로는 아직 확인되지 않았다**: `VaultReader.read` 가
        //     `acl.replacePages` 로 `retainAll` 하므로 삭제 방향은 권한 술어가 이미
        //     거른다(실측). 남는 자리는 `reload()` 의 `replacePages` → `rebuild` 사이
        //     창뿐이다. "고친 버그" 가 아니라 아직 안 일어난 것을 막는 것으로 읽을 것.
        //
        // **이 술어가 유일한 권한 관문이다.** 아래 융합 루프에는 재확인이 없다 —
        // `canSee(userKey, p)` 는 정의상 `canSee(tokensFor(userKey), p)` 이고, 이 요청은
        // 위에서 `tokens` 를 한 번 잡아 어휘 검색까지 그것으로 했다. 재확인은 **그 안에서
        // 유일하게 토큰을 다시 읽던 자리**라, 없앤 쪽이 요청 내 일관성이 높다(권한이 요청
        // 도중 바뀌어도 한 요청은 한 권한 스냅샷으로 답한다).
        //
        // **옮기거나 지우지 말 것** — `SearchServiceTest` 의 "권한 없는 페이지는 학습
        // 힌트로도 새지 않는다" 가 이것 하나에 걸려 있다(빼면 빨개진다, 확인함).
        val hints = store.hints(terms, priors, limit) { pid ->
            acl.canSee(tokens, pid) && index.metaOf(pid) != null
        }

        data class Acc(var score: Double, var source: String, var rel: Double?)
        val acc = LinkedHashMap<String, Acc>()
        val meta = lexical.associateBy { it.id }

        lexical.forEachIndexed { rank, s ->
            acc[s.id] = Acc(1.0 / (RRF_K + rank + 1), "lexical", null)
        }
        hints.forEachIndexed { rank, h ->
            val boost = LEARNED_WEIGHT * h.reliability / (RRF_K + rank + 1)
            val cur = acc[h.pageId]
            if (cur != null) {
                cur.score += boost; cur.source = "both"; cur.rel = h.reliability
            } else {
                acc[h.pageId] = Acc(boost, "learned", h.reliability)
            }
        }

        // 학습 힌트로만 발견된 페이지는 메타 캐시에서 채운다. 여기서 버리면
        // `source="learned"` 가 도달 불가능한 분기가 되는데, **어휘 검색이 못 찾는 문서를
        // 찾아주는 것이 학습 레이어의 존재 이유다.** `take` 는 필터 **뒤에** 와야 한다.
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
            .take(limit)

        return SearchResponse(req.query, terms, lexical.size, hints.size, hits)
    }
}
