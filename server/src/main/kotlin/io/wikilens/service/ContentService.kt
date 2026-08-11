package io.wikilens.service

/*
 * `api/` 에서 분리했다. 거기엔 HTTP 표면(`Controller`·`Dto`)만 남는다 —
 * 한 패키지가 "라우팅"과 "무엇을 하는가"를 함께 갖고 있으면, 검색 랭킹을 고치려는
 * 사람과 엔드포인트를 추가하려는 사람이 같은 자리를 연다.
 */

import io.wikilens.api.GrepResponse
import io.wikilens.api.ReadResponse

import io.wikilens.acl.AclRegistry
import io.wikilens.index.LuceneIndex
import io.wikilens.vault.VaultLayout
import io.wikilens.config.WikiLensProperties
import io.wikilens.vault.VaultLocator
import io.wikilens.vault.VaultText
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.nio.file.Files

/**
 * 콘텐츠 서빙.
 *
 * **서버가 본문을 서빙하는 것이 배포보다 낫다** — 배포된 사본은 회수할 수 없어 권한
 * 취소가 불가능해진다. 서빙하면 매 요청 ACL 이 다시 걸린다. 부수 효과로 훅이 불필요해진다:
 * 읽기가 서버를 거치므로 서버가 궤적을 직접 관측한다.
 */
@Service
class ContentService(
    private val acl: AclRegistry,
    private val index: LuceneIndex,
    private val locator: VaultLocator,
    private val ripgrep: RipgrepEngine,
    private val fallback: JvmGrepEngine,
    private val props: WikiLensProperties,
) {
    /**
     * **기동 시 한 번 정한다** — 요청마다 달라지면 같은 질의가 다른 경로로 처리되고 그
     * 갈림을 아무도 못 본다. 두 경로의 답이 같은지는 `GrepEngineParityTest` 가 지킨다.
     */
    private val engine: GrepEngine = when (props.grepEngine.lowercase()) {
        "jvm" -> fallback
        "ripgrep" -> ripgrep
        else -> if (ripgrep.isAvailable()) ripgrep else fallback
    }
    /**
     * 어느 엔진인지. **밖에서 보여야 한다** — 응답의 `engine` 은 grep 을 던져야 보이고
     * 기동 로그는 콘솔 전용이라, `/api/stats`·`--status` 가 운영자에게 닿는 유일한 길이다.
     */
    val engineName: String get() = engine.name

    /** `grep-engine=ripgrep` 인데 rg 가 없으면 **매 요청이 폴백**이고 겉으로는 정상이다. */
    val engineUsable: Boolean get() = engine.isAvailable()

    private val log = LoggerFactory.getLogger(ContentService::class.java)


    fun read(pageId: String, userKey: String?): ReadResponse? {
        if (!acl.canSee(userKey, pageId)) return null   // 존재 여부도 알리지 않는다
        val meta = index.metaOf(pageId) ?: return null
        val f = locator.root.resolve(VaultLayout.relPagePath(pageId))
        if (!Files.exists(f)) return null
        // 읽기 실패를 그냥 null 로 돌리면 **권한 없음과 똑같은 404** 가 되어, 디스크
        // 문제가 "그런 문서 없음" 으로 보인다. 응답은 그대로 두되(존재 비노출) 로그로 남긴다.
        val body = runCatching { VaultText.reader(f).use { it.readText() } }
            .onFailure { log.warn("페이지 {} 를 읽지 못했습니다 ({}). 404 로 응답합니다.", pageId, f, it) }
            .getOrNull() ?: return null
        return ReadResponse(pageId, meta.title, meta.space, body)
    }

    /**
     * 리터럴 검색. 권한 있는 문서만 스캔한다. Lucene 이 아니라 실제 파일 스캔인 이유는
     * 형태소 분석을 안 거친 정확 일치가 필요해서다(식별자·코드 조각·정확한 문구).
     *
     * **정규식 엔진은 RE2 다 — `java.util.regex` 가 아니다.** 표준 엔진은 재귀
     * 백트래킹이라 사용자 패턴 하나로 둘이 났다(실측): `(.+)+@@@@` 는 20초를 넘겨도 안
     * 끝나 스레드를 묶었고, `(a|aa)+c` 는 스택을 넘겨 HTTP 500 이 됐다. 상한 셋으로
     * 하나씩 막았지만 전부 **증상을 재는 장치**였다 — RE2 는 유한 오토마타라 두 실패가
     * 원리적으로 없다. ripgrep 과 같은 계열이라 **두 경로가 갈리지 않는 근거**이기도
     * 하다(`DECISIONS.md` D12).
     *
     * 대가는 역참조·전방탐색을 못 쓰는 것이다(ripgrep 도 같다). 쓰면 조용히 0건이 되지
     * 않게 `error` 로 돌려준다.
     *
     * 남은 상한은 안전장치가 아니라 자원 배분이다 — 패턴 길이 · 시간 예산(백트래킹이
     * 아니라 **I/O** 를 끊는다) · `limit`(응답 크기).
     */
    fun grep(
        pattern: String,
        userKey: String?,
        limit: Int,
        regex: Boolean,
        // 테스트가 예산을 줄여 폭발적 패턴을 몇 초가 아니라 밀리초에 확인하게 한다.
        budgetNanos: Long = GREP_BUDGET_NANOS,
    ): GrepResponse {
        val tokens = acl.tokensFor(userKey)
        if (tokens.isEmpty() || pattern.isBlank()) {
            return GrepResponse(pattern, 0, emptyList(), false)
        }
        // 거부 이유는 알려준다. "왜 거부됐는지가 탐색 수단이 된다" 는 **ACL 에만**
        // 해당한다 — 침묵하면 "쓸 수 없는 질의" 와 "정말 일치 없음" 이 똑같이 0건이 된다.
        if (pattern.length > MAX_PATTERN) {
            return GrepResponse(pattern, 0, emptyList(), false, "패턴이 너무 깁니다 (최대 $MAX_PATTERN 자)")
        }
        // 상한이 없으면 매치당 300자 객체가 매치 줄 수만큼 쌓인다 — 16.4만 줄 볼트에
        // `.` 한 글자면 65MB, 10만 문서면 수 GB 다. 잘린 것은 `truncated` 가 알린다.
        val cap = limit.coerceIn(1, MAX_LIMIT)

        // **ACL 은 여기서 한 번만 건다** — 엔진에는 통과한 목록만 넘어간다. 권한 해석이
        // 엔진마다 갈리면 한쪽이 조용히 더 보여준다.
        //
        // **볼트도 루프 밖에서 한 번 푼다.** `locator.root` 는 폴백을 볼 때 stat 두 번 +
        // config.json 파싱이라 문서마다 부르면 그만큼이 스캔에 얹힌다(실측: 2,383회 66ms).
        val vaultRoot = locator.root
        val visible = index.allMeta()
            .filter { acl.canSee(tokens, it.id) }
            .map { PageRef(it.id, it.title) }

        val q = GrepQuery(vaultRoot, visible, pattern, regex, cap, budgetNanos)
        var used = engine
        var out = used.search(q)
        if (!out.usable) {
            // rg 를 띄우지 못했다 — 조용히 0건을 주면 "일치 없음" 과 구별되지 않는다.
            log.warn("{} 엔진이 동작하지 않아 {} 로 넘어갑니다", used.name, fallback.name)
            used = fallback
            out = used.search(q)
        }
        return GrepResponse(pattern, out.scanned, out.matches, out.truncated, out.error, used.name)
    }

    companion object {
        /** 사람이 쓰는 질의가 넘을 일이 없는 선. RE2 로 바꾼 뒤로는 안전장치가 아니다. */
        const val MAX_PATTERN = 200

        /**
         * 전체 시간 예산. **이제 막는 것은 백트래킹이 아니라 I/O 다** — RE2 로 바꾼
         * 뒤로 폭발적 패턴이 없어졌고, 남은 비용은 파일을 읽는 시간이다. 넘으면 실패가
         * 아니라 `truncated=true` 다 — 부분 결과가 침묵보다 낫다.
         *
         * **한계를 "몇 건" 으로 적지 않는다.** 그건 그 코퍼스의 평균 문서 크기를 숨긴
         * 채 옮겨지는 값이다. 코퍼스와 무관한 형태는 이것이고, `GrepScaleTest` 가
         * 합성 볼트에서 두 점을 재어 유도한다:
         *
         *     문서당 비용 ≈ 28 us (파일 고정비) + 13 us/KB     (JVM · 이 머신)
         *     한계 문서 수 = 예산 / 문서당 비용
         *
         * 예: 평균 4KB 문서면 문서당 약 80us → 3초에 약 37,000건. 문서가 4배 두꺼우면
         * 한계도 그만큼 내려간다. rg 는 문서당 비용이 약 3~4배 싸다.
         *
         * **예전에 여기 적혀 있던 `13,921건 2.44초(문서당 175us) → 약 17,000건 한계` 는
         * 재현되지 않는다.** 같은 하네스로 같은 코퍼스를 다시 재니 1.21초(문서당 86us)
         * 였고, 그것은 위 모델이 그 코퍼스의 평균 문서 크기(3.9KB)로 예측한 80us 와
         * 8% 안에서 맞는다. 옛 값이 어떻게 나왔는지는 복원할 수 없다 — 그래서 상수를
         * 적어두는 대신 **다시 잴 수 있는 장치**를 둔다.
         */
        const val GREP_BUDGET_NANOS = 3_000_000_000L

        /** 클라이언트가 요청할 수 있는 최대 매치 수. 기본값 40 의 25배까지 허용한다. */
        const val MAX_LIMIT = 1_000
    }
}
