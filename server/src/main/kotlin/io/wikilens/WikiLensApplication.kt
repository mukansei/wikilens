package io.wikilens

import com.fasterxml.jackson.databind.ObjectMapper
import io.wikilens.acl.AclRegistry
import io.wikilens.acl.UserStore
import io.wikilens.config.UserConfig
import io.wikilens.config.WikiLensProperties
import io.wikilens.index.AnalyzerKind
import io.wikilens.index.LuceneIndex
import io.wikilens.service.IndexingService
import io.wikilens.learn.FileTrajectorySink
import io.wikilens.learn.StateDirLock
import io.wikilens.learn.TrajectoryStore
import org.slf4j.LoggerFactory
import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.scheduling.annotation.EnableScheduling
import java.nio.file.Path

/**
 * WikiLens 서버.
 *
 * **서버가 색인을 갖는다.** 클라이언트 분산 색인을 철회한 이유: Confluence API 부하가
 * 사용자 수에 비례하고(200명이면 200배), 임베딩이 중복 계산되고, 권한이 좁은 사용자는
 * IDF 추정이 망가지고, **사용자마다 랭킹 척도가 달라 학습에 이질적 관측이 섞인다.**
 * 대가는 질의 시점 ACL 시행인데 공유 배포를 하는 이상 어차피 풀어야 하는 문제다.
 */
@SpringBootApplication
@EnableConfigurationProperties(WikiLensProperties::class)
@EnableScheduling
class WikiLensApplication {

    /**
     * 시행 여부가 설정이라 여기서 주입한다(`@Component` 는 무인자 생성이라 못 받는다).
     * **꺼져 있으면 기동에서 크게 알린다** — 조용히 열려 있는 것이 가장 나쁜 상태다.
     */
    @Bean
    fun aclRegistry(props: WikiLensProperties): AclRegistry {
        if (!props.aclEnforced) {
            LoggerFactory.getLogger(WikiLensApplication::class.java).warn(
                "ACL 시행이 꺼져 있습니다 (wikilens.acl-enforced=false) — **등록 없이 전원이 전 문서를 봅니다.** " +
                    "볼트를 싱크한 계정의 권한 범위를 이 서버에 닿는 전원이 공유해도 되는 경우에만 맞습니다.",
            )
        }
        return AclRegistry(props.aclEnforced, UserStore(abs(props.stateDir), ObjectMapper()))
    }

    @Bean
    fun luceneIndex(props: WikiLensProperties): LuceneIndex =
        LuceneIndex(abs(props.indexDir), AnalyzerKind.of(props.analyzer)).also { it.openIfExists() }

    /** **싱크보다 먼저 만들어져야** 하므로 싱크가 인자로 받는다 — 그래야 순서가 보장된다. */
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
     * **없으면 서버가 반쪽으로 뜬다.** Lucene 색인은 디스크에서 되살아나고 궤적은 로그에서
     * 재생되는데 ACL 페이지 맵은 순수 힙이라 비어서 시작한다 — `search` 는 Lucene 필터를,
     * `read` 는 `acl.canSee` 를 쓰므로 **검색은 되고 읽기는 전부 404** 가 되고 겉으로는
     * 정상으로 보인다(실측: `indexedDocs=2377`·`aclPages=0`).
     *
     * **ACL 만 채우지 않고 전체 재구축을 한다** — 서버가 내려간 사이 `sync` 가 돌았다면
     * 디스크 색인도 낡았는데 이걸로 함께 해소된다. 비용은 2,377건에 6.7초(실측)이고,
     * 전체 재구축을 싸게 보는 것이 이 프로젝트의 전제다.
     *
     * **빈 생성 시점에 적재한다** — `ApplicationRunner` 는 포트가 열린 뒤에 돌아서 그
     * 사이 요청이 같은 반쪽 상태를 본다.
     *
     * 볼트를 못 읽어도 기동은 계속한다. 안 뜨면 `--status` 로 진단할 길까지 사라진다 —
     * 대신 그 진단이 `INDEXED_DOCS=0` 으로 잡는다.
     */
    @Bean
    fun vaultBootstrap(props: WikiLensProperties, indexing: IndexingService): VaultBootstrap {
        val log = LoggerFactory.getLogger(WikiLensApplication::class.java)

        // **절대경로로 찍는다.** 기본값이 상대경로라 실제 위치가 실행 디렉터리에 달려
        // 있고, 다른 자리에서 띄우면 빈 볼트를 보고 `문서 0` 으로 **정상 기동한다**(실측).
        log.info("볼트 {} · 색인 {} · 상태 {}",
            indexing.vaultRoot, abs(props.indexDir), abs(props.stateDir))

        // 적재는 `/admin/reindex` 와 **같은 코드**를 쓴다 — 따로 두었더니 한쪽에만 방어가
        // 들어가 다른 쪽이 색인을 지웠다(`IndexingService`).
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

    /**
     * 경로 해석은 [UserConfig.resolve] 하나를 지난다 — **`~` 확장이 거기 있다.**
     * 기본값이 `~/.wikilens/…` 인데 Spring 도 JVM 도 `~` 를 안 풀어서, 여기서 안 거치면
     * 실행 디렉터리 밑에 `~` 라는 디렉터리가 생긴다.
     */
    private fun abs(p: String): Path = UserConfig.resolve(p)
}
