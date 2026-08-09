package dev.wikilens.service

import com.fasterxml.jackson.databind.ObjectMapper
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import org.junit.jupiter.api.Assumptions.assumeTrue
import org.junit.jupiter.api.io.TempDir

/**
 * **두 엔진이 같은 답을 내는지 대조한다.**
 *
 * 경로가 둘인 것 자체는 피할 수 없다 — rg 가 없는 머신이 있어 폴백이 영구히 공존한다.
 * 그러면 남은 선택은 "갈리는지 모르는 채로 두기" 와 "갈리면 빨개지게 하기" 뿐이고,
 * 이 파일이 후자다. RE2 를 고른 이유 자체가 rg 와 같은 계열이라 이 대조가 성립한다는
 * 것이었다(`DECISIONS.md` D12).
 *
 * rg 가 없는 머신에서는 **건너뛴다** — 없는 것을 검사할 수는 없고, 그 머신은 어차피
 * JVM 경로만 쓴다.
 */
class GrepEngineParityTest {

    private val jvm = JvmGrepEngine()
    private val rg = RipgrepEngine(ObjectMapper())

    /** 대소문자·한글·정규식·특수문자·유니코드를 고루 밟는다. */
    private val patterns = listOf(
        "coway" to false, "COWAY" to false, "Coway" to false,   // 대소문자 무시 계약
        "루트" to false, "자식" to false, "없는말zzz" to false,
        "루.트" to true, "^이" to true, "다\\.$" to true,
        "[가-힣]+" to true, "\\d+" to true, "(자식|루트)" to true,
        "." to true, "본문|없음" to true,
    )

    private fun vault(tmp: Path): Path {
        val root = tmp.resolve("v")
        Files.createDirectories(root.resolve("mirror").resolve("pages").resolve("00"))
        // 픽스처를 복사하지 않고 직접 만든다 — 대조에 필요한 것은 다양성이지
        // 골든 픽스처와의 일치가 아니다.
        mapOf(
            "100" to "# 루트\n이 문서는 루트다. Coway 2,383건.\n표: | a | b |\n",
            "200" to "# 자식A\n여기서 시작하려면 루트로 가라.\ncoway 소문자.\n마지막 줄.\n",
            "300" to "# 고아B\n본문 없음.\nCOWAY 대문자 3개.\n",
        ).forEach { (id, body) ->
            Files.writeString(root.resolve("mirror/pages/00/$id.md"), body)
        }
        return root
    }

    private fun pages() = listOf(
        PageRef("100", "루트", "mirror/pages/00/100.md"),
        PageRef("200", "자식A", "mirror/pages/00/200.md"),
        PageRef("300", "고아B", "mirror/pages/00/300.md"),
    )

    @Test
    fun `두 엔진이 같은 매치를 낸다`(@TempDir tmp: Path) {
        assumeTrue(rg.isAvailable(), "이 머신에 ripgrep 이 없다")
        val root = vault(tmp)

        for ((pattern, regex) in patterns) {
            val q = GrepQuery(root, pages(), pattern, regex, cap = 1000,
                              budgetNanos = 10_000_000_000L)
            val a = jvm.search(q)
            val b = rg.search(q)

            fun key(o: GrepOutcome) = o.matches
                .map { Triple(it.pageId, it.line, it.text) }
                .sortedWith(compareBy({ it.first }, { it.second }))

            assertEquals(a.error != null, b.error != null,
                "패턴 '$pattern'(regex=$regex): 한쪽만 문법 오류라고 했다 " +
                    "(jvm=${a.error} rg=${b.error})")
            if (a.error != null) continue
            assertEquals(key(a), key(b), "패턴 '$pattern'(regex=$regex) 에서 답이 갈렸다")
        }
    }

    /** 두 엔진 모두 `-i` 계약을 지켜야 한다 — 판을 옮긴 사용자가 다른 답을 받으면 안 된다. */
    @Test
    fun `두 엔진 모두 대소문자를 무시한다`(@TempDir tmp: Path) {
        val root = vault(tmp)
        val engines = listOfNotNull(jvm, rg.takeIf { it.isAvailable() })
        for (e in engines) {
            val lower = e.search(GrepQuery(root, pages(), "coway", false, 1000, 10_000_000_000L))
            val upper = e.search(GrepQuery(root, pages(), "COWAY", false, 1000, 10_000_000_000L))
            assertEquals(lower.matches.size, upper.matches.size, "${e.name}: 대소문자로 갈렸다")
            assertTrue(lower.matches.size >= 3, "${e.name}: 세 문서 모두에서 찾아야 한다")
        }
    }

    /** 백트래킹 문법은 **둘 다** 거부해야 한다 — 한쪽만 받으면 답이 갈린다. */
    @Test
    fun `두 엔진 모두 역참조를 거부한다`(@TempDir tmp: Path) {
        val root = vault(tmp)
        val q = GrepQuery(vault(tmp), pages(), "(a)\\1", true, 1000, 10_000_000_000L)
        assertTrue(jvm.search(q).error != null, "jvm 이 역참조를 받았다")
        if (rg.isAvailable()) assertTrue(rg.search(q).error != null, "rg 가 역참조를 받았다")
    }

    /** ACL 로 걸러진 문서는 rg 가 읽더라도 **응답에 실리면 안 된다.** */
    @Test
    fun `엔진은 넘겨받은 목록 밖을 내보내지 않는다`(@TempDir tmp: Path) {
        assumeTrue(rg.isAvailable(), "이 머신에 ripgrep 이 없다")
        val root = vault(tmp)
        val onlyOne = listOf(pages()[0])          // 100 만 볼 수 있다
        val out = rg.search(GrepQuery(root, onlyOne, "coway", false, 1000, 10_000_000_000L))
        assertTrue(out.matches.all { it.pageId == "100" },
            "권한 밖 문서가 새어 나왔다: ${out.matches.map { it.pageId }}")
    }
}
