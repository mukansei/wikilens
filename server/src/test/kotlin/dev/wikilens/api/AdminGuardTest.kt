package dev.wikilens.api

import dev.wikilens.config.WikiLensProperties
import jakarta.servlet.http.HttpServletRequest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import org.springframework.http.HttpStatus
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.web.server.ResponseStatusException

/**
 * 관리 API 통제.
 *
 * 없으면 서버에 닿는 누구나 `acl/user` 로 스스로 권한을 부여한다 — 권한을 정확히
 * 수집해도 이게 열려 있으면 의미가 없다.
 */
class AdminGuardTest {

    private fun req(token: String? = null): HttpServletRequest =
        MockHttpServletRequest("POST", "/api/admin/acl/user").apply {
            if (token != null) addHeader("X-WikiLens-Admin", token)
        }

    private fun guard(token: String) = AdminGuard(WikiLensProperties(adminToken = token))

    private fun status(block: () -> Unit): HttpStatus =
        assertFailsWith<ResponseStatusException> { block() }.statusCode as HttpStatus

    /** **기본값이 잠김이어야 한다.** 열림이 기본이면 조용히 열린 채 배포된다. */
    @Test
    fun `토큰을 설정하지 않으면 전부 막힌다`() {
        val g = guard("")
        assertEquals(HttpStatus.NOT_FOUND, status { g.check(req()) })
        // 아무 토큰이나 준다고 열리면 안 된다.
        assertEquals(HttpStatus.NOT_FOUND, status { g.check(req("아무거나")) })
    }

    @Test
    fun `맞는 토큰만 통과한다`() {
        val g = guard("s3cret")
        g.check(req("s3cret"))                                    // 예외가 나면 실패
        assertEquals(HttpStatus.NOT_FOUND, status { g.check(req("s3cre")) })
        assertEquals(HttpStatus.NOT_FOUND, status { g.check(req("s3cret ")) })
        assertEquals(HttpStatus.NOT_FOUND, status { g.check(req()) })
    }

    /**
     * **403 이 아니라 404 다.** 403 은 "그 엔드포인트가 있다"를 알려준다 —
     * `read` 가 권한 없음을 404 로 내는 것과 같은 이유다.
     */
    @Test
    fun `거부는 존재를 알리지 않는다`() {
        assertEquals(HttpStatus.NOT_FOUND, status { guard("t").check(req("틀림")) })
    }
}
