package dev.wikilens.learn

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
}
