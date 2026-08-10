package dev.wikilens.api

import dev.wikilens.config.WikiLensProperties
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.slf4j.LoggerFactory
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Component
import org.springframework.web.server.ResponseStatusException
import org.springframework.web.servlet.HandlerInterceptor
import java.security.MessageDigest

/**
 * `/api/admin` 하위 전체의 접근 통제.
 *
 * **없으면 서버에 닿는 누구나 `POST /api/admin/acl/user?userKey=자기자신` 으로 스스로
 * 권한을 부여한다.** 권한을 아무리 정확히 수집해도 이게 열려 있으면 의미가 없다.
 *
 * ### 왜 공유 토큰인가
 *
 * 셋을 놓고 골랐다(`DECISIONS.md` D18):
 *
 *   - **리버스 프록시(SSO)** — 유일하게 `userKey` 위조까지 막지만 **인프라를 요구한다.**
 *     없는 환경에서는 아무 보호도 못 받는다.
 *   - **루프백 바인딩** — 가장 싸지만 서버를 팀에 공유하는 순간 성립하지 않는다.
 *   - **공유 토큰** — 인프라가 필요 없고, 프록시를 쓸 거면 그 뒤에 두면 그만이다.
 *
 * 공유 토큰을 **바닥**으로 깐다. 프록시가 있으면 그 위에 겹치면 되고, 없어도 최소한의
 * 보호가 있다. 이것으로 못 푸는 것은 문서에 적었다 — `userKey` 는 여전히 자기주장이다.
 *
 * ### 기본값이 "잠김" 이다
 *
 * 토큰을 안 주면 관리 API 가 **전부 404** 다. 열어두는 것을 기본으로 하면 조용히 열린 채
 * 배포되고, 그건 이 프로젝트에서 가장 나쁜 상태다. 404 인 이유는 read 와 같다 —
 * 403 은 "그 엔드포인트가 있다"를 알려준다.
 *
 * 재색인처럼 운영자가 자주 부르는 것도 함께 잠긴다. 그게 맞다 — 재색인을 아무나
 * 부를 수 있으면 서비스 거부가 된다.
 *
 * ### 엔드포인트마다 부르지 않는다 — **경로로 걸린다**
 *
 * 예전에는 각 핸들러가 `guard.check(req)` 를 직접 불렀다. 그러면 새 엔드포인트를
 * 추가하는 사람이 **기억해야** 하고, 잊으면 열린 채로 겉보기 정상이다. `AclRegistry`
 * 에 스위치를 한 곳만 둔 것과 같은 이유로 여기도 한 곳이어야 한다.
 *
 * 계약이 세고 있었지만 `@PostMapping` 만 셌다 — 실측: 인증 없는
 * `@GetMapping("/api/admin/dump")` 를 넣어도 계약이 통과했다. 다른 컨트롤러 파일에
 * 넣어도 마찬가지였다. 세는 것으로는 다 못 막는다.
 *
 * 이제 [AdminGuardConfig] 가 `/api/admin` 하위 전체에 인터셉터로 건다. 메서드·파일과
 * 무관하게 걸리므로 잊을 자리가 없다.
 */
@Component
class AdminGuard(private val props: WikiLensProperties) : HandlerInterceptor {

    /** `/api/admin` 하위 전체에 매핑돼 핸들러보다 먼저 돈다. 통과 못 하면 여기서 끝난다. */
    override fun preHandle(req: HttpServletRequest, res: HttpServletResponse, handler: Any): Boolean {
        check(req)
        return true
    }

    private val log = LoggerFactory.getLogger(javaClass)

    init {
        if (props.adminToken.any { it.code > 127 }) {
            // 비교는 아래에서 바이트로 하므로 **동작한다.** 다만 헤더 값에 비ASCII 를
            // 쓰는 것은 RFC 7230 밖이라 중간 프록시가 손대면 그 순간 전부 404 가 되고,
            // 그 상태는 "토큰이 틀렸다"·"안 켰다" 와 구별되지 않는다.
            log.warn("관리 토큰에 ASCII 밖 문자가 있습니다 — 프록시를 거치면 깨질 수 있습니다.")
        }
        if (props.adminToken.isBlank()) {
            log.warn(
                "관리 API 가 잠겨 있습니다 (wikilens.admin-token 미설정) — " +
                    "/api/admin/* 이 전부 404 입니다. 사용자 등록·재색인을 하려면 토큰을 주세요.",
            )
        }
    }

    /**
     * 통과하지 못하면 404 를 던진다.
     *
     * **비교는 상수 시간이다.** `==` 는 첫 불일치에서 멈춰 응답 시간이 맞은 자릿수를
     * 흘린다 — 관리 API 라 호출 횟수 제한도 없다.
     *
     * **바이트로 비교한다.** 서블릿은 헤더를 ISO-8859-1 로 디코드하는데 설정값은
     * UTF-8 이라, 문자열끼리 비교하면 **비ASCII 토큰이 영원히 안 맞는다** — 실측:
     * `admin-token=테스트토큰` 으로 띄우고 같은 값을 헤더로 보냈는데 404 였다.
     * 게다가 기동 경고는 값이 **비었을 때만** 나오므로 아무 단서가 없다.
     */
    fun check(req: HttpServletRequest) {
        val expected = props.adminToken
        if (expected.isBlank()) throw ResponseStatusException(HttpStatus.NOT_FOUND)

        // ISO-8859-1 로 되돌리면 클라이언트가 보낸 **원래 바이트**가 나온다.
        val given = req.getHeader("X-WikiLens-Admin").orEmpty().toByteArray(Charsets.ISO_8859_1)
        val ok = MessageDigest.isEqual(given, expected.toByteArray(Charsets.UTF_8))
        if (!ok) {
            log.warn("관리 API 인증 실패: {} {}", req.method, req.requestURI)
            throw ResponseStatusException(HttpStatus.NOT_FOUND)
        }
    }
}
