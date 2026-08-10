package dev.wikilens

import dev.wikilens.vault.VaultLayout
import java.nio.file.Files
import java.nio.file.Path

/**
 * 파라미터로 만드는 볼트. **성능 측정을 코퍼스에서 떼어내려고 만들었다.**
 *
 * ### 왜
 *
 * 예전에는 성능을 `~/.wikilens/vault`(Coway 위키 13,921건)로 쟀다. 그러면 나오는 값이
 * **WikiLens 에 대한 사실이 아니라 그 위키에 대한 사실**이다 — 문서 크기 분포·언어·
 * 머신이 다르면 전부 달라진다. 그런데 저장소는 그 값을 `약 17,000건에서 잘린다` 처럼
 * **용량 계획 상수**로 적어놨다.
 *
 * 더 나쁜 것은 재현이 안 된다는 점이다. `RipgrepBudgetTest` 는 실코퍼스가 없으면
 * 통째로 건너뛰었다 — 즉 **이 머신 밖에서는 아무것도 검증하지 않았다.**
 *
 * ### 무엇을 흉내내나
 *
 * `VaultReader` 와 두 grep 엔진이 실제로 읽는 것만 만든다 — `mirror/.sync-state.json`
 * 과 `mirror/pages/{샤드}/{id}.md`. 샤딩은 [VaultLayout] 을 그대로 쓴다(규칙이 갈리면
 * 엔진이 파일을 못 찾는다).
 *
 * 문서 내용은 **결정적**이다(`seed` 로 고정). 같은 파라미터면 어느 머신에서도 같은
 * 볼트가 나오므로, 측정값을 서로 비교할 수 있다.
 *
 * ### 파라미터가 곧 결론의 단위다
 *
 * `docs` 와 `lines` 를 바꿔가며 재면 "문서당 비용" 이 나오고, 그것이 코퍼스와 무관한
 * 값이다. 특정 N 에서의 총시간은 그 값에 N 을 곱한 것일 뿐이라 문서에 적을 값이 아니다.
 */
object SyntheticVault {

    /** 매치가 **없어야** 하는 패턴. 어느 생성 문서에도 안 나오게 골랐다. */
    const val NO_MATCH = "존재하지않는문자열zzqqxx9999"

    /** 모든 문서에 정확히 한 번씩 나오는 패턴. 매치 수 = 문서 수가 된다. */
    const val IN_EVERY_DOC = "MARKERUNIQUE"

    /**
     * [docs] 개 문서를 가진 볼트를 만들고 루트를 돌려준다.
     *
     * @param lines 문서당 줄 수. 스캔 비용은 문서 수가 아니라 **바이트**에 붙으므로
     *   이것도 파라미터여야 한다 — 문서 수만 바꿔 재면 "문서가 작은 위키" 하나를
     *   또 만드는 것이다.
     */
    fun create(dir: Path, docs: Int, lines: Int = 40, seed: Long = 42): Path {
        val pages = StringBuilder("""{"cursor":"2026-01-01T00:00:00Z","pages":{""")
        val rnd = java.util.Random(seed)
        val words = listOf("배포", "인증", "파이프라인", "권한", "색인", "궤적",
                           "deploy", "auth", "index", "token", "Coway", "Jenkins")

        for (i in 0 until docs) {
            val id = (100_000 + i).toString()
            val file = dir.resolve(VaultLayout.relPagePath(id))
            Files.createDirectories(file.parent)
            val body = buildString {
                append("# 문서 $id\n\n")
                append("$IN_EVERY_DOC $id\n")
                repeat(lines) {
                    for (w in 0 until 8) append(words[rnd.nextInt(words.size)]).append(' ')
                    append('\n')
                }
            }
            Files.writeString(file, body)

            if (i > 0) pages.append(',')
            pages.append(""""$id":{"title":"문서 $id","space":"SYN","version":1,""")
            pages.append(""""updated":"2026-01-01T00:00:00Z","ancestors":[]}""")
        }
        pages.append("}}")

        val state = dir.resolve("mirror").resolve(".sync-state.json")
        Files.createDirectories(state.parent)
        Files.writeString(state, pages.toString())
        return dir
    }
}
