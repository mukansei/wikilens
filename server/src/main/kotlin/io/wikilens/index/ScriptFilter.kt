package io.wikilens.index

/**
 * 선언한 문자 집합 밖의 문서를 색인에서 뺀다. **다국어 코퍼스 대응이다.**
 *
 * 푸는 문제: 같은 내용이 여러 언어로 있으면, 읽지 못하는 언어의 문서가 어휘 순위에서
 * 이길 수 있다. 실측(2026-08-14, 한·베 혼재 코퍼스): 한국어 질의에 **베트남어 번역본이
 * 1위이고 한국어 원본이 10위 밖**이었다 — 두 문서가 같은 영문 식별자(`ga`·URL)를
 * 공유하는데 번역본이 두 배 길어 tf 가 높았다. 어휘 층은 정상 동작한 것이고,
 * **언어가 유일한 분리 신호**다.
 *
 * ### 낱말 단위로 센다 — 글자가 아니라
 *
 * 베트남어는 글자 대부분이 평범한 라틴이고 성조부호만 다르다(`Sử dụng` 에서 이질적인
 * 글자는 2/7). 글자로 세면 신호가 묽어진다. 낱말에 선언 밖 글자가 **하나라도** 있으면
 * 그 낱말을 밖으로 세면, 글자마다 다른 한자·태국어와 부호만 다른 베트남어가 **같은
 * 척도**로 잡힌다. 실측(같은 문서):
 *
 *     글자 단위   한국어 0.00% · 베트남어 4.17% · 베트남어(성조 많음) 24.26%
 *     낱말 단위   한국어 0.00% · 베트남어 16.6% · 베트남어(성조 많음) 76.6%
 *
 * 그래서 문턱 하나가 모든 문자 집합에 통한다.
 *
 * ### 기본이 꺼짐이다
 *
 * 선언이 비면 전부 색인한다. 켜짐이 기본이면 **처음 띄운 사람의 문서가 조용히
 * 사라진다** — 이 저장소가 ACL(조용히 실패 10)과 빈 볼트(14)로 두 번 물린 모양이다.
 *
 * ### 빠진 문서는 통째로 사라진다
 *
 * 색인에 없으면 `search`·`read`·`grep`·`tree` 넷 다 못 찾는다(전부 `index` 의 메타를
 * 거친다). 그게 이 기능의 값어치이자 위험이라, 제외 수를 기동 로그·`/api/stats`·
 * `--status` 가 말한다. 안 그러면 "문서가 없다" 와 구별되지 않는다.
 */
class ScriptFilter(specs: List<String>, private val threshold: Double) {

    private val declared: List<ScriptSet> = specs.mapNotNull { it.trim().ifEmpty { null } }
        .map { ScriptSet.of(it) }

    val enabled: Boolean get() = declared.isNotEmpty()

    /** 설정을 사람이 읽는 한 줄로. 기동 로그와 `/api/stats` 가 쓴다. */
    val describe: String
        get() = if (!enabled) "꺼짐" else
            "${declared.joinToString("·") { it.name }} · 문턱 ${(threshold * 100).toInt()}%"

    /**
     * 선언 밖 낱말의 비율. 선언이 없으면 0(=전부 통과).
     *
     * **글자가 없는 낱말은 안 센다** — 숫자·기호만 있는 토큰(`2026`·`v1.2`)은 어느
     * 언어에도 속하지 않아서, 분모에 넣으면 코드가 많은 문서가 무조건 통과한다.
     */
    fun foreignWordRatio(text: String): Double {
        if (!enabled) return 0.0
        var total = 0
        var foreign = 0
        var hasLetter = false
        var isForeign = false
        for (ch in text) {
            val cp = ch.code
            if (Character.isLetter(cp)) {
                hasLetter = true
                if (declared.none { cp in it }) isForeign = true
            } else if (!Character.isDigit(cp) && cp != '_'.code) {
                // 낱말 경계. 숫자·밑줄은 낱말 안에 있어도 언어를 안 가른다.
                if (hasLetter) { total++; if (isForeign) foreign++ }
                hasLetter = false; isForeign = false
            }
        }
        if (hasLetter) { total++; if (isForeign) foreign++ }
        return if (total > 0) foreign.toDouble() / total else 0.0
    }

    /** 색인할 것인가. 제목도 함께 본다 — 본문이 짧고 제목만 다른 언어인 문서가 있다. */
    fun accepts(page: IndexedPage): Boolean =
        !enabled || foreignWordRatio(page.title + "\n" + page.body) <= threshold
}
