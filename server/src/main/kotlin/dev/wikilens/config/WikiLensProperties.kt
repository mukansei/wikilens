package dev.wikilens.config

import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.boot.context.properties.NestedConfigurationProperty


@ConfigurationProperties(prefix = "wikilens")
data class WikiLensProperties(
    /**
     * Python `wikilens sync` 가 만든 미러 루트. 서비스 계정으로 1회만 싱크한다.
     *
     * 이 값이 [DEFAULT_VAULT_ROOT] 그대로이고 그 경로가 없으면 `~/.wikilens/config.json`
     * 의 `vault` 로 폴백한다([UserConfig]). 명시로 준 값은 폴백하지 않는다.
     */
    val vaultRoot: String = DEFAULT_VAULT_ROOT,
    val indexDir: String = "./.wikilens/index",
    val stateDir: String = "./.wikilens/state",
    /**
     * 본문 분석기: `korean`(기본) · `english` · `standard`.
     *
     * **바꾸면 반드시 재색인해야 한다.** 색인과 질의가 다른 분석기를 쓰면 예외가 아니라
     * 조용히 0건이 된다. 선택은 색인 커밋 데이터에 기록되고 기동 시 대조한다
     * (`LuceneIndex.checkAnalyzerMatches`).
     *
     * 학습 궤적도 영향을 받는다 — `trajectories.jsonl` 에는 **분석된 항**이 저장돼
     * 있어서, 분석기를 바꾸면 옛 항과 새 질의가 안 맞아 그만큼 학습이 무효가 된다.
     * 궤적은 유일한 복구 불가 자산이므로 지우지는 않는다.
     */
    val analyzer: String = "korean",
    /**
     * 질의 시점 ACL 시행. **기본은 켜짐.**
     *
     * 끄면 등록 없이 전원이 전 문서를 본다. 볼트를 서비스 계정 하나로 싱크했고 **그
     * 권한 범위를 이 서버에 닿는 전원이 공유해도 되는** 배포에서만 정당하다
     * (개인 서버·개발·신뢰 경계 안의 소규모 팀). 자세한 근거는 [AclRegistry].
     */
    val aclEnforced: Boolean = true,
    /**
     * `/api/admin` 하위 공유 토큰. **비어 있으면 관리 API 가 전부 404 다.**
     *
     * 열어두는 것을 기본으로 하면 조용히 열린 채 배포된다 — 서버에 닿는 누구나
     * 스스로 권한을 부여할 수 있게 된다. 자세한 근거는 [dev.wikilens.api.AdminGuard].
     */
    val adminToken: String = "",
    /**
     * 본문 스캔 엔진: `auto`(기본) · `jvm` · `ripgrep`.
     *
     * `auto` 는 rg 가 있으면 rg 다 — 문서당 스캔 비용이 약 3.3배 싸서 예산에 닿는
     * 지점을 그만큼 뒤로 민다. 한계를 "몇 건" 으로 적지 않는 이유와 다시 재는 법은
     * `ContentService.GREP_BUDGET_NANOS` 와 `GrepScaleTest` 에 있다.
     * 두 경로가 같은 답을 내는지는 `GrepEngineParityTest` 가 지킨다.
     */
    val grepEngine: String = "auto",
    @NestedConfigurationProperty val learn: LearnProps = LearnProps(),
) {
    companion object {
        /**
         * 폴백이 걸리는지 판단하려면 "사용자가 값을 줬는가"를 알아야 하는데,
         * Spring 은 기본값과 명시값을 구분해주지 않는다. 기본값을 상수로 두고
         * 같은지 비교하는 것이 그 구분을 얻는 방법이다.
         *
         * `application.yml` 에도 같은 값이 적혀 있다 — 계약이 둘의 일치를 검사한다.
         */
        const val DEFAULT_VAULT_ROOT = "./mirror-root"
    }
}
