package io.wikilens.learn

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * `Reliability.meetsThreshold` 는 `ebLower(...) >= T` 의 **빠른 동치식**이다
 * (cdf 1회 vs 이분법 80회). 둘이 어긋나면 **서빙 여부가 조용히 달라진다** — 검색은
 * 정상으로 보이고 힌트만 다르게 나온다.
 *
 * **커버리지 축을 반드시 넣는다.** `hints()` 의 실제 판정은 `ebLower * c >= 문턱` 이라
 * 페이지별 문턱이 `문턱 / c` 다. `c = 1` 만 대조하면 다항 질의에서 흔한 경우
 * (한 후보가 항의 일부만 덮는 경우)를 통째로 안 본 것이 된다.
 */
class ReliabilityThresholdTest {

    private val serveThreshold = 0.45

    @Test
    fun `커버리지를 포함한 격자에서 이분법과 완전히 일치한다`() {
        var checked = 0
        for (hits in 0..12) {
            for (misses in 0..12) {
                for (prior in listOf(0.05, 0.30, 0.60, 0.85)) {
                    // c = 덮은 항 수 / 전체 항 수. 1..4항 질의에서 나올 수 있는 값들.
                    for (c in listOf(1.0, 1.0 / 2, 2.0 / 3, 1.0 / 3, 1.0 / 4)) {
                        val slow = Reliability.ebLower(hits, misses, prior) * c >= serveThreshold
                        val fast = Reliability.meetsThreshold(hits, misses, prior, serveThreshold / c)
                        assertEquals(slow, fast,
                            "hits=$hits misses=$misses prior=$prior c=$c")
                        checked++
                    }
                }
            }
        }
        assertEquals(13 * 13 * 4 * 5, checked)
    }

    /**
     * `c` 가 작으면 문턱이 1 을 넘는다 — `betaPpf` 는 (0,1) 안의 값이라 그때는 자명하게
     * 기각이어야 한다. 이 분기가 없으면 `betaCdf(x>=1)` 이 1.0 을 돌려주며 우연히
     * 맞는데, 우연에 기대면 안 된다.
     */
    @Test
    fun `문턱이 범위 밖이면 자명하게 판정한다`() {
        assertFalse(Reliability.meetsThreshold(100, 0, 0.85, 1.0))
        assertFalse(Reliability.meetsThreshold(100, 0, 0.85, 4.5))   // c = 0.1
        assertTrue(Reliability.meetsThreshold(0, 100, 0.05, 0.0))
    }

    /** 관측이 쌓이면 통과해야 한다 — 항상 기각하는 함수도 위 격자를 통과할 수 있다. */
    @Test
    fun `충분한 히트는 실제로 문턱을 넘는다`() {
        assertTrue(Reliability.meetsThreshold(20, 0, 0.30, serveThreshold))
        assertFalse(Reliability.meetsThreshold(0, 0, 0.30, serveThreshold))
    }

    @Test
    fun `서빙 문턱 기본값이 실제로 게이트 노릇을 한다`() {
        // **돌연변이 감사로 나왔다**: `serveThreshold` 기본값을 `0.45 → 0.0` 으로 내려도
        // 계약 88개와 JUnit 전부가 초록이었다. 그러면 **한 번 관측된 목적지가 전부
        // 힌트로 서빙된다** — 학습 레이어가 잡음을 그대로 검색 결과에 붓는다.
        //
        // 안 잡힌 이유가 구조적이다: 다른 테스트 대부분이 `serveThreshold = 0.0` 으로
        // 게이트를 **일부러 우회**하고(그쪽 관심사가 아니다), 0.45 를 쓰는 이 파일은
        // 위에 **자기 사본**을 들고 있었다. 프로덕션 기본값에 걸린 단언이 없었다.
        assertEquals(serveThreshold, TrajectoryStore.DEFAULT_SERVE_THRESHOLD,
            "이 파일의 사본이 프로덕션 기본값과 갈렸다")

        // 게이트가 실제로 막고 실제로 연다. 사전확률 0.3(어휘에 없는 페이지의 기본값) ·
        // 커버리지 1.0 에서 측정한 경계는 6승(0.4419) 과 7승(0.4816) 사이다.
        assertFalse(Reliability.meetsThreshold(1, 0, 0.30, serveThreshold),
            "1관측이 서빙되면 잡음이 그대로 결과에 실린다")
        assertFalse(Reliability.meetsThreshold(6, 0, 0.30, serveThreshold))
        assertTrue(Reliability.meetsThreshold(7, 0, 0.30, serveThreshold),
            "충분히 확인된 목적지가 영원히 안 서빙되면 학습 레이어가 죽은 것과 같다")
    }
}
