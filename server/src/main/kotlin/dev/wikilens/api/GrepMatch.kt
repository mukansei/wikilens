package dev.wikilens.api


data class GrepMatch(
    val pageId: String,
    val title: String,
    val line: Int,
    val text: String,
)
