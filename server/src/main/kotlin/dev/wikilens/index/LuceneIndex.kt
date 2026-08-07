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

/** 부모 하나. 루트부터 직속 부모까지 순서대로 온다(Confluence ancestors 그대로). */
data class Ancestor(val id: String, val title: String)

data class IndexedPage(
    val id: String,
    val title: String,
    val space: String,
    val path: String,
    val body: String,
    val anchors: List<String>,
    val aclTokens: List<String>,
    val ancestors: List<Ancestor> = emptyList(),
)

data class Scored(val id: String, val title: String, val space: String, val score: Float)

/** 색인된 문서의 메타데이터. 본문은 담지 않는다 — 콘텐츠는 미러에서 읽는다. */
data class PageMeta(val id: String, val title: String, val space: String)

/**
 * 본문 분석기 선택지.
 *
 * **색인과 질의가 같은 분석기를 써야 한다.** 다르면 예외가 아니라 **조용히 0건**이 되고,
 * 그것이 이 프로젝트가 겪은 대표적 실패다(`CLAUDE.md` 조용히 실패하는 것들 1번).
 * 그래서 선택을 색인에 기록하고([LuceneIndex.ANALYZER_KEY]) 기동 시 대조한다.
 */
enum class AnalyzerKind(val key: String) {
    /**
     * 한국어. 교착어라 조사가 붙는다 — '로그인을/로그인은/로그인이'.
     * 형태소 분석 없이는 BM25 가 무너진다. 이것이 JVM 을 고른 유일하고 결정적인 이유다.
     *
     * 영문도 깨뜨리지 않는다 — 공백·구두점으로 자르고 소문자화한다(실측:
     * `OAuth 2.0 authorization code flow` → `[oauth, 2, 0, authorization, code, flow]`).
     * 다만 **어간을 안 줄인다.** 문서 `production servers` 에 질의 `production server` 가
     * 안 맞는다(실측: 굴절 쌍 5개 중 Nori 0 · English 5). 한국어 문서에 영문
     * 고유명사·식별자가 섞인 코퍼스에서는 그것들이 굴절하지 않으므로 문제가 안 된다.
     */
    KOREAN("korean"),

    /**
     * 영어. 어간 추출과 불용어 제거를 한다 — `the deploy to a server` 가 2토큰이 된다.
     * **영어가 주된 코퍼스일 때만 고를 것.** 한국어 본문에 쓰면 조사를 못 떼서
     * Nori 를 쓰는 이유가 그대로 사라진다 — 대칭인 실패다.
     */
    ENGLISH("english"),

    /**
     * 언어 규칙 없이 유니코드 단어 경계로만 자르고 소문자화한다.
     * 어느 언어에도 최선은 아니지만 **어느 언어도 망가뜨리지 않는다** —
     * 다국어가 섞였는데 주 언어를 못 고르겠을 때의 안전한 기본값이다.
     */
    STANDARD("standard");

    companion object {
        /** 모르는 이름은 거부한다 — 오타가 조용히 기본값으로 떨어지면 그게 0건의 원인이 된다. */
        fun of(key: String): AnalyzerKind =
            entries.firstOrNull { it.key.equals(key.trim(), ignoreCase = true) }
                ?: throw IllegalArgumentException(
                    "알 수 없는 분석기 '$key'. 가능한 값: ${entries.joinToString("·") { it.key }}"
                )
    }
}

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
     * 검색기·메타데이터·트리·**분석기**를 한 덩어리로 들고 있다.
     *
     * 예전엔 셋을 각각 `AtomicReference` 로 따로 교체했는데, 그 사이에 들어온 요청이
     * 새 트리 + 옛 메타처럼 뒤섞인 상태를 볼 수 있었다. 하나로 묶으면 교체가 원자적이다.
     * [dir] 를 함께 들고 있는 이유는 예전에 `DirectoryReader.open(MMapDirectory(...))` 의
     * Directory 를 아무도 안 닫아 교체마다 누수됐기 때문이다.
     *
     * **분석기가 여기 있는 이유도 같다.** 색인과 분석기는 한 쌍이라 따로 교체되면
     * 그 틈에 들어온 질의가 새 색인을 옛 분석기로(또는 그 반대로) 두드린다.
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
            val EMPTY = Snapshot(
                null, null, null, emptyMap(), TreeIndex.EMPTY,
                AnalyzerKind.KOREAN, analyzerFor(AnalyzerKind.KOREAN),
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

    private val snapshotRef = AtomicReference(Snapshot.EMPTY)

    val docCount: Int get() = snapshotRef.get().reader?.numDocs() ?: 0

    /**
     * 전체 재구축 후 스냅샷을 원자적으로 교체한다.
     *
     * 제자리 갱신을 하지 않는 이유: 10k 문서 재구축이 수 초라 증분 갱신의 이득이 없고,
     * 증분은 드리프트가 조용히 쌓이는 자리다. 재구축 중 질의는 이전 스냅샷을 계속 쓴다.
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

    /**
     * 새 스냅샷을 열어 원자 교체하고 이전 것을 닫는다.
     *
     * [meta]/[tree] 를 안 주면(기동 시 기존 색인 열기) 현재 값을 유지한다 — 그 경우
     * 메타·트리는 `/admin/reindex` 가 채운다.
     */
    private fun swap(meta: Map<String, PageMeta>? = null, tree: TreeIndex? = null) {
        val d = MMapDirectory(dir)
        val reader = runCatching { DirectoryReader.open(d) }
            .onFailure { runCatching { d.close() } }   // 열기 실패 시 Directory 를 흘리지 않는다
            .getOrThrow()
        // **디스크가 정본이다.** 색인이 어떤 분석기로 지어졌는지는 커밋 데이터에 적혀
        // 있으므로, 설정이 아니라 그것을 따라간다. 기록이 없으면(이 기능 이전 색인)
        // 설정을 쓴다 — 그때는 대조할 근거가 없다.
        val recorded = reader.indexCommit.userData[ANALYZER_KEY]
        val kind = recorded?.let { runCatching { AnalyzerKind.of(it) }.getOrNull() } ?: buildKind
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
     * 설정과 디스크가 다르면 알린다. **경고일 뿐 고장이 아니다** — 질의는 디스크가
     * 지어진 분석기를 쓰므로 검색은 정상 동작한다.
     *
     * 예전에는 여기서 ERROR 를 찍고 그대로 **설정된** 분석기로 질의했다. 색인에 답이
     * 적혀 있는데 그것을 안 쓰고 어긋남을 재고만 있었던 셈이라, 재색인 전까지 검색이
     * 조용히 0건이었다. 지금은 그 상태가 성립하지 않는다.
     */
    private fun reportAnalyzer() {
        val built = builtWith() ?: return   // 색인이 없거나 분석기 기록 이전 색인
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
     * 검색. [aclTokens] 는 요청자의 권한 토큰(그룹, 사용자 ID, 공개 마커).
     *
     * ACL 필터는 **선택이 아니라 필수 절**이다. 빈 목록이면 결과가 비어야 한다 —
     * 실수로 전체가 노출되는 것보다 아무것도 안 나오는 편이 낫다.
     */
    fun search(queryText: String, aclTokens: Collection<String>, limit: Int): List<Scored> {
        val snap = snapshotRef.get()
        val searcher = snap.searcher ?: return emptyList()
        if (aclTokens.isEmpty()) return emptyList()

        val text = buildTextQuery(queryText, snap.analyzer) ?: return emptyList()
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
     * 메타데이터 캐시 조회. 스냅샷의 일부라 색인 재구축 시 함께 교체된다.
     * ContentService 가 제목을 얻으려고 Lucene 을 매번 조회하지 않게 한다.
     */
    fun metaOf(pageId: String): PageMeta? = snapshotRef.get().meta[pageId]
    fun allMeta(): Collection<PageMeta> = snapshotRef.get().meta.values

    /**
     * 계층 렌더링. 실제 로직은 [TreeRenderer] 에 있다 — 순수 자료구조라
     * Lucene 없이 단위 테스트할 수 있어야 해서 분리했다.
     */
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
     * 서버가 질의를 토큰화한다. 클라이언트는 원문만 보낸다.
     *
     * 이렇게 하면 토크나이저 정본이 하나가 된다. 양쪽이 각자 토큰화하면
     * 규칙이 달라졌을 때 에러 없이 조용히 0건이 되는데, 실제로 겪은 버그다.
     */
    fun analyze(text: String): List<String> {
        val out = ArrayList<String>()
        snapshotRef.get().analyzer.tokenStream(Fields.BODY, text).use { ts ->
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
        snapshotRef.getAndSet(Snapshot.EMPTY).close()
    }

    companion object {
        /** 색인 커밋 데이터에 분석기 이름을 남기는 키. */
        const val ANALYZER_KEY = "wikilens.analyzer"
    }
}
