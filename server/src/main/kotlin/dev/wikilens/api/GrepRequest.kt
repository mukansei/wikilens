package dev.wikilens.api


data class GrepRequest(
    val pattern: String,
    val userKey: String? = null,
    val sessionId: String? = null,
    val limit: Int = 40,
    /** 리터럴 매칭이 기본. 정규식은 명시적으로 켠다. */
    val regex: Boolean = false,
)
