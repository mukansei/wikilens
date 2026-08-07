package dev.wikilens.api


data class ReadRequest(
    val pageId: String,
    val userKey: String? = null,
    val sessionId: String? = null,
)
