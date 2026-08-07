package dev.wikilens.index

/**
 * 본문 분석기 선택지.
 *
 * **색인과 질의가 같은 분석기를 써야 한다.** 다르면 예외가 아니라 **조용히 0건**이 되고,
 * 그것이 이 프로젝트가 겪은 대표적 실패다(`CLAUDE.md` 조용히 실패하는 것들 1번).
 * 그래서 선택을 색인에 기록하고([LuceneIndex.ANALYZER_KEY]) 기동 시 대조한다.
 */
enum class AnalyzerKind(val key: String) {
    /**
     * 한국어. 교착어라 조사가 붙는다 — '로그인을/로그인은/로그인이'.
     * 형태소 분석 없이는 BM25 가 무너진다. 이것이 JVM 을 고른 유일하고 결정적인 이유다.
     *
     * 영문도 깨뜨리지 않는다 — 공백·구두점으로 자르고 소문자화한다(실측:
     * `OAuth 2.0 authorization code flow` → `[oauth, 2, 0, authorization, code, flow]`).
     * 다만 **어간을 안 줄인다.** 문서 `production servers` 에 질의 `production server` 가
     * 안 맞는다(실측: 굴절 쌍 5개 중 Nori 0 · English 5). 한국어 문서에 영문
     * 고유명사·식별자가 섞인 코퍼스에서는 그것들이 굴절하지 않으므로 문제가 안 된다.
     */
    KOREAN("korean"),

    /**
     * 영어. 어간 추출과 불용어 제거를 한다 — `the deploy to a server` 가 2토큰이 된다.
     * **영어가 주된 코퍼스일 때만 고를 것.** 한국어 본문에 쓰면 조사를 못 떼서
     * Nori 를 쓰는 이유가 그대로 사라진다 — 대칭인 실패다.
     */
    ENGLISH("english"),

    /**
     * 언어 규칙 없이 유니코드 단어 경계로만 자르고 소문자화한다.
     * 어느 언어에도 최선은 아니지만 **어느 언어도 망가뜨리지 않는다** —
     * 다국어가 섞였는데 주 언어를 못 고르겠을 때의 안전한 기본값이다.
     */
    STANDARD("standard");

    companion object {
        /** 모르는 이름은 거부한다 — 오타가 조용히 기본값으로 떨어지면 그게 0건의 원인이 된다. */
        fun of(key: String): AnalyzerKind =
            entries.firstOrNull { it.key.equals(key.trim(), ignoreCase = true) }
                ?: throw IllegalArgumentException(
                    "알 수 없는 분석기 '$key'. 가능한 값: ${entries.joinToString("·") { it.key }}"
                )
    }
}
