package dev.wikilens

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.LuceneIndex
import dev.wikilens.learn.FileTrajectorySink
import dev.wikilens.learn.TrajectoryStore
import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.boot.runApplication
import org.springframework.context.annotation.Bean
import java.nio.file.Path

/**
 * WikiLens 서버.
 *
 * 서버가 색인을 갖는다. 클라이언트 분산 색인을 철회한 이유:
 *   - Confluence API 부하가 사용자 수에 비례한다 (200명이면 200배)
 *   - dense 임베딩이 사용자마다 중복 계산된다
 *   - 권한이 좁은 사용자는 IDF 추정이 망가진다
 *   - **사용자마다 랭킹 척도가 달라 학습 레이어에 이질적 관측이 섞인다**
 *
 * 대가는 질의 시점 ACL 시행이다. 이색적인 요구가 아니라 사내 검색의 표준이며,
 * 공유 배포를 하는 이상 어차피 풀어야 하는 문제다.
 *
 * 읽기는 여전히 클라이언트 로컬이다. 서버는 좌표만 반환한다.
 */
@SpringBootApplication
@EnableConfigurationProperties(WikiLensProperties::class)
class WikiLensApplication {

    @Bean
    fun luceneIndex(props: WikiLensProperties): LuceneIndex =
        LuceneIndex(Path.of(props.indexDir)).also { it.openIfExists() }

    @Bean
    fun trajectorySink(props: WikiLensProperties, mapper: ObjectMapper): FileTrajectorySink =
        FileTrajectorySink(Path.of(props.stateDir), mapper)

    @Bean
    fun trajectoryStore(props: WikiLensProperties, sink: FileTrajectorySink): TrajectoryStore =
        TrajectoryStore(
            sink = sink,
            serveThreshold = props.learn.serveThreshold,
            reformulationOverlap = props.learn.reformulationOverlap,
        ).also { sink.replayInto(it) }
}

fun main(args: Array<String>) {
    runApplication<WikiLensApplication>(*args)
}
