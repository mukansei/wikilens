package io.wikilens.learn

data class Trajectory(
    val ts: Long,
    val session: String,
    val keywords: List<String>,
    val kind: QueryKind,
    val reads: List<String>,
    val dest: String,
    val success: Boolean,
    /**
     * 이 질의에 **서빙한** 힌트들. 끝까지 안 읽힌 것은 틀린 힌트이므로 미스로 charge 한다 —
     * `pWrong` 이 재려던 값이다. 옛 로그에는 없다(기본값이라 재생이 통과한다).
     */
    val served: List<String> = emptyList(),
    /**
     * `dest` 가 검색 결과에서 몇 번째였나(0-based, 결과에 없었으면 -1).
     * **아래에서 건져 올린 것이 강한 신호다** — 근거는 `TrajectoryStore.hitWeight`.
     */
    val rank: Int = -1,
    /**
     * 세션의 **권한 범위 식별자**(`AclRegistry.scopeOf`) — **신원이 아니다.**
     * 지금은 기록·집계만 한다. 미리 넣는 이유는 로그가 append-only 라서다.
     */
    val scope: String = "",
    /**
     * [reads] 와 같은 길이·순서의 읽은 시각(epoch ms).
     * **`dest = reads.last()` 전제를 나중에 검증하려는 것** — 마지막으로 읽은 것과 가장
     * 오래 머문 것이 같은가. 기록만 한다.
     */
    val readTs: List<Long> = emptyList(),
)
