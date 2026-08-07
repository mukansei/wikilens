package dev.wikilens.learn

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min


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
