package io.wikilens.api

import org.springframework.context.annotation.Configuration
import org.springframework.web.servlet.config.annotation.InterceptorRegistry
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer

/**
 * [AdminGuard] 를 `/api/admin` 하위 전체에 건다.
 *
 * **경로로 거는 것이 요점이다.** 핸들러가 각자 부르면 새 엔드포인트를 추가하는 사람이
 * 기억해야 하고, 잊으면 열린 채로 겉보기 정상이다 — 그리고 그 실수는 "누구나 스스로
 * 권한을 부여할 수 있다" 로 곧장 이어진다. 여기 한 줄이면 메서드·파일과 무관하게 걸린다.
 */
@Configuration
class AdminGuardConfig(private val guard: AdminGuard) : WebMvcConfigurer {
    override fun addInterceptors(registry: InterceptorRegistry) {
        registry.addInterceptor(guard).addPathPatterns(ADMIN_PATHS)
    }

    companion object {
        const val ADMIN_PATHS = "/api/admin/**"
    }
}
