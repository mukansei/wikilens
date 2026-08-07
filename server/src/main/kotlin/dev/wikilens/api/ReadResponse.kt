package dev.wikilens.api


data class ReadResponse(
    val pageId: String,
    val title: String,
    val space: String,
    val markdown: String,
)
