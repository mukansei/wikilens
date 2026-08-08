package dev.wikilens.learn

data class Trajectory(
    val ts: Long,
    val session: String,
    val keywords: List<String>,
    val kind: QueryKind,
    val reads: List<String>,
    val dest: String,
    val success: Boolean,
    /**
     * 학습 레이어가 이 질의에 **서빙한** 힌트 페이지들. 그중 세션이 끝까지 읽지 않은
     * 것은 **틀린 힌트**이므로 미스로 charge 한다 — `pWrong` 이 원래 재려던 값이다.
     *
     * 옛 로그에는 이 필드가 없다. 기본값이 빈 목록이라 재생이 그대로 통과한다.
     */
    val served: List<String> = emptyList(),
    /**
     * `dest` 가 검색 결과에서 몇 번째였나 (0-based). 결과에 없었으면 -1.
     *
     * 위에서 고른 것보다 **아래에서 건져 올린 것이 강한 신호**다 — 1위를 읽는 건
     * 기본 행동이지만, 7위를 읽으려면 앞의 여섯을 지나쳐야 한다. 웹 검색 클릭
     * 모델의 position bias 와 같은 논리다. 학습 레이어의 존재 이유도 이쪽이다:
     * 어휘 랭킹이 이미 1위로 준 것을 배워봐야 새로 얻는 게 없다.
     */
    val rank: Int = -1,
    /**
     * 이 궤적을 만든 세션의 **권한 범위 식별자**(`AclRegistry.scopeOf`). 신원이 아니다.
     *
     * 학습이 권한 폭에 오염되는 문제를 나중에 다루려면 이 값이 로그에 있어야 한다 —
     * 나중에 넣으면 그전 궤적에는 영영 없다. 지금은 기록과 집계만 하고 가중은 바꾸지
     * 않는다(그건 별도 설계이고 측정이 필요하다).
     *
     * 옛 로그에는 이 필드가 없다. 기본값이 빈 문자열이라 재생이 그대로 통과한다.
     */
    val scope: String = "",
    /**
     * [reads] 와 같은 길이·순서의 읽은 시각(epoch ms). 옛 로그에는 없다.
     *
     * **`dest = reads.last()` 전제를 나중에 검증하기 위한 것**이다 — 마지막으로 읽은
     * 것과 가장 오래 머문 것이 같은지. 지금은 기록만 하고 판정은 안 바꾼다.
     * 로그가 append-only 라 지금 넣지 않으면 그때까지의 궤적은 이 질문에 못 답한다.
     */
    val readTs: List<Long> = emptyList(),
)
