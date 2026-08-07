package dev.wikilens.index

/** 필드별 가중치. 앵커 > 제목 > 본문. */
object FieldBoost {
    const val ANCHOR = 4.0f
    const val TITLE = 3.0f
    const val BODY = 1.0f
}
