package io.wikilens.learn

/** 한 질의와 그 뒤에 이어진 읽기들. */
class QuerySpan(val keywords: List<String>, val kind: QueryKind) {
    val reads = ArrayList<String>()

    /**
     * `reads` 와 **같은 길이·같은 순서**의 읽은 시각(epoch ms).
     *
     * `dest = reads.last()`("탐색은 성공에서 멈춘다")가 이 설계의 최대 미해결인데,
     * 그게 틀리는 빈도를 **재려면 시각이 있어야 한다** — 마지막으로 읽은 것과 가장
     * 오래 머문 것이 같은가. 체류 시간은 웹 검색에서 확립된 신호다.
     *
     * **지금은 기록만 한다.** 판정을 바꾸는 것은 실사용 궤적이 쌓인 뒤의 일이고,
     * 지금 넣는 이유는 **로그가 append-only** 라서다 — 나중에 넣으면 그전 궤적에는
     * 영영 없다(`scope` 를 미리 넣은 것과 같은 이유).
     */
    val readTs = ArrayList<Long>()

    /** 이 질의에 학습 레이어가 서빙한 힌트 페이지들 (`onServed` 가 채운다). */
    var served: List<String> = emptyList()
    /** 서빙한 **전체** 결과를 순위 순으로. `dest` 의 순위를 알아내는 데 쓴다. */
    var ranked: List<String> = emptyList()
    /** on_query와 on_end가 같은 스팬을 두 번 확정하는 것을 막는다. */
    var finalized = false

    /**
     * 모델이 **답이라고 말한** 페이지. 비면 [reads] 의 마지막으로 추정한다(폴백).
     *
     * 위 [readTs] 가 재려던 그 미해결을 **다르게 푼다** — 체류 시간으로 추정하는 대신
     * 물어본다. 실측(2026-08-14, C 케이스 6세션 `docs/declared-answer-design.md`):
     * 읽기 1건은 3/3 자명하게 일치하는데 **2건 이상은 3건 중 2건이 어긋났다.**
     * 마지막 읽기가 답이 아니라 *확인*이나 *배제*인 경우가 흔하다.
     *
     * 한 번 틀리면 셋이 함께 틀린다 — 간선(`postings[항][dest]`) · 미스
     * (`passedOver = reads - {dest}`) · 깊이 가중(`rank`)이 전부 이 값에 걸려 있다.
     *
     * **진술이 맞다는 보장은 아니다.** 모델이 오답을 확신하면 그 오답이 `dest` 가 된다
     * — 그건 미스 벌점이 잡을 몫이고, 이 필드가 정확히 하는 것은 *"모델이 무엇을
     * 답이라 여겼나"* 뿐이다.
     */
    var declaredDest: String? = null

    fun addRead(pageId: String, at: Long = System.currentTimeMillis()) {
        // 같은 페이지를 연속으로 읽으면 한 번으로 센다. 시각은 **첫 읽기**를 남긴다 —
        // 체류 시간은 "언제부터 봤나" 로 재야 하고, 연속 재요청은 같은 열람이다.
        if (reads.isEmpty() || reads.last() != pageId) {
            reads.add(pageId)
            readTs.add(at)
        }
    }
}
