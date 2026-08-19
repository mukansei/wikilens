package io.wikilens.vault

import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Paths
import java.time.Instant

/**
 * **두 판이 같은 커서에 같은 나이를 내는지.** 문턱이 같은 것만으로는 부족하다 —
 * 파싱이 갈리면 한쪽만 `null`("모름")이 되어 같은 볼트에 다른 진단이 나온다.
 *
 * 기존 계약은 `STALE_DAYS = 7` 문자열만 grep 한다. 이 테스트는 **실제로 파싱해서**
 * 대조한다(`shared_contract.sh` 와 `contract/shared-fixture/` 의 관계와 같다).
 */
class VaultAgeParityTest {

    @Test
    fun `파이썬과 같은 입력에 같은 답을 낸다`() {
        val py = Paths.get("..", "plugin", "local", "scripts", "vault_status.py")
        if (!Files.exists(py)) return   // 서버만 떼어 빌드하는 경우

        val now = Instant.parse("2026-08-19T00:00:00Z")
        val cases = listOf(
            "2026-08-18 01:10",            // sync 가 실제로 쓰는 형식
            "2026-08-18T01:10:00Z",
            "2026-08-18T01:10:00+09:00",
            "2026-08-12 00:00",            // 7일 — 아직 stale 아님
            "2026-08-11 00:00",            // 8일 — stale
            "2026-08-18",                  // 시각 없음
            "",
            "어제",
        )
        val script = """
            import json, sys
            sys.path.insert(0, "../plugin/local/scripts")
            from datetime import datetime, timezone
            import vault_status as V
            now = datetime(2026, 8, 19, tzinfo=timezone.utc)
            out = {}
            for raw in json.loads(sys.argv[1]):
                c = V._parse_cursor(raw or None)
                out[raw] = [c.isoformat() if c else None, (now - c).days if c else None]
            print(json.dumps(out))
        """.trimIndent()

        val json = cases.joinToString(",", "[", "]") { "\"$it\"" }
        val proc = ProcessBuilder("python3", "-c", script, json)
            .redirectErrorStream(true).start()
        val out = proc.inputStream.bufferedReader().readText().trim()
        check(proc.waitFor() == 0) { "파이썬 실행 실패:\n$out" }

        val mapper = com.fasterxml.jackson.databind.ObjectMapper()
        val pyResult = mapper.readTree(out)

        val mismatches = mutableListOf<String>()
        for (raw in cases) {
            val kt = VaultAge.parse(raw)
            val ktAge = VaultAge.ageDays(kt, now)
            val node = pyResult.get(raw)
            val pyAge = if (node.get(1).isNull) null else node.get(1).asLong()
            // **"모름" 이 갈리는 것이 가장 나쁘다** — 한쪽만 조용해진다.
            if ((kt == null) != node.get(0).isNull || ktAge != pyAge) {
                mismatches += "\"$raw\": Kotlin=${kt}/${ktAge} · Python=${node.get(0)}/${pyAge}"
            }
        }
        check(mismatches.isEmpty()) {
            "두 판이 갈렸다 — 같은 볼트에 다른 진단이 나온다:\n  " + mismatches.joinToString("\n  ")
        }
    }
}
