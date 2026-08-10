package dev.wikilens.acl

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * **등록만으로는 부족하다.** `canSee` 는 조건이 둘인데(`등록됐나` + `토큰이 겹치나`)
 * 진단은 오래 첫째만 봤다.
 *
 * 둘째는 가설이 아니라 **`wikilens acl` 을 처음 돌리면 반드시 걸리는** 경로다.
 * 그전에는 `mirror/acl/` 이 없어 전 페이지가 `@public` 폴백이고, 수집 후에는 페이지
 * 토큰이 `@space:<KEY>` 로 바뀐다. `["@public"]` 로 등록한 사람은 그 순간 전원 0건이
 * 되는데 등록도 색인도 멀쩡하고 `ACL_USERS` 도 초록이다.
 */
class TokenOverlapTest {

    @Test
    fun `권한 수집을 켜는 순간 전원이 빈손이 되는 것을 짚는다`() {
        val acl = AclRegistry()

        // 수집 전: acl 디렉터리가 없어 전 페이지가 @public 폴백
        acl.replacePages(mapOf("1" to listOf("@public"), "2" to listOf("@public")))
        acl.putUser("alice@corp", listOf("@public"))
        assertTrue(acl.canSee("alice@corp", "1"))
        assertEquals(1, acl.tokenOverlap(), "정상 상태인데 겹침이 0으로 나온다")

        // `wikilens acl` 을 돌리고 재색인했다 — 페이지 토큰이 바뀐다
        acl.replacePages(mapOf("1" to listOf("@space:DOCS"), "2" to listOf("group:개발팀")))

        assertFalse(acl.canSee("alice@corp", "1"), "전제가 깨졌다 — 안 보여야 한다")
        assertEquals(0, acl.tokenOverlap(), "전원 빈손인데 겹침이 0으로 안 나온다")
        assertEquals(setOf("@public"), acl.userTokens())
        assertEquals(setOf("@space:DOCS", "group:개발팀"), acl.pageTokens())
    }

    @Test
    fun `자기 userKey 는 사용자 토큰으로 세지 않는다`() {
        val acl = AclRegistry()
        acl.replacePages(mapOf("1" to listOf("@space:DOCS")))
        acl.putUser("alice@corp", emptyList())

        // putUser 가 userKey 자신을 토큰 집합에 넣는다. 그건 페이지 토큰이 될 수 없으므로
        // 진단에서 빼야 한다 — 안 빼면 "토큰이 있다" 로 보여 경고가 안 나간다.
        assertEquals(emptySet(), acl.userTokens())
        assertEquals(0, acl.tokenOverlap())
    }

    /**
     * 페이지 토큰 합집합이 늘기만 하면 **사라진 스페이스의 토큰이 남아** 겹침이 있는
     * 것처럼 보인다. 시행을 껐을 때 `tokensFor` 가 돌려주는 집합도 같은 값이라
     * 둘이 함께 틀린다.
     */
    @Test
    fun `사라진 페이지의 토큰은 합집합에서도 빠진다`() {
        val acl = AclRegistry()
        acl.replacePages(mapOf("1" to listOf("@space:OLD"), "2" to listOf("@space:NEW")))
        acl.putUser("alice@corp", listOf("@space:OLD"))
        assertEquals(1, acl.tokenOverlap())

        // OLD 스페이스를 볼트에서 뺐다
        acl.replacePages(mapOf("2" to listOf("@space:NEW")))

        assertEquals(setOf("@space:NEW"), acl.pageTokens(), "사라진 토큰이 남았다")
        assertEquals(0, acl.tokenOverlap())
    }
}
