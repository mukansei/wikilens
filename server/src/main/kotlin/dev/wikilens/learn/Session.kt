package dev.wikilens.learn

class Session(val id: String) {
    val spans = ArrayList<QuerySpan>()
    /** 권한 범위 식별자. 세션 단위로 고정이라 첫 질의에서 한 번만 받는다. */
    @Volatile var scope: String = ""
    @Volatile var lastTouch: Long = System.currentTimeMillis()
    val current: QuerySpan? get() = spans.lastOrNull()
}
