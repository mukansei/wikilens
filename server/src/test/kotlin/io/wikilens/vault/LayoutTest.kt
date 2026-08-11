package io.wikilens.vault

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

/**
 * 샤딩 규칙이 Python `cli/wikilens/layout.py` 와 같은 경로를 내는지 대조한다.
 *
 * 두 언어가 **파일로만** 연결되어 있어, 규칙이 갈라지면 서버가 파일을 못 찾는데
 * 에러는 안 난다 — 그냥 결과가 빈다. 기대값은 Python 구현으로 뽑아 박아둔 것이다.
 *
 * 특히 **뒤에서 자른다**는 점이 핵심이다. 앞자리는 엔트로피가 낮아 한 디렉터리에
 * 뭉친다(실측: 앞2/앞4 최대 378개 → 뒤2 최대 37개).
 */
class LayoutTest {

    @Test
    fun `Python 과 같은 경로를 낸다`() {
        assertEquals("mirror/pages/03/102728003.md", VaultLayout.relPagePath("102728003"))
        assertEquals("mirror/pages/37/43933937.md", VaultLayout.relPagePath("43933937"))
        assertEquals("mirror/pages/45/12345.md", VaultLayout.relPagePath("12345"))
    }

    @Test
    fun `짧은 ID 는 0으로 패딩한다`() {
        assertEquals("mirror/pages/07/7.md", VaultLayout.relPagePath("7"))
        assertEquals("mirror/pages/00/100.md", VaultLayout.relPagePath("100"))
    }
}
