package dev.wikilens.learn

import kotlin.math.abs

/**
 * 순수 로직 검증기.
 *
 * Spring도 Lucene도 없이 kotlinc 만으로 컴파일·실행된다. Python 구현(33개 테스트 통과)과
 * 수치가 일치하는지, 개발 중 겪은 버그들이 재발하지 않는지 확인한다.
 *
 *   kotlinc -language-version 2.1 Scoring.kt TrajectoryStore.kt Verify.kt \
 *     -include-runtime -d verify.jar && java -jar verify.jar
 */

private var passed = 0
private var failed = 0

private fun check(name: String, cond: Boolean, detail: String = "") {
    if (cond) { passed++; println("  PASS  $name") }
    else { failed++; println("  FAIL  $name ${if (detail.isNotEmpty()) "-> $detail" else ""}") }
}

private fun near(name: String, actual: Double, expected: Double, tol: Double = 1e-3) =
    check(name, abs(actual - expected) < tol, "actual=%.6f expected=%.6f".format(actual, expected))

private fun collectingStore(threshold: Double = 0.45): Pair<TrajectoryStore, MutableList<Trajectory>> {
    val log = mutableListOf<Trajectory>()
    return TrajectoryStore({ log.add(it) }, serveThreshold = threshold) to log
}

private fun session(s: TrajectoryStore, sid: String, query: String,
                    keywords: List<String>, reads: List<String>) {
    s.onQuery(sid, query, keywords)
    reads.forEach { s.onRead(sid, it) }
    s.onEnd(sid)
}

fun main() {
    println("=== 1. Beta 분위수: Python/scipy 기준값과 대조 ===")
    // Python 구현이 scipy와 1e-9까지 일치함을 확인했으므로 그 값을 기준으로 쓴다
    // 기준값은 Python 구현(scipy와 1e-9 일치 검증됨)에서 자동 생성했다.
    // 손으로 적었다가 두 번 틀렸으므로 생성된 값만 쓴다.
    near("EB(4승3패, 사전0.30)", Reliability.ebLower(4, 3, 0.3), 0.234549444, 1e-6)
    near("EB(4승3패, 사전0.50)", Reliability.ebLower(4, 3, 0.5), 0.309662748, 1e-6)
    near("EB(4승3패, 사전0.70)", Reliability.ebLower(4, 3, 0.7), 0.391761128, 1e-6)
    near("EB(5승0패, 사전0.30)", Reliability.ebLower(5, 0, 0.3), 0.396043431, 1e-6)
    near("EB(1승0패, 사전0.85)", Reliability.ebLower(1, 0, 0.85), 0.615658063, 1e-6)
    near("EB(15승0패, 사전0.85)", Reliability.ebLower(15, 0, 0.85), 0.877932582, 1e-6)

    println("\n=== 2. 사전분포 클램프 (겪은 버그: 1관측에 신뢰도 1.0) ===")
    check("사전 1.0이 확신을 주지 않음", Reliability.ebLower(1, 0, 1.0) < 0.75)
    near("사전 1.0 == 사전 CEIL",
        Reliability.ebLower(1, 0, 1.0), Reliability.ebLower(1, 0, Reliability.PRIOR_CEIL), 1e-9)

    println("\n=== 3. 신뢰도는 증거에 따라 오르고, 사전분포는 사라진다 ===")
    var prev = 0.0
    var monotone = true
    for (h in listOf(1, 2, 5, 10, 30)) {
        val cur = Reliability.ebLower(h, 0, 0.3)
        if (cur <= prev) monotone = false
        prev = cur
    }
    check("증거에 따라 단조 증가", monotone)
    val gapSmall = Reliability.ebLower(4, 3, 0.85) - Reliability.ebLower(4, 3, 0.05)
    val gapLarge = Reliability.ebLower(400, 300, 0.85) - Reliability.ebLower(400, 300, 0.05)
    check("표본이 쌓이면 사전분포 영향 소멸", gapLarge < gapSmall / 10,
        "small=%.4f large=%.4f".format(gapSmall, gapLarge))
    check("균등 사전에서 Wilson과 근사",
        abs(Reliability.ebLower(20, 5, 0.5) - Reliability.wilsonLower(20, 5)) < 0.12)

    println("\n=== 4. 경로 의존성 게이트 ===")
    check("조회 동사 인식", Gate.classify("로그인 붙이는 법 알려줘") == QueryKind.LOCALIZATION)
    check("도메인 명사 오탐 없음", Gate.classify("배포 파이프라인 문서 어디") == QueryKind.LOCALIZATION)
    check("흐름 질의", Gate.classify("토큰이 어떻게 흐르나") == QueryKind.TRACING)
    check("근거 질의", Gate.classify("왜 이 정책이지") == QueryKind.RATIONALE)
    check("짧은 질의는 조회", Gate.classify("온보딩") == QueryKind.LOCALIZATION)
    check("TRACING 캐싱 불가", !QueryKind.TRACING.cacheable && !QueryKind.RATIONALE.cacheable)
    check("마커 없는 7토큰 자연어 질의도 조회로 잡힘 (실측 실패 사례, 임계값 8)",
        Gate.classify("컨텐츠 노출 권한 필터링에 대한 3가지 방법") == QueryKind.LOCALIZATION)
    check("경계값: 8토큰(마커 없음)은 조회, 9토큰은 UNKNOWN",
        Gate.classify("컨텐츠 노출 권한 필터링에 대한 세가지 처리 절차") == QueryKind.LOCALIZATION &&
        Gate.classify("컨텐츠 노출 권한 필터링에 대한 세가지 처리 절차 정리") == QueryKind.UNKNOWN)
    check("배경 마커가 임계값 상향과 무관하게 근거 질의를 방어",
        Gate.classify("이 기능을 이렇게 구현한 배경이 궁금해") == QueryKind.RATIONALE)

    println("\n=== 5. 항 단위 포스팅 (겪은 버그: 집합 키라 카운트 분산) ===")
    val (s5, _) = collectingStore()
    listOf(
        "로그인 붙이는 법 문서 어디 있어" to listOf("로그인", "붙이는", "문서", "어디"),
        "로그인 붙이는 법 알려줘" to listOf("로그인", "붙이는", "알려줘"),
        "로그인 붙이는 법" to listOf("로그인", "붙이는"),
        "로그인 붙이는 법 페이지" to listOf("로그인", "붙이는", "페이지"),
        "로그인 붙이는 법 가이드" to listOf("로그인", "붙이는", "가이드"),
    ).forEachIndexed { i, (q, kw) -> session(s5, "s$i", q, kw, listOf("300000001")) }

    val h5 = s5.hints(listOf("로그인", "붙이는"), mapOf("300000001" to 0.75))
    check("표현이 달라도 신뢰도 합산", h5.isNotEmpty() && h5[0].pageId == "300000001")
    check("5회 전부 집계됨", h5.isNotEmpty() && h5[0].hits == 5,
        "hits=${h5.firstOrNull()?.hits}")
    check("사전분포 없으면 게이트가 더 엄격", s5.hints(listOf("로그인", "붙이는")).isEmpty())

    println("\n=== 6. 경로 의존 질의는 궤적만 남고 간선을 만들지 않는다 ===")
    val (s6, log6) = collectingStore()
    repeat(8) { session(s6, "t$it", "토큰이 어떻게 흐르나", listOf("토큰", "흐르"), listOf("A", "B")) }
    check("궤적은 기록됨", log6.size == 8, "size=${log6.size}")
    check("간선은 없음", s6.hints(listOf("토큰", "흐르")).isEmpty())
    check("포스팅 항 0개", (s6.stats()["terms"] as Int) == 0)

    println("\n=== 7. 모호한 질의는 분포이지 실패가 아니다 ===")
    val (s7, _) = collectingStore()
    repeat(6) { session(s7, "a$it", "설정 문서 어디", listOf("설정", "문서"), listOf("P1")) }
    repeat(3) { session(s7, "b$it", "설정 문서 어디", listOf("설정", "문서"), listOf("P2")) }
    check("모호함을 실패로 기록하지 않음", (s7.stats()["misses"] as Int) == 0)
    check("모호한 항으로 인식", (s7.stats()["ambiguousTerms"] as Int) > 0)
    val h7 = s7.hints(listOf("설정", "문서"), mapOf("P1" to 0.75, "P2" to 0.5))
    check("우세 목적지가 상위", h7.isNotEmpty() && h7[0].pageId == "P1" && h7[0].hits == 6)

    println("\n=== 8. 재구성 신호 (겪은 버그: 이중 확정) ===")
    val (s8, log8) = collectingStore()
    s8.onQuery("r1", "배포 파이프라인 어디", listOf("배포", "파이프라인", "어디"))
    s8.onRead("r1", "WRONG")
    s8.onQuery("r1", "배포 파이프라인 문서", listOf("배포", "파이프라인", "문서"))
    s8.onRead("r1", "RIGHT")
    s8.onEnd("r1")
    check("궤적 2건 (3건이면 이중 확정)", log8.size == 2, "size=${log8.size}")
    check("앞 시도는 실패로", log8.size == 2 && log8[0].dest == "WRONG" && !log8[0].success)
    check("뒤 시도는 성공으로", log8.size == 2 && log8[1].dest == "RIGHT" && log8[1].success)
    check("pWrong 에 반영", (s8.stats()["misses"] as Int) == 1)

    println("\n=== 9. 질의 없는 읽기는 궤적이 아니다 ===")
    val (s9, log9) = collectingStore()
    s9.onRead("z1", "X"); s9.onEnd("z1")
    check("무관한 파일 읽기 무시", log9.isEmpty())

    println("\n=== 10. 재생으로 간선 복구 ===")
    val (s10a, log10) = collectingStore()
    repeat(6) { session(s10a, "p$it", "온보딩 문서 어디", listOf("온보딩", "문서"), listOf("ONB")) }
    val before = s10a.hints(listOf("온보딩", "문서"), mapOf("ONB" to 0.75))
    val (s10b, _) = collectingStore()
    log10.forEach { s10b.replay(it) }
    val after = s10b.hints(listOf("온보딩", "문서"), mapOf("ONB" to 0.75))
    check("재기동 후 동일", before == after, "before=$before after=$after")

    println("\n" + "=".repeat(52))
    println("통과 $passed · 실패 $failed")
    if (failed > 0) kotlin.system.exitProcess(1)
}
