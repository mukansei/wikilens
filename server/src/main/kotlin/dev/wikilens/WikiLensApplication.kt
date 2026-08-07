package dev.wikilens

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.AnalyzerKind
import dev.wikilens.index.LuceneIndex
import dev.wikilens.service.IndexingService
import dev.wikilens.learn.FileTrajectorySink
import dev.wikilens.learn.StateDirLock
import dev.wikilens.learn.TrajectoryStore
import org.slf4j.LoggerFactory
import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.scheduling.annotation.EnableScheduling
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
 * 대가는 질의 시점 ACL 시행이다. 이색적인 요구가 아니라 엔터프라이즈 검색의 표준이며,
 * 공유 배포를 하는 이상 어차피 풀어야 하는 문제다.
 *
 * 읽기는 여전히 클라이언트 로컬이다. 서버는 좌표만 반환한다.
 */
@SpringBootApplication
@EnableConfigurationProperties(WikiLensProperties::class)
@EnableScheduling
class WikiLensApplication {

    // 경로는 전부 절대경로로 푼다 — 기본값이 상대경로라 그대로 두면 실행 디렉터리에
    // 따라 다른 자리를 쓰고, 로그·오류 메시지도 어디였는지 말해주지 못한다.
    // 오타는 여기서 죽는 편이 낫다. 조용히 기본값으로 떨어지면 그게 "검색이 0건" 의
    // 원인이 되고, 그때는 설정이 아니라 색인을 의심하게 된다.
    @Bean
    fun luceneIndex(props: WikiLensProperties): LuceneIndex =
        LuceneIndex(abs(props.indexDir), AnalyzerKind.of(props.analyzer)).also { it.openIfExists() }

    /**
     * 상태 디렉터리 단일 쓰기 보증. **싱크보다 먼저 만들어져야** 하므로 싱크가 이걸
     * 인자로 받는다 — 빈 이름만 다르면 Spring 이 순서를 보장하지 않는다.
     */
    @Bean
    fun stateDirLock(props: WikiLensProperties): StateDirLock = StateDirLock(abs(props.stateDir))

    @Bean
    fun trajectorySink(
        props: WikiLensProperties,
        mapper: ObjectMapper,
        @Suppress("UNUSED_PARAMETER") lock: StateDirLock,
    ): FileTrajectorySink = FileTrajectorySink(abs(props.stateDir), mapper)

    @Bean
    fun trajectoryStore(props: WikiLensProperties, sink: FileTrajectorySink): TrajectoryStore =
        TrajectoryStore(
            sink = sink,
            serveThreshold = props.learn.serveThreshold,
            reformulationOverlap = props.learn.reformulationOverlap,
        ).also { sink.replayInto(it) }

    /**
     * 기동 시 볼트를 한 번 읽어 색인과 ACL 을 함께 채운다.
     *
     * **없으면 서버가 반쪽으로 뜬다.** Lucene 색인은 디스크에 있어 `openIfExists()` 로
     * 되살아나고 궤적은 로그에서 재생되는데, ACL 페이지 맵은 순수 힙이라 비어서
     * 시작한다. `search` 는 Lucene 필터를, `read` 는 `acl.canSee` 를 쓰므로 **검색은
     * 되고 읽기는 전부 404** 가 된다 — 겉으로는 전부 정상으로 보인다
     * (2026-08-06 실측: 재기동 직후 `indexedDocs=2377`·`aclPages=0`, read 404).
     * 그동안은 운영자가 기동 후 `/admin/reindex` 를 부르는 것으로 우연히 가려져 있었다.
     *
     * 전체 재구축을 하는 이유는 ACL 만 채우는 것보다 강하기 때문이다 — 서버가 내려간
     * 사이 `sync` 가 돌았다면 디스크 색인이 낡았는데, 이걸로 함께 해소된다. 비용은
     * 2,377건에 6.7초(실측)이고 전체 재구축을 싸게 보는 것이 이 프로젝트의 전제다.
     *
     * **빈 생성 시점에 적재한다.** `ApplicationRunner`·`CommandLineRunner` 는 웹 서버가
     * 이미 포트를 연 뒤에 돌아서, 적재 전에 도착한 요청이 같은 반쪽 상태를 본다.
     *
     * 볼트를 못 읽어도 기동은 계속한다. 설정이 틀렸다고 서버가 아예 안 뜨면 `--status`
     * 로 진단할 길까지 사라진다 — 대신 그 진단이 `INDEXED_DOCS=0` 으로 잡아준다.
     */
    @Bean
    fun vaultBootstrap(props: WikiLensProperties, indexing: IndexingService): VaultBootstrap {
        val log = LoggerFactory.getLogger(WikiLensApplication::class.java)

        // **경로를 절대경로로 풀어서 찍는다.** 기본값이 `./mirror-root` 처럼 상대경로라
        // 실제 위치가 **실행 디렉터리에 달려 있다.** 다른 디렉터리에서 jar 를 띄우면
        // 빈 볼트를 보고 `문서 0` 으로 **정상 기동한다**(2026-08-06 실측). 어디를 봤는지
        // 로그가 말해주지 않으면 그때 원인을 찾을 방법이 없다.
        log.info("볼트 {} · 색인 {} · 상태 {}",
            indexing.vaultRoot, abs(props.indexDir), abs(props.stateDir))

        // 적재는 `/admin/reindex` 와 **같은 코드**를 쓴다 — 따로 두었더니 한쪽에만
        // 방어가 들어가 다른 쪽이 색인을 지웠다(`IndexingService` 주석 참고).
        //
        // 볼트를 못 읽어도 기동은 계속한다. 설정이 틀렸다고 서버가 아예 안 뜨면 `--status`
        // 로 진단할 길까지 사라진다 — 대신 그 진단이 `INDEXED_DOCS=0` 으로 잡아준다.
        return runCatching {
            val r = indexing.reload()
            if (!r.skipped) log.info("기동 적재 완료: 문서 {} · ACL 페이지 {}", r.indexed, r.aclPages)
            VaultBootstrap(r.indexed, r.aclPages)
        }.getOrElse { e ->
            log.error("기동 적재 실패. 색인이 빈 채로 시작합니다 — " +
                "`--status` 가 INDEXED_DOCS=0 으로 잡습니다.", e)
            VaultBootstrap(0, 0)
        }
    }

    private fun abs(p: String): Path = Path.of(p).toAbsolutePath().normalize()
}
