package io.wikilens.vault

/**
 * 디스크 레이아웃 규칙. Python `cli/wikilens/layout.py`와 짝을 이루는 **계약**이다.
 *
 * 권위 있는 식별자는 페이지 ID다 — 제목이 아니다. 샤딩은 `{id뒤2}` 한 단계다.
 * 이 규칙의 Kotlin 정의처는 이 파일 하나여야 한다 (shared_contract.sh 가 확인한다).
 *
 * **앞이 아니라 뒤를 쓴다.** 앞자리는 엔트로피가 낮다 — 실측(2,377건) 1번째 자리
 * 1.93 bit vs 9번째 3.32 bit. 앞2/앞4 는 최대 378개, 뒤2 는 최대 37개다.
 * 측정한 것과 추론한 것의 구분은 `cli/wikilens/layout.py` 주석에 있다.
 */
object VaultLayout {
    const val SHARD_WIDTH = 2

    fun relPagePath(pid: String): String =
        "mirror/pages/${pid.padStart(SHARD_WIDTH, '0').takeLast(SHARD_WIDTH)}/$pid.md"
}
