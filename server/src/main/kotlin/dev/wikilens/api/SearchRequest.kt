package dev.wikilens.api

/** 검색 요청. 와이어 포맷 전반은 `Dto.kt` 참고. */
data class SearchRequest(
    val query: String,
    /** 요청자 식별자. ACL 필터의 입력. 없으면 결과가 비어야 한다. */
    val userKey: String? = null,
    /** MCP 프록시 프로세스 하나 = 세션 하나. 궤적 조립의 키. */
    val sessionId: String? = null,
    val limit: Int = 8,
)
