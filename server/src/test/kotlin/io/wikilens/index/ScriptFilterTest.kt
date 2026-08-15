package io.wikilens.index

import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * 다국어 코퍼스에서 읽지 못하는 언어의 문서를 뺀다.
 *
 * 실측이 이 설계를 정했다(2026-08-14, 한·베 혼재 코퍼스 13,933건):
 * 한국어 질의에 **베트남어 번역본이 1위, 한국어 원본이 10위 밖**이었고, 두 문서가
 * 같은 영문 식별자를 공유해 **어휘 층으로는 원리적으로 못 가른다.**
 */
class ScriptFilterTest {

    private fun page(title: String, body: String) = IndexedPage(
        id = "1", title = title, space = "SP", path = "p/1.md",
        body = body, anchors = emptyList(), aclTokens = listOf("@public"),
    )

    @Test
    fun `선언이 비면 전부 통과한다`() {
        val f = ScriptFilter(emptyList(), 0.30)
        assertFalse(f.enabled)
        assertTrue(f.accepts(page("タイトル", "日本語の本文です")))
        assertEquals("꺼짐", f.describe)
    }

    /**
     * **낱말 단위인 것이 이 설계의 핵심이다.** 베트남어는 글자 대부분이 평범한 라틴이고
     * 성조부호만 다르다 — 글자로 세면 신호가 4배 묽어진다(실측: 같은 문서가 글자 4.17%
     * 대 낱말 16.6%). 낱말에 선언 밖 글자가 하나라도 있으면 그 낱말을 밖으로 세야,
     * 글자마다 다른 한자와 부호만 다른 베트남어가 같은 척도로 잡힌다.
     */
    @Test
    fun `베트남어는 글자가 아니라 낱말로 잡힌다`() {
        val f = ScriptFilter(listOf("hangul", "ascii"), 0.30)
        val vi = "Sử dụng GCP Console để truy cập"
        val ch = vi.count { !it.isLetter() || it.code < 128 }
        assertTrue(f.foreignWordRatio(vi) > 0.5,
                   "낱말 단위면 절반 넘게 잡혀야 한다 (글자로 세면 훨씬 낮다: 라틴 $ch 자)")
        assertEquals(0.0, f.foreignWordRatio("GA 접속 방법 문서"))
    }

    @Test
    fun `선언하면 그 문자 집합이 통과한다`() {
        val vi = page("Truy cập", "Sử dụng GCP Console để truy cập tài khoản")
        assertFalse(ScriptFilter(listOf("hangul", "ascii"), 0.30).accepts(vi))
        assertTrue(ScriptFilter(listOf("hangul", "ascii", "vietnamese"), 0.30).accepts(vi))
        assertTrue(ScriptFilter(listOf("hangul", "latin"), 0.30).accepts(vi),
                   "latin 은 베트남어를 포함한다")
    }

    /**
     * **숫자·기호만 있는 토큰은 분모에서 뺀다.** 안 그러면 코드·URL 이 많은 문서가
     * 무조건 통과한다 — 이 코퍼스는 본문의 46%가 ASCII 다.
     */
    @Test
    fun `숫자와 기호는 낱말로 안 센다`() {
        val f = ScriptFilter(listOf("hangul", "ascii"), 0.30)
        assertEquals(0.0, f.foreignWordRatio("2026 v1.2 --- 한글 문서"),
                     "숫자·기호가 분모에 들어가면 비율이 희석된다")
        // 분모가 낱말 둘(`Sử`·`한글`)이라 0.5 다. 숫자 `2026` 은 분모에 없다.
        assertEquals(0.5, f.foreignWordRatio("Sử 한글 2026"))
    }

    /**
     * **`ascii` 를 빼면 코드·URL·영문이 전부 밖이 된다.** 이 코퍼스는 본문의 46%가
     * ASCII 라 `[hangul]` 만 선언하면 거의 전 문서가 걸린다 — 쓸 수 있는 조합이 아니다.
     * 설정을 읽는 사람이 이걸 모르면 색인이 통째로 비는데 그 상태가 조용하다.
     */
    @Test
    fun `ascii 를 안 넣으면 영문 낱말이 전부 밖이다`() {
        val f = ScriptFilter(listOf("hangul"), 0.30)
        assertEquals(1.0, f.foreignWordRatio("Jenkins deploy pipeline"))
        assertFalse(f.accepts(page("", "GA 접속 GCP Console")))
    }

    @Test
    fun `제목만 다른 언어여도 잡힌다`() {
        val f = ScriptFilter(listOf("hangul"), 0.30)
        assertFalse(f.accepts(page("Chính sách bảo mật thông tin khách hàng", "정책")))
    }

    /** 모르는 이름을 조용히 무시하면 운영자는 필터가 걸린 줄 알고 배포한다(D14 와 같은 규칙). */
    @Test
    fun `모르는 이름은 기동을 실패시킨다`() {
        val e = assertFailsWith<IllegalArgumentException> { ScriptFilter(listOf("french"), 0.3) }
        assertTrue(e.message!!.contains("hangul"), "가능한 이름을 알려줘야 고칠 수 있다")
    }

    /** 언어를 다 이름 붙일 수 없다는 것이 전제이므로 **문자로 말할 길**을 연다. */
    @Test
    fun `이름 대신 범위를 직접 쓸 수 있다`() {
        val f = ScriptFilter(listOf("ascii", "U+0100-017F"), 0.30)
        // `Łąka` 는 Ł(U+0141)·ą(U+0105) 로 확장-A 안에 있다. 반면 `ó`(U+00F3)는
        // **라틴-1 보충**이라 이 선언 밖이다 — 폴란드어를 다 받으려면 그것도 적어야 한다.
        assertEquals(0.0, f.foreignWordRatio("Łąka"), "라틴 확장-A 를 선언했다")
        assertTrue(f.foreignWordRatio("Łódź") > 0.0, "ó 는 라틴-1 이라 선언 밖이다")
        assertTrue(f.foreignWordRatio("Sử dụng") > 0.0, "확장 추가는 선언 안 했다")
    }

    @Test
    fun `문턱이 경계에서 어느 쪽인가`() {
        // 낱말 넷 중 하나가 밖 = 0.25
        val text = "Sử 한글 문서 입니다"
        assertTrue(ScriptFilter(listOf("hangul"), 0.25).accepts(page("", text)), "문턱과 같으면 통과")
        assertFalse(ScriptFilter(listOf("hangul"), 0.24).accepts(page("", text)))
    }
}
