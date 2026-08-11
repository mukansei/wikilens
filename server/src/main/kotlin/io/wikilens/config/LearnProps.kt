package io.wikilens.config

/**
 * `wikilens.learn.*`.
 *
 * **`sweep-interval-millis` 는 여기 없다** — `@Scheduled` 는 애노테이션 인자라 빈을 못
 * 읽고 `SessionSweeper` 가 프로퍼티 문자열로 직접 받는다. 필드로도 두면 아무도 안 읽는
 * 사본이 하나 생긴다. 기본값은 그 애노테이션과 `application.yml` 에 있다.
 */
data class LearnProps(
    /** EB 하한이 이 값 미만이면 힌트를 서빙하지 않는다. */
    val serveThreshold: Double = 0.45,
    /** 앞 질의와 키워드가 이만큼 겹치면 앞 시도를 실패로 본다. */
    val reformulationOverlap: Double = 0.5,
    /** 이만큼 조용하면 세션이 끝난 것으로 본다. */
    val sessionIdleMillis: Long = 1_800_000,
)
