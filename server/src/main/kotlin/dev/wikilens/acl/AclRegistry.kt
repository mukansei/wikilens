package dev.wikilens.acl

import org.springframework.stereotype.Component
import java.security.MessageDigest
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

    fun canSee(userKey: String?, pageId: String): Boolean = canSee(tokensFor(userKey), pageId)

    /**
     * 토큰을 이미 계산해 둔 경우용. grep 처럼 문서 수천 개를 도는 루프에서
     * 문서마다 `tokensFor` 를 다시 조회하지 않게 한다.
     */
    fun canSee(tokens: Set<String>, pageId: String): Boolean =
        tokens.isNotEmpty() && tokensOf(pageId).any { it in tokens }

    /**
     * 이 요청자의 **권한 범위 식별자**. 궤적 로그에 남겨 학습을 범위별로 볼 수 있게 한다.
     *
     * **`userKey` 를 남기지 않는 것이 요점이다.** 해결하려는 문제가 셋인데 전부
     * *권한 폭*의 문제이지 *신원*의 문제가 아니다:
     *
     *   - 권한이 좁은 사용자는 진짜 정답을 못 봐서 차선책을 읽고, 그 궤적이
     *     **전 사용자의 포스팅**을 오염시킨다(D4 가 클라이언트 분산 색인을 기각한
     *     바로 그 이질성이 ACL 을 통해 서버판으로 되돌아온다).
     *   - `rank` 가중(×1/×2/×3)이 권한 폭에 편향된다 — 목록이 짧으면 같은 문서가
     *     낮은 순위로 나와 같은 발견이 다른 무게로 기록된다.
     *   - 권한이 바뀌었을 때 어느 궤적이 그 범위에서 나왔는지 골라낼 수 없다.
     *     궤적 로그는 append-only 이고 유일한 복구 불가 자산이다.
     *
     * 셋 다 범위만 알면 풀리고, 신원은 필요 없다. 신원을 남기면 "누가 무엇을
     * 검색했나"가 영구 기록으로 남는데 그건 이 도구가 지금 안 하는 일이다.
     *
     * **지금은 기록만 한다.** 이 값으로 가중을 바꾸거나 포스팅을 쪼개는 것은 별도
     * 설계이고 측정이 필요하다. 로그가 커지기 전에 자리를 잡아두는 것이 목적이다 —
     * 나중에 넣으면 그전 궤적에는 영영 없다.
     *
     * 토큰을 정렬해 해싱하므로 같은 권한을 가진 사람은 같은 값이 된다. 12자면
     * 충돌 확률이 무시할 만하고(범위 수는 많아야 수백), 원문 토큰은 복원되지 않는다.
     */
    fun scopeOf(userKey: String?): String {
        val tokens = tokensFor(userKey)
        if (tokens.isEmpty()) return ""
        // 사용자 키 자체는 `putUser` 가 토큰 집합에 넣어두므로 빼고 해싱한다 —
        // 안 빼면 사람마다 다른 값이 되어 "범위" 가 아니라 신원이 된다.
        val scope = tokens.filterNot { it == userKey }.sorted().joinToString(" ")
        if (scope.isEmpty()) return ""
        val digest = MessageDigest.getInstance("SHA-256").digest(scope.toByteArray())
        return digest.take(6).joinToString("") { "%02x".format(it) }
    }

    fun pageCount(): Int = byPage.size
    fun userCount(): Int = byUser.size
}
