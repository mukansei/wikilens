package dev.wikilens.config

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import java.nio.file.Files
import java.nio.file.Path

/**
 * `~/.wikilens/config.json` — 세 소비자가 공유하는 **설정 정본**.
 *
 * 비밀 아닌 설정은 여기, 토큰류는 `~/.wikilens/env.sh`(600) 다(`DECISIONS.md` D10).
 * 이 파일을 읽는 자리가 이제 셋이다 — 로컬판 `vault_status.py`, 서버판 MCP 프록시
 * `wikilens_mcp.py`, 그리고 여기.
 *
 * **서버가 이걸 읽는 이유:** 예전에는 서버만 정본 밖에 있어서, 볼트를 가리키는 방법이
 * `server/mirror-root` **심링크를 손으로 만드는 것**뿐이었다. 그 단계는 `server/README.md`
 * 에만 적혀 있고 루트 `README.md` 에는 없어서, 문서를 따라간 사람이 빈 볼트로 기동하는
 * 경로가 남아 있었다. CLI 가 만든 볼트를 서버가 그대로 찾으면 그 단계가 사라진다.
 *
 * **명시 설정이 항상 이긴다.** 이 폴백은 `vault-root` 가 기본값 그대로일 때만,
 * 그리고 그 기본 경로가 실제로 없을 때만 쓰인다 — 운영 배포는 절대경로를 주므로
 * 여기 닿지 않는다.
 *
 * ### 조용히 틀어질 수 있는 자리
 * 키 이름 `vault` 가 Python 쪽과 **문자열로만** 이어져 있다. 바뀌면 예외 없이 폴백만
 * 조용히 멈춘다 — `shared_contract.sh` 가 세 파일의 키를 함께 검사한다.
 */
object UserConfig {
    /** Python 쪽 `_config()["vault"]` 와 같은 키. 함께 바꿔야 한다. */
    private const val VAULT_KEY = "vault"

    /**
     * **`HOME` 을 `user.home` 보다 먼저 본다.** 파이썬 쪽 `Path.home()` 이 그렇게
     * 동작하는데(POSIX 에서 `HOME` 우선, 없으면 passwd), macOS JDK 는 `HOME` 을 무시하고
     * `getpwuid` 결과를 `user.home` 에 넣는다 — 실측: `HOME=/tmp/x java …` 가
     * `user.home=/Users/hyunwpark` 를 냈다. 둘이 갈리면 **CLI 가 쓴 설정을 서버가 다른
     * 자리에서 찾아** 폴백이 조용히 안 걸린다. 서버는 systemd·cron 처럼 `HOME` 이
     * 다르게 잡히는 환경에서 도는 물건이라 가정이 아니라 실제 갈림이다.
     *
     * **부를 때마다 다시 푼다.** 상수로 굳히면 테스트가 임시 홈을 가리킬 수 없고,
     * 그러면 이 폴백은 개발자 머신의 실제 설정에 따라 결과가 달라지는 검사 불가능한
     * 코드가 된다.
     */
    /**
     * 테스트 전용 홈 재정의. JVM 은 자기 환경변수를 못 바꾸는데 이 폴백이 보는 것이
     * 바로 `HOME` 이라, 이 이음매가 없으면 **개발자 머신의 실제 `~/.wikilens` 에 따라
     * 결과가 달라지는** 검사 불가능한 코드가 된다.
     */
    internal var homeOverride: Path? = null

    internal fun defaultHome(): Path {
        homeOverride?.let { return it }
        val home = System.getenv("HOME")?.takeIf { it.isNotBlank() }
            ?: System.getProperty("user.home")
        return Path.of(home)
    }

    private val mapper = ObjectMapper()

    /**
     * 설정에 적힌 볼트 경로. 파일이 없거나 깨졌거나 키가 없으면 `null`.
     *
     * 읽기 실패로 기동을 막지 않는다 — 이건 편의 폴백이고, 실패하면 그냥 원래의
     * "빈 볼트" 경로로 돌아가 `IndexingService` 가 ERROR 로 알린다.
     */
    fun vaultRoot(home: Path = defaultHome()): Path? {
        val file = home.resolve(".wikilens").resolve("config.json")
        if (!Files.isRegularFile(file)) return null
        val node: JsonNode = runCatching { mapper.readTree(file.toFile()) }.getOrNull() ?: return null
        val raw = node.path(VAULT_KEY).takeIf { it.isTextual }?.asText()?.trim().orEmpty()
        if (raw.isEmpty()) return null
        return runCatching { Path.of(raw).toAbsolutePath().normalize() }.getOrNull()
    }
}
