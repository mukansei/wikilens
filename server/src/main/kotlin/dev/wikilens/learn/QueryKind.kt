package dev.wikilens.learn

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min

/**
 * 학습 레이어의 순수 로직. Spring도 Lucene도 참조하지 않는다.
 *
 * 이 파일이 의존성 없이 유지되는 이유: 알고리즘 핵심을 프레임워크 없이 컴파일·검증할 수
 * 있어야 하기 때문이다. Python 구현(33개 테스트 통과)의 이식이며 수치가 일치해야 한다.
 */

enum class QueryKind(val cacheable: Boolean, val rationale: String) {
    /** 목적지 자체가 답 — 경유 노드를 건너뛰어도 무손실 */
    LOCALIZATION(true, "목적지가 곧 답이므로 경유 노드를 건너뛰어도 무손실"),

    /** 경로가 곧 답 — 압축하면 답을 삭제하는 것 */
    TRACING(false, "경로 자체가 답이므로 숏컷은 답을 삭제하는 것과 같음"),

    /** 근거가 경유 노드에 분산 */
    RATIONALE(false, "근거가 경유 노드에 분산되어 목적지만으로 재구성 불가"),

    /** 분류 불가 — 보수적으로 제외 */
    UNKNOWN(false, "분류 신뢰도 부족 — 보수적으로 캐싱 제외"),
}
