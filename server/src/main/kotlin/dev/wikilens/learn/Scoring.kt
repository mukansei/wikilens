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

object Gate {
    /**
     * 주의: 도메인 명사를 흐름 마커로 쓰면 안 된다. '파이프라인', '워크플로'는
     * "배포 파이프라인 문서 어디"처럼 순수 조회 질의에도 흔히 등장한다.
     * 흐름을 *묻는* 표현만 넣는다.
     */
    private val TRACING = listOf(
        "흐름", "어떻게 동작", "어떻게 흐르", "어떻게 처리", "호출 경로", "호출 체인",
        "거쳐", "생명주기", "단계별로",
        "how does", "how is", "trace through", "end to end", "walk through",
    )

    private val RATIONALE = listOf(
        "왜 ", "왜?", "이유", "근거", "의도", "배경", "설계 결정", "트레이드오프",
        "why ", "why?", "rationale", "reason for", "trade-off", "tradeoff",
    )

    /**
     * 위치 명사뿐 아니라 조회 동사도 포함한다. "로그인 붙이는 법 알려줘"는 명백한
     * 조회인데 위치 명사가 없어 UNKNOWN으로 빠지던 사례가 있었다.
     */
    private val LOCALIZATION = listOf(
        "어디", "어딨", "위치", "정의", "찾아", "찾기", "문서", "가이드", "페이지",
        "알려줘", "보여줘", "알려주", "보여주", "있나", "있어", "뭐야", "어느",
        "where is", "where are", "which page", "find the", "locate", "docs for",
        "show me", "tell me", "look up",
    )

    /**
     * LLM 호출 없이 어휘 규칙으로만 판정한다. 조회 경로에 LLM이 들어가면
     * 아끼려던 비용을 되불러들여 구조가 뒤집힌다(Minton의 효용 문제).
     *
     * 오분류 비용이 비대칭이므로 경로 의존 신호를 우선한다.
     */
    /**
     * 마커 어디에도 안 걸리는 짧은 질의는 대개 심볼/제목 조회다. 문턱을 8로 잡은 근거:
     * 실측 실패 사례("컨텐츠 노출 권한 필터링에 대한 3가지 방법", 마커 없음)가 7토큰이었고
     * 자연어 조회 질의 대부분이 마커 없이 3토큰을 넘긴다 — 3은 너무 타이트해서
     * 학습 간선이 거의 안 생겼다. 마커 체크가 이 폴백보다 먼저 실행되므로, 마커가
     * 커버하는 RATIONALE/TRACING 질의는 길이와 무관하게 안전하다. 마커에 안 걸리는
     * 긴 경로의존 질의(예: "이유"/"배경" 같은 단어 없이 완곡하게 묻는 경우)를
     * 오분류할 잔여 위험은 남는다 — `pWrong`으로 모니터링할 것.
     */
    fun classify(query: String): QueryKind {
        val q = query.lowercase()
        if (RATIONALE.any { it in q }) return QueryKind.RATIONALE
        if (TRACING.any { it in q }) return QueryKind.TRACING
        if (LOCALIZATION.any { it in q }) return QueryKind.LOCALIZATION
        return if (query.trim().split(Regex("\\s+")).size <= 8) QueryKind.LOCALIZATION
        else QueryKind.UNKNOWN
    }
}

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
