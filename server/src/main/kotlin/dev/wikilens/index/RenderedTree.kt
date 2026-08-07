package dev.wikilens.index

/**
 * [TreeRenderer] 결과. [truncated] 는 깊이 제한으로 잘린 가지가 있었는지 —
 * 마크다운 본문의 문구를 파싱하지 않고도 논-LLM 소비자가 확인할 수 있는 구조화된
 * 신호다 (`GrepResponse.truncated` 와 같은 패턴).
 */
data class RenderedTree(val markdown: String, val truncated: Boolean)
