package io.wikilens.service

import io.wikilens.acl.AclRegistry
import io.wikilens.config.WikiLensProperties
import io.wikilens.index.LuceneIndex
import io.wikilens.index.ScriptFilter
import io.wikilens.vault.VaultLocator
import io.wikilens.vault.VaultReader
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.nio.file.Path

/**
 * 볼트를 읽어 색인과 ACL 을 채운다.
 *
 * **부르는 곳이 둘이라 여기 모았다** — 기동 적재와 `/admin/reindex`. 각자 부르던 시절
 * 기동 쪽에만 방어를 넣자 엔드포인트 쪽이 그대로 남았고, 볼트 경로가 틀린 상태에서
 * 그것을 부르니 **살아 있던 색인 2,383건이 0으로 지워졌다**(실측).
 */
@Service
class IndexingService(
    private val vault: VaultReader,
    private val index: LuceneIndex,
    private val acl: AclRegistry,
    private val locator: VaultLocator,
    props: WikiLensProperties,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    /**
     * 문자 집합 필터. **생성자에서 만든다** — 설정 이름이 틀렸으면 기동이 실패해야지,
     * 첫 재색인까지 갔다가 실패하면 그때는 이미 색인이 지워질 수 있다.
     */
    private val scripts = ScriptFilter(props.indexScripts, props.scriptThreshold)

    /**
     * 마지막 적재에서 필터가 뺀 문서 수. **밖으로 내야 한다** — 빠진 문서는 검색 결과에
     * 안 나오는 것으로만 드러나고 그건 "문서가 없다" 와 구별되지 않는다.
     * 재기동하면 기동 적재가 다시 채운다(로그가 아니라 이 값이 정본이다).
     */
    @Volatile private var dropped_ = 0
    val droppedByScript: Int get() = dropped_

    /** 필터 설정을 사람이 읽는 한 줄로. 꺼져 있으면 `"꺼짐"`. */
    val scriptFilter: String get() = scripts.describe

    /** 볼트 위치는 [VaultLocator] 하나가 정한다 — 여기서 다시 풀면 read 와 갈린다. */
    val vaultRoot: Path get() = locator.root

    /**
     * 전량 재적재. **볼트가 비면 색인도 권한 맵도 건드리지 않는다** — `VaultReader` 는
     * 미러가 없어도 빈 목록을 주는데, 그대로 넘기면 멀쩡한 색인이 0건으로 덮인다.
     * 볼트를 못 읽는 것은 고치면 되지만 **지워진 색인은 다시 싱크해야 한다.**
     *
     * 권한 맵 쪽 가드는 `AclRegistry.replacePages` 에 있다. 둘이 함께 살아남아야 한다 —
     * 한쪽만 지키면 검색은 되는데 읽기가 전부 404 인 상태가 된다(조용히 실패 12·14번).
     * `IndexingServiceTest` 가 그 짝을 단언한다.
     */
    fun reload(): LoadResult {
        val root = vaultRoot
        val pages = vault.read(root, acl)
        if (pages.isEmpty()) {
            log.error(
                "볼트에서 문서를 하나도 못 읽었습니다: {} — 경로가 맞는지 확인하세요" +
                    "(상대경로는 실행 디렉터리 기준입니다). **색인과 권한 맵을 그대로 둡니다** — " +
                    "이미 적재된 적이 있으면 검색·읽기가 옛 내용으로 계속 동작하고, " +
                    "그렇지 않으면(첫 기동) 둘 다 비어 전 요청이 0건·404 입니다. " +
                    "어느 쪽인지는 /api/stats 의 indexedDocs·aclPages 로 갈립니다.",
                root,
            )
            return LoadResult(index.docCount, acl.pageCount(), skipped = true)
        }
        // **거르는 것은 색인 직전이다.** ACL 맵은 `vault.read` 가 이미 전량으로 채웠는데
        // 그대로 둔다 — 빠진 문서는 색인에 없어서 `read`·`grep`·`tree` 가 못 닿으므로
        // 무해하고, ACL 을 함께 줄이면 "왜 이 페이지만 권한이 없나" 를 나중에 못 푼다.
        val kept = if (scripts.enabled) pages.filter { scripts.accepts(it) } else pages
        val dropped = pages.size - kept.size
        if (scripts.enabled) {
            // **조용하면 안 된다.** 빠진 문서는 검색 결과에 안 나오는 것으로만 드러나고,
            // 그건 "문서가 없다" 와 구별되지 않는다(조용히 실패 10번과 같은 계열).
            log.info("문자 집합 필터({}): {}건 중 {}건 제외", scripts.describe, pages.size, dropped)
            if (dropped == pages.size) {
                log.error("**전 문서가 필터에 걸렸습니다.** `wikilens.index-scripts` 가 이 코퍼스의 " +
                    "문자 집합과 안 맞습니다 — 색인이 0건이 됩니다. 설정을 비우면 전부 색인합니다.")
            }
        }
        dropped_ = dropped
        index.rebuild(kept)
        return LoadResult(kept.size, acl.pageCount(), droppedByScript = dropped)
    }
}
