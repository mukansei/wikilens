package dev.wikilens.api


data class SearchResponse(
    val query: String,
    val terms: List<String>,
    val lexicalCandidates: Int,
    val learnedHints: Int,
    val hits: List<SearchHit>,
)
