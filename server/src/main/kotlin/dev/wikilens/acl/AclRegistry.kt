package dev.wikilens.acl

import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap

/**
 * 페이지별 접근 권한.
 *
 * **권한 변경은 `lastModified` 를 건드리지 않는다.** 콘텐츠 증분 싱크로는 잡히지 않으므로
 * ACL 싱크를 분리해 더 자주 돌려야 한다. 그러지 않으면 더는 볼 수 없게 된 페이지를
 * 계속 서빙하게 되고, 공유 서버에서는 그것이 전 사용자에게 나간다.
 *
 * ### 시행을 끌 수 있다 ([enforced])
 *
 * **끄면 모두가 모든 토큰을 가진 것으로 본다** — 등록 없이 전 문서가 보인다.
 * 소비자(`search`·`read`·`grep`·`tree`·학습 힌트)는 전부 `tokensFor`·`canSee` 만
 * 거치므로 스위치가 여기 하나면 된다.
 *
 * **왜 필요한가:** 지금의 "ACL" 은 실질적으로 **사용자 허용목록**이다. `sync` 가 권한을
 * 수집하지 않아 전 페이지가 `@public` 이고, 그래서 시행이 하는 일은 "등록된 사람인가"
 * 뿐이다. 그런데 등록을 안 하면 fail-closed 로 전원이 빈손이 되고, 그 상태가 **"문서가
 * 없다"와 구별되지 않는다**(`CLAUDE.md` 조용히 실패 10번). 혼자 쓰거나 신뢰 경계 안에
 * 띄우는 경우엔 그 허용목록이 얻는 것 없이 함정만 된다.
 *
 * **끄는 것이 정당한 경우는 좁다** — 볼트를 서비스 계정 하나로 싱크했고 **그 계정의 권한
 * 범위를 이 서버에 닿는 전원이 공유해도 되는** 배포뿐이다. 개인 서버·개발·신뢰 경계
 * 안의 소규모 팀이 그것이다. 그 밖에서는 못 볼 문서가 그대로 나간다.
 *
 * 기본값은 **켜짐**이다. 조용히 열리는 것보다 조용히 빈손인 편이 낫다 — 후자는 눈에
 * 띄고 전자는 안 띈다. 꺼져 있으면 기동 로그와 `/api/stats`·`--status` 가 말한다.
 */
class AclRegistry(
    private val enforced: Boolean = true,
    /** 등록을 디스크에 남긴다. 없으면(테스트) 메모리 전용 — 예전 동작 그대로다. */
    private val store: UserStore? = null,
) {
    /** pageId -> 이 페이지를 볼 수 있는 토큰들 (그룹 키, 사용자 키, 공개 마커) */
    private val byPage = ConcurrentHashMap<String, Set<String>>()

    /** userKey -> 그 사용자가 가진 토큰들 */
    private val byUser = ConcurrentHashMap<String, Set<String>>()

    init {
        // 디스크에서 되살린다. **없으면 재기동마다 전원이 사라지고**, 그 상태가
        // "문서가 없다"와 구별되지 않는다(조용히 실패 10·12번).
        store?.load()?.forEach { (u, tokens) -> byUser[u] = tokens }
    }

    /**
     * 지금까지 본 모든 권한 토큰. 시행을 껐을 때 "모두가 가진 것" 으로 넘긴다.
     *
     * 페이지마다 합집합을 다시 구하면 검색 한 번이 전 페이지 스캔이 된다. 크기는
     * 페이지 수가 아니라 **그룹 수**에 묶여 있어(지금은 `@public` 하나) 저렴하다.
     */
    private val allTokens = ConcurrentHashMap.newKeySet<String>()

    fun putPage(pageId: String, tokens: Collection<String>) {
        byPage[pageId] = tokens.toSet()
        allTokens.addAll(tokens)
    }
    fun putUser(userKey: String, tokens: Collection<String>) {
        byUser[userKey] = tokens.toSet() + userKey
        // **바로 저장한다.** 등록은 드물고(운영자가 손으로 부른다) 잃으면 전원이 빈손이
        // 되므로, 주기적 저장으로 창을 남길 이유가 없다.
        store?.save(byUser)
    }
    fun tokensOf(pageId: String): Set<String> = byPage[pageId] ?: emptySet()

    /**
     * 요청자의 권한 토큰. **모르는 사용자에게는 빈 집합을 준다.**
     * 실수로 전체가 노출되는 것보다 아무것도 안 나오는 편이 낫다.
     */
    fun tokensFor(userKey: String?): Set<String> {
        // 시행을 끄면 **등록 여부와 무관하게** 전부 준다. 여기서 한 번만 갈라놓으면
        // 소비자 넷이 그대로 동작한다 — 각자 분기하면 한 곳이 빠져 반쪽으로 열린다.
        if (!enforced) return allTokens
        return if (userKey.isNullOrBlank()) emptySet() else byUser[userKey] ?: emptySet()
    }

    /** 시행 중인가. 진단에 쓴다 — 꺼진 것을 아무도 모르면 안 된다. */
    fun isEnforced(): Boolean = enforced

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
