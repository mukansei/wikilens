package io.wikilens.api


data class SearchResponse(
    val query: String,
    val terms: List<String>,
    val lexicalCandidates: Int,
    val learnedHints: Int,
    val hits: List<SearchHit>,
    /**
     * 질의 자체가 거부된 이유. 정상 검색에서는 null 이다.
     *
     * `GrepResponse.error` 와 같은 모양이다 — 없으면 "쓸 수 없는 질의" 와 "정말 결과가
     * 없음" 이 똑같이 0건으로 보인다.
     */
    val error: String? = null,
)
