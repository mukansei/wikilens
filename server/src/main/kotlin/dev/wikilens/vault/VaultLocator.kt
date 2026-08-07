package dev.wikilens.vault

import dev.wikilens.config.UserConfig
import dev.wikilens.config.WikiLensProperties
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Component
import java.nio.file.Files
import java.nio.file.Path

/**
 * 볼트가 어디 있는지 정하는 **유일한 자리**.
 *
 * 예전에는 `IndexingService`(색인·ACL 적재)와 `ContentService`(read·grep)가 각자
 * `props.vaultRoot` 를 풀었다. 그 둘이 갈릴 수 있다는 것이 문제인데, 실제로 갈렸다:
 *
 *   - `ContentService` 는 `toAbsolutePath()` 를 안 걸어서 **실행 디렉터리에 매달려**
 *     있었다. 색인 로그는 절대경로를 찍는데 read 는 다른 자리를 보고 있었다.
 *   - 설정 폴백을 넣자 갈림이 결정적이 됐다 — 실측: 폴백으로 기동한 서버가 문서 3건을
 *     색인하고 검색까지 정상인데 **read 는 전부 404** 였다(색인은 폴백 경로, read 는
 *     기본 경로). "검색은 되고 읽기는 안 되는" 그 조합은 이 저장소가 이미 한 번
 *     겪은 실패다(`CLAUDE.md` 조용히 실패 12번).
 *
 * 해석이 한 곳이면 그 갈림이 성립하지 않는다.
 *
 * ### 폴백 규칙
 * 설정값이 [WikiLensProperties.DEFAULT_VAULT_ROOT] 그대로이고 그 자리가 비어 있을
 * 때만 `~/.wikilens/config.json` 의 `vault` 를 쓴다. **명시로 준 경로는 폴백하지
 * 않는다** — 오타를 조용히 덮으면 "왜 빈 볼트인가"를 영영 못 찾는다.
 */
@Component
class VaultLocator(private val props: WikiLensProperties) {

    private val log = LoggerFactory.getLogger(javaClass)

    /** 마지막으로 로그에 남긴 폴백 경로. 같은 줄을 매번 다시 찍지 않기 위한 것뿐이다. */
    @Volatile
    private var loggedFallback: Path? = null

    /**
     * 볼트 루트(절대경로). 기본값이 상대경로라 절대화가 필수다 — 안 하면 실행
     * 디렉터리에 따라 다른 자리를 쓰고, 로그도 어디였는지 말해주지 못한다.
     *
     * **부를 때마다 다시 푼다.** 고정해두면 "볼트 없이 기동 → 싱크 → `/admin/reindex`"
     * 순서에서 기동 시점의 빈 경로를 계속 보게 된다.
     *
     * **그래서 공짜가 아니다** — 폴백을 볼 때 stat 두 번과 `config.json` 파싱이 든다
     * (실측: 2,383회 66ms). 문서마다 부르지 말고 **요청당 한 번 지역 변수로 받을 것**.
     */
    val root: Path
        get() {
            val configured = Path.of(props.vaultRoot).toAbsolutePath().normalize()
            if (props.vaultRoot != WikiLensProperties.DEFAULT_VAULT_ROOT) return configured
            if (Files.isDirectory(configured)) return configured
            val fromUserConfig = UserConfig.vaultRoot()?.takeIf { Files.isDirectory(it) } ?: return configured
            if (loggedFallback != fromUserConfig) {
                loggedFallback = fromUserConfig
                log.info("볼트 기본 경로({})가 없어 ~/.wikilens/config.json 의 vault 를 씁니다: {}",
                    configured, fromUserConfig)
            }
            return fromUserConfig
        }
}
