package dev.wikilens.api

import com.fasterxml.jackson.annotation.JsonInclude
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.registerKotlinModule
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * **DTO 필드 이름이 곧 JSON 키다.** 바꾸면 MCP 프록시가 함께 깨진다
 * (`plugin/client/mcp/wikilens_mcp.py`). 둘은 문자열로만 이어져 있어 한쪽만 바꾸면
 * **컴파일도 테스트도 통과하는데 런타임에 빈 값**이 된다.
 *
 * 예전에는 그 사실이 선언 없는 `api/Dto.kt` 주석에만 있었다(그 파일은 지웠다).
 * 실측으로 확인했다 —
 * `ReadResponse.markdown` 을 `body` 로 바꿨더니 Kotlin 테스트가 이름 참조로 걸리기는
 * 했지만 **그건 Kotlin API 를 잡은 것이지 와이어 계약이 아니다.** 이름을 바꾸는 사람은
 * 테스트도 같이 고칠 텐데, 그때 아무도 "프록시가 깨진다" 고 말해주지 않는다. 프록시
 * 테스트는 가짜 서버라 키를 손으로 적어두기 때문이다.
 *
 * 그래서 정본을 `contract/wire-format.json` 에 두고 **양쪽이 그것에 맞는지** 본다 —
 * 여기(직렬화 결과)와 `plugin/tests/test_mcp_proxy.py`(가짜 서버가 내는 키).
 *
 * **직렬화기를 앱과 같게 맞춘다** — `application.yml` 의
 * `default-property-inclusion: non_null` 이라 null 필드는 키가 아예 안 나온다.
 * 그래서 `always` 와 `optional` 을 나눠서 검사한다.
 */
class WireFormatTest {

    private val mapper = ObjectMapper().registerKotlinModule()
        .setSerializationInclusion(JsonInclude.Include.NON_NULL)

    private val spec: JsonNode = ObjectMapper()
        .readTree(Files.readString(Path.of("..", "contract", "wire-format.json")))

    private fun keys(v: Any): Set<String> =
        mapper.readTree(mapper.writeValueAsString(v)).fieldNames().asSequence().toSet()

    private fun expected(node: JsonNode, withOptional: Boolean): Set<String> {
        val out = node["always"].map { it.asText() }.toMutableSet()
        if (withOptional) out += node["optional"].map { it.asText() }
        return out
    }

    /** 모든 필드를 채운 응답. 키가 `always + optional` 전부여야 한다. */
    @Test
    fun `전부 채우면 정본의 always + optional 과 같다`() {
        val hit = SearchHit("1", "제목", "SP", 0.5, "both", 0.7)
        assertEquals(expected(spec["search"]["hit"], true), keys(hit), "SearchHit")
        assertEquals(expected(spec["search"], true),
            keys(SearchResponse("q", listOf("t"), 1, 1, listOf(hit), error = "e")), "SearchResponse")

        assertEquals(expected(spec["read"], true),
            keys(ReadResponse("1", "제목", "SP", "# 본문")), "ReadResponse")

        val match = GrepMatch("1", "제목", 42, "줄")
        assertEquals(expected(spec["grep"]["match"], true), keys(match), "GrepMatch")
        assertEquals(expected(spec["grep"], true),
            keys(GrepResponse("p", 1, listOf(match), false, "e", "jvm")), "GrepResponse")

        assertEquals(expected(spec["tree"], true), keys(TreeResponse("md", true)), "TreeResponse")
    }

    /**
     * null 을 비워둔 응답. `optional` 은 사라지고 `always` 만 남아야 한다.
     *
     * 이쪽이 중요하다 — 프록시가 `always` 키를 못 받으면 그 자리가 조용히 빈다.
     * `optional` 을 `always` 로 잘못 옮기면 여기서 걸린다.
     */
    @Test
    fun `null 을 비우면 정본의 always 만 남는다`() {
        assertEquals(expected(spec["search"]["hit"], false),
            keys(SearchHit("1", "제목", "SP", 0.5, "lexical")), "SearchHit")
        assertEquals(expected(spec["search"], false),
            keys(SearchResponse("q", emptyList(), 0, 0, emptyList())), "SearchResponse")
        assertEquals(expected(spec["grep"], false),
            keys(GrepResponse("p", 0, emptyList(), false)), "GrepResponse")
        assertEquals(expected(spec["tree"], false), keys(TreeResponse("md")), "TreeResponse")
    }
}