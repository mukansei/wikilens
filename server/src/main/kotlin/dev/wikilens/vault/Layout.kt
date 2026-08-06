package dev.wikilens.vault

/**
 * 디스크 레이아웃 규칙. Python `cli/wikilens/layout.py`와 짝을 이루는 **계약**이다.
 *
 * 권위 있는 식별자는 페이지 ID다 — 제목이 아니다. 샤딩은 `{id뒤2}` 한 단계다.
 * 이 규칙의 Kotlin 정의처는 이 파일 하나여야 한다 (shared_contract.sh 가 확인한다).
 *
 * **앞이 아니라 뒤를 쓴다.** Confluence 페이지 ID 는 시간순 연속 할당이라 앞자리에
 * 엔트로피가 거의 없다. 앞2/앞4 로 쪼갰을 때 실측(2,377건)이 최대 378개였고, 뒤2 는
 * 최대 37개다. 근거와 수치는 `cli/wikilens/layout.py` 주석에 있다.
 */
object VaultLayout {
    const val SHARD_WIDTH = 2

    fun relPagePath(pid: String): String =
        "mirror/pages/${pid.padStart(SHARD_WIDTH, '0').takeLast(SHARD_WIDTH)}/$pid.md"
}
