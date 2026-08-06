package dev.wikilens.api

import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultLayout
import org.springframework.stereotype.Service
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
        return ReadResponse(pageId, meta.title, meta.space, Files.readString(f))
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
        val rx = if (regex) runCatching { Regex(pattern) }.getOrNull() else null
        if (regex && rx == null) return GrepResponse(pattern, 0, emptyList(), false)

        val matches = ArrayList<GrepMatch>()
        var scanned = 0
        var truncated = false
        val deadline = System.nanoTime() + budgetNanos

        for (meta in index.allMeta()) {
            // limit 이 찼는데 아직 볼 문서가 남았다 = 잘렸다.
            if (matches.size >= limit) { truncated = true; break }
            // 시간 예산은 문서 경계에서만 본다 — 줄마다 보면 그 자체가 비용이다.
            if (System.nanoTime() > deadline) { truncated = true; break }
            // 루프 밖에서 한 번 계산한 tokens 를 재사용한다 (문서마다 재조회하면 수천 회).
            if (!acl.canSee(tokens, meta.id)) continue
            val f = root.resolve(VaultLayout.relPagePath(meta.id))
            // exists + open 두 번 왕복하는 대신 열어보고 실패하면 넘어간다.
            val reader = runCatching { Files.newBufferedReader(f) }.getOrNull() ?: continue
            scanned++
            reader.useLines { lines ->
                // forEach + return@forEach 는 그 줄만 건너뛸 뿐 파일 잔여를 계속 읽는다.
                // for + break 라야 실제로 읽기를 멈춘다.
                for ((i, line) in lines.withIndex()) {
                    if (matches.size >= limit) { truncated = true; break }
                    // 한 줄 안에서도 예산을 본다. **정규식 하나가 이 줄에서 안 끝나면
                    // 파일 경계까지 못 가므로, 문서 단위 검사만으로는 못 막는다.**
                    if ((i and LINE_CHECK_MASK) == 0 && System.nanoTime() > deadline) {
                        truncated = true; break
                    }
                    // 백트래킹 비용은 줄 길이에 비선형이다. 긴 줄은 잘라서 본다.
                    val target = if (line.length > MAX_LINE) line.take(MAX_LINE) else line
                    val hit = rx?.containsMatchIn(target)
                        ?: target.contains(pattern, ignoreCase = true)
                    if (hit) {
                        matches.add(GrepMatch(meta.id, meta.title, i + 1, line.trim().take(300)))
                    }
                }
            }
        }
        return GrepResponse(pattern, scanned, matches, truncated)
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

        /** 예산 검사 간격(줄). 63 = 64줄마다 — 시계 읽기가 매칭보다 비싸지 않게. */
        const val LINE_CHECK_MASK = 63
    }
}
