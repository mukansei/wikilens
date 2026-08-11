package io.wikilens.service

/** 적재 결과. 빈 볼트로 건너뛴 경우 [skipped] 가 참이고 개수는 **남아 있는 색인**의 것이다. */
data class LoadResult(val indexed: Int, val aclPages: Int, val skipped: Boolean = false)
