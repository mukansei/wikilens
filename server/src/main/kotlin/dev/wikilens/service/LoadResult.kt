package dev.wikilens.service

import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultReader
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.nio.file.Path

/** 적재 결과. 빈 볼트로 건너뛴 경우 [skipped] 가 참이고 개수는 **남아 있는 색인**의 것이다. */
data class LoadResult(val indexed: Int, val aclPages: Int, val skipped: Boolean = false)
