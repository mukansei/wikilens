package dev.wikilens.service

import dev.wikilens.api.GrepMatch
import dev.wikilens.vault.VaultLayout
import java.nio.file.Path

/**
 * 본문 스캔 엔진. 구현이 둘이다 — JVM 내장 스캔과 ripgrep 프로세스.
 *
 * **ACL 은 여기 없다.** 호출부([ContentService])가 이미 거른 목록을 넘긴다 — 권한
 * 해석이 엔진마다 갈리면 한쪽이 조용히 더 보여준다. `AclRegistry` 에 스위치를 한 곳만
 * 둔 것과 같은 이유다.
 *
 * **두 구현이 같은 답을 내야 한다.** 그것을 지키는 것은 이 인터페이스가 아니라
 * `GrepEngineParityTest` 다 — 같은 픽스처에 같은 패턴을 돌려 결과를 대조한다.
 * 경로가 둘인 것 자체는 피할 수 없다(rg 가 없는 머신이 있다). 대조 가능한 형태로
 * 두는 것이 할 수 있는 최선이다.
 */
interface GrepEngine {
    /** 진단·응답에 싣는 이름. 어느 경로로 처리됐는지 밖에서 보여야 한다. */
    val name: String

    /** 이 머신에서 쓸 수 있나. rg 는 없을 수 있다. */
    fun isAvailable(): Boolean

    fun search(q: GrepQuery): GrepOutcome
}

/**
 * 스캔 대상 한 건. 엔진이 ACL 을 다시 보지 않도록 **이미 걸러진** 것만 담긴다.
 *
 * [relPath] 는 **읽을 때 만든다.** rg 경로는 디렉터리를 통째로 넘기고 결과를 거르므로
 * 이 값을 한 번도 안 쓴다 — 미리 만들면 요청마다 문서 수만큼(이 코퍼스면 13,921회)
 * 문자열을 조립하고 버린다.
 *
 * **`by lazy` 가 아니라 그냥 계산 프로퍼티다.** 값은 문서당 **최대 한 번** 읽히는데
 * (`JvmGrepEngine` 의 `resolve(p.relPath)` 하나뿐) `by lazy` 는 인스턴스마다 홀더
 * 객체와 락을 만든다. 캐시할 것이 없다.
 *
 * 실측(13,921건 · 순서를 뒤집어 대조): JVM 경로 lazy 0.56~0.66ms · getter 0.42~0.48ms,
 * rg 경로는 둘이 노이즈 범위 안에서 겹친다. **전체가 1ms 미만이라 여기서 더 짜낼 것은
 * 없다** — 다시 최적화하러 오지 말 것.
 */
data class PageRef(val id: String, val title: String) {
    val relPath: String get() = VaultLayout.relPagePath(id)
}

data class GrepQuery(
    val vaultRoot: Path,
    val pages: List<PageRef>,
    val pattern: String,
    val regex: Boolean,
    val cap: Int,
    val budgetNanos: Long,
)

/**
 * [error] 가 있으면 그 요청은 실패다 — 정규식 문법 오류처럼 사용자가 고칠 수 있는 것.
 * 엔진이 아예 못 돌았을 때는 호출부가 폴백을 고를 수 있게 [usable] 이 false 다.
 */
data class GrepOutcome(
    val scanned: Int,
    val matches: List<GrepMatch>,
    val truncated: Boolean,
    val error: String? = null,
    val usable: Boolean = true,
)
