package io.wikilens.api

import io.wikilens.config.WikiLensProperties
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
 * 권한을 부여한다** — 권한을 아무리 정확히 수집해도 이게 열려 있으면 의미가 없다.
 *
 * 공유 토큰을 **바닥**으로 깐다(D18). 리버스 프록시(SSO)만이 `userKey` 위조까지 막지만
 * 인프라를 요구하고, 루프백 바인딩은 팀에 공유하는 순간 성립하지 않는다. 프록시가 있으면
 * 그 위에 겹치면 된다 — 이것만으로는 `userKey` 가 여전히 자기주장이다.
 *
 * **기본값이 잠김이다.** 토큰을 안 주면 전부 404 — 열림이 기본이면 조용히 열린 채
 * 배포되고 그게 이 프로젝트에서 가장 나쁜 상태다. 403 이 아닌 이유는 read 와 같다.
 *
 * **엔드포인트마다 부르지 않고 [AdminGuardConfig] 가 경로로 건다.** 핸들러가 각자 부르면
 * 새 엔드포인트를 추가하는 사람이 기억해야 하고, 잊으면 열린 채로 겉보기 정상이다.
 * 계약이 세고는 있었지만 `@PostMapping` 만 세서 인증 없는 `@GetMapping` 이 통과했다
 * (실측) — **세는 것으로는 다 못 막는다.**
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
            // **바이트 비교라 가드는 통과시킨다**(`AdminInterceptorTest` 가 그 경로를
            // 재현한다). 그런데 전선에서 못 쓴다 — 헤더 값에 비ASCII 는 RFC 7230 밖이라
            // **표준 클라이언트가 전송 자체를 거부한다**(실측: JDK HTTP 클라이언트가
            // `IllegalArgumentException: invalid header value`). 프록시 이전의 문제다.
            log.warn(
                "관리 토큰에 ASCII 밖 문자가 있습니다 — 표준 HTTP 클라이언트가 이 헤더를 " +
                    "보내지 못합니다. **ASCII 로 바꾸세요.**",
            )
        }
        if (props.adminToken.isBlank()) {
            log.warn(
                "관리 API 가 잠겨 있습니다 (wikilens.admin-token 미설정) — " +
                    "/api/admin/* 이 전부 404 입니다. 사용자 등록·재색인을 하려면 토큰을 주세요.",
            )
        }
    }

    /**
     * 통과하지 못하면 404.
     *
     * **비교는 상수 시간**이다 — `==` 는 첫 불일치에서 멈춰 맞은 자릿수를 흘리고, 관리
     * API 라 횟수 제한도 없다. **그리고 바이트로 비교한다**: 서블릿은 헤더를 ISO-8859-1
     * 로 디코드하는데 설정값은 UTF-8 이라 문자열 비교면 **비ASCII 토큰이 영원히 안
     * 맞는다**(실측). 기동 경고는 값이 비었을 때만 나오므로 단서도 없다.
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
