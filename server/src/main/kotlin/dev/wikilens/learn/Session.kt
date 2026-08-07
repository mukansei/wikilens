package dev.wikilens.learn

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger


class Session(val id: String) {
    val spans = ArrayList<QuerySpan>()
    @Volatile var lastTouch: Long = System.currentTimeMillis()
    val current: QuerySpan? get() = spans.lastOrNull()
}
