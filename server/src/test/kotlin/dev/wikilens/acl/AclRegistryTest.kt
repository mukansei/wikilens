package dev.wikilens.acl

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * ACL 은 **fail-closed** 여야 한다 — 모르는 사용자에게는 빈 토큰을 주고,
 * 빈 토큰은 아무것도 못 본다. 실수로 전체가 노출되는 것보다 아무것도 안 나오는 편이 낫다.
 */
class AclRegistryTest {

    private fun registry() = AclRegistry().apply {
        putPage("pub", listOf("@public"))
        putPage("sec", listOf("@finance"))
        putUser("alice", listOf("@public"))
        putUser("bob", listOf("@public", "@finance"))
    }

    @Test
    fun `가진 토큰이 페이지 토큰과 겹치면 보인다`() {
        val r = registry()
        assertTrue(r.canSee("alice", "pub"))
        assertTrue(r.canSee("bob", "sec"))
    }

    @Test
    fun `겹치지 않으면 못 본다`() {
        assertFalse(registry().canSee("alice", "sec"))
    }

    @Test
    fun `미등록 사용자와 null 은 아무것도 못 본다`() {
        val r = registry()
        assertFalse(r.canSee("모르는사람", "pub"), "미등록 사용자에게 공개 문서도 주면 안 된다")
        assertFalse(r.canSee(null, "pub"))
        assertFalse(r.canSee("", "pub"))
    }

    @Test
    fun `등록 안 된 페이지는 아무도 못 본다`() {
        assertFalse(registry().canSee("bob", "없는페이지"))
    }

    @Test
    fun `사용자 키 자신도 토큰이 된다 (개인 공유용)`() {
        val r = registry()
        r.putPage("mine", listOf("carol"))
        r.putUser("carol", emptyList())
        assertTrue(r.canSee("carol", "mine"))
    }

    @Test
    fun `토큰 집합 오버로드가 userKey 경로와 같은 답을 준다`() {
        val r = registry()
        for (u in listOf("alice", "bob", "모르는사람")) {
            for (p in listOf("pub", "sec", "없는페이지")) {
                assertTrue(
                    r.canSee(u, p) == r.canSee(r.tokensFor(u), p),
                    "두 경로가 갈리면 grep 만 권한이 새거나 막힌다 ($u/$p)",
                )
            }
        }
    }
}
