package io.wikilens.api

import io.wikilens.acl.AclRegistry
import io.wikilens.index.LuceneIndex
import io.wikilens.learn.FileTrajectorySink
import io.wikilens.learn.TrajectoryStore
import io.wikilens.service.ContentService
import io.wikilens.service.IndexingService
import io.wikilens.service.SearchService
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.web.bind.annotation.*
import org.springframework.web.server.ResponseStatusException

/**
 * 서버판 HTTP 표면.
 *
 * 클라이언트에 볼트를 배포하지 않는다 — 배포된 사본은 회수할 수 없어 권한 취소가
 * 불가능해진다. 그래서 관측 훅도 필요 없다: 읽기가 서버를 거치므로 서버가 궤적을 직접
 * 본다. `sessionId` 는 MCP 프록시 프로세스 하나당 하나이고 그것이 세션 경계다.
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
        //
        // **거부된 질의는 관측하지 않는다** — 검색이 아예 안 돌았으니 셀 것이 없다.
        // 관측하면 세션 객체가 생기고 `sinceStart` 가 클라이언트 오류로 오염된다.
        // (결과 0건과는 다르다 — 그건 진짜 시도라 일부러 센다.)
        if (res.error != null) return res
        req.sessionId?.let { sid ->
            // 권한 **범위**를 남긴다 — 신원이 아니다(`AclRegistry.scopeOf`).
            store.onQuery(sid, req.query, res.terms, acl.scopeOf(req.userKey))
            // 서빙한 힌트도 넘긴다. 끝까지 안 읽히면 틀린 힌트라 미스로 되돌아간다.
            store.onServed(
                sid,
                hinted = res.hits.filter { it.source != "lexical" }.map { it.pageId },
                ranked = res.hits.map { it.pageId },   // 몇 번째를 골랐는지가 신호다
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

    /**
     * 모델이 답을 정했을 때. **안 불러도 동작이 같다** — `dest` 가 `reads.last()` 로
     * 폴백한다. 그래서 응답이 `accepted` 를 낸다: 읽지 않은 페이지나 없는 세션은
     * 조용히 버려지는데, 클라이언트가 그 사실을 알 방법이 이것뿐이다.
     *
     * 오류로 만들지 않는 이유는 이 호출이 **부가 신호**이기 때문이다 — 실패해도
     * 검색·읽기는 이미 끝났고, 4xx 를 던지면 모델이 그걸 고치려 든다.
     */
    @PostMapping("/answer")
    fun answer(@RequestBody req: AnswerRequest): Map<String, Any> {
        return mapOf("accepted" to store.onAnswer(req.sessionId, req.pageId))
    }

    /**
     * 원문 스캔. **궤적 관측 대상이 아니다** — `search`·`read` 와 달리 `store` 를
     * 부르지 않는다.
     *
     * 학습 포스팅의 키가 **분석된 항**(Nori 토큰)인데 grep 패턴은 정규식이거나 토큰
     * 경계와 무관한 부분문자열이다(`cow` 로 `coway` 를 찾는 것이 grep 의 존재 이유다).
     * 같은 포스팅에 넣으면 키가 오염돼 어휘 질의의 학습까지 흐려진다. `tree` 를 뺀 것과
     * 같은 이유다 — 그쪽은 분류 신호라서, 이쪽은 어휘 단위가 달라서.
     *
     * **그래서 grep 으로 찾아 읽은 것은 학습에 안 들어간다.** `onRead` 는 열린 스팬이
     * 없으면 버리므로(질의 없는 읽기는 궤적이 아니다) 조용히 사라진다 — 의도한 손실이다.
     * 되돌리려면 항 단위 포스팅과 어떻게 섞을지부터 정해야 한다.
     */
    @PostMapping("/grep")
    fun grep(@RequestBody req: GrepRequest): GrepResponse =
        content.grep(req.pattern, req.userKey, req.limit, req.regex)

    /** 계층 목차. 어휘가 아니라 분류 신호라 궤적 관측 대상이 아니다. */
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
     * 수동 재색인. **적재 로직은 [IndexingService] 하나뿐이다** — 따로 두었더니 기동 쪽에만
     * 넣은 "빈 볼트면 건너뛴다" 방어가 여기엔 없었다(실측: 색인 2,383건이 0으로 지워짐).
     */
    @PostMapping("/admin/reindex")
    fun reindex(): Map<String, Any> {
        val r = indexing.reload()
        return mapOf("indexed" to r.indexed, "aclPages" to r.aclPages, "skipped" to r.skipped)
    }

    @PostMapping("/admin/acl/user")
    fun putUser(
        @RequestParam userKey: String,
        @RequestBody tokens: List<String>,
    ): Map<String, Any> {
        acl.putUser(userKey, tokens)
        return mapOf("userKey" to userKey, "tokens" to tokens.size)
    }

    @PostMapping("/admin/sweep")
    fun sweep(): Map<String, Any> {
        return mapOf("finalized" to store.sweep())
    }

    @GetMapping("/stats")
    fun stats(): Map<String, Any?> =
        store.stats() + mapOf(
            "indexedDocs" to index.docCount,
            "aclPages" to acl.pageCount(),
            "aclUsers" to acl.userCount(),
            // **꺼져 있으면 등록 없이 전원이 전 문서를 본다** — 겉으로는 정상이라 밖에서
            // 못 보면 아무도 모른다.
            "aclEnforced" to acl.isEnforced(),
            // **등록 여부만으로는 부족하다** — 토큰이 안 겹치면 등록이 있어도 전원이
            // 빈손이고 그 상태가 "문서가 없다" 와 구별되지 않는다. 양쪽 토큰을 함께 실어
            // 무엇과 무엇이 안 맞는지 보이게 한다.
            "aclTokenOverlap" to acl.tokenOverlap(),
            "aclUserTokens" to acl.userTokens().sorted().take(TOKEN_SAMPLE),
            "aclPageTokens" to acl.pageTokens().sorted().take(TOKEN_SAMPLE),
            // 둘이 **다를 때만** 진단 가치가 있다 — 재색인이 안 된 상태이고, 실질적으로는
            // 볼트를 못 읽어 기동 적재가 건너뛰어진 경우다.
            "analyzer" to index.activeKind.key,          // 질의에 실제로 쓰이는 것 (= 색인 기록)
            "analyzerConfigured" to index.buildKind.key, // 이 프로세스의 설정
            // 스캔 경로가 둘이라 어느 쪽인지가 답의 근거가 된다.
            // `build` 가 문자 집합으로 뺀 문서 수. **0 이 아니면 볼트에는 있는데 서버
            // 에서는 없는 문서가 그만큼**이다 — 넷 다 못 찾는다.
            "droppedByScript" to indexing.droppedByScript,
            "grepEngine" to content.engineName,
            "grepEngineUsable" to content.engineUsable,
            // **append-only 라 줄지 않는다** — 크기와 재생 시간이 압축 시점을 알려준다.
            "trajectoryLog" to log.status(),
        )

    companion object {
        /** 진단에 실을 토큰 표본 수. 전부 실으면 스페이스가 많은 배포에서 응답이 커진다. */
        private const val TOKEN_SAMPLE = 20
    }

    @GetMapping("/health")
    fun health() = mapOf("ok" to true)
}
