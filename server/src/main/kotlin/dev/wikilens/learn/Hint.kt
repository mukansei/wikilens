package dev.wikilens.learn

/**
 * 궤적 저장소. Spring도 Lucene도 참조하지 않는다.
 *
 * 서버가 보관하는 것:
 *     term -> pageId -> (hits, misses)
 *     trajectory(session, keywords, reads, dest, success)
 *
 * 색인은 별도 레이어(Lucene)에 있고, 이 레이어는 **관측과 신뢰도만** 다룬다.
 */

data class Hint(
    val pageId: String,
    val hits: Int,
    val misses: Int,
    val reliability: Double,
)
