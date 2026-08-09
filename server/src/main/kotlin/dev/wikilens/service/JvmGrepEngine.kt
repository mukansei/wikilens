package dev.wikilens.service

import dev.wikilens.api.GrepMatch
import com.google.re2j.Pattern as Re2
import com.google.re2j.PatternSyntaxException
import java.io.BufferedReader
import java.io.InputStreamReader
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import org.springframework.stereotype.Component

/**
 * JVM 내장 스캔. **의존성이 없어 항상 쓸 수 있다** — 그래서 폴백이기도 하다.
 *
 * 정규식 엔진은 RE2(`com.google.re2j`)다. 표준 `java.util.regex` 는 재귀 백트래킹이라
 * 사용자 패턴 하나로 스레드가 묶이거나(`(.+)+@@@@`) 스택을 넘겨 500 이 났다.
 * RE2 를 고른 것은 **ripgrep 과 같은 계열**이라 두 엔진의 답이 갈리지 않게 하려는
 * 목적도 있었다 — 이제 실제로 그 대조를 한다(`GrepEngineParityTest`).
 */
@Component
class JvmGrepEngine : GrepEngine {
    override val name = "jvm"
    override fun isAvailable() = true

    override fun search(q: GrepQuery): GrepOutcome {
        var rx: Re2? = null
        if (q.regex) {
            try {
                // **CASE_INSENSITIVE 는 리터럴 경로와 맞추려는 것이다.** 없으면 `regex`
                // 토글이 문법뿐 아니라 대소문자 민감도까지 바꾼다 — 실측: 본문이
                // `Acme` 일 때 `acme` 가 리터럴 1건 · 정규식 0건. rg 에도 `-i` 를
                // 똑같이 넘긴다.
                rx = Re2.compile(q.pattern, Re2.CASE_INSENSITIVE)
            } catch (e: PatternSyntaxException) {
                return GrepOutcome(0, emptyList(), false, error = syntaxError(e.message))
            }
        }

        val matches = ArrayList<GrepMatch>()
        var scanned = 0
        var truncated = false
        val deadline = System.nanoTime() + q.budgetNanos

        for (p in q.pages) {
            // limit 이 찼는데 아직 볼 문서가 남았다 = 잘렸다.
            if (matches.size >= q.cap) { truncated = true; break }
            if (System.nanoTime() > deadline) { truncated = true; break }
            val f = q.vaultRoot.resolve(p.relPath)
            // exists + open 두 번 왕복하는 대신 열어보고 실패하면 넘어간다.
            val reader = runCatching { lenientReader(f) }.getOrNull() ?: continue
            scanned++
            reader.useLines { lines ->
                // forEach + return@forEach 는 그 줄만 건너뛸 뿐 파일 잔여를 계속 읽는다.
                // for + break 라야 실제로 읽기를 멈춘다.
                for ((i, line) in lines.withIndex()) {
                    if (matches.size >= q.cap) { truncated = true; break }
                    // 줄 단위로도 예산을 본다. 문서 경계에서만 보면 파일 하나가 통째로
                    // 예산을 넘겨도 못 끊는다. 매 줄 시계를 읽으면 그 자체가 비용이라
                    // 64줄마다 본다.
                    if ((i and LINE_CHECK_MASK) == 0 && System.nanoTime() > deadline) {
                        truncated = true; break
                    }
                    // 줄을 자르지 않는다. RE2 는 줄 길이에 선형이라 자를 이유가 없고,
                    // 자르면 긴 줄(표 등) 뒤쪽의 일치를 **조용히 놓친다.**
                    if (rx?.matcher(line)?.find() ?: line.contains(q.pattern, ignoreCase = true)) {
                        matches.add(GrepMatch(p.id, p.title, i + 1, line.trim().take(SNIPPET)))
                    }
                }
            }
        }
        return GrepOutcome(scanned, matches, truncated)
    }

    /**
     * 볼트 파일 읽기. **깨진 바이트를 예외가 아니라 대체 문자로 넘긴다.**
     *
     * 기본 디코더는 `CodingErrorAction.REPORT` 라 잘못된 바이트를 만나면 **읽는 도중에**
     * `MalformedInputException` 을 던진다. 파일 열기만 감싸도 소용없다 — 터지는 건 열 때가
     * 아니라 줄을 당길 때이고, 그대로 위로 나가 **HTTP 500** 이 된다. 파일 하나 때문에
     * 나머지 문서 전부를 잃었다. `REPLACE` 면 깨진 지점만 `U+FFFD` 가 되고 나머지 본문은
     * 온전히 검색된다 — rg 의 lossy 변환과 같은 태도다.
     */
    private fun lenientReader(f: Path): BufferedReader {
        val dec = StandardCharsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPLACE)
            .onUnmappableCharacter(CodingErrorAction.REPLACE)
        return BufferedReader(InputStreamReader(Files.newInputStream(f), dec))
    }

    companion object {
        /** 매치 줄을 담는 상한. 없으면 표 한 줄이 응답을 통째로 먹는다. */
        const val SNIPPET = 300

        /** 예산 확인 주기(줄). 매 줄 시계를 읽으면 그 자체가 비용이다. */
        const val LINE_CHECK_MASK = 63

        /**
         * RE2·rg 의 파싱 오류를 사용자가 고칠 수 있는 말로 바꾼다.
         *
         * 원문은 ``invalid escape sequence: `\1` `` 처럼 나오는데, 왜 안 되는지
         * (엔진이 유한 오토마타다)를 모르면 고칠 수가 없다. **두 엔진이 같은 문구를
         * 쓴다** — 같은 제약이고, 사용자에게 엔진을 알릴 이유가 없다.
         */
        fun syntaxError(raw: String?): String {
            val m = raw.orEmpty()
            val why = when {
                "backreference" in m || "invalid escape sequence" in m -> "역참조(\\1)는 쓸 수 없습니다"
                "look-around" in m || "Perl syntax" in m || "lookahead" in m ->
                    "전방탐색((?=), (?!))은 쓸 수 없습니다"
                else -> "정규식 문법 오류"
            }
            return "$why — 이 서버는 ripgrep 과 같은 계열의 유한 오토마타 엔진을 씁니다. $m"
        }
    }
}
