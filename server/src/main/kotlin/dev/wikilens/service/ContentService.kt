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
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultLayout
import com.google.re2j.Pattern as Re2
import com.google.re2j.PatternSyntaxException
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
    private val props: WikiLensProperties,
) {
    private val log = LoggerFactory.getLogger(ContentService::class.java)

    private val root: Path get() = Path.of(props.vaultRoot)

    fun read(pageId: String, userKey: String?): ReadResponse? {
        if (!acl.canSee(userKey, pageId)) return null   // 존재 여부도 알리지 않는다
        val meta = index.metaOf(pageId) ?: return null
        val f = root.resolve(VaultLayout.relPagePath(pageId))
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
        // RE2 는 역참조·전방탐색을 파싱 단계에서 거부한다. 그 메시지를 그대로 넘기는
        // 편이 낫다 — 사용자는 `\1` 을 쓴 줄도 모르고 "일치 없음" 을 볼 것이다.
        var rx: Re2? = null
        if (regex) {
            try {
                // **CASE_INSENSITIVE 는 리터럴 경로와 맞추려는 것이다.** 없으면 `regex`
                // 토글이 문법뿐 아니라 대소문자 민감도까지 바꾼다 — 실측: 본문이
                // `Coway` 일 때 `coway` 가 리터럴 1건 · 정규식 0건. 도구 설명은 이
                // 플래그가 문법만 바꾼다고 말하므로 그대로면 설명이 거짓이 된다.
                // 나중에 rg 프로세스를 붙인다면 `-i` 를 함께 넘겨야 답이 같다.
                rx = Re2.compile(pattern, Re2.CASE_INSENSITIVE)
            } catch (e: PatternSyntaxException) {
                return GrepResponse(pattern, 0, emptyList(), false, syntaxError(e))
            }
        }

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
                    // 줄을 자르지 않는다. RE2 는 줄 길이에 선형이라 자를 이유가 없고,
                    // 자르면 긴 줄(표 등) 뒤쪽의 일치를 **조용히 놓친다.**
                    if (rx?.matcher(line)?.find() ?: line.contains(pattern, ignoreCase = true)) {
                        matches.add(GrepMatch(meta.id, meta.title, i + 1, line.trim().take(300)))
                    }
                }
            }
        }
        return GrepResponse(pattern, scanned, matches, truncated)
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

    /**
     * RE2 의 파싱 오류를 사용자가 고칠 수 있는 말로 바꾼다.
     *
     * 원문은 ``error parsing regexp: invalid escape sequence: `\1` `` 처럼 나오는데,
     * 왜 안 되는지(엔진이 다르다)를 모르면 고칠 수가 없다.
     */
    private fun syntaxError(e: PatternSyntaxException): String {
        val why = when {
            "invalid escape sequence" in (e.message ?: "") -> "역참조(\\1)는 쓸 수 없습니다"
            "Perl syntax" in (e.message ?: "") -> "전방탐색((?=), (?!))은 쓸 수 없습니다"
            else -> "정규식 문법 오류"
        }
        return "$why — 이 서버는 ripgrep 과 같은 RE2 엔진을 씁니다. ${e.message}"
    }

    companion object {
        /** 사람이 쓰는 질의가 넘을 일이 없는 선. RE2 로 바꾼 뒤로는 안전장치가 아니다. */
        const val MAX_PATTERN = 200

        /**
         * 전체 시간 예산. **이제 막는 것은 백트래킹이 아니라 I/O 다** — RE2 로 바꾼
         * 뒤로 폭발적 패턴이 없어졌고, 남은 비용은 파일을 읽는 시간이다(10만 문서면
         * 27초, `DECISIONS.md` D12). 넘으면 실패가 아니라 `truncated=true` 다 —
         * 부분 결과가 침묵보다 낫고, 클라이언트가 이미 그 플래그를 표시한다.
         */
        const val GREP_BUDGET_NANOS = 3_000_000_000L

        /** 클라이언트가 요청할 수 있는 최대 매치 수. 기본값 40 의 25배까지 허용한다. */
        const val MAX_LIMIT = 1_000

        /** 예산 검사 간격(줄). 63 = 64줄마다 — 시계 읽기가 매칭보다 비싸지 않게. */
        const val LINE_CHECK_MASK = 63
    }
}
