package io.wikilens.config

import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.boot.context.properties.NestedConfigurationProperty

/**
 * `wikilens.*`. **운영자용 설명은 `application.yml` 에 있다** — 그쪽이 유일한 발견
 * 창구라 길게 쓰는 쪽은 거기다. 여기에는 코드가 의존하는 사실만 둔다.
 */
@ConfigurationProperties(prefix = "wikilens")
data class WikiLensProperties(
    /**
     * `wikilens sync` 가 만든 미러 루트.
     *
     * [DEFAULT_VAULT_ROOT] 그대로이고 그 경로가 없을 때만 `~/.wikilens/config.json` 의
     * `vault` 로 폴백한다([UserConfig]) — **명시로 준 값은 폴백하지 않는다.**
     */
    val vaultRoot: String = DEFAULT_VAULT_ROOT,
    val indexDir: String = "./.wikilens/index",
    val stateDir: String = "./.wikilens/state",
    /**
     * 본문 분석기: `korean`(기본) · `english` · `standard`.
     *
     * "무엇으로 **지을까**" 다 — 질의는 색인이 실제로 지어진 분석기를 쓴다
     * ([io.wikilens.index.LuceneIndex]). 둘은 재색인에서 만난다.
     */
    val analyzer: String = "korean",
    /**
     * 질의 시점 ACL 시행. **기본이 꺼짐이다** — 지금의 시행은 실질적으로 사용자
     * 허용목록이라(`sync` 가 권한을 안 가져온다) 얻는 것 없이 전원이 빈손이 되는
     * 함정이었다. 켜는 조건과 그때 생기는 일은 [AclRegistry] 참고.
     *
     * **꺼져 있다는 사실은 기동 로그·`/api/stats`·`--status` 셋이 계속 말한다.**
     * 조용히 열린 것이 아니라 시끄럽게 열린 상태여야 이 기본값이 정당하다.
     */
    val aclEnforced: Boolean = false,
    /** `/api/admin` 공유 토큰. **비면 관리 API 가 전부 404.** 근거는 [io.wikilens.api.AdminGuard]. */
    val adminToken: String = "",
    /**
     * 본문 스캔 엔진: `auto`(기본) · `jvm` · `ripgrep`. `auto` 는 rg 가 있으면 rg 다.
     * 비용 모델은 `ContentService.GREP_BUDGET_NANOS`, 두 경로의 답이 같은지는
     * `GrepEngineParityTest` 가 지킨다.
     */
    val grepEngine: String = "auto",
    @NestedConfigurationProperty val learn: LearnProps = LearnProps(),
) {
    companion object {
        /**
         * 폴백 여부를 판단하려면 "사용자가 값을 줬는가" 를 알아야 하는데 Spring 은 기본값과
         * 명시값을 구분해주지 않는다. 상수로 두고 같은지 비교하는 것이 그 구분을 얻는
         * 방법이다. `application.yml` 에도 같은 값이 있고 계약이 일치를 검사한다.
         */
        const val DEFAULT_VAULT_ROOT = "./mirror-root"
    }
}
