package dev.wikilens.service

import dev.wikilens.api.GrepMatch
import dev.wikilens.vault.VaultText
import com.google.re2j.Pattern as Re2
import com.google.re2j.PatternSyntaxException
import org.springframework.stereotype.Component

/**
 * JVM 내장 스캔. **의존성이 없어 항상 쓸 수 있다** — 그래서 폴백이다.
 *
 * 정규식 엔진은 RE2(`com.google.re2j`)다. 이유는 `ContentService.grep` 에 있고, 부수
 * 효과로 **ripgrep 과 같은 계열**이라 두 엔진의 답이 갈리지 않는다.
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
                // `Coway` 일 때 `coway` 가 리터럴 1건 · 정규식 0건. rg 에도 `-i` 를
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
            val reader = runCatching { VaultText.reader(f) }.getOrNull() ?: continue
            scanned++
            reader.useLines { lines ->
                // `forEach` + `return@forEach` 는 그 줄만 건너뛰고 파일을 계속 읽는다.
                for ((i, line) in lines.withIndex()) {
                    if (matches.size >= q.cap) { truncated = true; break }
                    // 줄 단위로도 예산을 본다 — 문서 경계에서만 보면 파일 하나가 통째로
                    // 예산을 넘겨도 못 끊는다. 시계 읽기 자체가 비용이라 64줄마다.
                    if ((i and LINE_CHECK_MASK) == 0 && System.nanoTime() > deadline) {
                        truncated = true; break
                    }
                    // 줄을 자르지 않는다 — RE2 는 줄 길이에 선형이고, 자르면 긴 줄(표 등)
                    // 뒤쪽의 일치를 **조용히 놓친다.**
                    if (rx?.matcher(line)?.find() ?: line.contains(q.pattern, ignoreCase = true)) {
                        matches.add(GrepMatch(p.id, p.title, i + 1, line.trim().take(SNIPPET)))
                    }
                }
            }
        }
        return GrepOutcome(scanned, matches, truncated)
    }

    companion object {
        /** 매치 줄을 담는 상한. 없으면 표 한 줄이 응답을 통째로 먹는다. */
        const val SNIPPET = 300

        /** 예산 확인 주기(줄). 매 줄 시계를 읽으면 그 자체가 비용이다. */
        const val LINE_CHECK_MASK = 63

        /**
         * 파싱 오류를 사용자가 고칠 수 있는 말로. 원문(``invalid escape sequence``)만으로는
         * 왜 안 되는지 모른다. **두 엔진이 같은 문구를 쓴다** — 제약이 같기 때문이다.
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
