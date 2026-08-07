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
