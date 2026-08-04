package dev.wikilens.api

import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.LuceneIndex
import dev.wikilens.vault.VaultLayout
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path

/**
 * 콘텐츠 서빙.
 *
 * **서버가 본문을 서빙하는 것이 배포보다 낫다.** 배포된 사본은 회수할 수 없어
 * 권한 취소가 불가능해진다. 서버가 서빙하면 매 요청마다 ACL을 다시 확인하므로
 * 권한 변경이 즉시 반영된다.
 *
 * 부수 효과로 훅이 통째로 불필요해진다 — 읽기가 서버를 거치므로 서버가 궤적을
 * 직접 관측한다. 클라이언트 버퍼링도, 핫 패스 비용도, 세션 조립도 사라진다.
 */
@Service
class ContentService(
    private val acl: AclRegistry,
    private val index: LuceneIndex,
    private val props: WikiLensProperties,
) {
    private val root: Path get() = Path.of(props.vaultRoot)

    fun read(pageId: String, userKey: String?): ReadResponse? {
        if (!acl.canSee(userKey, pageId)) return null   // 존재 여부도 알리지 않는다
        val meta = index.metaOf(pageId) ?: return null
        val f = root.resolve(VaultLayout.relPagePath(pageId))
        if (!Files.exists(f)) return null
        return ReadResponse(pageId, meta.title, meta.space, Files.readString(f))
    }

    /**
     * 리터럴 검색. 권한 있는 문서만 스캔한다.
     *
     * Lucene 질의가 아니라 실제 파일 스캔인 이유: 형태소 분석을 거치지 않은
     * 정확 일치가 필요한 경우가 있다(식별자, 코드 조각, 정확한 문구).
     * 10k 문서 ~100MB 스캔은 수백 ms라 감당 가능하다.
     */
    fun grep(pattern: String, userKey: String?, limit: Int, regex: Boolean): GrepResponse {
        val tokens = acl.tokensFor(userKey)
        if (tokens.isEmpty() || pattern.isBlank()) {
            return GrepResponse(pattern, 0, emptyList(), false)
        }
        val rx = if (regex) runCatching { Regex(pattern) }.getOrNull() else null
        if (regex && rx == null) return GrepResponse(pattern, 0, emptyList(), false)

        val matches = ArrayList<GrepMatch>()
        var scanned = 0
        var truncated = false

        for (meta in index.allMeta()) {
            if (matches.size >= limit) { truncated = true; break }
            if (!acl.canSee(userKey, meta.id)) continue
            val f = root.resolve(VaultLayout.relPagePath(meta.id))
            if (!Files.exists(f)) continue
            scanned++
            Files.newBufferedReader(f).useLines { lines ->
                lines.forEachIndexed { i, line ->
                    if (matches.size >= limit) return@forEachIndexed
                    val hit = rx?.containsMatchIn(line) ?: line.contains(pattern, ignoreCase = true)
                    if (hit) {
                        matches.add(GrepMatch(meta.id, meta.title, i + 1, line.trim().take(300)))
                    }
                }
            }
        }
        return GrepResponse(pattern, scanned, matches, truncated)
    }
}
