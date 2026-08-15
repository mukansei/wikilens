package io.wikilens.index

/**
 * 이름 붙은 유니코드 문자 범위. 색인할 문서를 고르는 데 쓴다([ScriptFilter]).
 *
 * **문자 집합이지 언어가 아니다.** 이 구분이 이 파일 전체의 전제다 — 라틴 하나에
 * 100개 넘는 언어가 들어 있어서, `french` 같은 이름은 원리적으로 만들 수 없다
 * (`é è à ç` 를 스페인어·포르투갈어가 똑같이 쓴다). 갈리는 것은 **자기만 쓰는 문자
 * 범위가 있는 언어**뿐이고, [VIETNAMESE] 가 그 예다(U+1EA0~1EF9 는 베트남어 전용).
 *
 * 그래서 이름을 다 갖출 수 없다. 대신 설정이 **범위를 직접 적을 수 있게** 한다
 * (`"U+0100-017F"`) — 폴란드어·터키어 서명을 미리 만들어 둘 필요가 없어진다.
 */
data class ScriptSet(val name: String, private val ranges: List<IntRange>) {

    operator fun contains(cp: Int): Boolean = ranges.any { cp in it }

    companion object {
        /** 라틴 중 A–Z a–z. 코드·URL·영문이 전부 여기라 거의 항상 선언된다. */
        val ASCII = ScriptSet("ascii", listOf(0x0041..0x005A, 0x0061..0x007A))

        /**
         * 라틴 전체. **ASCII 를 포함한다** — `latin` 을 선언하면 `ascii` 는 필요 없다.
         * 확장 영역에 유럽어와 베트남어가 함께 들어 있어, 이것을 선언하면 그 전부를 받는다.
         */
        val LATIN = ScriptSet("latin", listOf(
            0x0041..0x005A, 0x0061..0x007A,   // 기본
            0x00C0..0x024F,                   // 라틴-1 보충 · 확장-A · 확장-B
            0x1E00..0x1EFF,                   // 확장 추가 (베트남어가 여기 산다)
            0x2C60..0x2C7F, 0xA720..0xA7FF,   // 확장-C · 확장-D
        ))

        /**
         * 베트남어 전용 범위. **[LATIN] 의 부분집합이므로 따로 선언해야 의미가 있다** —
         * `[hangul, ascii, vietnamese]` 는 "한국어·영어·베트남어를 읽는다" 이고,
         * `[hangul, latin]` 은 그 셋에 유럽어까지 더한 것이다.
         *
         * U+1EA0~1EF9 는 다른 언어가 쓰지 않는다. 낮은 영역의 몇 자는 유럽어와 겹치지만
         * (`à á â ã è é ê ì í ò ó ô õ ù ú ý`), 베트남어 문장은 U+1EXX 를 거의 반드시
         * 포함하므로 그것을 놓치지 않는다.
         */
        val VIETNAMESE = ScriptSet("vietnamese", listOf(
            0x1EA0..0x1EF9,
            0x00C0..0x00C3, 0x00C8..0x00CA, 0x00CC..0x00CD, 0x00D2..0x00D5,
            0x00D9..0x00DA, 0x00DD..0x00DD, 0x00E0..0x00E3, 0x00E8..0x00EA,
            0x00EC..0x00ED, 0x00F2..0x00F5, 0x00F9..0x00FA, 0x00FD..0x00FD,
            0x0102..0x0103, 0x0110..0x0111, 0x0128..0x0129, 0x0168..0x0169,
            0x01A0..0x01B0,
        ))

        val HANGUL = ScriptSet("hangul", listOf(
            0xAC00..0xD7A3,   // 음절
            0x1100..0x11FF, 0x3130..0x318F, 0xA960..0xA97F,   // 낱자
        ))

        val HAN = ScriptSet("han", listOf(
            0x4E00..0x9FFF, 0x3400..0x4DBF, 0xF900..0xFAFF, 0x20000..0x2A6DF,
        ))

        val KANA = ScriptSet("kana", listOf(0x3040..0x309F, 0x30A0..0x30FF, 0x31F0..0x31FF))
        val CYRILLIC = ScriptSet("cyrillic", listOf(0x0400..0x04FF, 0x0500..0x052F, 0x2DE0..0x2DFF))
        val ARABIC = ScriptSet("arabic", listOf(0x0600..0x06FF, 0x0750..0x077F, 0x08A0..0x08FF, 0xFB50..0xFDFF))
        val DEVANAGARI = ScriptSet("devanagari", listOf(0x0900..0x097F, 0xA8E0..0xA8FF))
        val THAI = ScriptSet("thai", listOf(0x0E00..0x0E7F))
        val HEBREW = ScriptSet("hebrew", listOf(0x0590..0x05FF, 0xFB1D..0xFB4F))
        val GREEK = ScriptSet("greek", listOf(0x0370..0x03FF, 0x1F00..0x1FFF))
        val ARMENIAN = ScriptSet("armenian", listOf(0x0530..0x058F))
        val GEORGIAN = ScriptSet("georgian", listOf(0x10A0..0x10FF, 0x2D00..0x2D2F))
        val BENGALI = ScriptSet("bengali", listOf(0x0980..0x09FF))
        val TAMIL = ScriptSet("tamil", listOf(0x0B80..0x0BFF))
        val TELUGU = ScriptSet("telugu", listOf(0x0C00..0x0C7F))
        val KANNADA = ScriptSet("kannada", listOf(0x0C80..0x0CFF))
        val MALAYALAM = ScriptSet("malayalam", listOf(0x0D00..0x0D7F))
        val SINHALA = ScriptSet("sinhala", listOf(0x0D80..0x0DFF))
        val KHMER = ScriptSet("khmer", listOf(0x1780..0x17FF))
        val LAO = ScriptSet("lao", listOf(0x0E80..0x0EFF))
        val MYANMAR = ScriptSet("myanmar", listOf(0x1000..0x109F))
        val ETHIOPIC = ScriptSet("ethiopic", listOf(0x1200..0x137F))
        val TIBETAN = ScriptSet("tibetan", listOf(0x0F00..0x0FFF))

        private val BY_NAME = listOf(
            ASCII, LATIN, VIETNAMESE, HANGUL, HAN, KANA, CYRILLIC, ARABIC, DEVANAGARI,
            THAI, HEBREW, GREEK, ARMENIAN, GEORGIAN, BENGALI, TAMIL, TELUGU, KANNADA,
            MALAYALAM, SINHALA, KHMER, LAO, MYANMAR, ETHIOPIC, TIBETAN,
        ).associateBy { it.name }

        /** 설정에 쓸 수 있는 이름들. 기동 실패 메시지에 싣는다. */
        val NAMES: List<String> = BY_NAME.keys.sorted()

        private val RANGE = Regex("""U\+([0-9A-Fa-f]{4,6})-([0-9A-Fa-f]{4,6})""")

        /**
         * 설정 문자열 하나를 문자 집합으로. 이름이거나 `U+0100-017F` 꼴의 범위다.
         *
         * **모르는 이름은 예외를 던진다** — 조용히 무시하면 운영자는 필터가 걸린 줄 알고
         * 배포한다. 분석기 이름을 틀렸을 때와 같은 규칙이다(D14).
         */
        fun of(spec: String): ScriptSet {
            val s = spec.trim()
            BY_NAME[s.lowercase()]?.let { return it }
            RANGE.matchEntire(s)?.let { m ->
                val a = m.groupValues[1].toInt(16)
                val b = m.groupValues[2].toInt(16)
                require(a <= b) { "범위가 뒤집혔습니다: $s" }
                return ScriptSet(s, listOf(a..b))
            }
            throw IllegalArgumentException(
                "알 수 없는 문자 집합 '$s'. 가능한 이름: ${NAMES.joinToString("·")} " +
                    "— 또는 범위를 직접 적으세요(예: U+0100-017F)"
            )
        }
    }
}
