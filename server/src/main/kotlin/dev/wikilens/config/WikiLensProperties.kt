package dev.wikilens.config

import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.boot.context.properties.NestedConfigurationProperty

@ConfigurationProperties(prefix = "wikilens")
data class WikiLensProperties(
    /** Python `wikilens sync` 가 만든 미러 루트. 서비스 계정으로 1회만 싱크한다. */
    val vaultRoot: String = "./mirror-root",
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
)

data class LearnProps(
    /** EB 하한이 이 값 미만이면 힌트를 서빙하지 않는다. */
    val serveThreshold: Double = 0.45,
    /** 앞 질의와 키워드가 이만큼 겹치면 앞 시도를 실패로 본다. */
    val reformulationOverlap: Double = 0.5,
    /**
     * 버려진 세션을 거두는 주기(ms). `SessionSweeper` 가 이 주기마다 돌면서
     * [sessionIdleMillis] 넘게 조용한 세션을 확정한다.
     */
    val sweepIntervalMillis: Long = 300_000,
    /** 이만큼 조용하면 세션이 끝난 것으로 본다. */
    val sessionIdleMillis: Long = 1_800_000,
)
