package dev.wikilens.api

/*
 * HTTP 와이어 포맷 — 이 패키지의 `*Request` · `*Response` 타입들.
 *
 * **필드 이름이 곧 JSON 키**다. 바꾸면 MCP 프록시가 함께 깨진다
 * (`plugin/client/mcp/wikilens_mcp.py`). 문자열로만 이어져 있어 한쪽만 바꾸면
 * **컴파일도 테스트도 통과하는데 런타임에 그 자리가 조용히 빈다.**
 *
 * **이제 주석이 아니라 가드가 지킨다.** 정본은 `contract/wire-format.json` 이고,
 * `WireFormatTest` 가 직렬화 결과로 · `plugin/tests/test_mcp_proxy.py` 가 가짜 서버가
 * 내는 키로 같은 파일을 검사한다. 예전에는 이 주석이 전부였고, 실제로 재보니
 * `ReadResponse.markdown` 을 바꿔도 **Kotlin 테스트만 고치면 아무도 안 막았다**.
 *
 * `service/` 가 이 타입을 **그대로 반환한다** — 서비스 전용 타입을 따로 두고 매핑하는
 * 층은 넣지 않았다. 타입이 넷뿐이라 매핑이 얻는 것 없이 코드만 두 배가 되고, 이
 * 서비스들은 HTTP 말고 다른 소비자가 없다. 소비자가 생기면 그때 나눌 자리다.
 *
 * 파일 하나에 선언 하나가 이 저장소의 규칙이라, 이 파일에는 선언이 없다 —
 * 패키지 설명만 둔다(Java 의 `package-info.java` 자리).
 */
