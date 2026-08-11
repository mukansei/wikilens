package io.wikilens.index

data class IndexedPage(
    val id: String,
    val title: String,
    val space: String,
    val path: String,
    val body: String,
    val anchors: List<String>,
    val aclTokens: List<String>,
    val ancestors: List<Ancestor> = emptyList(),
)
