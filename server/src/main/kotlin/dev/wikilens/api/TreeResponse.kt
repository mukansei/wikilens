package dev.wikilens.api

/**
 * 계층 목차. 로컬판 TREE.md와 같은 신호를 서버판에도 노출한다 —
 * 앵커 색인이 못 잡는 고아 문서(링크는 안 걸렸지만 페이지 트리엔 있는 것)를
 * 위에서부터 내려가며 찾는 용도. 앵커/학습과는 완전히 분리된 경로다.
 *
 * [truncated] 는 depth 제한으로 잘린 가지가 있었는지 — markdown 본문 속
 * "… (+N개 하위, rootId=...)" 문구를 파싱하지 않고도 확인할 수 있는 구조화된
 * 신호다 (GrepResponse.truncated 와 같은 패턴).
 */
data class TreeResponse(val markdown: String, val truncated: Boolean = false)
