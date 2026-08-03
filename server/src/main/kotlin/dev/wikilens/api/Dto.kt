package dev.wikilens.api

data class SearchRequest(
    val query: String,
    /** 요청자 식별자. ACL 필터의 입력. 없으면 결과가 비어야 한다. */
    val userKey: String? = null,
    /** MCP 프록시 프로세스 하나 = 세션 하나. 궤적 조립의 키. */
    val sessionId: String? = null,
    val limit: Int = 8,
)

data class SearchHit(
    val pageId: String,
    val title: String,
    val space: String,
    val score: Double,
    /** "lexical" | "learned" | "both" */
    val source: String,
    val reliability: Double? = null,
)

data class SearchResponse(
    val query: String,
    val terms: List<String>,
    val lexicalCandidates: Int,
    val learnedHints: Int,
    val hits: List<SearchHit>,
)

data class ReadRequest(
    val pageId: String,
    val userKey: String? = null,
    val sessionId: String? = null,
)

data class ReadResponse(
    val pageId: String,
    val title: String,
    val space: String,
    val markdown: String,
)

data class GrepRequest(
    val pattern: String,
    val userKey: String? = null,
    val sessionId: String? = null,
    val limit: Int = 40,
    /** 리터럴 매칭이 기본. 정규식은 명시적으로 켠다. */
    val regex: Boolean = false,
)

data class GrepMatch(
    val pageId: String,
    val title: String,
    val line: Int,
    val text: String,
)

data class GrepResponse(
    val pattern: String,
    val scanned: Int,
    val matches: List<GrepMatch>,
    val truncated: Boolean,
)

data class SessionEndRequest(val sessionId: String)
