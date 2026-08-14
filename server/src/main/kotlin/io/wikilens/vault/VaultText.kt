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
 * **다섯 소비자가 같은 디코더를 써야 한다** — `read`(ContentService) · JVM 스캔 ·
 * rg 의 stdout · **색인**(`VaultReader.readBody`) · **궤적 재생**
 * (`FileTrajectorySink.replayInto`). 한 벌만 REPORT 로 되돌아가면 그 경로만 갈린다.
 *
 * **다섯째는 볼트 파일이 아니다** — 궤적 로그는 서버가 쓴다. 그래도 같은 디코더를 쓰는
 * 이유는 실패 모양이 같아서다: 쓰기 도중 죽으면 마지막 줄이 글자 중간에서 잘리고,
 * REPORT 면 그 한 줄이 재생을 통째로 죽여 **서버가 안 뜬다**(실측).
 *
 * **rg 는 디코더만으로 부족하다.** stdout 을 이 리더로 읽어도 rg 가 `--json` 에서
 * UTF-8 아닌 줄을 `lines.text` 가 아니라 `lines.bytes`(base64)로 내므로, 그쪽을 안 풀면
 * 그 줄만 빈 문자열이 된다 — `RipgrepEngine.lineText`.
 *
 * **넷째가 오래 빠져 있었다.** 색인만 `Files.readString` 을 쓰고 실패를 빈 문자열로
 * 삼켜서, 깨진 파일 하나가 `grep`·`read` 에서는 정상인데 `search` 에서만 사라졌다
 * (실측 2026-08-12). 이 목록이 셋이라고 적혀 있던 것이 그 결함을 가렸다 —
 * **소비자를 추가하면 여기도 고칠 것.** `MalformedBodyTest` 가 색인 경로를 잠근다.
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
