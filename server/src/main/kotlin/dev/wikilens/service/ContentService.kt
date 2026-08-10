package dev.wikilens.service

/*
 * `api/` 에서 분리했다. 거기엔 HTTP 표면(`Controller`·`Dto`)만 남는다 —
 * 한 패키지가 "라우팅"과 "무엇을 하는가"를 함께 갖고 있으면, 검색 랭킹을 고치려는
 * 사람과 엔드포인트를 추가하려는 사람이 같은 자리를 연다.
 */

import dev.wikilens.api.GrepMatch
import dev.wikilens.api.GrepResponse
import dev.wikilens.api.ReadResponse

import dev.wikilens.acl.AclRegistry
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultLayout
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.vault.VaultLocator
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.io.BufferedReader
import java.io.InputStreamReader
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path

/**
 * 콘텐츠 서빙.
 *
 * **서버가 본문을 서빙하는 것이 배포보다 낫다.** 배포된 사본은 회수할 수 없어
 * 권한 취소가 불가능해진다. 서버가 서빙하면 매 요청마다 ACL을 다시 확인하므로
 * 권한 변경이 즉시 반영된다.
 *
 * 부수 효과로 훅이 통째로 불필요해진다 — 읽기가 서버를 거치므로 서버가 궤적을
 * 직접 관측한다. 클라이언트 버퍼링도, 핫 패스 비용도, 세션 조립도 사라진다.
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
     * 어느 엔진으로 스캔하나. **기동 시 한 번 정하고 로그에 남긴다** — 요청마다 달라지면
     * 같은 질의가 다른 경로로 처리되고, 그 갈림을 아무도 못 본다.
     *
     * `auto` 는 rg 가 있으면 rg 다. 머신에 따라 경로가 달라지는 것이 마음에 걸리지만,
     * 두 경로가 같은 답을 내는지는 `GrepEngineParityTest` 가 지킨다. 고정하고 싶으면
     * `wikilens.grep-engine=jvm|ripgrep` 으로 명시한다.
     */
    private val engine: GrepEngine = when (props.grepEngine.lowercase()) {
        "jvm" -> fallback
        "ripgrep" -> ripgrep
        else -> if (ripgrep.isAvailable()) ripgrep else fallback
    }
    /**
     * 어느 엔진으로 정해졌는지. **밖에서 보여야 한다.**
     *
     * 응답의 `engine` 은 grep 을 한 번 던져야 보이고 기동 로그는 콘솔로만 나간다 —
     * 로그를 못 보는 운영자에게는 닿지 않는다. `/api/stats` 와 `--status` 가 이것을 낸다.
     */
    val engineName: String get() = engine.name

    /**
     * 그 엔진이 지금 쓸 수 있나. **`grep-engine=ripgrep` 을 명시했는데 rg 가 없으면
     * 매 요청이 폴백**이고, 동작은 하므로 겉으로는 정상이다(로그의 WARN 뿐이다).
     */
    val engineUsable: Boolean get() = engine.isAvailable()

    private val log = LoggerFactory.getLogger(ContentService::class.java)


    fun read(pageId: String, userKey: String?): ReadResponse? {
        if (!acl.canSee(userKey, pageId)) return null   // 존재 여부도 알리지 않는다
        val meta = index.metaOf(pageId) ?: return null
        val f = locator.root.resolve(VaultLayout.relPagePath(pageId))
        if (!Files.exists(f)) return null
        // 읽기 실패를 그냥 null 로 돌리면 **권한 없음과 똑같은 404** 가 되어, 디스크
        // 문제가 "그런 문서 없음" 으로 보인다. 응답은 그대로 두되(존재 비노출) 로그로 남긴다.
        val body = runCatching { lenientReader(f).use { it.readText() } }
            .onFailure { log.warn("페이지 {} 를 읽지 못했습니다 ({}). 404 로 응답합니다.", pageId, f, it) }
            .getOrNull() ?: return null
        return ReadResponse(pageId, meta.title, meta.space, body)
    }

    /**
     * 리터럴 검색. 권한 있는 문서만 스캔한다.
     *
     * Lucene 질의가 아니라 실제 파일 스캔인 이유: 형태소 분석을 거치지 않은
     * 정확 일치가 필요한 경우가 있다(식별자, 코드 조각, 정확한 문구).
     * 실측(2026-08-06): 2,383 문서 전체 스캔 0.64초.
     *
     * **정규식 엔진은 RE2 다 — `java.util.regex` 가 아니다.** 표준 엔진은 재귀
     * 백트래킹이라 사용자 패턴 하나로 두 가지가 났다(둘 다 실측):
     *
     *   - `(.+)+@@@@` — 요청이 20초를 넘겨도 안 끝나고 스레드가 영구히 묶임
     *   - `(a|aa)+c` + 매치 실패하는 긴 줄 — 스택을 넘겨 0.02초 만에 HTTP 500
     *
     * 시간 예산·줄 길이 상한·`StackOverflowError` 삼키기로 하나씩 막았지만, 전부
     * **증상을 재는 장치**였다. RE2 는 유한 오토마타라 입력 길이에 선형이고 재귀를
     * 안 써서 두 실패가 **원리적으로 없다.** ripgrep 이 쓰는 것과 같은 계열이라,
     * 10만 규모에서 속도 때문에 rg 서브프로세스를 붙이더라도 두 경로가 같은 답을
     * 낸다 — 문법이 다르면 조용히 갈리는 자리다(`DECISIONS.md` D12).
     *
     * 대가는 **역참조와 전방탐색을 못 쓰는 것**이다. ripgrep 도 같은 이유로 못 쓴다.
     * 어휘 격차를 메우는 것이 이 프로젝트의 일이고 그 둘이 필요한 질의는 없었다.
     * 다만 쓰면 **조용히 0건이 되지 않게** `error` 로 돌려준다 — 정규식 문법 오류는
     * ACL 과 달리 코퍼스에 대해 아무것도 알려주지 않으므로 숨길 이유가 없다.
     *
     * 남은 상한은 안전장치가 아니라 자원 배분이다:
     *   - 패턴 길이 — 사람이 쓰는 질의가 넘을 일이 없는 선
     *   - 시간 예산 — 백트래킹이 아니라 **I/O** 를 끊는다(10만 문서면 27초다)
     *   - `limit` — 응답 크기
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
        // 거부된 이유는 알려준다. 한때 침묵시켰는데, 근거였던 "왜 거부됐는지가 탐색
        // 수단이 된다" 는 **ACL 에만** 해당한다 — 패턴이 길다거나 문법이 틀렸다는 것은
        // 코퍼스에 대해 아무것도 말해주지 않는다. 침묵하면 "쓸 수 없는 질의" 와
        // "정말 일치가 없음" 이 똑같이 0건으로 보일 뿐이다.
        if (pattern.length > MAX_PATTERN) {
            return GrepResponse(pattern, 0, emptyList(), false, "패턴이 너무 깁니다 (최대 $MAX_PATTERN 자)")
        }
        // limit 은 클라이언트가 정한다. 상한이 없으면 매치 하나당 최대 300자를 담은
        // 객체가 매치 줄 수만큼 쌓인다 — 이 볼트가 16.4만 줄이니 `.` 한 글자로 65MB,
        // 10만 문서면 수 GB 다. 잘린 것은 `truncated` 가 이미 알려준다.
        val cap = limit.coerceIn(1, MAX_LIMIT)

        // **ACL 은 여기서 한 번만 건다.** 엔진에는 통과한 목록만 넘어간다 — 권한 해석이
        // 엔진마다 갈리면 한쪽이 조용히 더 보여준다.
        //
        // **루프 밖에서 볼트를 한 번 푼다.** `locator.root` 는 폴백을 볼 때 stat 두 번 +
        // config.json 파싱이라, 문서마다 부르면 스캔에 그만큼이 통째로 얹힌다
        // (실측: 2,383회 66ms — 스캔 전체가 0.64초였다).
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

    /**
     * 볼트 파일 읽기. **깨진 바이트를 예외가 아니라 대체 문자로 넘긴다.**
     *
     * `Files.newBufferedReader`·`readString` 의 기본 디코더는 `CodingErrorAction.REPORT`
     * 라, 잘못된 바이트를 만나면 **읽는 도중에** `MalformedInputException` 을 던진다.
     * 파일 열기만 `runCatching` 으로 감싸도 소용없다 — 터지는 건 열 때가 아니라 줄을
     * 당길 때이고, 그대로 위로 나가 **HTTP 500** 이 된다. grep 은 파일 하나 때문에
     * 나머지 문서 전부를 잃었다.
     *
     * 볼트는 Python 싱크가 UTF-8 로 쓰므로 정상 경로에서는 안 생긴다. 다만 디스크가
     * 차거나 싱크가 도중에 죽으면 멀티바이트 문자가 반토막 난 파일이 남고, 그 한 개가
     * 전원의 검색을 죽인다. 건너뛰는 대신 `REPLACE` 로 읽는 이유는 깨진 지점만
     * `U+FFFD` 가 되고 **나머지 본문은 온전히 검색·열람되기** 때문이다.
     */
    private fun lenientReader(f: Path): BufferedReader {
        val dec = StandardCharsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPLACE)
            .onUnmappableCharacter(CodingErrorAction.REPLACE)
        return BufferedReader(InputStreamReader(Files.newInputStream(f), dec))
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
