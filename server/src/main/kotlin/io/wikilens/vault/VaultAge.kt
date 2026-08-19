package io.wikilens.vault

import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/**
 * 볼트가 얼마나 낡았는지. `.sync-state.json` 의 `cursor` 가 원본이다.
 *
 * **파일 mtime 이 아니라 커서다.** mtime 은 아무 이유로나 바뀌고(빌드가 다시 돌기만
 * 해도), 커서는 `sync` 가 Confluence 에서 **어디까지 받았는지**를 가리킨다. 재빌드는
 * 커서를 안 옮기므로 "빌드는 돌았는데 싱크가 죽은" 상태가 그대로 드러난다.
 *
 * **왜 서버가 이것을 알아야 하나.** 서버는 색인한 문서 수만 알 뿐 그것이 언제 것인지
 * 몰랐다. cron 이 조용히 멈추면 몇 주 낡은 답을 정상으로 서빙하고, 겉으로는 모든
 * 지표가 초록이다 — 에러가 아니라 침묵이라 아무도 안 본다. 로컬판은 `AGE_DAYS`·
 * `STATUS=stale` 로 이미 말하고 있었고 서버만 빠져 있었다.
 *
 * **자동 재색인을 안 만든 대가로 이것이 있다.** 타이머로 재색인하면 싱크 도중에
 * 터져 반쪽 볼트를 색인할 수 있어 `&&` 사슬을 깬다. 자동화 대신 **자동화가 죽은 것을
 * 시끄럽게** 만드는 쪽을 골랐다(`DECISIONS.md` D17 과 같은 계열).
 */
object VaultAge {

    /** `sync` 가 쓰는 형식(UTC). ISO 도 받는다 — 로컬판 `_parse_cursor` 와 같은 목록이다. */
    private val FORMATS = listOf(
        DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"),
        DateTimeFormatter.ISO_OFFSET_DATE_TIME,
        DateTimeFormatter.ISO_INSTANT,
    )

    /**
     * **로컬판 `STALE_DAYS` 와 같은 값이어야 한다**(`vault_status.py`). 두 판이 다른
     * 날짜에 "낡았다" 고 말하면 판을 옮긴 사용자가 같은 볼트에 다른 진단을 받는다.
     * 계약이 검사한다.
     */
    const val STALE_DAYS = 7L

    /** 커서를 못 읽으면 null — "낡지 않았다" 가 아니라 **모른다** 다. 둘을 뭉개지 않는다. */
    fun parse(raw: String?): Instant? {
        if (raw.isNullOrBlank()) return null
        for (f in FORMATS) {
            runCatching {
                return if (f == FORMATS[0]) LocalDateTime.parse(raw, f).toInstant(ZoneOffset.UTC)
                else Instant.from(f.parse(raw))
            }
        }
        return null
    }

    fun ageDays(cursor: Instant?, now: Instant = Instant.now()): Long? =
        cursor?.let { Duration.between(it, now).toDays() }

    fun isStale(ageDays: Long?): Boolean = ageDays != null && ageDays > STALE_DAYS
}
