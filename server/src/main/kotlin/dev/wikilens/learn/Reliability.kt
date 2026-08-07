package dev.wikilens.learn

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min


object Reliability {
    /**
     * 사전분포는 부드러운 신호이지 확신이 아니다. 0이나 1에 붙으면 Beta의 한쪽 모수가
     * 0이 되어 사전분포가 관측을 완전히 압도한다 — 한 번 관측에 신뢰도 1.0이 나온다.
     */
    const val PRIOR_FLOOR = 0.05
    const val PRIOR_CEIL = 0.85

    /** 참고용. 실제 게이트에는 [ebLower]를 쓴다. */
    fun wilsonLower(hits: Int, misses: Int, z: Double = 1.96): Double {
        val n = hits + misses
        if (n == 0) return 0.0
        val p = hits.toDouble() / n
        val z2 = z * z
        val centre = p + z2 / (2 * n)
        val margin = z * Math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
        return ((centre - margin) / (1 + z2 / n)).coerceIn(0.0, 1.0)
    }

    /**
     * 검색 점수를 사전분포로 쓰는 Beta-Binomial 사후 하한.
     *
     * Wilson은 균등 사전(정보 없음)의 특수한 경우다. Lucene이 계산한 랭킹 점수를
     * 사전분포로 주면 같은 전적이라도 검색 신호가 강한 후보를 더 믿는다.
     * 표본이 쌓이면 사전분포 영향이 자동으로 사라진다.
     */
    fun ebLower(hits: Int, misses: Int, priorMean: Double,
                kappa: Double = 5.0, q: Double = 0.05): Double {
        val pm = min(PRIOR_CEIL, max(PRIOR_FLOOR, priorMean))
        return betaPpf(q, pm * kappa + hits, (1.0 - pm) * kappa + misses)
    }

    // ---- Beta 분포. scipy 없이 계산해 의존성을 줄인다 ----

    private fun logBeta(a: Double, b: Double): Double =
        lgamma(a) + lgamma(b) - lgamma(a + b)

    /** Lanczos 근사. JDK에 lgamma가 없다. */
    private fun lgamma(x: Double): Double {
        val g = doubleArrayOf(
            676.5203681218851, -1259.1392167224028, 771.32342877765313,
            -176.61502916214059, 12.507343278686905, -0.13857109526572012,
            9.9843695780195716e-6, 1.5056327351493116e-7,
        )
        if (x < 0.5) return ln(Math.PI / Math.sin(Math.PI * x)) - lgamma(1.0 - x)
        val z = x - 1.0
        var a = 0.99999999999980993
        for (i in g.indices) a += g[i] / (z + i + 1.0)
        val t = z + g.size - 0.5
        return 0.5 * ln(2 * Math.PI) + (z + 0.5) * ln(t) - t + ln(a)
    }

    private fun betaCdf(x: Double, a: Double, b: Double): Double {
        if (x <= 0.0) return 0.0
        if (x >= 1.0) return 1.0
        val lb = logBeta(a, b)
        return if (x < (a + 1) / (a + b + 2)) {
            exp(a * ln(x) + b * ln(1 - x) - lb) * betacf(x, a, b) / a
        } else {
            1.0 - exp(b * ln(1 - x) + a * ln(x) - lb) * betacf(1 - x, b, a) / b
        }
    }

    private fun betacf(x: Double, a: Double, b: Double): Double {
        val tiny = 1e-30
        val qab = a + b; val qap = a + 1.0; val qam = a - 1.0
        var c = 1.0
        var d = 1.0 - qab * x / qap
        if (abs(d) < tiny) d = tiny
        d = 1.0 / d
        var h = d
        for (m in 1..200) {
            val m2 = 2 * m
            var aa = m * (b - m) * x / ((qam + m2) * (a + m2))
            d = 1.0 + aa * d; if (abs(d) < tiny) d = tiny
            c = 1.0 + aa / c; if (abs(c) < tiny) c = tiny
            d = 1.0 / d
            h *= d * c
            aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
            d = 1.0 + aa * d; if (abs(d) < tiny) d = tiny
            c = 1.0 + aa / c; if (abs(c) < tiny) c = tiny
            d = 1.0 / d
            val delta = d * c
            h *= delta
            if (abs(delta - 1.0) < 3e-12) break
        }
        return h
    }

    /** 이분법. 안정성이 속도보다 중요하다. */
    private fun betaPpf(q: Double, a: Double, b: Double): Double {
        var lo = 0.0; var hi = 1.0
        repeat(80) {
            val mid = (lo + hi) / 2
            if (betaCdf(mid, a, b) < q) lo = mid else hi = mid
            if (hi - lo < 1e-9) return (lo + hi) / 2
        }
        return (lo + hi) / 2
    }
}
