package dev.wikilens.index

import dev.wikilens.acl.AclRegistry
import kotlin.io.path.createTempDirectory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlin.test.assertFailsWith

/**
 * 색인 시점에 분석기를 고르는 것.
 *
 * **이 선택이 색인과 질의에서 갈리면 예외가 아니라 조용히 0건이 된다.** 이 프로젝트가
 * 겪은 실패 1번이 정확히 그 모양이었다(클라이언트와 서버가 각자 토큰화). 그래서
 * 선택을 색인 커밋 데이터에 기록하고 기동 시 대조한다.
 */
class AnalyzerChoiceTest {

    private fun page(id: String, body: String) =
        IndexedPage(id, "제목 $id", "S", "p/$id.md", body, emptyList(), listOf("@public"))

    @Test
    fun `영어 분석기는 굴절을 넘어 찾고 한국어 분석기는 못 찾는다`() {
        // 문서는 복수형, 질의는 단수형. **한 낱말만 던진다** — 파서 기본이 OR 이라
        // 여러 낱말을 주면 다른 낱말이 맞아서 굴절 실패가 가려진다.
        val body = "we run production servers behind the gateway"
        val acl = listOf("@public")

        LuceneIndex(createTempDirectory("ko"), AnalyzerKind.KOREAN).use { ko ->
            ko.rebuild(listOf(page("1", body)))
            assertEquals(1, ko.search("servers", acl, 5).size, "그대로 치면 찾는다")
            assertTrue(
                ko.search("server", acl, 5).isEmpty(),
                "Nori 는 servers→server 를 못 줄인다 — 영어 코퍼스에서의 한계다",
            )
        }

        LuceneIndex(createTempDirectory("en"), AnalyzerKind.ENGLISH).use { en ->
            en.rebuild(listOf(page("1", body)))
            assertEquals(1, en.search("server", acl, 5).size, "어간 추출로 단수형도 찾는다")
        }
    }

    @Test
    fun `한국어 분석기는 조사를 뗀다 — 영어 분석기로는 그것을 잃는다`() {
        val body = "배포 파이프라인을 구성했습니다"
        val acl = listOf("@public")

        LuceneIndex(createTempDirectory("ko2"), AnalyzerKind.KOREAN).use { ko ->
            ko.rebuild(listOf(page("1", body)))
            assertEquals(1, ko.search("파이프라인", acl, 5).size, "조사를 떼고 찾아야 한다")
        }
        LuceneIndex(createTempDirectory("en2"), AnalyzerKind.ENGLISH).use { en ->
            en.rebuild(listOf(page("1", body)))
            assertTrue(
                en.search("파이프라인", acl, 5).isEmpty(),
                "대칭인 실패다 — 영어 분석기는 '파이프라인을' 을 통째로 들고 있다",
            )
        }
    }

    @Test
    fun `색인이 어떤 분석기로 지어졌는지 기록한다`() {
        val dir = createTempDirectory("mark")
        LuceneIndex(dir, AnalyzerKind.ENGLISH).use { it.rebuild(listOf(page("1", "hello"))) }

        // 다른 분석기로 같은 디렉터리를 열어도 **디스크에 적힌 것**이 나와야 한다.
        LuceneIndex(dir, AnalyzerKind.KOREAN).use { reopened ->
            reopened.openIfExists()
            assertEquals("english", reopened.builtWith(), "색인을 지은 분석기가 기록돼야 한다")
            assertEquals(AnalyzerKind.ENGLISH, reopened.activeKind, "질의도 그것을 따라야 한다")
            assertNotEquals(reopened.buildKind, reopened.activeKind, "설정과는 다른 상태다")
        }
    }

    @Test
    fun `설정이 색인과 달라도 검색은 정상 동작한다`() {
        // 예전에는 이 상황이 **조용한 0건**이었다. 색인에는 `production servers` 가
        // English 어간으로 들어가 있는데 질의를 설정대로 Nori 로 토큰화했기 때문이다.
        // 기록을 그대로 쓰면 그 불일치가 애초에 성립하지 않는다.
        val dir = createTempDirectory("mismatch")
        val acl = listOf("@public")
        LuceneIndex(dir, AnalyzerKind.ENGLISH).use {
            it.rebuild(listOf(page("1", "we run production servers")))
        }
        LuceneIndex(dir, AnalyzerKind.KOREAN).use { reopened ->
            reopened.openIfExists()
            assertEquals(AnalyzerKind.KOREAN, reopened.buildKind, "설정은 korean 이지만")
            assertEquals(AnalyzerKind.ENGLISH, reopened.activeKind, "질의는 색인을 따른다")
            assertEquals(
                1, reopened.search("server", acl, 5).size,
                "English 어간 추출이 그대로 살아 있어야 한다 — 0건이면 옛 결함이 돌아온 것",
            )
        }
    }

    @Test
    fun `재색인이 설정을 적용하고 그때부터 질의도 따라간다`() {
        val dir = createTempDirectory("switch")
        val acl = listOf("@public")
        val doc = listOf(page("1", "we run production servers"))

        LuceneIndex(dir, AnalyzerKind.KOREAN).use { it.rebuild(doc) }

        LuceneIndex(dir, AnalyzerKind.ENGLISH).use { idx ->
            idx.openIfExists()
            // 재색인 전: 디스크가 korean 이므로 굴절을 못 넘는다
            assertEquals(AnalyzerKind.KOREAN, idx.activeKind)
            assertTrue(idx.search("server", acl, 5).isEmpty(), "아직 옛 색인이다")

            idx.rebuild(doc)   // 설정(english)으로 다시 짓는다

            assertEquals(AnalyzerKind.ENGLISH, idx.activeKind, "재색인이 전환 지점이다")
            assertEquals("english", idx.builtWith())
            assertEquals(1, idx.search("server", acl, 5).size, "이제 어간 추출이 된다")
        }
    }

    @Test
    fun `항과 검색 결과가 같은 분석기에서 나온다`() {
        // `analyze()` 와 `search()` 를 따로 부르면 그 사이에 재색인이 끝날 수 있다.
        // 결과는 새 색인에서 맞게 나오지만 항은 옛 분석기 것이고, **그 항이 학습
        // 포스팅의 키**라 같은 질의가 그 순간에만 다른 키로 기록된다.
        val dir = createTempDirectory("atomic")
        val acl = listOf("@public")
        val doc = listOf(page("1", "we run production servers"))

        LuceneIndex(dir, AnalyzerKind.KOREAN).use { it.rebuild(doc) }

        LuceneIndex(dir, AnalyzerKind.ENGLISH).use { idx ->
            idx.openIfExists()
            // 디스크는 korean 이므로 항도 korean 이어야 한다
            val before = idx.analyzeAndSearch("production servers", acl, 5)
            assertEquals(listOf("production", "servers"), before.terms, "Nori 는 어간을 안 줄인다")
            assertEquals(1, before.hits.size)

            idx.rebuild(doc)   // english 로 교체

            val after = idx.analyzeAndSearch("production servers", acl, 5)
            assertEquals(listOf("product", "server"), after.terms, "이제 English 어간이다")
            assertEquals(1, after.hits.size, "항과 색인이 함께 바뀌었으므로 여전히 찾는다")
        }
    }

    @Test
    fun `기록 없는 옛 색인은 설정이 아니라 korean 으로 읽는다`() {
        // 이 기능 이전 버전은 `KoreanAnalyzer` 를 하드코딩했으므로, 기록 없는 색인은
        // 전부 korean 이다. 설정을 따르면 english 로 띄운 순간 옛 색인을 english 로
        // 두드리게 된다 — 재색인 전까지 조용히 어긋난다.
        val dir = createTempDirectory("legacy")
        org.apache.lucene.store.MMapDirectory(dir).use { d ->
            org.apache.lucene.index.IndexWriter(
                d,
                org.apache.lucene.index.IndexWriterConfig(
                    org.apache.lucene.analysis.ko.KoreanAnalyzer()
                ),
            ).use { w ->
                w.addDocument(org.apache.lucene.document.Document().apply {
                    add(org.apache.lucene.document.StringField(Fields.ID, "1", org.apache.lucene.document.Field.Store.YES))
                    add(org.apache.lucene.document.TextField(Fields.BODY, "배포 파이프라인을", org.apache.lucene.document.Field.Store.NO))
                })
                w.commit()   // setLiveCommitData 없음 = 기록 없는 옛 색인
            }
        }

        LuceneIndex(dir, AnalyzerKind.ENGLISH).use { idx ->
            idx.openIfExists()
            assertNull(idx.builtWith(), "선행 조건: 기록이 없다")
            assertEquals(
                AnalyzerKind.KOREAN, idx.activeKind,
                "설정(english)이 아니라 옛 색인이 지어진 korean 으로 읽어야 한다",
            )
        }
    }

    @Test
    fun `분석기 이름 오타는 기동 시 죽는다`() {
        // 조용히 기본값으로 떨어지면 그게 "검색이 0건" 의 원인이 되고,
        // 그때는 설정이 아니라 색인을 의심하게 된다.
        val e = assertFailsWith<IllegalArgumentException> { AnalyzerKind.of("korea") }
        assertTrue("korean" in e.message!!, "가능한 값을 알려줘야 한다: ${e.message}")
        assertEquals(AnalyzerKind.KOREAN, AnalyzerKind.of(" Korean "), "공백·대소문자는 관대하게")
    }

    @Test
    fun `색인이 없어도 설정된 분석기를 쓴다`() {
        // 첫 배포에서 볼트 경로를 틀리게 준 상황. 색인이 없으니 검색은 어차피 0건이지만,
        // `analyze()` 가 내는 항은 **학습 포스팅의 키**라 여기서 korean 으로 굳으면
        // english 설정이 조용히 무시된 채 궤적이 쌓인다.
        LuceneIndex(createTempDirectory("noindex"), AnalyzerKind.ENGLISH).use { idx ->
            idx.openIfExists()   // 색인이 없어 실패한다
            assertEquals(AnalyzerKind.ENGLISH, idx.activeKind, "설정을 따라야 한다")
            assertEquals(listOf("product", "server"), idx.analyze("production servers"),
                "English 어간이 나와야 한다 — korean 이면 [production, servers] 다")
        }
    }

    @Test
    fun `분석기 기록 이전 색인은 null 이고 경고하지 않는다`() {
        LuceneIndex(createTempDirectory("empty"), AnalyzerKind.KOREAN).use {
            it.openIfExists()
            assertNull(it.builtWith(), "색인이 없으면 대조할 것도 없다")
            assertEquals(it.buildKind, it.activeKind, "기록이 없으면 설정을 쓴다")
        }
    }
}
