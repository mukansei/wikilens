package dev.wikilens

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.LuceneIndex
import dev.wikilens.learn.FileTrajectorySink
import dev.wikilens.learn.TrajectoryStore
import dev.wikilens.vault.VaultReader
import org.slf4j.LoggerFactory
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
    fun vaultBootstrap(
        props: WikiLensProperties,
        vault: VaultReader,
        index: LuceneIndex,
        acl: AclRegistry,
    ): VaultBootstrap {
        val log = LoggerFactory.getLogger(WikiLensApplication::class.java)
        return runCatching {
            val pages = vault.read(Path.of(props.vaultRoot), acl)
            index.rebuild(pages)
            VaultBootstrap(pages.size, acl.pageCount()).also {
                log.info("기동 적재 완료: 문서 {} · ACL 페이지 {}", it.indexed, it.aclPages)
            }
        }.getOrElse { e ->
            log.error("기동 적재 실패 (vault-root={}). 색인이 빈 채로 시작합니다 — " +
                "`--status` 가 INDEXED_DOCS=0 으로 잡습니다.", props.vaultRoot, e)
            VaultBootstrap(0, 0)
        }
    }
}

/** 기동 적재 결과. 빈으로 두는 이유는 적재가 실제로 일어났음을 테스트가 확인하기 위해서다. */
data class VaultBootstrap(val indexed: Int, val aclPages: Int)

fun main(args: Array<String>) {
    runApplication<WikiLensApplication>(*args)
}
