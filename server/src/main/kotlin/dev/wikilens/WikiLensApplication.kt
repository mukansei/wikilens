package dev.wikilens

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.AnalyzerKind
import dev.wikilens.index.LuceneIndex
import dev.wikilens.learn.FileTrajectorySink
import dev.wikilens.learn.TrajectoryStore
import dev.wikilens.vault.VaultReader
import org.slf4j.LoggerFactory
import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.boot.runApplication
import org.springframework.context.annotation.Bean
import org.springframework.scheduling.annotation.EnableScheduling
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
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
@EnableScheduling
class WikiLensApplication {

    // 경로는 전부 절대경로로 푼다 — 기본값이 상대경로라 그대로 두면 실행 디렉터리에
    // 따라 다른 자리를 쓰고, 로그·오류 메시지도 어디였는지 말해주지 못한다.
    // 오타는 여기서 죽는 편이 낫다. 조용히 기본값으로 떨어지면 그게 "검색이 0건" 의
    // 원인이 되고, 그때는 설정이 아니라 색인을 의심하게 된다.
    @Bean
    fun luceneIndex(props: WikiLensProperties): LuceneIndex =
        LuceneIndex(abs(props.indexDir), AnalyzerKind.of(props.analyzer)).also { it.openIfExists() }

    @Bean
    fun trajectorySink(props: WikiLensProperties, mapper: ObjectMapper): FileTrajectorySink =
        FileTrajectorySink(abs(props.stateDir), mapper)

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

        // **경로를 절대경로로 풀어서 찍는다.** 기본값이 `./mirror-root` 처럼 상대경로라
        // 실제 위치가 **실행 디렉터리에 달려 있다.** 문서는 `cd server && ./gradlew bootRun`
        // 만 안내해서 늘 `server/` 였지만, 실배포는 jar 다 — 다른 디렉터리에서 띄우면
        // 빈 볼트를 보고 `문서 0` 으로 **정상 기동한다**(2026-08-06 실측). 어디를 봤는지
        // 로그가 말해주지 않으면 그때 원인을 찾을 방법이 없다.
        val root = abs(props.vaultRoot)
        log.info("볼트 {} · 색인 {} · 상태 {}", root, abs(props.indexDir), abs(props.stateDir))

        return runCatching {
            val pages = vault.read(root, acl)
            if (pages.isEmpty()) {
                // `VaultReader` 는 미러가 없어도 예외를 안 던지고 빈 목록을 준다. 그래서
                // 예전엔 이 경우가 `기동 적재 완료: 문서 0` 으로 찍혀 정상처럼 보였다.
                //
                // **여기서 재색인하면 안 된다.** `rebuild(emptyList())` 는 디스크에 있던
                // 멀쩡한 색인을 0건으로 덮어쓴다 — 경로 하나 잘못 준 재기동이 마지막으로
                // 성공한 색인까지 지운다(실측: `색인 재구축 0건`). 볼트를 못 읽는 것은
                // 고칠 수 있는 문제지만 지워진 색인은 다시 싱크해야 한다.
                log.error(
                    "볼트에서 문서를 하나도 못 읽었습니다: {} — 경로가 맞는지 확인하세요" +
                        "(상대경로는 실행 디렉터리 기준입니다). **기존 색인은 그대로 둡니다** — " +
                        "검색은 옛 색인으로 계속 동작하고, ACL 페이지 맵만 비어 읽기가 404 가 됩니다.",
                    root,
                )
                return@runCatching VaultBootstrap(index.docCount, acl.pageCount())
            }
            index.rebuild(pages)
            log.info("기동 적재 완료: 문서 {} · ACL 페이지 {}", pages.size, acl.pageCount())
            VaultBootstrap(pages.size, acl.pageCount())
        }.getOrElse { e ->
            log.error("기동 적재 실패 (볼트={}). 색인이 빈 채로 시작합니다 — " +
                "`--status` 가 INDEXED_DOCS=0 으로 잡습니다.", root, e)
            VaultBootstrap(0, 0)
        }
    }

    private fun abs(p: String): Path = Path.of(p).toAbsolutePath().normalize()
}

/**
 * 버려진 세션을 주기적으로 거둔다.
 *
 * **이게 없으면 `sweep` 은 아무도 안 부른다.** `/api/admin/sweep` 은 있었지만 수동이고,
 * 세션 종료는 MCP 프록시의 `atexit` 에만 걸려 있어 프로세스가 SIGKILL 되거나 크래시하면
 * `onEnd` 가 영영 안 온다. 결과가 둘이었다:
 *
 *   - `sessions` 맵이 단조증가한다 (상주 서버에서 영구 누수)
 *   - 그 세션의 궤적이 **확정되지 않아 학습에 반영되지 않는다** — 사용자는 정상적으로
 *     검색하고 읽었는데 배운 게 없다. 조용한 쪽이 이쪽이라 더 나쁘다.
 *
 * `Controller` 의 주석이 "놓쳐도 sweep 이 처리한다" 라고 적고 있었는데, 그 문장을
 * 참으로 만드는 코드가 없었다.
 *
 * 스케줄링은 Spring 의 일이므로 여기 둔다 — `learn/` 에는 프레임워크 import 가
 * 들어가면 안 된다(`shared_contract.sh` 가 강제한다).
 */
@Component
class SessionSweeper(
    private val store: TrajectoryStore,
    private val props: WikiLensProperties,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    @Scheduled(
        fixedDelayString = "\${wikilens.learn.sweep-interval-millis:300000}",
        initialDelayString = "\${wikilens.learn.sweep-interval-millis:300000}",
    )
    fun sweep() {
        val n = store.sweep(props.learn.sessionIdleMillis)
        if (n > 0) log.info("버려진 세션에서 궤적 {}건을 확정했습니다", n)
    }
}

/** 기동 적재 결과. 빈으로 두는 이유는 적재가 실제로 일어났음을 테스트가 확인하기 위해서다. */
data class VaultBootstrap(val indexed: Int, val aclPages: Int)

fun main(args: Array<String>) {
    runApplication<WikiLensApplication>(*args)
}
