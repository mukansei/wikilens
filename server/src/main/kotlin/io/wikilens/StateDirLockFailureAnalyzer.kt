package io.wikilens

import io.wikilens.learn.StateDirLock
import org.springframework.boot.diagnostics.AbstractFailureAnalyzer
import org.springframework.boot.diagnostics.FailureAnalysis

/**
 * 락 충돌을 **읽을 수 있는 형태**로 낸다.
 *
 * 그냥 던지면 Spring 이 빈 생성 실패로 감싸서, 같은 메시지가 스택 트레이스 세 겹 속에
 * 세 번 나온다(실측). 이 저장소는 **기동 로그를 진단에 쓰는 구조**라 — `--enable-native-access`
 * 를 켜는 이유도 무해한 경고가 그 줄을 가리지 않게 하려는 것이다 — 정작 기동을 막는
 * 오류가 스택에 파묻히면 앞뒤가 안 맞는다.
 *
 * `FailureAnalyzer` 는 `META-INF/spring.factories` 로 등록한다.
 */
class StateDirLockFailureAnalyzer : AbstractFailureAnalyzer<StateDirLock.AlreadyRunning>() {
    override fun analyze(rootFailure: Throwable, cause: StateDirLock.AlreadyRunning) =
        FailureAnalysis(
            cause.message,
            "이미 떠 있는 서버를 끄거나, 이 인스턴스에 --wikilens.state-dir 로 다른 " +
                "디렉터리를 주세요. 두 프로세스가 같은 궤적 로그를 쓰면 각자 절반만 " +
                "아는 상태가 되고, 그 갈림은 재기동 전까지 드러나지 않습니다.",
            cause,
        )
}
