package dev.wikilens.vault

/**
 * 디스크 레이아웃 규칙. Python `cli/wikilens/layout.py`와 짝을 이루는 **계약**이다.
 *
 * 권위 있는 식별자는 페이지 ID다 — 제목이 아니다. 샤딩은 `{id앞2}/{id다음2}`.
 * 이 규칙의 Kotlin 정의처는 이 파일 하나여야 한다 (shared_contracts.sh 가 확인한다).
 */
object VaultLayout {
    fun relPagePath(pid: String): String {
        val padded = pid.padStart(4, '0')
        return "mirror/pages/${padded.substring(0, 2)}/${padded.substring(2, 4)}/$pid.md"
    }
}
