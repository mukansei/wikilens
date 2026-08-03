package dev.wikilens.vault

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.acl.AclRegistry
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

        return pages.map { (pid, meta) ->
            val tokens = aclByPage[pid] ?: listOf(PUBLIC)
            acl.putPage(pid, tokens)
            IndexedPage(
                id = pid,
                title = meta["title"]?.toString().orEmpty(),
                space = meta["space"]?.toString().orEmpty(),
                path = relPagePath(pid),
                body = readBody(root, pid),
                anchors = anchors[pid].orEmpty(),
                aclTokens = tokens,
            )
        }
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

    private fun readAcl(root: Path): Map<String, List<String>> {
        val dir = root.resolve("mirror").resolve("acl")
        if (!Files.isDirectory(dir)) return emptyMap()
        val out = HashMap<String, List<String>>()
        Files.list(dir).use { s ->
            s.filter { it.toString().endsWith(".json") }.forEach { f ->
                runCatching {
                    @Suppress("UNCHECKED_CAST")
                    val m = mapper.readValue(Files.readString(f), Map::class.java)
                        as Map<String, List<String>>
                    out.putAll(m)
                }
            }
        }
        return out
    }

    private fun readBody(root: Path, pid: String): String {
        val f = root.resolve(relPagePath(pid))
        return if (Files.exists(f)) runCatching { Files.readString(f) }.getOrDefault("") else ""
    }

    /** 로컬판과 동일한 샤딩 규칙. 계약이므로 바꾸면 안 된다. */
    private fun relPagePath(pid: String): String {
        val padded = pid.padStart(4, '0')
        return "mirror/pages/${padded.substring(0, 2)}/${padded.substring(2, 4)}/$pid.md"
    }

    companion object { const val PUBLIC = "@public" }
}
