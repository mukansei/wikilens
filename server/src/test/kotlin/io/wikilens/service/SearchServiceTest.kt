package io.wikilens.service

import io.wikilens.api.SearchRequest

import io.wikilens.acl.AclRegistry
import io.wikilens.index.IndexedPage
import io.wikilens.index.LuceneIndex
import io.wikilens.learn.TrajectoryStore
import kotlin.io.path.createTempDirectory
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * 어휘 랭킹 + 학습 힌트 융합 테스트.
 *
 * 이 클래스가 없어서 **학습 전용 힌트가 항상 버려지는 결함**이 오래 살아남았다:
 * `meta` 를 어휘 결과로만 채워놓고 거기 없는 후보를 `mapNotNull` 에서 걸렀기 때문에,
 * 어휘 검색에 안 걸리고 궤적으로만 발견된 페이지가 결과에서 조용히 사라졌다.
 * 그게 학습 레이어의 존재 이유인데도.
 */
class SearchServiceTest {

    private lateinit var index: LuceneIndex
    private lateinit var acl: AclRegistry
    private lateinit var store: TrajectoryStore
    private lateinit var svc: SearchService

    private val user = "tester"

    @BeforeTest
    fun setUp() {
        index = LuceneIndex(createTempDirectory("search-svc-test"))
        acl = AclRegistry()
        // 임계값을 낮춰 EB 튜닝이 아니라 융합 자체를 검증한다.
        store = TrajectoryStore({ }, serveThreshold = 0.10)
        svc = SearchService(index, store, acl)

        load(
            page("1", "로그인 인증 가이드", "OAuth 로그인 절차 설명"),
            page("2", "완전히 무관한 주제", "커피 머신 청소 방법"),
        )
        acl.putUser(user, listOf("@public"))
    }

    @AfterTest
    fun tearDown() = index.close()

    private fun page(id: String, title: String, body: String) = IndexedPage(
        id = id, title = title, space = "DOCS", path = "mirror/pages/00/0$id.md",
        body = body, anchors = emptyList(), aclTokens = listOf("@public"),
    )

    /**
     * 색인과 ACL 레지스트리를 함께 채운다.
     *
     * **프로덕션과 한 군데 다르다 — 알고 그렇게 둔다.** `VaultReader.read` 는
     * `acl.replacePages` 를 부르고 그것이 `retainAll` 로 **사라진 페이지를 지운다.**
     * 여기 `putPage` 는 추가 전용이라 지우지 않으므로, 이 헬퍼로 "페이지가 없어진"
     * 상황을 만들면 **ACL 맵과 색인 메타가 갈린 상태**가 된다. 프로덕션에서 그 둘은
     * `reload()` 안에서 함께 갱신되므로 재색인 사이에만 갈린다.
     *
     * 그 차이가 중요한 이유는 아래 `갈린 상태에서도…` 테스트가 정확히 그 상태를
     * 재현하기 때문이다 — 헬퍼를 `replacePages` 로 바꾸면 그 테스트는 존재 술어 없이도
     * 통과한다(실측 2026-08-10). 즉 이 헬퍼는 **더 나쁜 쪽을 만들어 두는 장치**다.
     *
     * 색인만 채우면 어휘 검색은 Lucene 자체 ACL 필드로 되지만 힌트는 `hints()` 의 술어가
     * 전부 걸러낸다 — 그 술어가 **유일한 권한 관문**이라는 뜻이다.
     */
    private fun load(vararg pages: IndexedPage) {
        pages.forEach { acl.putPage(it.id, it.aclTokens) }
        index.rebuild(pages.toList())
    }

    /** 질의를 학습시킨다 — 서버가 쓰는 것과 같은 토크나이저로 항을 맞춘다. */
    private fun train(sessionId: String, query: String, dest: String) {
        store.onQuery(sessionId, query, index.analyze(query))
        store.onRead(sessionId, dest)
        store.onEnd(sessionId)
    }

    @Test
    fun `어휘에 없고 학습 힌트로만 발견된 페이지도 결과에 나온다`() {
        val q = "커피"
        // 사전 확인: 이 질의로 어휘 검색은 2번 문서를 찾는다.
        // 학습은 그 반대로, 어휘가 못 찾는 1번을 목적지로 쌓는다.
        repeat(6) { train("s$it", q, "1") }

        val res = svc.search(SearchRequest(query = q, userKey = user, limit = 8))

        val hit = res.hits.firstOrNull { it.pageId == "1" }
        assertNotNull(hit, "학습 힌트로만 발견된 페이지가 버려졌다 (hits=${res.hits.map { it.pageId }})")
        assertEquals("learned", hit.source)
        assertEquals("로그인 인증 가이드", hit.title, "제목이 메타데이터 캐시에서 채워져야 한다")
        assertEquals("DOCS", hit.space)
        assertNotNull(hit.reliability)
    }

    @Test
    fun `어휘와 학습 양쪽에 있으면 both 로 표시된다`() {
        val q = "로그인"
        repeat(6) { train("b$it", q, "1") }

        val hit = svc.search(SearchRequest(query = q, userKey = user, limit = 8))
            .hits.first { it.pageId == "1" }
        assertEquals("both", hit.source)
        assertNotNull(hit.reliability)
    }

    @Test
    fun `limit 은 버려진 후보가 아니라 실제 결과 수를 센다`() {
        // 예전엔 take 가 필터보다 먼저라, 버려질 후보가 limit 슬롯을 먹어
        // 요청한 것보다 적게 나갔다.
        val res = svc.search(SearchRequest(query = "로그인 커피", userKey = user, limit = 2))
        assertEquals(2, res.hits.size, "가용 문서가 2개인데 limit=2 요청이 덜 반환했다")
    }

    @Test
    fun `권한 토큰이 없으면 어휘도 힌트도 내지 않는다`() {
        repeat(6) { train("n$it", "로그인", "1") }

        assertTrue(svc.search(SearchRequest(query = "로그인", userKey = "미등록", limit = 8)).hits.isEmpty())
        assertTrue(svc.search(SearchRequest(query = "로그인", userKey = null, limit = 8)).hits.isEmpty())
    }

    @Test
    fun `권한 없는 페이지는 학습 힌트로도 새지 않는다`() {
        // 학습 레이어는 권한을 모른다. 볼 수 없는 문서를 목적지로 쌓아도 새면 안 된다.
        load(
            page("1", "로그인 인증 가이드", "OAuth 로그인 절차 설명"),
            IndexedPage(
                id = "9", title = "기밀 문서", space = "DOCS", path = "mirror/pages/00/09.md",
                body = "커피", anchors = emptyList(), aclTokens = listOf("@secret"),
            ),
        )
        repeat(6) { train("x$it", "커피", "9") }

        val res = svc.search(SearchRequest(query = "커피", userKey = user, limit = 8))
        assertTrue(res.hits.none { it.pageId == "9" }, "권한 없는 문서가 학습 힌트로 새어 나왔다")
    }

    /**
     * `limit` 은 클라이언트가 정한다. **상한이 없으면 셋이 깨진다** — 실서버로 확인했다:
     * `limit<=0` 은 Lucene 예외로 500, 큰 값은 `limit*3` 오버플로우로 500, 그리고
     * 서빙한 힌트가 궤적 로그에 `served` 로 **영구히** 남는다(append-only 이고 유일한
     * 복구 불가 자산이다). `grep` 은 같은 이유로 이미 죄고 있었는데 여기만 안 죄었다.
     */
    @Test
    fun `limit 이 0 이하여도 죽지 않는다`() {
        load(page("1", "배포 가이드", "배포 절차"))
        val res = svc.search(SearchRequest(query = "배포", userKey = user, limit = 0))
        assertTrue(res.hits.size <= 1)
    }

    @Test
    fun `limit 이 커도 곱셈이 넘치지 않는다`() {
        load(page("1", "배포 가이드", "배포 절차"))
        // 715_827_883 * 3 은 Int 를 넘어 음수가 된다 — 실서버에서 500 이었다.
        val res = svc.search(SearchRequest(query = "배포", userKey = user, limit = 715_827_883))
        assertTrue(res.hits.isNotEmpty())
    }

    @Test
    fun `limit 은 상한을 넘지 않는다`() {
        val pages = (1..150).map { page("$it", "배포 문서 $it", "배포 절차 $it") }
        load(*pages.toTypedArray())
        val res = svc.search(SearchRequest(query = "배포", userKey = user, limit = 10_000))
        assertTrue(res.hits.size <= SearchService.MAX_LIMIT,
                   "상한을 넘었다: ${res.hits.size}")
    }

    /**
     * **색인에 없는 페이지의 학습 간선이 살아있는 것을 밀어내면 안 된다.**
     *
     * 포스팅은 한 번도 지워지지 않는다 — 궤적 로그가 정본이고 append-only 라, 페이지가
     * 색인에서 빠져도 그 간선은 남는다(재기동하면 로그에서 되살아나므로 지우는 것도
     * 답이 아니다). 그래서 **서빙 시점에 거르고, 자르기 전에 거른다.** `take` 뒤에서
     * 걸면 서빙 못 할 후보가 limit 슬롯을 먹고 아래에서 폐기된다 — `CLAUDE.md` 8·22번과
     * 같은 실패의 세 번째 자리다.
     *
     * **이 테스트가 잠그는 것과 잠그지 않는 것을 구분할 것.**
     *
     * 잠그는 것은 `SearchService` 의 **존재 술어가 `hints()` 안에 배선돼 있다**는 사실이다
     * (빼면 빨개진다 — 확인함). 잠그지 않는 것은 **이 상태가 프로덕션에서 일어난다**는
     * 것이다. 여기서 쓰는 `load` 는 `acl.putPage`(추가 전용)라 ACL 맵에 옛 페이지가 남지만,
     * 프로덕션의 `VaultReader.read` 는 `acl.replacePages` 로 `retainAll` 하므로 **삭제
     * 방향은 권한 술어가 이미 거른다** — 헬퍼를 `replacePages` 로 바꾸면 존재 술어 없이도
     * 통과한다(실측 2026-08-10).
     *
     * 그래서 이것은 "고친 버그의 회귀 테스트" 가 아니라 **두 맵이 갈린 상태를 일부러
     * 만들어 두는 장치**다. 프로덕션에서 그 갈림이 생기는 자리는 `reload()` 의
     * `replacePages` → `rebuild` 사이 창이다.
     */
    @Test
    fun `갈린 상태에서도 색인에 없는 간선이 슬롯을 먹지 않는다`() {
        val learned = (1..4).map { page("L$it", "학습전용 $it", "커피 머신 청소 $it") }
        load(*(learned + page("X", "무관", "커피")).toTypedArray())
        repeat(6) { r -> learned.forEach { train("s$r-${it.id}", "로그인", it.id) } }

        val before = svc.search(SearchRequest("로그인", user, limit = 3))
        assertEquals(3, before.hits.count { it.pageId.startsWith("L") },
                     "전제가 깨졌다 — 학습 힌트 셋이 나와야 한다")

        // L1·L2 가 색인에서 빠진다. `load` 는 ACL 맵을 안 지우므로(위 KDoc) 여기서
        // **byPage 는 넷 · meta 는 둘**인 갈린 상태가 만들어진다. 학습은 그대로 남는다.
        load(learned[2], learned[3], page("X", "무관", "커피"))

        val after = svc.search(SearchRequest("로그인", user, limit = 3))
        assertEquals(setOf("L3", "L4"), after.hits.map { it.pageId }.filter { it.startsWith("L") }.toSet(),
                     "색인에 없는 페이지가 슬롯을 먹어 살아있는 간선이 밀려났다")
    }
}
