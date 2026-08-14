package io.wikilens.index

import org.junit.jupiter.api.Test
import java.nio.file.Files
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * **질의는 문법이 아니라 글자다.**
 *
 * `QueryParserBase.escape` 가 `+ - ( ) : ^ [ ] " { } ~ * ? | & /` 를 막지만
 * `AND`·`OR`·`NOT` 은 글자라 못 막는다 — 대문자로 친 순간 파서가 연산자로 읽고,
 * 그 질의는 **에러 없이 통째로 사라졌다.** 실측(고치기 전, korean 분석기):
 *
 *     NOT NULL      → 0건
 *     not null      → 1건   ← 같은 색인 · 같은 문서 · 대소문자만 다름
 *     AND 조건      → 0건
 *
 * 스킬이 "사용자의 표현을 **그대로** 넣으세요" 라고 지시하므로 `NOT NULL` 같은 SQL 이나
 * 영문 약어는 실제로 이 모양 그대로 들어온다. 좁히기 층에서 빠진 문서는 모델이
 * 아무리 좋아져도 복구되지 않는다.
 *
 * 고침은 소문자화다(`LuceneQuery.textQuery`). **항은 안 바뀐다** — 세 분석기가 전부
 * `LowerCaseFilter` 를 거치고 한글은 대소문자가 없다. 그 중립성을 아래
 * `대소문자가 답을 안 바꾼다` 가 직접 검사한다.
 */
class QueryIsLiteralTest {

    private fun page(id: String, body: String) = IndexedPage(
        id = id, title = "문서 $id", space = "SP", path = "p/$id.md",
        body = body, anchors = emptyList(), aclTokens = listOf("@public"),
    )

    private fun index(dir: java.nio.file.Path) = LuceneIndex(dir).also {
        it.rebuild(listOf(
            page("1", "컬럼에 NOT NULL 제약을 건다"),
            page("2", "AND 조건과 OR 조건을 섞어 쓴다"),
            page("3", "아무 상관 없는 고양이 이야기"),
        ))
    }

    private fun ids(idx: LuceneIndex, q: String) =
        idx.analyzeAndSearch(q, listOf("@public"), 10).hits.map { it.id }

    @Test
    fun `낱말꼴 연산자를 글자로 읽는다`() {
        index(Files.createTempDirectory("literal")).use { idx ->
            assertTrue("1" in ids(idx, "NOT NULL"), "NOT NULL 이 연산자로 읽혔다")
            assertTrue("2" in ids(idx, "AND 조건"), "AND 가 연산자로 읽혔다")
            assertTrue("2" in ids(idx, "OR 조건"), "OR 가 연산자로 읽혔다")
        }
    }

    @Test
    fun `대소문자가 답을 안 바꾼다`() {
        index(Files.createTempDirectory("literal-case")).use { idx ->
            // 소문자화가 **항을 안 바꾼다**는 것이 이 고침의 전제다. 대문자 질의와
            // 소문자 질의가 같은 답을 내야 그 전제가 성립한다.
            for (q in listOf("NOT NULL" to "not null",
                             "제약" to "제약",
                             "고양이 이야기" to "고양이 이야기")) {
                assertEquals(ids(idx, q.first), ids(idx, q.second), "'${q.first}' 가 갈렸다")
            }
        }
    }
}
