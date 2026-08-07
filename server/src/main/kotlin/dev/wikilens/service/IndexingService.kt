package dev.wikilens.service

import dev.wikilens.acl.AclRegistry
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultLocator
import dev.wikilens.vault.VaultReader
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.nio.file.Path

/**
 * 볼트를 읽어 색인과 ACL 을 채운다.
 *
 * **부르는 곳이 둘이라 여기 모았다** — 기동 적재(`vaultBootstrap`)와 `/admin/reindex`.
 * 예전에는 각자 `vault.read` + `index.rebuild` 를 직접 불렀고, 그래서 기동 쪽에만 방어를
 * 넣었을 때 **엔드포인트 쪽은 그대로 남았다.** 실측: 볼트 경로가 틀린 상태에서
 * `/admin/reindex` 를 부르니 살아 있던 색인 2,383건이 0으로 지워졌다.
 *
 * 로직이 한 곳이면 그 갈림이 성립하지 않는다.
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
     * 전량 재적재. **볼트가 비면 색인을 건드리지 않는다.**
     *
     * `VaultReader` 는 미러가 없어도 예외를 안 던지고 빈 목록을 준다. 그대로 `rebuild` 에
     * 넘기면 디스크에 있던 멀쩡한 색인이 0건으로 덮이는데, 볼트를 못 읽는 것은 고치면
     * 되지만 **지워진 색인은 다시 싱크해야 한다.**
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
