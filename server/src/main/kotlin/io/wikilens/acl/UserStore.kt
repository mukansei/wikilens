package io.wikilens.acl

import com.fasterxml.jackson.databind.ObjectMapper
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption

/**
 * 사용자 등록을 디스크에 남긴다.
 *
 * **없으면 재기동마다 전원이 사라진다** — 색인·ACL 맵은 볼트에서, 궤적은 로그에서
 * 되살아나는데 등록만 순수 힙이었다. 그 사이 전원이 빈손이 되고 그 상태가 "문서가
 * 없다" 와 구별되지 않는다(`CLAUDE.md` 조용히 실패 10·12번).
 *
 * **볼트가 아니라 상태 디렉터리에 둔다** — 볼트는 `sync` 가 덮어쓰는 파생물이고 이건
 * 운영자가 만든 원본이다. 백업 대상도 `trajectories.jsonl` 과 같다.
 *
 * 쓰기는 임시 파일 → `ATOMIC_MOVE`. 반쪽 파일이 남으면 다음 기동에 전원이 사라지는데,
 * 그게 이 파일이 막으려는 바로 그 상태다.
 */
class UserStore(stateDir: Path, private val mapper: ObjectMapper) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val file: Path = stateDir.resolve("acl-users.json")

    init {
        Files.createDirectories(stateDir)
    }

    /** 기동 시 1회. 못 읽으면 빈 맵 — 등록이 없는 것과 같고, fail-closed 라 안전하다. */
    fun load(): Map<String, Set<String>> {
        if (!Files.isRegularFile(file)) return emptyMap()
        return runCatching {
            @Suppress("UNCHECKED_CAST")
            val raw = mapper.readValue(Files.readString(file), Map::class.java) as Map<String, List<String>>
            raw.mapValues { (_, v) -> v.toSet() }
        }.onFailure {
            // **삼키면 안 된다** — 조용히 빈 맵이면 이 파일이 막으려던 상태로 돌아간다.
            log.error("사용자 등록을 읽지 못했습니다: {} — 전원이 빈손이 됩니다. " +
                "파일을 고치거나 다시 등록하세요.", file, it)
        }.getOrDefault(emptyMap())
    }

    fun save(byUser: Map<String, Set<String>>) {
        runCatching {
            val tmp = Files.createTempFile(file.parent, "acl-users", ".tmp")
            Files.writeString(tmp, mapper.writeValueAsString(byUser.mapValues { it.value.sorted() }))
            Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
        }.onFailure {
            log.error("사용자 등록을 저장하지 못했습니다: {} — **재기동하면 사라집니다.**", file, it)
        }
    }
}
