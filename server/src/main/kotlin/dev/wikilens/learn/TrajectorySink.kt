package dev.wikilens.learn

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * 궤적 로그를 받는 싱크. 파일이든 DB든 이 인터페이스만 만족하면 된다.
 * 순수 로직을 I/O에서 떼어내기 위한 경계.
 */
fun interface TrajectorySink {
    fun append(t: Trajectory)
}
