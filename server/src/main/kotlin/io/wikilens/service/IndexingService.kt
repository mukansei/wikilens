package io.wikilens.service

import io.wikilens.acl.AclRegistry
import io.wikilens.index.LuceneIndex
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
) {
    private val log = LoggerFactory.getLogger(javaClass)

    /** 볼트 위치는 [VaultLocator] 하나가 정한다 — 여기서 다시 풀면 read 와 갈린다. */
    val vaultRoot: Path get() = locator.root

    /**
     * 전량 재적재. **볼트가 비면 색인을 건드리지 않는다** — `VaultReader` 는 미러가 없어도
     * 빈 목록을 주는데, 그대로 넘기면 멀쩡한 색인이 0건으로 덮인다. 볼트를 못 읽는 것은
     * 고치면 되지만 **지워진 색인은 다시 싱크해야 한다.**
     */
    fun reload(): LoadResult {
        val root = vaultRoot
        val pages = vault.read(root, acl)
        if (pages.isEmpty()) {
            log.error(
                "볼트에서 문서를 하나도 못 읽었습니다: {} — 경로가 맞는지 확인하세요" +
                    "(상대경로는 실행 디렉터리 기준입니다). **기존 색인은 그대로 둡니다** — " +
                    "검색은 옛 색인으로 계속 동작하고, ACL 페이지 맵만 비어 읽기가 404 가 됩니다.",
                root,
            )
            return LoadResult(index.docCount, acl.pageCount(), skipped = true)
        }
        index.rebuild(pages)
        return LoadResult(pages.size, acl.pageCount())
    }
}
