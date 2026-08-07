package dev.wikilens.api


data class SearchHit(
    val pageId: String,
    val title: String,
    val space: String,
    val score: Double,
    /** "lexical" | "learned" | "both" */
    val source: String,
    val reliability: Double? = null,
)
