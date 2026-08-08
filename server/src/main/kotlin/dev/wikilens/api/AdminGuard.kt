package dev.wikilens.api

import dev.wikilens.config.WikiLensProperties
import jakarta.servlet.http.HttpServletRequest
import org.slf4j.LoggerFactory
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Component
import org.springframework.web.server.ResponseStatusException
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
 */
@Component
class AdminGuard(private val props: WikiLensProperties) {

    private val log = LoggerFactory.getLogger(javaClass)

    init {
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
     */
    fun check(req: HttpServletRequest) {
        val expected = props.adminToken
        if (expected.isBlank()) throw ResponseStatusException(HttpStatus.NOT_FOUND)

        val given = req.getHeader("X-WikiLens-Admin").orEmpty()
        val ok = MessageDigest.isEqual(given.toByteArray(), expected.toByteArray())
        if (!ok) {
            log.warn("관리 API 인증 실패: {} {}", req.method, req.requestURI)
            throw ResponseStatusException(HttpStatus.NOT_FOUND)
        }
    }
}
