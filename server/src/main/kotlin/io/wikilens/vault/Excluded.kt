package io.wikilens.vault

/**
 * `build` 가 문자 집합으로 뺀 페이지와 **그 근거**(`derived/excluded.json`).
 *
 * 개수만으로는 "무슨 설정이었나" 를 못 푼다 — 서버에는 이 설정이 없고 볼트에만 있으니
 * 볼트가 스스로 말해야 한다. `/api/stats` 가 [describe] 를 그대로 낸다.
 *
 * **비어 있는 것과 파일이 없는 것을 구별하지 않는다** — 둘 다 "전부 색인" 이고,
 * 그 구별이 필요한 자리가 아직 없다.
 */
data class Excluded(
    val ids: Set<String> = emptySet(),
    val scripts: List<String> = emptyList(),
    val threshold: Double? = null,
) {
    /** 사람이 읽는 한 줄. 꺼져 있으면 `"꺼짐"`. */
    val describe: String
        get() = if (scripts.isEmpty()) "꺼짐"
        else "${scripts.joinToString("·")} · 문턱 ${((threshold ?: 0.0) * 100).toInt()}%"
}
