package io.wikilens.config

import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

/**
 * 볼트 경로 폴백. 이게 있어서 `server/mirror-root` 심링크를 손으로 만들 필요가 없다.
 *
 * **읽기 실패가 기동을 막으면 안 된다** — 이건 편의 폴백이고, 실패하면 원래의 빈 볼트
 * 경로로 돌아가 `IndexingService` 가 ERROR 로 알린다. 그래서 깨진 JSON·없는 키·엉뚱한
 * 타입이 전부 `null` 이어야 하고, 예외로 새면 안 된다.
 */
class UserConfigTest {

    private fun writeConfig(home: Path, text: String) {
        val dir = home.resolve(".wikilens")
        Files.createDirectories(dir)
        Files.writeString(dir.resolve("config.json"), text)
    }

    @Test
    fun `설정에 적힌 볼트를 절대경로로 준다`(@TempDir tmp: Path) {
        val vault = Files.createDirectories(tmp.resolve("wiki"))
        writeConfig(tmp, """{"vault": "$vault", "cli": "/x/y"}""")
        val got = UserConfig.vaultRoot(tmp)
        assertEquals(vault.toAbsolutePath().normalize(), got)
        assertTrue(got!!.isAbsolute, "상대경로면 실행 디렉터리에 따라 다른 자리를 가리킨다")
    }

    @Test
    fun `파일이 없으면 null`(@TempDir tmp: Path) {
        assertNull(UserConfig.vaultRoot(tmp))
    }

    @Test
    fun `깨진 JSON 은 예외가 아니라 null`(@TempDir tmp: Path) {
        writeConfig(tmp, "{ 이건 JSON 이 아니다")
        assertNull(UserConfig.vaultRoot(tmp))
    }

    @Test
    fun `키가 없거나 문자열이 아니면 null`(@TempDir tmp: Path) {
        writeConfig(tmp, """{"cli": "/x/y"}""")
        assertNull(UserConfig.vaultRoot(tmp), "키가 없다")

        writeConfig(tmp, """{"vault": 3}""")
        assertNull(UserConfig.vaultRoot(tmp), "숫자는 경로가 아니다")

        writeConfig(tmp, """{"vault": "   "}""")
        assertNull(UserConfig.vaultRoot(tmp), "빈 문자열은 미설정이다")
    }

    /**
     * **`HOME` 이 `user.home` 을 이겨야 한다.** macOS JDK 는 `HOME` 을 무시하고
     * `getpwuid` 결과를 `user.home` 에 넣는데(실측: `HOME=/tmp/x java …` 가
     * `user.home=/Users/…` 를 냈다), 파이썬 `Path.home()` 은 `HOME` 을 먼저 본다.
     * 갈리면 CLI 가 쓴 설정을 서버가 다른 자리에서 찾아 폴백이 조용히 안 걸린다.
     */
    @Test
    fun `홈은 HOME 환경변수를 먼저 본다`() {
        val env = System.getenv("HOME")
        if (env.isNullOrBlank()) return          // HOME 없는 환경에서는 검사할 것이 없다
        assertEquals(Path.of(env), UserConfig.defaultHome())
    }
}
