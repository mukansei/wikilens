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
