package dev.wikilens.api

/**
 * HTTP 와이어 포맷. **필드 이름이 곧 JSON 키**이므로 바꾸면 MCP 프록시가 함께 깨진다
 * (`plugin/client/mcp/wikilens_mcp.py`).
 *
 * `service/` 가 이 타입을 **그대로 반환한다** — 서비스 전용 타입을 따로 두고 매핑하는
 * 층은 넣지 않았다. 타입이 넷뿐이라 매핑이 얻는 것 없이 코드만 두 배가 되고,
 * 이 서비스들은 HTTP 말고 다른 소비자가 없다. 소비자가 생기면 그때 나눌 자리다.
 */
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
    /**
     * 패턴 자체가 거부된 이유. 정상 검색에서는 null 이다.
     *
     * 없으면 "쓸 수 없는 문법" 과 "정말 일치가 없음" 이 **똑같이 0건**으로 보인다.
     * ACL 과 달리 정규식 문법 오류는 코퍼스에 대해 아무것도 알려주지 않는다.
     */
    val error: String? = null,
)

data class SessionEndRequest(val sessionId: String)

data class TreeRequest(
    /** 요청자 식별자. ACL 필터의 입력. 없으면 빈 트리가 나와야 한다. */
    val userKey: String? = null,
    /** 지정하면 이 페이지를 루트로 한 서브트리만. 권한이 없으면 빈 트리 (존재 비노출). */
    val rootId: String? = null,
    /** 최대 깊이. 0 = 무제한. 잘린 가지는 rootId로 파고들 수 있게 요약 라인이 남는다. */
    val depth: Int = 0,
)

/**
 * 계층 목차. 로컬판 TREE.md와 같은 신호를 서버판에도 노출한다 —
 * 앵커 색인이 못 잡는 고아 문서(링크는 안 걸렸지만 페이지 트리엔 있는 것)를
 * 위에서부터 내려가며 찾는 용도. 앵커/학습과는 완전히 분리된 경로다.
 *
 * [truncated] 는 depth 제한으로 잘린 가지가 있었는지 — markdown 본문 속
 * "… (+N개 하위, rootId=...)" 문구를 파싱하지 않고도 확인할 수 있는 구조화된
 * 신호다 (GrepResponse.truncated 와 같은 패턴).
 */
data class TreeResponse(val markdown: String, val truncated: Boolean = false)
