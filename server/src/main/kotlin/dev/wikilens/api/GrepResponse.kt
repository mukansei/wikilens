package dev.wikilens.api


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
