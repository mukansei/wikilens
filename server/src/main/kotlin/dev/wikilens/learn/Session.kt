package dev.wikilens.learn

class Session(val id: String) {
    val spans = ArrayList<QuerySpan>()
    @Volatile var lastTouch: Long = System.currentTimeMillis()
    val current: QuerySpan? get() = spans.lastOrNull()
}
