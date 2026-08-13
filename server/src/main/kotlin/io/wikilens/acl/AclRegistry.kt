package io.wikilens.acl

import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap

/**
 * 페이지별 접근 권한.
 *
 * **권한 변경은 `lastModified` 를 건드리지 않는다** — 증분 싱크가 영영 못 잡으므로
 * `wikilens acl` 을 따로, 더 자주 돌려야 한다.
 *
 * **[enforced] 스위치는 여기 하나뿐이다.** 소비자(`search`·`read`·`grep`·`tree`·학습
 * 힌트)는 전부 [tokensFor]·[canSee] 만 거친다 — 각자 분기하면 한 곳이 빠져 반쪽으로
 * 열리고 그건 겉으로 정상이다. 계약이 검사한다.
 *
 * 배포 기본값이 꺼짐인 근거와 켜야 하는 경우는 **`application.yml` 의
 * `acl-enforced`** 에 있다(운영자가 읽는 자리). 꺼진 것이 시끄러워야 한다는 것도
 * 거기 — 기동 로그 WARN · `/api/stats` · `--status` 셋이 계약이다.
 */
class AclRegistry(
    /**
     * **여기 기본값은 `true` 로 둔다 — 배포 기본값(`wikilens.acl-enforced=false`)과 다르다.**
     * 배포 기본은 `WikiLensProperties` 가 정하고 이 인자로 주입된다. 이 자리는 인자를
     * 안 주는 호출부(대부분 테스트)가 보는 값이라, 여기까지 꺼두면 **ACL 을 검증하는
     * 테스트가 조용히 자명해진다** — 권한 필터를 지워도 초록이 된다.
     */
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

    /**
     * 재적재 결과로 페이지 맵을 **갈아끼운다.** [putPage] 만 쓰면 맵이 늘기만 해서
     * 볼트에서 사라진 페이지가 계속 "볼 수 있음" 으로 남는다.
     *
     * **비었으면 아무것도 안 한다** — 여기서 비우면 색인은 남고 권한만 사라져 읽기가
     * 전부 404 가 된다(조용히 실패 12·14번). 넣고 나서 지우는 순서인 것도 같은 이유로,
     * 그 사이 요청이 보이던 페이지를 잠깐 못 보면 안 된다.
     */
    fun replacePages(pages: Map<String, Collection<String>>) {
        if (pages.isEmpty()) return
        pages.forEach { (id, tokens) -> putPage(id, tokens) }
        byPage.keys.retainAll(pages.keys)
        // [allTokens] 도 함께. 늘기만 하면 사라진 스페이스의 토큰이 남아 시행을 껐을 때의
        // "모두가 가진 것" 과 [tokenOverlap] 진단이 낙관적으로 틀린다.
        allTokens.retainAll(pages.values.flatten().toSet())
    }

    /**
     * 등록된 사용자 토큰과 페이지 토큰이 **몇 개나 겹치는가.** 0 이면 등록이 있어도
     * 전원이 빈손이다.
     *
     * `wikilens acl` 을 처음 돌리면 **반드시 걸린다** — 그전에는 전 페이지가 `@public`
     * 폴백이라 `["@public"]` 로 등록한 사람이 다 보이는데, 수집 후에는 페이지 토큰이
     * `@space:<KEY>` 로 바뀌어 전원이 0건이 된다. 등록도 색인도 멀쩡하고 `ACL_USERS` 도
     * 초록이라, **옳은 조치가 전원 블랙아웃으로 나타난다.**
     */
    fun tokenOverlap(): Int = userTokens().count { it in allTokens }

    /** 등록된 사용자들이 가진 토큰. 자기 `userKey` 는 뺀다 — 페이지 토큰이 될 수 없다. */
    fun userTokens(): Set<String> =
        byUser.entries.flatMapTo(HashSet()) { (u, ts) -> ts.filterNot { it == u } }

    /** 지금 페이지들이 실제로 요구하는 토큰. 진단 메시지에 실어 양쪽을 보여준다. */
    fun pageTokens(): Set<String> = allTokens.toSet()
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
        if (!enforced) return allTokens      // 등록 여부와 무관하게 전부 (클래스 KDoc)
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
     * 이 요청자의 **권한 범위 식별자**. 궤적 로그에 남긴다 — **신원이 아니다.**
     *
     * 푸는 문제 셋이 전부 *권한 폭*이지 *신원*이 아니다: 좁은 권한의 궤적이 전 사용자의
     * 포스팅을 오염시키고, `rank` 가중이 목록 길이에 편향되고, 권한이 바뀌어도 어느
     * 궤적이 그 범위 것인지 골라낼 수 없다. 신원을 남기면 "누가 무엇을 검색했나" 가
     * 영구 기록이 되는데 그건 이 도구가 안 하는 일이다.
     *
     * **지금은 기록만 한다** — 가중에 쓰는 것은 측정이 필요하다. 미리 넣는 이유는 로그가
     * append-only 라서다: 나중에 넣으면 그전 궤적에는 영영 없다.
     *
     * 12자면 충돌이 무시할 만하고(범위 수는 많아야 수백) 원문 토큰은 복원되지 않는다.
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
