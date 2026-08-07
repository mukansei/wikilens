package dev.wikilens.api

import dev.wikilens.acl.AclRegistry
import dev.wikilens.index.LuceneIndex
import dev.wikilens.learn.FileTrajectorySink
import dev.wikilens.learn.TrajectoryStore
import dev.wikilens.service.ContentService
import dev.wikilens.service.IndexingService
import dev.wikilens.service.SearchService
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.web.bind.annotation.*
import org.springframework.web.server.ResponseStatusException

/**
 * 서버판 API.
 *
 * 클라이언트에 볼트를 배포하지 않는다. 배포된 사본은 회수할 수 없어 권한 취소가
 * 불가능해지기 때문이다. 서버가 검색·읽기·grep 을 전부 서빙하고 매 요청마다
 * ACL 을 확인한다.
 *
 * 그래서 관측 훅이 필요 없다 — 읽기가 서버를 거치므로 서버가 궤적을 직접 본다.
 * `sessionId` 는 MCP 프록시 프로세스 하나당 하나이며, 그것이 곧 세션 경계다.
 */
@RestController
@RequestMapping("/api", produces = [MediaType.APPLICATION_JSON_VALUE])
class Controller(
    private val searchService: SearchService,
    private val content: ContentService,
    private val store: TrajectoryStore,
    private val log: FileTrajectorySink,
    private val indexing: IndexingService,
    private val index: LuceneIndex,
    private val acl: AclRegistry,
) {

    @PostMapping("/search")
    fun search(@RequestBody req: SearchRequest): SearchResponse {
        val res = searchService.search(req)
        // 질의 관측. 별도 훅 없이 도구 호출 자체가 궤적이 된다.
        req.sessionId?.let { sid ->
            // 권한 **범위** 를 함께 남긴다 — 신원이 아니다(`AclRegistry.scopeOf`).
            // 학습이 권한 폭에 오염되는 문제를 나중에 다루려면 로그에 있어야 하고,
            // 나중에 넣으면 그전 궤적에는 영영 없다.
            store.onQuery(sid, req.query, res.terms, acl.scopeOf(req.userKey))
            // 학습 레이어가 서빙한 힌트를 함께 넘긴다. 세션이 끝까지 안 읽으면 그
            // 힌트가 틀린 것이므로 미스로 되돌아간다 — `pWrong` 이 재려던 값이다.
            store.onServed(
                sid,
                hinted = res.hits.filter { it.source != "lexical" }.map { it.pageId },
                // 전체를 순위 순으로 넘긴다 — 사용자가 몇 번째를 골랐는지가 신호다.
                ranked = res.hits.map { it.pageId },
            )
        }
        return res
    }

    @PostMapping("/read")
    fun read(@RequestBody req: ReadRequest): ReadResponse {
        val r = content.read(req.pageId, req.userKey)
            // 권한이 없으면 404 다. 403 은 "존재하지만 못 본다"를 알려주므로 유출이다.
            ?: throw ResponseStatusException(HttpStatus.NOT_FOUND)
        req.sessionId?.let { store.onRead(it, req.pageId) }
        return r
    }

    @PostMapping("/grep")
    fun grep(@RequestBody req: GrepRequest): GrepResponse =
        content.grep(req.pattern, req.userKey, req.limit, req.regex)

    /**
     * 계층 목차(로컬판 TREE.md의 서버판). 앵커/학습과 분리된 별도 경로라
     * 궤적 관측 대상이 아니다 — 어휘 신호가 아니라 분류 신호이기 때문이다.
     */
    @PostMapping("/tree")
    fun tree(@RequestBody req: TreeRequest): TreeResponse {
        val rendered = index.renderTree({ pid -> acl.canSee(req.userKey, pid) }, req.rootId, req.depth)
        return TreeResponse(rendered.markdown, rendered.truncated)
    }

    /** MCP 프록시가 종료 시 호출한다. 놓치면 `SessionSweeper` 가 주기적으로 거둔다. */
    @PostMapping("/session/end")
    fun endSession(@RequestBody req: SessionEndRequest): Map<String, Any> =
        mapOf("finalized" to store.onEnd(req.sessionId))

    /**
     * 수동 재색인. **적재 로직은 [IndexingService] 하나뿐이다** — 기동 적재와 같은 코드다.
     * 예전엔 여기서 `vault.read` + `index.rebuild` 를 직접 불러서, 기동 쪽에만 넣은
     * "빈 볼트면 건너뛴다" 방어가 이 경로에는 없었다(실측: 색인 2,383건이 0으로 지워짐).
     */
    @PostMapping("/admin/reindex")
    fun reindex(): Map<String, Any> {
        val r = indexing.reload()
        return mapOf("indexed" to r.indexed, "aclPages" to r.aclPages, "skipped" to r.skipped)
    }

    @PostMapping("/admin/acl/user")
    fun putUser(@RequestParam userKey: String, @RequestBody tokens: List<String>): Map<String, Any> {
        acl.putUser(userKey, tokens)
        return mapOf("userKey" to userKey, "tokens" to tokens.size)
    }

    @PostMapping("/admin/sweep")
    fun sweep(): Map<String, Any> = mapOf("finalized" to store.sweep())

    @GetMapping("/stats")
    fun stats(): Map<String, Any?> =
        store.stats() + mapOf(
            "indexedDocs" to index.docCount,
            "aclPages" to acl.pageCount(),
            "aclUsers" to acl.userCount(),
            // 서버는 알고 있는데 밖으로 안 내보내던 값이다. 둘이 **다를 때만** 진단
            // 가치가 있다 — 재색인이 안 된 상태라는 뜻이고, 실질적으로는 볼트를 못 읽어
            // 기동 적재가 건너뛰어진 경우다. 그전에는 기동 로그를 뒤져야만 알 수 있었다.
            "analyzer" to index.activeKind.key,          // 질의에 실제로 쓰이는 것 (= 색인 기록)
            "analyzerConfigured" to index.buildKind.key, // 이 프로세스의 설정
            // 궤적 로그 상태. **append-only 라 줄지 않는다** — 크기와 재생 시간이
            // 조용히 늘어나는 자리이고, 그 둘이 압축을 설계할 시점을 알려준다.
            "trajectoryLog" to log.status(),
        )

    @GetMapping("/health")
    fun health() = mapOf("ok" to true)
}
