package io.wikilens.acl

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * ACL 시행 스위치.
 *
 * 소비자(`search`·`read`·`grep`·`tree`·학습 힌트)가 전부 `tokensFor`·`canSee` 만
 * 거치므로 스위치는 여기 하나다. **각 소비자가 각자 분기하면 한 곳이 빠져 반쪽으로
 * 열린다** — 그 상태는 겉으로 정상이라 아무도 모른다.
 */
class AclModeTest {

    private fun registry(enforced: Boolean) = AclRegistry(enforced).apply {
        putPage("공개", listOf("@public"))
        putPage("팀문서", listOf("team-a"))
        putPage("기밀", listOf("secret"))
    }

    @Test
    fun `켜져 있으면 등록 안 된 사용자는 아무것도 못 본다`() {
        val acl = registry(enforced = true)
        assertTrue(acl.isEnforced())
        assertTrue(acl.tokensFor("모르는사람").isEmpty())
        for (p in listOf("공개", "팀문서", "기밀")) {
            assertFalse(acl.canSee("모르는사람", p), "$p 가 보였다")
        }
    }

    @Test
    fun `켜져 있으면 가진 토큰만큼만 본다`() {
        val acl = registry(enforced = true)
        acl.putUser("alice", listOf("@public", "team-a"))
        assertTrue(acl.canSee("alice", "공개"))
        assertTrue(acl.canSee("alice", "팀문서"))
        assertFalse(acl.canSee("alice", "기밀"), "가지지 않은 토큰의 문서가 보였다")
    }

    @Test
    fun `꺼져 있으면 등록 없이 전부 본다`() {
        val acl = registry(enforced = false)
        assertFalse(acl.isEnforced())
        for (p in listOf("공개", "팀문서", "기밀")) {
            assertTrue(acl.canSee("모르는사람", p), "$p 가 안 보였다")
        }
        // Lucene 필터가 토큰 집합을 받으므로 비어 있으면 안 된다 — 비면 검색이 0건이다.
        assertEquals(setOf("@public", "team-a", "secret"), acl.tokensFor("모르는사람"))
        // 식별자를 아예 안 줘도 동작해야 한다. 끈 상태에서 userKey 는 궤적용일 뿐이다.
        assertTrue(acl.canSee(null, "기밀"))
    }

    @Test
    fun `꺼져 있어도 색인이 비면 빈 집합이다`() {
        // 문서가 없는 것과 권한이 없는 것은 다르지만, 둘 다 결과는 0건이 맞다.
        assertTrue(AclRegistry(enforced = false).tokensFor("아무나").isEmpty())
    }

    /** 끈 상태에서는 전원이 같은 토큰을 가지므로 학습 범위도 하나여야 한다. */
    @Test
    fun `꺼져 있으면 권한 범위가 전원 동일하다`() {
        val acl = registry(enforced = false)
        assertEquals(acl.scopeOf("alice"), acl.scopeOf("bob"))
    }
}
