package dev.wikilens.learn

/**
 * 궤적 로그를 받는 싱크. 파일이든 DB든 이 인터페이스만 만족하면 된다.
 * 순수 로직을 I/O에서 떼어내기 위한 경계.
 */
fun interface TrajectorySink {
    fun append(t: Trajectory)

    /**
     * 지금까지 **쓰기에 실패한** 궤적 수.
     *
     * 실패해도 메모리 학습은 계속 진행되므로(그 편이 낫다 — 로그가 막혔다고 검색까지
     * 나빠질 이유는 없다), 이 값이 0 이 아니면 **메모리와 로그가 갈라지고 있다**는
     * 뜻이다. 재기동하면 그만큼이 사라진다. 궤적은 유일한 복구 불가 자산인데
     * 예전에는 WARN 한 줄이 전부라 아무도 몰랐다.
     */
    val failures: Int get() = 0
}
