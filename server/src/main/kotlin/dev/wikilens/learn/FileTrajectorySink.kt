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

    init {
        Files.createDirectories(stateDir)
    }

    override fun append(t: Trajectory) {
        runCatching {
            Files.writeString(
                file, mapper.writeValueAsString(t) + "\n",
                StandardOpenOption.CREATE, StandardOpenOption.APPEND,
            )
        }.onFailure { log.warn("궤적 기록 실패: {}", it.message) }
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
        Files.newBufferedReader(file).useLines { lines ->
            for (line in lines) {
                if (line.isBlank()) continue
                runCatching { store.replay(mapper.readValue<Trajectory>(line)) }
                    .onSuccess { n++ }
            }
        }
        log.info("궤적 {}건 재생", n)
        return n
    }
}
