package io.wikilens.service

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import io.wikilens.api.GrepMatch
import io.wikilens.vault.VaultText
import java.util.Base64
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Component

/**
 * ripgrep 프로세스로 스캔한다. 문서당 비용이 JVM 스캔의 **약 1/3~1/4** 다.
 *
 * D12 의 "65배" 는 셸에서 **워밍 없이** 잰 값이라 이 맥락에서는 틀리다. 그 차이를 "ACL
 * 목록 고정비를 두 엔진이 똑같이 낸다" 로 설명한 적도 있는데 **그것도 틀렸다** —
 * 고정비는 전체의 0.18% 다. 재현 장치는 `GrepScaleTest`.
 *
 * **왜 필요한가:** JVM 스캔이 예산에 닿으면 `truncated=true` 가 상시화돼 뒤쪽 문서가
 * 영영 안 읽히는데, 실패가 아니라 조용한 부분 응답이라 사용자에게는 "없다" 로 보인다.
 *
 * **파일 목록이 아니라 디렉터리를 준다** — ACL 통과 경로만 넘기는 편이 정확하지만
 * 13,921개면 `ARG_MAX` 를 넘는다. 통째로 주고 **결과를 받아서 거른다**: 못 볼 문서까지
 * 읽는 낭비가 있지만 유출은 아니고, 3~4배가 그 낭비를 덮는다. `scanned` 는 ACL 을
 * 통과한 문서 수로 보고한다(rg 는 중간에 안 멈추므로 전량을 본 것이 맞다).
 *
 * **`--no-config`·`--no-ignore` 가 필수다** — 없으면 운영자의 `~/.ripgreprc` 나 볼트의
 * `.gitignore` 가 **같은 질의에 머신마다 다른 답**을 내게 한다.
 */
@Component
class RipgrepEngine(private val mapper: ObjectMapper) : GrepEngine {

    private val log = LoggerFactory.getLogger(javaClass)
    override val name = "ripgrep"

    /** 한 번만 확인하고 기억한다. 요청마다 프로세스를 띄울 이유가 없다. */
    private val available: Boolean by lazy {
        runCatching {
            val p = ProcessBuilder("rg", "--version").redirectErrorStream(true).start()
            val ok = p.waitFor(5, TimeUnit.SECONDS) && p.exitValue() == 0
            if (!ok) p.destroyForcibly()
            ok
        }.getOrDefault(false).also {
            if (it) log.info("ripgrep 을 찾았습니다 — 본문 스캔에 사용합니다")
            else log.info("ripgrep 이 없습니다 — JVM 스캔을 씁니다(느리지만 동작은 같습니다)")
        }
    }

    override fun isAvailable() = available

    override fun search(q: GrepQuery): GrepOutcome {
        val visible = q.pages.associateBy { it.id }
        val cmd = buildList {
            add("rg"); add("--json")
            add("--no-config")          // 운영자의 ~/.ripgreprc 가 답을 바꾸면 안 된다
            add("--no-ignore")          // .gitignore 가 생겨도 조용히 빠지지 않게
            add("--no-messages")        // 읽기 실패는 JVM 경로처럼 조용히 건너뛴다
            add("-i")                   // 대소문자 무시 — 두 판이 함께 지키는 계약이다
            add("--glob"); add("*.md")
            if (!q.regex) add("-F")     // 리터럴 모드
            add("--"); add(q.pattern)
            add(q.vaultRoot.resolve("mirror").resolve("pages").toString())
        }

        val proc = runCatching { ProcessBuilder(cmd).start() }.getOrElse {
            log.warn("ripgrep 을 띄우지 못했습니다: {}", it.message)
            return GrepOutcome(0, emptyList(), false, usable = false)
        }

        val matches = ArrayList<GrepMatch>()
        // rg 의 JSON 은 **매치가 있는 파일만** `begin` 을 낸다 — 실제로 훑은 수는 알 수
        // 없다. 그래서 잘렸을 때는 이것이 하한이고, 끝까지 갔으면 전량을 본 것이 맞다.
        val seen = HashSet<String>()
        var truncated = false
        val deadline = System.nanoTime() + q.budgetNanos
        val killed = AtomicBoolean(false)
        val watchdog = Thread {
            runCatching {
                if (!proc.waitFor(remainingMillis(deadline), TimeUnit.MILLISECONDS) && proc.isAlive) {
                    killed.set(true)
                    proc.destroyForcibly()
                }
            }
        }.apply { isDaemon = true; name = "rg-watchdog"; start() }
        try {
            // stdout 을 흘려 읽고 cap 이 차면 **바로 죽인다**(전량을 받아놓고 자르면 rg 가
            // 끝까지 도는 동안 기다린다). 감시 스레드가 죽이면 스트림은 EOF 가 아니라
            // `IOException` 으로 끝나는데 그건 **우리가 시킨 마감**이라, 던지지 않고
            // 그때까지 받은 매치를 살린다.
            runCatching {
                VaultText.reader(proc.inputStream).useLines { lines ->
                    for (line in lines) {
                        if (matches.size >= q.cap) { truncated = true; break }
                        val m = parseMatch(line, visible, seen) ?: continue
                        matches.add(m)
                    }
                }
            }.onFailure { if (!killed.get()) throw it }
            if (killed.get()) truncated = true
            // 거두는 것은 예산 밖이다. 남은 예산으로 기다리면 **완주한 스캔이 잘림으로**
            // 보고된다(위 실측). EOF 까지 읽었으면 rg 는 사실상 끝나 있다.
            val done = proc.waitFor(REAP_GRACE_MILLIS, TimeUnit.MILLISECONDS)
            // rg 는 일치 없음이 1, 오류가 2 다 — 문법 오류를 "0건" 으로 뭉개면 안 된다.
            // 다만 **이미 받은 매치가 있으면 오류로 안 본다**: cap 이 차서 파이프를 일찍
            // 닫으면 rg 가 브로큰 파이프로 죽고 그 종료 코드는 버전·플랫폼마다 다르다.
            // 문법 오류는 정의상 매치가 0 이라 이 조건으로 잃는 진단은 없다.
            if (done && proc.exitValue() >= 2 && matches.isEmpty() && !truncated) {
                val err = VaultText.reader(proc.errorStream).readText().trim()
                return GrepOutcome(0, emptyList(), false,
                    error = JvmGrepEngine.syntaxError(err))
            }
        } finally {
            watchdog.interrupt()
            if (proc.isAlive) proc.destroyForcibly()
        }
        // 끝까지 갔으면 전량을 본 것이다. 잘렸으면 아는 것은 매치가 나온 파일 수뿐이라
        // **하한**이다 — JVM 엔진도 잘리면 부분 수를 낸다.
        return GrepOutcome(if (truncated) seen.size else q.pages.size, matches, truncated)
    }

    /** rg 가 낸 한 줄(JSON)을 매치로. 우리 목록에 없는 문서(=권한 없음)는 버린다. */
    private fun parseMatch(line: String, visible: Map<String, PageRef>,
                          seen: MutableSet<String>): GrepMatch? {
        val node: JsonNode = runCatching { mapper.readTree(line) }.getOrNull() ?: return null
        if (node.path("type").asText() != "match") return null
        val data = node.path("data")
        val path = data.path("path").path("text").asText()
        val id = path.substringAfterLast('/').removeSuffix(".md")
        val page = visible[id] ?: return null          // ACL 로 걸러진 문서
        seen.add(id)
        val text = lineText(data.path("lines"))
        val no = data.path("line_number").asInt()
        return GrepMatch(page.id, page.title, no, text.trim().take(JvmGrepEngine.SNIPPET))
    }

    /**
     * `--json` 의 한 필드를 문자열로. **UTF-8 이 아닌 줄은 `text` 가 아니라
     * `bytes`(base64)로 온다** — rg 의 `--json` 규약이다.
     *
     * `text` 만 읽으면 그 줄의 스니펫이 **빈 문자열**이 된다(매치는 그대로 나오므로
     * 사용자에게는 "본문 없는 매치" 로 보인다). JVM 경로는 `VaultText` 의 REPLACE
     * 디코더로 U+FFFD 를 넣어 원문을 살리므로 **두 엔진이 갈렸다** — `GrepEngineParityTest`
     * 픽스처가 전부 `writeString` 이라 오래 안 보였다.
     *
     * `String(ByteArray, UTF_8)` 이 곧 REPLACE 다(잘못된 바이트열마다 U+FFFD). 그래서
     * 디코더를 따로 만들지 않는다 — [VaultText] 와 같은 결과를 낸다.
     */
    private fun lineText(lines: JsonNode): String {
        if (lines.has("text")) return lines.path("text").asText()
        val b64 = lines.path("bytes").asText("")
        if (b64.isEmpty()) return ""
        return runCatching { String(Base64.getDecoder().decode(b64), Charsets.UTF_8) }
            .getOrDefault("")
    }

    companion object {
        /**
         * EOF 뒤에 프로세스를 거두는 데 주는 여유. **예산과 무관하다** — 스캔은 이미
         * 끝났고 남은 것은 종료 처리뿐이라, 예산이 바닥났다는 이유로 잘렸다고 하면 안 된다.
         */
        private const val REAP_GRACE_MILLIS = 2_000L
    }

    private fun remainingMillis(deadline: Long): Long =
        ((deadline - System.nanoTime()) / 1_000_000).coerceAtLeast(1)

}
