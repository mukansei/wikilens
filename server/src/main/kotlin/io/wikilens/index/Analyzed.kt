package io.wikilens.index

/**
 * 한 스냅샷에서 나온 질의 항과 검색 결과.
 *
 * 둘이 같은 분석기에서 나왔음을 타입으로 보장한다 — 따로 부르면 재색인 사이에
 * 끼어 항과 색인이 어긋날 수 있다.
 */
data class Analyzed(val terms: List<String>, val hits: List<Scored>)
