package io.wikilens.index

import org.junit.jupiter.api.Test
import java.nio.file.Files
import kotlin.test.assertTrue

/**
 * **앵커 · 제목 · 본문 가중이 실제로 순위를 바꾼다.**
 *
 * `FieldBoost` 의 `4 : 3 : 1` 은 이 저장소의 핵심 주장(앵커 텍스트 색인)이 작동하는
 * 유일한 기제인데, 값을 1:1:1 로 만들어도 **전체 테스트도 계약 79개도 통과했다**
 * (실측 2026-08-12). 문서 두 곳(`README.md`·`docs/architecture.md`)에만 적혀 있고
 * 검사하는 곳이 없었다.
 *
 * 세 문서가 **같은 항을 서로 다른 필드에 하나씩만** 갖는다. 다른 필드는 길이가 같은
 * 채움말이라 BM25 의 IDF·필드 길이 정규화가 셋 다 같고, 그래서 **점수 차이는 가중만
 * 반영한다.** 순서가 아니라 점수로 단언하는 이유가 그것이다 — 1:1:1 이면 점수가
 * 같아지는데, 순서만 보면 docId 순으로 우연히 통과한다.
 */
class FieldBoostTest {

    private val term = "딸기"
    private val filler = "가"

    private fun page(id: String, anchor: String, title: String, body: String) = IndexedPage(
        id = id, title = title, space = "SP", path = "p/$id.md",
        body = body, anchors = listOf(anchor), aclTokens = listOf("@public"),
    )

    @Test
    fun `앵커가 제목보다, 제목이 본문보다 높게 매겨진다`() {
        val dir = Files.createTempDirectory("field-boost")
        LuceneIndex(dir).use { idx ->
            idx.rebuild(
                listOf(
                    page("anchor", anchor = term, title = filler, body = filler),
                    page("title", anchor = filler, title = term, body = filler),
                    page("body", anchor = filler, title = filler, body = term),
                )
            )
            val score = idx.analyzeAndSearch(term, listOf("@public"), 10).hits
                .associate { it.id to it.score }

            val a = score["anchor"] ?: error("앵커 문서가 안 잡혔다: $score")
            val t = score["title"] ?: error("제목 문서가 안 잡혔다: $score")
            val b = score["body"] ?: error("본문 문서가 안 잡혔다: $score")

            assertTrue(a > t, "앵커($a)가 제목($t)보다 높아야 한다 — FieldBoost 확인")
            assertTrue(t > b, "제목($t)이 본문($b)보다 높아야 한다 — FieldBoost 확인")

            // 순서만으로는 1:1:1 을 못 잡는다(점수가 같아도 docId 순으로 통과할 수 있다).
            // 배수까지 본다 — 필드 통계가 같으므로 점수비가 곧 가중비다.
            assertTrue(
                a / b > 2.0,
                "앵커/본문 배수가 ${"%.2f".format(a / b)} 다 — 가중이 사실상 꺼졌다",
            )
        }
    }
}
