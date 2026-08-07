package dev.wikilens.index

/** 부모 하나. 루트부터 직속 부모까지 순서대로 온다(Confluence ancestors 그대로). */
data class Ancestor(val id: String, val title: String)
