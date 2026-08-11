package io.wikilens

import io.wikilens.config.WikiLensProperties
import io.wikilens.learn.TrajectoryStore
import org.slf4j.LoggerFactory
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component

/**
 * 버려진 세션을 주기적으로 거둔다.
 *
 * **이게 없으면 `sweep` 을 아무도 안 부른다.** 세션 종료는 MCP 프록시의 `atexit` 에만
 * 걸려 있어 SIGKILL·크래시면 `onEnd` 가 영영 안 온다. 맵 누수보다 **궤적 미확정이 더
 * 나쁘다** — 사용자는 정상적으로 검색·읽기를 했는데 배운 게 없고, 그쪽이 조용하다.
 *
 * 스케줄링은 Spring 의 일이라 여기 둔다 — `learn/` 에는 프레임워크 import 가 들어가면
 * 안 된다(계약이 강제한다).
 */
@Component
class SessionSweeper(
    private val store: TrajectoryStore,
    private val props: WikiLensProperties,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    @Scheduled(
        fixedDelayString = "\${wikilens.learn.sweep-interval-millis:300000}",
        initialDelayString = "\${wikilens.learn.sweep-interval-millis:300000}",
    )
    fun sweep() {
        val n = store.sweep(props.learn.sessionIdleMillis)
        if (n > 0) log.info("버려진 세션에서 궤적 {}건을 확정했습니다", n)
    }
}
