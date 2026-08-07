package dev.wikilens.learn

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption

/**
 * append-only JSONL 궤적 로그.
 *
 * 궤적은 **유일하게 복구 불가능한 자산**이다. 포스팅과 신뢰도는 이 로그의 함수라
 * 언제든 재집계할 수 있다. 그래서 백업 대상은 이 파일 하나다.
 *
 * O_APPEND 원자성에 의존하므로 로컬 파일시스템이어야 한다. NFS 에서는 깨진다.
 */
class FileTrajectorySink(stateDir: Path, private val mapper: ObjectMapper) : TrajectorySink {
    private val log = LoggerFactory.getLogger(javaClass)
    private val file: Path = stateDir.resolve("trajectories.jsonl")

    private val failed = java.util.concurrent.atomic.AtomicInteger()
    override val failures: Int get() = failed.get()

    init {
        Files.createDirectories(stateDir)
    }

    override fun append(t: Trajectory) {
        runCatching {
            Files.writeString(
                file, mapper.writeValueAsString(t) + "\n",
                StandardOpenOption.CREATE, StandardOpenOption.APPEND,
            )
        }.onFailure {
            // **세어서 밖으로 낸다.** 실패해도 `apply` 는 계속 돌아 메모리 학습만
            // 앞서가는데, 예전에는 이 WARN 한 줄이 전부라 재기동 때까지 아무도 몰랐다.
            // `/api/stats` 의 `logWriteFailures` 와 `--status` 가 이 값을 짚는다.
            failed.incrementAndGet()
            log.warn("궤적 기록 실패({}건째): {}", failed.get(), it.message)
        }
    }

    /** 기동 시 재생. 포스팅은 궤적의 함수이므로 이것으로 완전히 복구된다. */
    fun replayInto(store: TrajectoryStore): Int {
        if (!Files.exists(file)) {
            // **조용히 0을 돌려주면 안 된다.** 상태 디렉터리 기본값이 상대경로라
            // 작업 디렉터리가 달라지면 여기가 다른 자리를 가리키고, 그러면 옛 궤적을
            // 못 읽은 채 **새 로그를 만들어 학습이 두 갈래로 갈린다.** 궤적은 유일한
            // 복구 불가 자산인데 그 분기가 아무 표시 없이 일어났다.
            //
            // 실제로 겪었다 — IntelliJ 가 main 함수에서 바로 띄우면 작업 디렉터리가
            // 저장소 루트라 `server/.wikilens/state` 가 아니라 `<루트>/.wikilens/state`
            // 를 쓴다. 첫 배포면 이 줄이 정상이고, 재기동인데 보이면 그게 신호다.
            log.warn(
                "기존 궤적 로그가 없어 새로 시작합니다: {} — 처음이면 정상이지만, " +
                    "재기동인데 이 줄이 보이면 작업 디렉터리가 달라져 **옛 궤적과 갈라진 것**입니다.",
                file,
            )
            return 0
        }
        var n = 0
        var bad = 0
        var firstError: String? = null
        Files.newBufferedReader(file).useLines { lines ->
            for (line in lines) {
                if (line.isBlank()) continue
                runCatching { store.replay(mapper.readValue<Trajectory>(line)) }
                    .onSuccess { n++ }
                    .onFailure {
                        // **삼키면 안 된다.** 예전에는 `onSuccess` 만 있어서 파싱 실패가
                        // 완전히 조용했다. `Trajectory` 에 필수 필드를 하나 더하는 순간
                        // 옛 줄이 전부 실패하는데, 로그는 `궤적 0건 재생` 이라고만 한다 —
                        // **첫 배포와 구별되지 않는다.** 궤적은 유일한 복구 불가 자산이고
                        // 스키마는 실제로 두 번 바뀌었다(`served`·`rank`, 그리고 `scope`).
                        bad++
                        if (firstError == null) firstError = it.message
                    }
            }
        }
        if (bad > 0) {
            log.error(
                "궤적 {}건을 읽지 못해 건너뜁니다 (성공 {}건): {} — 스키마를 바꿨다면 " +
                    "**옛 궤적이 통째로 버려지는 중**입니다. 로그는 지우지 마세요.",
                bad, n, firstError,
            )
        }
        log.info("궤적 {}건 재생", n)
        return n
    }
}
