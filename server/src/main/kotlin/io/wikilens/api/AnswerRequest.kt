package io.wikilens.api

/**
 * 모델이 **답이라고 말한다.** `dest = reads.last()` 추정을 진술로 바꾼다.
 *
 * `userKey` 가 없는 유일한 요청이다 — 권한은 이미 `read` 에서 걸렸고(안 읽은 페이지는
 * 거부된다), 이 호출은 볼트를 새로 여는 것이 아니라 **열린 스팬에 표시를 다는 것**이다.
 *
 * 근거와 실측은 `docs/declared-answer-design.md`.
 */
data class AnswerRequest(
    val sessionId: String,
    val pageId: String,
)
