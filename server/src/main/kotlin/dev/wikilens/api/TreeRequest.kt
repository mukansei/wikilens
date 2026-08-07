package dev.wikilens.api


data class TreeRequest(
    /** 요청자 식별자. ACL 필터의 입력. 없으면 빈 트리가 나와야 한다. */
    val userKey: String? = null,
    /** 지정하면 이 페이지를 루트로 한 서브트리만. 권한이 없으면 빈 트리 (존재 비노출). */
    val rootId: String? = null,
    /** 최대 깊이. 0 = 무제한. 잘린 가지는 rootId로 파고들 수 있게 요약 라인이 남는다. */
    val depth: Int = 0,
)
