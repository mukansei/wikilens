package dev.wikilens

import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.test.web.client.TestRestTemplate
import org.springframework.boot.test.web.server.LocalServerPort
import org.springframework.http.HttpEntity
import org.springframework.http.HttpHeaders
import org.springframework.http.HttpMethod
import org.springframework.http.HttpStatus
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * **실제 컨텍스트를 띄우는 유일한 테스트다.**
 *
 * 나머지 테스트는 전부 객체를 손으로 조립한다 — 빠르고 단위 테스트로서 옳지만, 그래서
 * **배선 자체는 아무도 안 본다.** 이것 없이 조용히 깨질 수 있던 것들:
 *
 *   - `@ConfigurationProperties` 바인딩. `application.yml` 의 키에 대응하는 필드가
 *     없거나 타입이 어긋나도 컴파일은 통과한다.
 *   - `AdminGuardConfig` 의 인터셉터 등록. `AdminInterceptorTest` 는
 *     `standaloneSetup` 으로 가드를 **손으로** 끼우므로, `@Configuration` 이 사라져도
 *     초록이다 — 그러면 관리 API 가 열린 채로 배포된다.
 *   - 빈 생성 순서. `stateDirLock` 이 `trajectorySink` 보다 먼저여야 하고
 *     (`WikiLensApplication`), 기동 적재는 포트가 열리기 **전에** 끝나야 한다.
 *   - `/api/stats` 의 키. MCP 프록시 `--status` 가 문자열로 읽는데
 *     `contract/wire-format.json` 은 search·read·grep·tree 만 덮는다.
 *
 * **`RANDOM_PORT` 를 쓰는 이유는 인터셉터 등록이 아니다** — 그건 `MOCK` +
 * `@AutoConfigureMockMvc` 도 잡는다(실측: `@Configuration` 을 지우면 둘 다 빨개진다).
 * 갈리는 것은 **전선**이다. 톰캣이 헤더를 ISO-8859-1 로 디코드하는 것을 MockMvc 는
 * 흉내내지 않아서, 관리 토큰이 실제로 오가는 모양은 여기서만 볼 수 있다 —
 * 그 덕에 비ASCII 토큰이 아예 전송되지 않는다는 것을 찾았다(`AdminGuard` 참고).
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class BootstrapTest {

    companion object {
        private const val TOKEN = "test-admin-token"

        /**
         * 상태·색인 디렉터리를 임시로 돌린다. **기본값이 상대경로라 안 주면 저장소 안에
         * `.wikilens/` 를 만들고**, 더 나쁘게는 개발자가 띄워 둔 서버와 같은 자리를 잡아
         * `StateDirLock` 이 기동을 막는다(그건 락이 옳게 동작하는 것이다).
         *
         * 볼트는 **일부러 비워 둔다** — 볼트를 못 읽어도 기동은 계속돼야 한다는 것이
         * `vaultBootstrap` 의 계약이고, 그것도 여기서 함께 잠긴다.
         */
        @JvmStatic
        @DynamicPropertySource
        fun props(reg: DynamicPropertyRegistry) {
            val base = Files.createTempDirectory("wikilens-boot-test")
            reg.add("wikilens.vault-root") { base.resolve("vault").toString() }
            reg.add("wikilens.state-dir") { base.resolve("state").toString() }
            reg.add("wikilens.index-dir") { base.resolve("index").toString() }
            reg.add("wikilens.admin-token") { TOKEN }
        }
    }

    @Autowired private lateinit var rest: TestRestTemplate
    @LocalServerPort private var port: Int = 0

    private fun url(path: String) = "http://localhost:$port$path"

    /**
     * 컨텍스트가 뜨고 포트가 열린다. **볼트가 비어 있어도** 그렇다 — 설정이 틀렸다고
     * 서버가 아예 안 뜨면 `--status` 로 진단할 길까지 사라진다.
     */
    @Test
    fun `볼트가 비어도 기동하고 health 가 답한다`() {
        val r = rest.getForEntity(url("/api/health"), Map::class.java)
        assertEquals(HttpStatus.OK, r.statusCode)
        assertEquals(true, r.body?.get("ok"))
    }

    /**
     * **`--status` 가 읽는 키가 실제로 나온다.**
     *
     * `wikilens_mcp.py` 가 이 이름들을 문자열로 꺼내 쓰는데 그 연결은 컴파일도 테스트도
     * 안 잡는다 — 하나를 지우거나 오타를 내면 진단이 조용히 "없음" 으로 떨어진다.
     * 진단이 진단을 못 하는 상태가 이 저장소가 반복해서 막아온 실패 모양이다.
     */
    @Test
    fun `stats 가 --status 의 키를 전부 낸다`() {
        val body = rest.getForEntity(url("/api/stats"), Map::class.java).body!!
        val want = listOf(
            "indexedDocs", "aclPages", "aclUsers", "aclEnforced",
            "aclTokenOverlap", "aclUserTokens", "aclPageTokens",
            "analyzer", "analyzerConfigured",
            "grepEngine", "grepEngineUsable",
            "trajectoryLog", "byKind", "droppedSessions", "permissionScopes",
        )
        val missing = want.filterNot { body.containsKey(it) }
        assertTrue(missing.isEmpty(), "stats 에서 사라진 키: $missing")

        @Suppress("UNCHECKED_CAST")
        val log = body["trajectoryLog"] as Map<String, Any?>
        val logWant = listOf("bytes", "replayed", "replaySkipped", "replayMillis", "writeFailures")
        assertTrue(logWant.all { log.containsKey(it) }, "trajectoryLog 키: ${log.keys}")
    }

    /**
     * **인터셉터가 실제 컨텍스트에 등록돼 있다.**
     *
     * `AdminInterceptorTest` 는 `standaloneSetup` 으로 가드를 손으로 끼우므로
     * `AdminGuardConfig` 의 `@Configuration` 이 사라져도 통과한다. 그 구멍이 여기서 막힌다 —
     * 토큰 없이 부르면 404 여야 하고, 그 404 는 우리가 던진 것이지 "그런 경로 없음" 이
     * 아니어야 한다(같은 요청이 토큰과 함께면 통과하는 것이 그 증거다).
     */
    @Test
    fun `관리 API 는 토큰 없이 404, 토큰과 함께면 통과한다`() {
        val without = rest.postForEntity(url("/api/admin/sweep"), null, Map::class.java)
        assertEquals(HttpStatus.NOT_FOUND, without.statusCode, "관리 API 가 열려 있다")

        val headers = HttpHeaders().apply { set("X-WikiLens-Admin", TOKEN) }
        val with = rest.exchange(
            url("/api/admin/sweep"), HttpMethod.POST, HttpEntity<Any>(headers), Map::class.java,
        )
        assertEquals(HttpStatus.OK, with.statusCode, "토큰을 줬는데도 막혔다")
        assertTrue(with.body!!.containsKey("finalized"))
    }

    /**
     * **`application.yml` 의 모든 키가 바인딩을 깨지 않는다.**
     *
     * `sweep-interval-millis` 는 대응하는 Kotlin 필드가 없다 — `@Scheduled` 가 애노테이션
     * 인자라 빈을 못 읽어 프로퍼티 문자열로 직접 받기 때문이다(`LearnProps` 참고).
     * 필드를 지울 때 이 조합이 안전한지 확인할 자리가 없어서 손으로 띄워 봐야 했다.
     * 컨텍스트가 떴다는 사실 자체가 그 검증이고, 이 테스트가 그것을 고정한다.
     */
    @Test
    fun `필드 없는 설정 키가 있어도 컨텍스트가 뜬다`() {
        val yml = Files.readString(java.nio.file.Path.of("src/main/resources/application.yml"))
        assertTrue("sweep-interval-millis" in yml, "전제가 사라졌다 — 이 테스트를 지울 것")
        assertEquals(HttpStatus.OK, rest.getForEntity(url("/api/health"), Map::class.java).statusCode)
    }
}
