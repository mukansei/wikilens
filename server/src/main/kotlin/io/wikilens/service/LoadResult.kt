package io.wikilens.service

/**
 * 적재 결과. 빈 볼트로 건너뛴 경우 [skipped] 가 참이고 개수는 **남아 있는 색인**의 것이다.
 *
 * [droppedByScript] 는 문자 집합 필터가 뺀 수다 — 0 이 아니면 볼트에는 있는데 서버에서는
 * 없는 문서가 그만큼이라, 밖으로 내야 "문서가 없다" 와 구별된다(`ScriptFilter`).
 */
data class LoadResult(
    val indexed: Int,
    val aclPages: Int,
    val skipped: Boolean = false,
    val droppedByScript: Int = 0,
)
