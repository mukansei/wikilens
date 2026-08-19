package io.wikilens.vault

import org.junit.jupiter.api.Test
import java.time.Instant
import java.time.temporal.ChronoUnit
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * 볼트 나이 판정. **로컬판 `vault_status.py` 와 같은 답을 내야 한다** — 두 판이 다른
 * 날짜에 "낡았다" 고 말하면 판을 옮긴 사용자가 같은 볼트에 다른 진단을 받는다.
 */
class VaultAgeTest {

    @Test
    fun `sync 가 쓰는 형식을 읽는다`() {
        // `cli/wikilens/sync.py` 가 '%Y-%m-%d %H:%M'(UTC)로 쓴다. 실제 볼트의 값이다.
        val got = VaultAge.parse("2026-08-18 01:10")
        assertEquals(Instant.parse("2026-08-18T01:10:00Z"), got)
    }

    @Test
    fun `ISO 형식도 읽는다`() {
        assertEquals(Instant.parse("2026-08-18T01:10:00Z"), VaultAge.parse("2026-08-18T01:10:00Z"))
    }

    @Test
    fun `못 읽으면 null 이다 — 0일이 아니다`() {
        // **"모른다" 와 "안 낡았다" 를 뭉개면 안 된다.** 0 을 돌려주면 커서가 깨진
        // 볼트가 방금 싱크한 것처럼 보이고, 그게 이 기능이 잡으려는 바로 그 상태다.
        assertNull(VaultAge.parse(null))
        assertNull(VaultAge.parse(""))
        assertNull(VaultAge.parse("어제"))
        assertNull(VaultAge.ageDays(null))
        assertFalse(VaultAge.isStale(null), "모르는 것을 낡았다고 하면 오경보가 상시로 뜬다")
    }

    @Test
    fun `문턱은 로컬판과 같은 7일이다`() {
        val now = Instant.parse("2026-08-18T00:00:00Z")
        fun age(d: Long) = VaultAge.ageDays(now.minus(d, ChronoUnit.DAYS), now)
        assertEquals(7L, age(7))
        assertFalse(VaultAge.isStale(age(7)), "7일은 아직 아니다 — 로컬판이 `> STALE_DAYS`")
        assertTrue(VaultAge.isStale(age(8)))
    }
}
