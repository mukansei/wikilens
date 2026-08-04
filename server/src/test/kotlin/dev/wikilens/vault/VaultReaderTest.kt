package dev.wikilens.vault

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.acl.AclRegistry
import dev.wikilens.index.IndexedPage
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * `shared-fixtures/mini-vault/`는 Python `cli/tests/test_contract_fixtures.py`와
 * **공유하는 정본 볼트**다. Python이 `build()`로 이 픽스처를 재생성해 형식을 검증하고,
 * 여기서는 같은 체크인된 산출물을 `VaultReader`가 그대로 소화하는지 확인한다.
 *
 * grep 기반 계약 검사(`shared_contracts.sh`)는 문자열이 존재하는지만 보므로 리팩터링에
 * 취약하다. 이 테스트는 실제 파싱 동작을 검증하므로, Python이 `ancestors`나
 * `anchors.jsonl` 스키마를 조용히 바꾸면 여기서 시끄럽게 깨진다.
 */
class VaultReaderTest {

    // server/ 가 Gradle 작업 디렉터리이므로 저장소 루트 기준 상대 경로.
    private val fixtureRoot: Path = Path.of("..", "shared-fixtures", "mini-vault")

    private fun readFixture(): List<IndexedPage> {
        val acl = AclRegistry()
        val reader = VaultReader(ObjectMapper())
        return reader.read(fixtureRoot, acl)
    }

    @Test
    fun `모든 페이지를 읽는다`() {
        val pages = readFixture()
        assertEquals(setOf("100", "200", "300"), pages.map { it.id }.toSet())
    }

    @Test
    fun `샤딩 경로가 Python 레이아웃과 일치한다`() {
        val pages = readFixture().associateBy { it.id }
        assertEquals("mirror/pages/01/00/100.md", pages.getValue("100").path)
        assertEquals("mirror/pages/02/00/200.md", pages.getValue("200").path)
        assertEquals("mirror/pages/03/00/300.md", pages.getValue("300").path)
    }

    @Test
    fun `ancestors 를 루트부터 직속 부모까지 순서대로 파싱한다`() {
        val pages = readFixture().associateBy { it.id }

        assertTrue(pages.getValue("100").ancestors.isEmpty(), "루트는 조상이 없어야 한다")

        val childAncestors = pages.getValue("200").ancestors
        assertEquals(1, childAncestors.size)
        assertEquals("100", childAncestors[0].id)
        assertEquals("루트", childAncestors[0].title)

        // 동기화 범위 밖 부모(999999)도 파싱 자체는 그대로 — "범위 밖이라 무시"는
        // LuceneIndex.buildTree() 의 책임이지 VaultReader 의 책임이 아니다.
        val orphanAncestors = pages.getValue("300").ancestors
        assertEquals(1, orphanAncestors.size)
        assertEquals("999999", orphanAncestors[0].id)
    }

    @Test
    fun `anchors_jsonl 을 target 기준으로 매핑한다`() {
        val pages = readFixture().associateBy { it.id }
        assertEquals(listOf("루트로"), pages.getValue("100").anchors)
        assertEquals(listOf("자식 문서"), pages.getValue("200").anchors)
        assertTrue(pages.getValue("300").anchors.isEmpty(), "고아 문서는 앵커가 없어야 한다")
    }

    @Test
    fun `acl 디렉터리가 없으면 모든 페이지가 공개 기본값이다`() {
        val pages = readFixture()
        assertTrue(pages.all { it.aclTokens == listOf(VaultReader.PUBLIC) })
    }

    @Test
    fun `본문을 파일에서 그대로 읽는다`() {
        val pages = readFixture().associateBy { it.id }
        assertTrue(pages.getValue("100").body.contains("루트다"))
    }
}
