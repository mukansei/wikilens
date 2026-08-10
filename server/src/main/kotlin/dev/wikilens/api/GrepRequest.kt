package dev.wikilens.api


/**
 * **`sessionId` 가 없다 — 일부러다.** `grep` 은 궤적 관측 대상이 아니다(이유는
 * [dev.wikilens.api.Controller.grep]). 필드만 있고 아무도 안 쓰면 "쓰는데 어딘가
 * 빠졌나" 로 읽히므로 `TreeRequest` 와 같이 아예 두지 않는다.
 */
data class GrepRequest(
    val pattern: String,
    val userKey: String? = null,
    val limit: Int = 40,
    /** 리터럴 매칭이 기본. 정규식은 명시적으로 켠다. */
    val regex: Boolean = false,
)
