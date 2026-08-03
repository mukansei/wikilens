package dev.wikilens.acl

import org.springframework.stereotype.Component
import java.util.concurrent.ConcurrentHashMap

/**
 * 페이지별 접근 권한.
 *
 * **권한 변경은 `lastModified` 를 건드리지 않는다.** 콘텐츠 증분 싱크로는 잡히지 않으므로
 * ACL 싱크를 분리해 더 자주 돌려야 한다. 그러지 않으면 더는 볼 수 없게 된 페이지를
 * 계속 서빙하게 되고, 공유 서버에서는 그것이 전 사용자에게 나간다.
 */
@Component
class AclRegistry {
    /** pageId -> 이 페이지를 볼 수 있는 토큰들 (그룹 키, 사용자 키, 공개 마커) */
    private val byPage = ConcurrentHashMap<String, Set<String>>()

    /** userKey -> 그 사용자가 가진 토큰들 */
    private val byUser = ConcurrentHashMap<String, Set<String>>()

    fun putPage(pageId: String, tokens: Collection<String>) { byPage[pageId] = tokens.toSet() }
    fun putUser(userKey: String, tokens: Collection<String>) { byUser[userKey] = tokens.toSet() + userKey }
    fun tokensOf(pageId: String): Set<String> = byPage[pageId] ?: emptySet()

    /**
     * 요청자의 권한 토큰. **모르는 사용자에게는 빈 집합을 준다.**
     * 실수로 전체가 노출되는 것보다 아무것도 안 나오는 편이 낫다.
     */
    fun tokensFor(userKey: String?): Set<String> =
        if (userKey.isNullOrBlank()) emptySet() else byUser[userKey] ?: emptySet()

    fun canSee(userKey: String?, pageId: String): Boolean {
        val u = tokensFor(userKey)
        return u.isNotEmpty() && tokensOf(pageId).any { it in u }
    }

    fun pageCount(): Int = byPage.size
    fun userCount(): Int = byUser.size
}
