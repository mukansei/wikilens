package io.wikilens.vault

import java.io.BufferedReader
import java.io.InputStream
import java.io.InputStreamReader
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path

/**
 * 깨진 바이트를 예외가 아니라 `U+FFFD` 로 넘기는 UTF-8 리더.
 *
 * 기본 디코더는 `REPORT` 라 **줄을 당길 때** `MalformedInputException` 을 던진다 —
 * 여는 자리만 감싸도 못 막고 그대로 HTTP 500 이 된다. 반토막 난 파일 하나가 전원의
 * grep 을 죽였다. `REPLACE` 면 그 지점만 깨지고 나머지 본문은 온전히 검색·열람된다.
 *
 * 볼트는 Python 싱크가 UTF-8 로 쓰므로 정상 경로에서는 안 생긴다. 디스크가 차거나
 * 싱크가 도중에 죽으면 남는다.
 *
 * **세 소비자가 같은 디코더를 써야 한다** — `read`(ContentService) · JVM 스캔 ·
 * rg 의 stdout. 한 벌만 REPORT 로 되돌아가면 그 경로만 500 이 난다.
 */
object VaultText {
    fun reader(f: Path): BufferedReader = reader(Files.newInputStream(f))

    fun reader(s: InputStream): BufferedReader = BufferedReader(
        InputStreamReader(
            s,
            StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPLACE)
                .onUnmappableCharacter(CodingErrorAction.REPLACE),
        )
    )
}
