package dev.wikilens

import dev.wikilens.config.WikiLensProperties
import dev.wikilens.learn.TrajectoryStore
import org.slf4j.LoggerFactory
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component

/**
 * 버려진 세션을 주기적으로 거둔다.
 *
 * **이게 없으면 `sweep` 은 아무도 안 부른다.** `/api/admin/sweep` 은 있었지만 수동이고,
 * 세션 종료는 MCP 프록시의 `atexit` 에만 걸려 있어 프로세스가 SIGKILL 되거나 크래시하면
 * `onEnd` 가 영영 안 온다. 결과가 둘이었다:
 *
 *   - `sessions` 맵이 단조증가한다 (상주 서버에서 영구 누수)
 *   - 그 세션의 궤적이 **확정되지 않아 학습에 반영되지 않는다** — 사용자는 정상적으로
 *     검색하고 읽었는데 배운 게 없다. 조용한 쪽이 이쪽이라 더 나쁘다.
 *
 * `Controller` 의 주석이 "놓쳐도 sweep 이 처리한다" 라고 적고 있었는데, 그 문장을
 * 참으로 만드는 코드가 없었다.
 *
 * 스케줄링은 Spring 의 일이므로 여기 둔다 — `learn/` 에는 프레임워크 import 가
 * 들어가면 안 된다(`shared_contract.sh` 가 강제한다).
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
