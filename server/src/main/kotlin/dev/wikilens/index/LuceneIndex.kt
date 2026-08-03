package dev.wikilens.index

import dev.wikilens.acl.AclRegistry
import org.apache.lucene.analysis.Analyzer
import org.apache.lucene.analysis.ko.KoreanAnalyzer
import org.apache.lucene.analysis.miscellaneous.PerFieldAnalyzerWrapper
import org.apache.lucene.document.Document
import org.apache.lucene.document.Field
import org.apache.lucene.document.StringField
import org.apache.lucene.document.TextField
import org.apache.lucene.index.DirectoryReader
import org.apache.lucene.index.IndexWriter
import org.apache.lucene.index.IndexWriterConfig
import org.apache.lucene.index.Term
import org.apache.lucene.search.BooleanClause
import org.apache.lucene.search.BooleanQuery
import org.apache.lucene.search.BoostQuery
import org.apache.lucene.search.IndexSearcher
import org.apache.lucene.search.Query
import org.apache.lucene.search.TermInSetQuery
import org.apache.lucene.store.MMapDirectory
import org.apache.lucene.util.BytesRef
import org.slf4j.LoggerFactory
import java.nio.file.Path
import java.util.concurrent.atomic.AtomicReference

/**
 * Lucene 색인.
 *
 * 서버가 색인을 갖는 이유는 클라이언트 분산 색인의 대가가 컸기 때문이다 —
 * Confluence API 부하가 사용자 수에 비례하고(200명이면 200배), dense 임베딩이
 * 중복 계산되며, 무엇보다 **사용자마다 랭킹 척도가 달라져** 학습 레이어에
 * 이질적인 관측이 섞인다.
 *
 * 대신 ACL을 질의 시점에 시행해야 한다. 이것은 이색적인 요구가 아니라
 * 사내 검색 시스템의 표준이다.
 *
 * 필드 가중은 앵커가 가장 높다. 앵커 텍스트는 **다른 문서들이 이 문서를 부르는 이름**
 * 이고, 그것이 사용자 어휘와 문서 제목 사이의 격차를 메우는 유일한 신호다.
 */
object Fields {
    const val ID = "id"
    const val TITLE = "title"
    const val ANCHOR = "anchor"
    const val BODY = "body"
    const val SPACE = "space"
    const val ACL = "acl"          // 이 문서를 볼 수 있는 그룹/사용자 토큰
}

/** 필드별 가중치. 앵커 > 제목 > 본문. */
object FieldBoost {
    const val ANCHOR = 4.0f
    const val TITLE = 3.0f
    const val BODY = 1.0f
}

data class IndexedPage(
    val id: String,
    val title: String,
    val space: String,
    val path: String,
    val body: String,
    val anchors: List<String>,
    val aclTokens: List<String>,
)

data class Scored(val id: String, val title: String, val space: String, val score: Float)

/** 색인된 문서의 메타데이터. 본문은 담지 않는다 — 콘텐츠는 미러에서 읽는다. */
data class PageMeta(val id: String, val title: String, val space: String)

class LuceneIndex(private val dir: Path) : AutoCloseable {
    private val log = LoggerFactory.getLogger(javaClass)

    /**
     * Nori. 한국어는 교착어라 조사가 붙는다 — '로그인을/로그인은/로그인이'.
     * 형태소 분석 없이는 BM25가 무너진다. 이것이 JVM을 고른 유일하고 결정적인 이유다.
     *
     * ID/SPACE/ACL 은 분석하지 않는다 (StringField 이므로 실제로는 무시되지만
     * 질의 파싱 경로에서 일관성을 위해 명시한다).
     */
    private val analyzer: Analyzer = PerFieldAnalyzerWrapper(
        KoreanAnalyzer(),
        mapOf(
            Fields.ID to org.apache.lucene.analysis.core.KeywordAnalyzer(),
            Fields.SPACE to org.apache.lucene.analysis.core.KeywordAnalyzer(),
            Fields.ACL to org.apache.lucene.analysis.core.KeywordAnalyzer(),
        ),
    )

    private val searcherRef = AtomicReference<IndexSearcher?>(null)
    private val readerRef = AtomicReference<DirectoryReader?>(null)

    val docCount: Int get() = readerRef.get()?.numDocs() ?: 0

    /**
     * 전체 재구축 후 참조를 원자적으로 교체한다.
     *
     * 제자리 갱신을 하지 않는 이유: 10k 문서 재구축이 수 초라 증분 갱신의 이득이 없고,
     * 증분은 드리프트가 조용히 쌓이는 자리다. 재구축 중 질의는 이전 searcher 를 계속 쓴다.
     */
    fun rebuild(pages: Collection<IndexedPage>) {
        val started = System.nanoTime()
        MMapDirectory(dir).use { d ->
            val cfg = IndexWriterConfig(analyzer).apply {
                openMode = IndexWriterConfig.OpenMode.CREATE
            }
            IndexWriter(d, cfg).use { w ->
                for (p in pages) w.addDocument(toDocument(p))
                w.commit()
            }
        }
        metaRef.set(pages.associate { it.id to PageMeta(it.id, it.title, it.space) })
        swapSearcher()
        log.info("색인 재구축 {}건 · {}ms", pages.size, (System.nanoTime() - started) / 1_000_000)
    }

    private fun toDocument(p: IndexedPage) = Document().apply {
        add(StringField(Fields.ID, p.id, Field.Store.YES))
        add(StringField(Fields.SPACE, p.space, Field.Store.YES))
        add(TextField(Fields.TITLE, p.title, Field.Store.YES))
        // 앵커를 하나의 필드로 합친다. 개별 앵커의 출처는 학습 레이어가 아니라
        // 로컬판 ALIASES.md 에서 다루므로 여기서는 어휘만 필요하다.
        add(TextField(Fields.ANCHOR, p.anchors.joinToString(" "), Field.Store.NO))
        add(TextField(Fields.BODY, p.body, Field.Store.NO))
        for (t in p.aclTokens) add(StringField(Fields.ACL, t, Field.Store.NO))
    }

    private fun swapSearcher() {
        val newReader = DirectoryReader.open(MMapDirectory(dir))
        val old = readerRef.getAndSet(newReader)
        searcherRef.set(IndexSearcher(newReader))
        old?.close()
    }

    fun openIfExists() {
        runCatching { swapSearcher() }
            .onFailure { log.info("기존 색인 없음 — reindex 필요") }
    }

    /**
     * 검색. [aclTokens] 는 요청자의 권한 토큰(그룹, 사용자 ID, 공개 마커).
     *
     * ACL 필터는 **선택이 아니라 필수 절**이다. 빈 목록이면 결과가 비어야 한다 —
     * 실수로 전체가 노출되는 것보다 아무것도 안 나오는 편이 낫다.
     */
    fun search(queryText: String, aclTokens: Collection<String>, limit: Int): List<Scored> {
        val searcher = searcherRef.get() ?: return emptyList()
        if (aclTokens.isEmpty()) return emptyList()

        val text = buildTextQuery(queryText) ?: return emptyList()
        val acl = TermInSetQuery(Fields.ACL, aclTokens.map { BytesRef(it) })

        val q = BooleanQuery.Builder()
            .add(text, BooleanClause.Occur.MUST)
            .add(acl, BooleanClause.Occur.FILTER)   // FILTER: 점수에 영향 없음
            .build()

        val top = searcher.search(q, limit)
        val stored = searcher.storedFields()
        return top.scoreDocs.map { sd ->
            val d = stored.document(sd.doc)
            Scored(d.get(Fields.ID), d.get(Fields.TITLE) ?: "", d.get(Fields.SPACE) ?: "", sd.score)
        }
    }

    /**
     * 메타데이터 캐시. 색인 재구축 시 함께 교체된다.
     * ContentService 가 제목을 얻으려고 Lucene 을 매번 조회하지 않게 한다.
     */
    private val metaRef = AtomicReference<Map<String, PageMeta>>(emptyMap())

    fun metaOf(pageId: String): PageMeta? = metaRef.get()[pageId]
    fun allMeta(): Collection<PageMeta> = metaRef.get().values

    /** 세 필드에 대한 가중 OR. 파싱 실패 시 null 을 반환해 호출부가 조용히 빈 결과를 내게 한다. */
    private fun buildTextQuery(text: String): Query? {
        val parser = org.apache.lucene.queryparser.classic.MultiFieldQueryParser(
            arrayOf(Fields.ANCHOR, Fields.TITLE, Fields.BODY),
            analyzer,
            mapOf(
                Fields.ANCHOR to FieldBoost.ANCHOR,
                Fields.TITLE to FieldBoost.TITLE,
                Fields.BODY to FieldBoost.BODY,
            ),
        )
        parser.defaultOperator = org.apache.lucene.queryparser.classic.QueryParser.Operator.OR
        return runCatching { parser.parse(escape(text)) }.getOrNull()
    }

    /** 사용자 질의를 그대로 파서에 넣으면 특수문자로 예외가 난다. */
    private fun escape(s: String): String =
        org.apache.lucene.queryparser.classic.QueryParserBase.escape(s)

    /**
     * 서버가 질의를 토큰화한다. 클라이언트는 원문만 보낸다.
     *
     * 이렇게 하면 토크나이저 정본이 하나가 된다. 양쪽이 각자 토큰화하면
     * 규칙이 달라졌을 때 에러 없이 조용히 0건이 되는데, 실제로 겪은 버그다.
     */
    fun analyze(text: String): List<String> {
        val out = ArrayList<String>()
        analyzer.tokenStream(Fields.BODY, text).use { ts ->
            val attr = ts.addAttribute(
                org.apache.lucene.analysis.tokenattributes.CharTermAttribute::class.java
            )
            ts.reset()
            while (ts.incrementToken()) out.add(attr.toString())
            ts.end()
        }
        return out
    }

    override fun close() {
        readerRef.getAndSet(null)?.close()
    }
}
