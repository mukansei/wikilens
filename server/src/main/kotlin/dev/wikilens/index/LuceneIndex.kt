package dev.wikilens.index

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
import org.apache.lucene.search.BooleanClause
import org.apache.lucene.search.BooleanQuery
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
 * **[buildKind] 는 "무엇으로 지을까"이지 "무엇으로 질의할까"가 아니다.** 질의는 항상
 * 디스크 색인이 실제로 지어진 분석기를 쓴다([Snapshot.kind]) — 색인에 기록이 있는데
 * 설정을 따라가면, 설정이 낡았을 때 **에러 없이 0건**이 된다. 기록을 그대로 쓰면
 * 그 불일치가 애초에 성립하지 않는다.
 *
 * 둘은 [rebuild] 에서 만난다: 재색인이 [buildKind] 로 다시 짓고, 그 순간부터 질의도
 * 그것을 쓴다. 그래서 분석기를 바꾸는 절차는 "설정 바꾸고 재색인" 하나뿐이고,
 * 재색인 전까지는 **옛 분석기로 정상 동작**한다.
 */
class LuceneIndex(
    private val dir: Path,
    /** 재색인할 때 쓸 분석기. 질의에 쓰이는 것은 [activeKind] 다. */
    val buildKind: AnalyzerKind = AnalyzerKind.KOREAN,
) : AutoCloseable {
    private val log = LoggerFactory.getLogger(javaClass)

    /**
     * 검색기·메타데이터·트리·**분석기**를 한 덩어리로. 따로 교체하면 그 틈에 들어온
     * 요청이 새 트리 + 옛 메타처럼 **뒤섞인 상태**를 본다 — 하나로 묶으면 교체가
     * 원자적이다. 분석기가 여기 있는 이유도 같다(색인과 한 쌍이다). [dir] 를 들고 있는
     * 것은 안 닫으면 교체마다 누수되기 때문이다.
     */
    private class Snapshot(
        val searcher: IndexSearcher?,
        val reader: DirectoryReader?,
        val dir: MMapDirectory?,
        val meta: Map<String, PageMeta>,
        val tree: TreeIndex,
        val kind: AnalyzerKind,
        val analyzer: Analyzer,
    ) {
        fun close() {
            runCatching { reader?.close() }
            runCatching { dir?.close() }
            runCatching { analyzer.close() }
        }

        companion object {
            /**
             * 색인이 아직 없을 때의 빈 스냅샷.
             *
             * **정적 상수로 두면 안 된다** — 상수는 [LuceneIndex.buildKind] 를 모르므로 늘
             * korean 이 되고, 그러면 설정이 english 인데 색인이 없을 때 **질의만 korean 으로
             * 분석된다.** 검색은 어차피 0건이라 안 보이지만 그 항이 학습 포스팅의 키다.
             */
            fun empty(kind: AnalyzerKind) = Snapshot(
                null, null, null, emptyMap(), TreeIndex.EMPTY, kind, analyzerFor(kind),
            )

            /**
             * ID/SPACE/ACL 은 분석하지 않는다 (StringField 이므로 실제로는 무시되지만
             * 질의 파싱 경로에서 일관성을 위해 명시한다).
             */
            fun analyzerFor(kind: AnalyzerKind): Analyzer = PerFieldAnalyzerWrapper(
                when (kind) {
                    AnalyzerKind.KOREAN -> KoreanAnalyzer()
                    AnalyzerKind.ENGLISH -> org.apache.lucene.analysis.en.EnglishAnalyzer()
                    AnalyzerKind.STANDARD -> org.apache.lucene.analysis.standard.StandardAnalyzer()
                },
                mapOf(
                    Fields.ID to org.apache.lucene.analysis.core.KeywordAnalyzer(),
                    Fields.SPACE to org.apache.lucene.analysis.core.KeywordAnalyzer(),
                    Fields.ACL to org.apache.lucene.analysis.core.KeywordAnalyzer(),
                ),
            )
        }
    }

    // 색인이 없어도 **설정된 분석기**로 시작한다. 이 시점의 분석기가 곧 `analyze()` 가
    // 쓰는 것이라, 여기서 korean 으로 굳으면 english 설정이 조용히 무시된다.
    private val snapshotRef = AtomicReference(Snapshot.empty(buildKind))

    val docCount: Int get() = snapshotRef.get().reader?.numDocs() ?: 0

    /**
     * 전체 재구축 후 스냅샷을 원자 교체. 제자리 갱신을 안 하는 이유는 증분이 드리프트가
     * 조용히 쌓이는 자리이고 이득이 없어서다. 재구축 중 질의는 이전 스냅샷을 쓴다.
     */
    fun rebuild(pages: Collection<IndexedPage>) {
        val started = System.nanoTime()
        val prev = snapshotRef.get().kind
        // 짓는 것은 **설정된** 분석기로. 질의는 이 뒤로 자동으로 같은 것을 쓴다.
        Snapshot.analyzerFor(buildKind).use { building ->
            MMapDirectory(dir).use { d ->
                val cfg = IndexWriterConfig(building).apply {
                    openMode = IndexWriterConfig.OpenMode.CREATE
                }
                IndexWriter(d, cfg).use { w ->
                    for (p in pages) w.addDocument(toDocument(p))
                    // **어떤 분석기로 지었는지를 색인 안에 남긴다.** 커밋 데이터라 커밋과
                    // 원자적이다 — 옆에 파일을 두면 색인과 따로 놀 수 있다.
                    w.setLiveCommitData(mapOf(ANALYZER_KEY to buildKind.key).entries)
                    w.commit()
                }
            }
        }
        swap(
            meta = pages.associate { it.id to PageMeta(it.id, it.title, it.space) },
            tree = TreeIndex.build(pages),
        )
        if (prev != buildKind) {
            log.warn(
                "분석기가 '{}' → '{}' 로 바뀌었습니다. 검색은 새 분석기로 정상 동작하지만, " +
                    "궤적 로그에는 옛 분석기로 만든 항이 남아 있어 그만큼 학습이 안 맞습니다.",
                prev.key, buildKind.key,
            )
        }
        log.info("색인 재구축 {}건 · 분석기 {} · {}ms",
            pages.size, buildKind.key, (System.nanoTime() - started) / 1_000_000)
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

    /** [meta]/[tree] 를 안 주면(기동 시 기존 색인 열기) 현재 값을 유지한다. */
    private fun swap(meta: Map<String, PageMeta>? = null, tree: TreeIndex? = null) {
        val d = MMapDirectory(dir)
        val reader = runCatching { DirectoryReader.open(d) }
            .onFailure { runCatching { d.close() } }   // 열기 실패 시 Directory 를 흘리지 않는다
            .getOrThrow()
        // **디스크가 정본이다** — 어떤 분석기로 지었는지는 커밋 데이터에 있으므로 설정이
        // 아니라 그것을 따라간다. 기록이 없으면 설정이 아니라 [LEGACY_KIND] 다: 여기 닿은
        // 것은 색인이 **있는데** 기록만 없다는 뜻이고 그건 기록 이전 버전이 지은 것뿐이다.
        // 설정을 쓰면 english 로 띄운 순간 옛 korean 색인을 english 로 두드린다.
        val recorded = reader.indexCommit.userData[ANALYZER_KEY]
        val kind = recorded?.let { runCatching { AnalyzerKind.of(it) }.getOrNull() } ?: LEGACY_KIND
        val cur = snapshotRef.get()
        val old = snapshotRef.getAndSet(
            Snapshot(
                searcher = IndexSearcher(reader),
                reader = reader,
                dir = d,
                meta = meta ?: cur.meta,
                tree = tree ?: cur.tree,
                kind = kind,
                analyzer = Snapshot.analyzerFor(kind),
            )
        )
        old.close()
    }

    fun openIfExists() {
        runCatching { swap() }
            .onFailure { log.info("기존 색인 없음 — reindex 필요") }
        reportAnalyzer()
    }

    /**
     * 설정과 디스크가 다르면 알린다. **경고일 뿐 고장이 아니다** — 질의는 디스크가 지어진
     * 분석기를 쓰므로 검색은 정상이다.
     */
    private fun reportAnalyzer() {
        val built = builtWith()
        if (built == null) {
            // 색인이 없으면 `activeKind` 가 설정과 같다. 다르면 **기록 이전 색인**이고,
            // 그건 재색인 전까지 조용히 어긋나는 자리다.
            if (activeKind != buildKind) {
                log.warn(
                    "분석기 기록이 없는 색인입니다(이 기능 이전에 지어진 것). '{}' 로 지어졌다고 " +
                        "보고 질의합니다 — 설정 '{}' 을 적용하려면 POST /api/admin/reindex 로 다시 지으세요.",
                    activeKind.key, buildKind.key,
                )
            }
            return
        }
        if (built == buildKind.key) return
        log.warn(
            "색인은 '{}' 로 지어졌고 설정은 '{}' 입니다. 검색은 '{}' 로 정상 동작합니다 — " +
                "설정을 적용하려면 POST /api/admin/reindex 로 다시 지으세요.",
            built, buildKind.key, built,
        )
    }

    /** 디스크 색인을 지은 분석기 이름. 없으면 null(색인 없음 또는 이 기록 이전 색인). */
    fun builtWith(): String? =
        snapshotRef.get().reader?.indexCommit?.userData?.get(ANALYZER_KEY)

    /** 지금 **질의에 쓰이는** 분석기. 디스크 색인이 지어진 것과 항상 같다. */
    val activeKind: AnalyzerKind get() = snapshotRef.get().kind

    /**
     * 항이 필요 없을 때의 [analyzeAndSearch]. **테스트 전용**이다 — 프로덕션 경로는
     * 항과 결과를 한 스냅샷에서 함께 받아야 한다(아래 참고).
     */
    internal fun search(queryText: String, aclTokens: Collection<String>, limit: Int): List<Scored> =
        analyzeAndSearch(queryText, aclTokens, limit).hits

    /**
     * 항 추출과 검색을 **한 스냅샷에서** 한다.
     *
     * 둘을 따로 부르면 그 사이에 재색인이 끝날 수 있다. 검색 결과는 새 색인에서
     * 맞게 나오지만 [Analyzed.terms] 는 옛 분석기 것이고, **그 항이 학습 포스팅의 키**다 —
     * 같은 질의가 재색인 순간에만 다른 키로 기록돼 카운트가 갈린다(조용히 실패하는
     * 것들 2번과 같은 형태). 창이 좁아서(재색인 몇 초) 눈에 안 띄는 종류다.
     * 스냅샷을 한 번만 읽으면 그 상태가 성립하지 않는다.
     *
     * [aclTokens] 필터는 **선택이 아니라 필수 절**이다. 빈 목록이면 결과가 비어야 한다 —
     * 실수로 전체가 노출되는 것보다 아무것도 안 나오는 편이 낫다.
     */
    fun analyzeAndSearch(queryText: String, aclTokens: Collection<String>, limit: Int): Analyzed {
        val snap = snapshotRef.get()
        val terms = tokenize(snap.analyzer, queryText)
        val searcher = snap.searcher ?: return Analyzed(terms, emptyList())
        if (aclTokens.isEmpty()) return Analyzed(terms, emptyList())

        val text = buildTextQuery(queryText, snap.analyzer) ?: return Analyzed(terms, emptyList())
        val acl = TermInSetQuery(Fields.ACL, aclTokens.map { BytesRef(it) })

        val q = BooleanQuery.Builder()
            .add(text, BooleanClause.Occur.MUST)
            .add(acl, BooleanClause.Occur.FILTER)   // FILTER: 점수에 영향 없음
            .build()

        val top = searcher.search(q, limit)
        val stored = searcher.storedFields()
        return Analyzed(terms, top.scoreDocs.map { sd ->
            val d = stored.document(sd.doc)
            Scored(d.get(Fields.ID), d.get(Fields.TITLE) ?: "", d.get(Fields.SPACE) ?: "", sd.score)
        })
    }

    /** 메타데이터 캐시. 스냅샷의 일부라 재구축 시 함께 교체된다. */
    fun metaOf(pageId: String): PageMeta? = snapshotRef.get().meta[pageId]
    fun allMeta(): Collection<PageMeta> = snapshotRef.get().meta.values

    /** 계층 렌더링. 로직은 [TreeRenderer] 에 있다 — Lucene 없이 테스트할 수 있어야 한다. */
    fun renderTree(canSee: (String) -> Boolean, rootId: String? = null, maxDepth: Int = 0): RenderedTree {
        val snap = snapshotRef.get()
        return TreeRenderer(snap.tree, snap.meta).render(canSee, rootId, maxDepth)
    }

    /** 세 필드에 대한 가중 OR. 파싱 실패 시 null 을 반환해 호출부가 조용히 빈 결과를 내게 한다. */
    private fun buildTextQuery(text: String, analyzer: Analyzer): Query? {
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
     * 서버가 질의를 토큰화한다 — 클라이언트는 원문만 보낸다. 양쪽이 각자 토큰화하면
     * 규칙이 갈렸을 때 **에러 없이 조용히 0건**이 된다(실제로 겪었다).
     */
    fun analyze(text: String): List<String> = tokenize(snapshotRef.get().analyzer, text)

    private fun tokenize(analyzer: Analyzer, text: String): List<String> {
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
        snapshotRef.getAndSet(Snapshot.empty(buildKind)).close()
    }

    companion object {
        /** 색인 커밋 데이터에 분석기 이름을 남기는 키. */
        const val ANALYZER_KEY = "wikilens.analyzer"

        /**
         * 기록 없는 색인이 지어졌을 분석기. 기록 이전 버전은 `KoreanAnalyzer` 하드코딩이라
         * 전부 korean 이다. 재색인 한 번이면 안 쓰인다 — 마이그레이션 전용.
         */
        val LEGACY_KIND = AnalyzerKind.KOREAN
    }
}
