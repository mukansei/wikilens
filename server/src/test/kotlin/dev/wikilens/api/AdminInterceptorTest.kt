package dev.wikilens.api

import dev.wikilens.config.WikiLensProperties
import org.springframework.http.MediaType
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController
import kotlin.test.Test

/**
 * **관리 API 는 엔드포인트가 아니라 경로로 잠긴다.**
 *
 * 예전에는 핸들러마다 `guard.check(req)` 를 직접 불렀다. 그러면 새 엔드포인트를 만드는
 * 사람이 기억해야 하고, 잊으면 열린 채로 겉보기 정상이다 — 그리고 그 실수는 "서버에
 * 닿는 누구나 스스로 권한을 부여할 수 있다" 로 곧장 이어진다.
 *
 * 계약이 개수를 세고 있었지만 `@PostMapping` 만 셌다. **실측: 인증 코드가 전혀 없는
 * `@GetMapping("/api/admin/dump")` 를 넣어도 계약 65개가 전부 통과했다.**
 *
 * 그래서 여기 있는 컨트롤러는 `AdminGuard` 를 **아예 모른다.** 그런데도 잠겨야 한다 —
 * 그것이 "잊을 자리가 없다" 의 뜻이다.
 */
class AdminInterceptorTest {

    /** 가드를 모르는 새 관리 엔드포인트. 실수로 추가된 것을 흉내낸다. */
    @RestController
    class ForgetfulController {
        @GetMapping("/api/admin/dump")
        fun dump() = mapOf("byUser" to "전부")

        @GetMapping("/api/stats")
        fun stats() = mapOf("ok" to true)
    }

    /**
     * **MockMvc 는 전선을 안 거친다** — 톰캣이 헤더를 ISO-8859-1 로 디코드하는 것을
     * 흉내내지 않고 준 문자열을 그대로 넘긴다. 그래서 여기 토큰은 ASCII 로 둔다.
     * 비ASCII 는 마지막 테스트가 그 디코딩을 손으로 재현해서 본다.
     */
    private fun mvc(token: String): MockMvc {
        val guard = AdminGuard(WikiLensProperties(adminToken = token))
        return MockMvcBuilders.standaloneSetup(ForgetfulController())
            .addMappedInterceptors(arrayOf(AdminGuardConfig.ADMIN_PATHS), guard)
            .build()
    }

    @Test
    fun `가드를 안 부르는 새 관리 엔드포인트도 토큰 없이는 404 다`() {
        mvc("s3cret").perform(get("/api/admin/dump"))
            .andExpect(status().isNotFound)
    }

    @Test
    fun `틀린 토큰도 404 다 - 403 은 엔드포인트의 존재를 알린다`() {
        mvc("s3cret").perform(get("/api/admin/dump").header("X-WikiLens-Admin", "nope"))
            .andExpect(status().isNotFound)
    }

    @Test
    fun `맞는 토큰이면 통과한다`() {
        mvc("s3cret").perform(get("/api/admin/dump").header("X-WikiLens-Admin", "s3cret"))
            .andExpect(status().isOk)
            .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers
                .content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
    }

    @Test
    fun `토큰이 비어 있으면 맞는 토큰이라는 것이 없다 - 기본은 잠김`() {
        mvc("").perform(get("/api/admin/dump").header("X-WikiLens-Admin", ""))
            .andExpect(status().isNotFound)
    }

    @Test
    fun `관리 경로가 아니면 안 걸린다`() {
        mvc("s3cret").perform(get("/api/stats")).andExpect(status().isOk)
    }

    /**
     * 서블릿은 헤더를 ISO-8859-1 로 디코드하는데 설정값은 UTF-8 이다. 문자열끼리
     * 비교하면 **비ASCII 토큰이 영원히 안 맞는다** — 실측으로 서버를 띄워 확인했다
     * (`admin-token=테스트토큰`, 같은 값을 헤더로 보내도 404). 게다가 기동 경고는
     * 값이 **비었을 때만** 나와서 아무 단서가 없다.
     */
    @Test
    fun `비ASCII 토큰도 맞으면 통과한다`() {
        val raw = "테스트토큰".toByteArray(Charsets.UTF_8)
        val asServletSeesIt = String(raw, Charsets.ISO_8859_1)   // 톰캣이 넘겨주는 모양
        mvc("테스트토큰").perform(get("/api/admin/dump").header("X-WikiLens-Admin", asServletSeesIt))
            .andExpect(status().isOk)
    }
}
