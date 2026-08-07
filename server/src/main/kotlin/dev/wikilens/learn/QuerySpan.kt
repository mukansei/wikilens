package dev.wikilens.learn

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/** 한 질의와 그 뒤에 이어진 읽기들. */
class QuerySpan(val keywords: List<String>, val kind: QueryKind) {
    val reads = ArrayList<String>()
    /** 이 질의에 학습 레이어가 서빙한 힌트 페이지들 (`onServed` 가 채운다). */
    var served: List<String> = emptyList()
    /** 서빙한 **전체** 결과를 순위 순으로. `dest` 의 순위를 알아내는 데 쓴다. */
    var ranked: List<String> = emptyList()
    /** on_query와 on_end가 같은 스팬을 두 번 확정하는 것을 막는다. */
    var finalized = false

    fun addRead(pageId: String) {
        // 같은 페이지를 연속으로 읽으면 한 번으로 센다
        if (reads.isEmpty() || reads.last() != pageId) reads.add(pageId)
    }
}
