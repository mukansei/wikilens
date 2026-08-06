package dev.wikilens.api

import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultLayout
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
    private val props: WikiLensProperties,
) {
    private val root: Path get() = Path.of(props.vaultRoot)

    fun read(pageId: String, userKey: String?): ReadResponse? {
        if (!acl.canSee(userKey, pageId)) return null   // 존재 여부도 알리지 않는다
        val meta = index.metaOf(pageId) ?: return null
        val f = root.resolve(VaultLayout.relPagePath(pageId))
        if (!Files.exists(f)) return null
        val body = runCatching { lenientReader(f).use { it.readText() } }.getOrNull() ?: return null
        return ReadResponse(pageId, meta.title, meta.space, body)
    }

    /**
     * 리터럴 검색. 권한 있는 문서만 스캔한다.
     *
     * Lucene 질의가 아니라 실제 파일 스캔인 이유: 형태소 분석을 거치지 않은
     * 정확 일치가 필요한 경우가 있다(식별자, 코드 조각, 정확한 문구).
     * 실측(2026-08-06): 2,383 문서 전체 스캔 0.64초.
     *
     * **`regex=true` 는 사용자 정규식을 그대로 실행하는 자리다.** JVM 정규식은
     * 백트래킹이라 `(.+)+@@@@` 같은 패턴 하나로 CPU 를 무한히 태운다 — 실측으로
     * 요청이 20초를 넘겨도 안 끝났고, ACL 이 fail-closed 라도 **등록된 사용자면
     * 누구나** 서버 스레드를 영구히 묶을 수 있었다. 그래서 아래 셋을 건다:
     *
     *   1. 패턴 길이 상한 — 폭발적 패턴은 대개 길다
     *   2. 전체 시간 예산 — 넘으면 지금까지 찾은 것으로 `truncated=true`
     *   3. 줄 길이 상한 — 한 줄이 길수록 백트래킹이 폭발적으로 는다
     *
     * ripgrep 으로 바꾸면 유한 오토마타라 이 문제가 원리적으로 사라지지만, 서버
     * 배포에 rg 바이너리를 요구하게 된다(jar 하나로 뜨던 것이 깨진다). 지금
     * 규모에서는 얻는 게 속도뿐이라 미뤘다 — 10만 규모의 재판단은 `DECISIONS.md` D12.
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
        // 잘못된 패턴과 너무 긴 패턴을 같게 취급한다 — 둘 다 "이 질의로는 못 찾는다"이고,
        // 왜 거부됐는지 알려주면 그 자체가 탐색 수단이 된다.
        if (pattern.length > MAX_PATTERN) return GrepResponse(pattern, 0, emptyList(), false)
        // limit 은 클라이언트가 정한다. 상한이 없으면 매치 하나당 최대 300자를 담은
        // 객체가 매치 줄 수만큼 쌓인다 — 이 볼트가 16.4만 줄이니 `.` 한 글자로 65MB,
        // 10만 문서면 수 GB 다. 잘린 것은 `truncated` 가 이미 알려준다.
        val cap = limit.coerceIn(1, MAX_LIMIT)
        val rx = if (regex) runCatching { Regex(pattern) }.getOrNull() else null
        if (regex && rx == null) return GrepResponse(pattern, 0, emptyList(), false)

        val matches = ArrayList<GrepMatch>()
        var scanned = 0
        var truncated = false
        val deadline = System.nanoTime() + budgetNanos

        for (meta in index.allMeta()) {
            // limit 이 찼는데 아직 볼 문서가 남았다 = 잘렸다.
            if (matches.size >= cap) { truncated = true; break }
            if (System.nanoTime() > deadline) { truncated = true; break }
            // 루프 밖에서 한 번 계산한 tokens 를 재사용한다 (문서마다 재조회하면 수천 회).
            if (!acl.canSee(tokens, meta.id)) continue
            val f = root.resolve(VaultLayout.relPagePath(meta.id))
            // exists + open 두 번 왕복하는 대신 열어보고 실패하면 넘어간다.
            val reader = runCatching { lenientReader(f) }.getOrNull() ?: continue
            scanned++
            reader.useLines { lines ->
                // forEach + return@forEach 는 그 줄만 건너뛸 뿐 파일 잔여를 계속 읽는다.
                // for + break 라야 실제로 읽기를 멈춘다.
                for ((i, line) in lines.withIndex()) {
                    if (matches.size >= cap) { truncated = true; break }
                    // 줄 단위로도 예산을 본다. 문서 경계에서만 보면 파일 하나가 통째로
                    // 예산을 넘겨도 못 끊는다. 매 줄 시계를 읽으면 그 자체가 비용이라
                    // 64줄마다 본다.
                    if ((i and LINE_CHECK_MASK) == 0 && System.nanoTime() > deadline) {
                        truncated = true; break
                    }
                    // 백트래킹 비용은 줄 길이에 비선형이다. 긴 줄은 잘라서 본다.
                    val target = if (line.length > MAX_LINE) line.take(MAX_LINE) else line
                    if (matchesLine(rx, pattern, target)) {
                        matches.add(GrepMatch(meta.id, meta.title, i + 1, line.trim().take(300)))
                    }
                }
            }
        }
        return GrepResponse(pattern, scanned, matches, truncated)
    }

    /**
     * 한 줄 매칭. **`StackOverflowError` 를 여기서 삼킨다.**
     *
     * JVM 정규식은 재귀로 백트래킹하므로 깊이가 스택을 넘기면 `Error` 가 난다 —
     * 예외가 아니라 `Error` 라 `runCatching` 의 일반적인 용법으로도 안 잡히고, 그대로
     * 위로 던져져 **HTTP 500** 이 됐다(실측: `(a|aa)+c` + 매치 안 되는 5,000자 줄).
     * 시간 예산으로는 못 막는다 — 터지는 데 0.02초밖에 안 걸린다.
     *
     * 그 줄만 건너뛴다. 한 줄이 스택을 넘겼다고 나머지 문서까지 버릴 이유가 없고,
     * 사용자에겐 "이 패턴으로는 그 줄을 못 본다"가 500 보다 낫다.
     */
    private fun matchesLine(rx: Regex?, pattern: String, target: String): Boolean =
        try {
            rx?.containsMatchIn(target) ?: target.contains(pattern, ignoreCase = true)
        } catch (e: StackOverflowError) {
            false
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
        /** 폭발적 정규식은 대개 길다. 정상 질의가 이 길이를 넘을 일은 없다. */
        const val MAX_PATTERN = 200

        /**
         * 한 줄에서 검사할 최대 길이. 백트래킹 비용이 줄 길이에 비선형이라,
         * 예산만으로는 **한 줄**에 갇히는 것을 못 막는다.
         */
        const val MAX_LINE = 4_000

        /**
         * 전체 시간 예산. 실측 전체 스캔이 0.64초이므로 정상 질의는 여유롭게 들어오고,
         * 폭발적 패턴은 여기서 끊긴다. 넘으면 실패가 아니라 `truncated=true` 다 —
         * 부분 결과가 침묵보다 낫고, 클라이언트가 이미 그 플래그를 표시한다.
         */
        const val GREP_BUDGET_NANOS = 3_000_000_000L

        /** 클라이언트가 요청할 수 있는 최대 매치 수. 기본값 40 의 25배까지 허용한다. */
        const val MAX_LIMIT = 1_000

        /** 예산 검사 간격(줄). 63 = 64줄마다 — 시계 읽기가 매칭보다 비싸지 않게. */
        const val LINE_CHECK_MASK = 63
    }
}
