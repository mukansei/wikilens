package dev.wikilens.vault

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.acl.AclRegistry
import dev.wikilens.index.Ancestor
import dev.wikilens.index.IndexedPage
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Component
import java.nio.file.Files
import java.nio.file.Path

/**
 * Python `wikilens sync` 가 만든 미러를 읽는다.
 *
 * 싱크를 Kotlin 으로 다시 만들지 않는 이유는 그쪽이 이미 동작하고 테스트되어 있기 때문이다.
 * 서버는 **1회 싱크된 미러**를 색인할 뿐이다. 사용자별 개인 싱크를 없앤 것이 핵심 변경으로,
 * Confluence API 부하가 사용자 수에 비례하던 문제가 사라진다(200명이면 200배였다).
 */
@Component
class VaultReader(private val mapper: ObjectMapper) {
    private val log = LoggerFactory.getLogger(javaClass)

    fun read(root: Path, acl: AclRegistry): List<IndexedPage> {
        val statePath = root.resolve("mirror").resolve(".sync-state.json")
        if (!Files.exists(statePath)) {
            log.warn("미러가 없습니다: {}", statePath)
            return emptyList()
        }

        val state: Map<String, Any?> = mapper.readValue(
            Files.readString(statePath), Map::class.java
        ) as Map<String, Any?>

        @Suppress("UNCHECKED_CAST")
        val pages = (state["pages"] as? Map<String, Map<String, Any?>>) ?: emptyMap()
        val anchors = readAnchors(root)
        val aclByPage = readAcl(root)

        var unresolved = 0
        val result = pages.map { (pid, meta) ->
            // **"수집한 적 없음" 과 "수집했는데 이 페이지가 없음" 은 다르다.**
            // 전자는 ACL 이전의 볼트라 전부 공개가 맞고, 후자는 Python `collect` 이
            // 권한을 확정하지 못해 **일부러 생략한** 것이라 아무도 못 봐야 한다.
            // 한 값으로 뭉개면 그쪽의 fail-closed 가 여기서 fail-open 으로 뒤집힌다.
            val tokens = when {
                aclByPage == null -> listOf(PUBLIC)
                else -> aclByPage[pid] ?: emptyList<String>().also { unresolved++ }
            }
            IndexedPage(
                id = pid,
                title = meta["title"]?.toString().orEmpty(),
                space = meta["space"]?.toString().orEmpty(),
                path = VaultLayout.relPagePath(pid),
                body = readBody(root, pid),
                anchors = anchors[pid].orEmpty(),
                aclTokens = tokens,
                ancestors = readAncestors(meta),
            )
        }
        // 맵을 갈아끼운다 — 페이지마다 넣기만 하면 사라진 페이지가 남는다.
        acl.replacePages(result.associate { it.id to it.aclTokens })
        if (unresolved > 0) {
            // 조용하면 "문서가 없다" 와 구별되지 않는다(조용히 실패 10번). 수집을 다시
            // 돌리면 대개 해소되므로 무엇을 하라는 말까지 같이 낸다.
            log.warn(
                "{}건은 acl.json 에 권한이 없어 **아무에게도 안 보입니다** — " +
                    "`wikilens acl` 이 조회에 실패했거나 조상을 못 읽은 페이지입니다. " +
                    "다시 돌린 뒤 재색인하세요.",
                unresolved,
            )
        }
        return result
    }

    /** 루트부터 직속 부모까지 순서대로. TREE.md와 같은 원본(.sync-state.json)을 쓴다. */
    @Suppress("UNCHECKED_CAST")
    private fun readAncestors(meta: Map<String, Any?>): List<Ancestor> =
        (meta["ancestors"] as? List<Map<String, Any?>>).orEmpty().mapNotNull { a ->
            val id = a["id"]?.toString() ?: return@mapNotNull null
            Ancestor(id = id, title = a["title"]?.toString().orEmpty())
        }

    /** 앵커 전치 결과. 로컬판이 만든 derived/anchors.jsonl 을 그대로 쓴다. */
    private fun readAnchors(root: Path): Map<String, List<String>> {
        val p = root.resolve("derived").resolve("anchors.jsonl")
        if (!Files.exists(p)) return emptyMap()
        val out = HashMap<String, List<String>>()
        Files.newBufferedReader(p).useLines { lines ->
            for (line in lines) {
                if (line.isBlank()) continue
                runCatching {
                    @Suppress("UNCHECKED_CAST")
                    val e = mapper.readValue(line, Map::class.java) as Map<String, Any?>
                    val target = e["target"].toString()
                    @Suppress("UNCHECKED_CAST")
                    val list = (e["anchors"] as? List<Map<String, Any?>>).orEmpty()
                    out[target] = list.mapNotNull { it["text"]?.toString() }
                }
            }
        }
        return out
    }

    /**
     * 페이지별 권한 토큰. **`null` 은 "수집한 적 없음"** 이고 빈 맵과 뜻이 다르다 —
     * 전자만 전 페이지 `@public` 폴백을 받는다.
     *
     * 파일이 있는데 파싱에 실패하면 빈 맵을 준다(널이 아니다). 못 읽은 것을 "권한 정보가
     * 없던 볼트" 로 취급하면 **깨진 파일 하나가 전 페이지를 공개로 만든다.**
     */
    private fun readAcl(root: Path): Map<String, List<String>>? {
        val dir = root.resolve("mirror").resolve("acl")
        if (!Files.isDirectory(dir)) return null
        val files = Files.list(dir).use { s ->
            s.filter { it.toString().endsWith(".json") }.toList()
        }
        if (files.isEmpty()) return null
        val out = HashMap<String, List<String>>()
        files.forEach { f ->
            runCatching {
                @Suppress("UNCHECKED_CAST")
                val m = mapper.readValue(Files.readString(f), Map::class.java)
                    as Map<String, List<String>>
                out.putAll(m)
            }.onFailure { log.warn("ACL 파일을 못 읽었습니다: {} ({})", f, it.message) }
        }
        return out
    }

    private fun readBody(root: Path, pid: String): String {
        val f = root.resolve(VaultLayout.relPagePath(pid))
        return if (Files.exists(f)) runCatching { Files.readString(f) }.getOrDefault("") else ""
    }

    companion object { const val PUBLIC = "@public" }
}
