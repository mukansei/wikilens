package io.wikilens.index

/** 색인된 문서의 메타데이터. 본문은 담지 않는다 — 콘텐츠는 미러에서 읽는다. */
data class PageMeta(val id: String, val title: String, val space: String)
