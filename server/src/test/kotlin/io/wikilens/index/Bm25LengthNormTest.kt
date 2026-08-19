package io.wikilens.index

import org.apache.lucene.analysis.Analyzer
import org.apache.lucene.index.DirectoryReader
import org.apache.lucene.search.IndexSearcher
import org.apache.lucene.search.similarities.BM25Similarity
import org.apache.lucene.store.MMapDirectory
import org.junit.jupiter.api.Assumptions.assumeTrue
import org.junit.jupiter.api.Test
import kotlin.test.assertTrue
import java.nio.file.Files
import java.nio.file.Path

/**
 * **BM25 길이 정규화(`b`)를 완화하면 순위가 어떻게 되나.**
 *
 * `b` 는 색인이 아니라 **질의 시점**에 norm 을 해석하는 데 쓰이므로 재색인 없이 갈아끼운다.
 * 그래서 이 측정은 싸다 — 같은 색인 하나에 유사도만 바꿔 30질의를 다시 돌린다.
 *
 * 세우려는 것은 하나다: **길이 정규화를 건드리는 것이 이 코퍼스에서 이득인가.**
 * `b=0.75` 는 Lucene 기본값이고 이 저장소는 유사도를 명시하지 않는다 — 바꾸려면
 * 여기 숫자가 근거여야 한다.
 *
 * **실측(2026-08-13)에서 `b` 를 낮추면 도달 23→26/30 · MRR 0.369→0.457 로 단조 개선이고
 * 30건 중 나빠진 것이 하나도 없다. 그런데 그것으로 채택하면 안 된다** — 이 30개의 정답이
 * 본문 항 수 중앙값 1,432 로 코퍼스(248)의 **5.8배**다. `b` 를 낮추는 것은 정의상 긴
 * 문서를 올리는 조작이므로, "정답을 잘 찾았다" 와 "정답이 길어서 올라왔다" 가 이 표본에서
 * 구별되지 않는다. 아래 두 단언이 그 구별 불가능성을 잠근다 — **깨지면 편향이 사라진
 * 것이므로 그때 다시 판단할 것.**
 *
 * 남은 위험은 이 표본이 아예 못 보는 쪽이다: **정답이 짧은 질의.** 여기엔 그런 사례가
 * 없어서, `b=0` 이 긴 문서로 상위를 덮는 전형적 실패가 보일 자리가 없다.
 *
 * 색인이 없으면(`bench/setup.sh up` 전) 건너뛴다.
 */
class Bm25LengthNormTest {

    private data class C(val group: String, val gold: String, val query: String)

    /**
     * **배포판에는 사례가 비어 있다 — 그래서 이 테스트는 항상 건너뛴다.**
     *
     * 원래 여기에는 실제 코퍼스에서 고른 질의 30개가 있었다. 그것은 실제 문서 제목과
     * 페이지 ID 라 공개판에서 뺐고, 애초에 **그 코퍼스가 없으면 돌지도 않는다**
     * (아래가 색인이 비면 건너뛴다).
     *
     * 되살리려면 `bench/queries.py` 를 자기 위키로 채운 뒤 같은 모양으로 옮겨 적으면
     * 된다. 그 파일이 정본이고 여기는 사본이다 — 원래 그것이 이 자리의 약점이었다.
     */
    private val cases = emptyList<C>()

    // **프로덕션과 같은 것을 쓴다.** 예전에는 분석기와 질의를 여기서 손으로 다시
    // 만들고 "같은 구성이어야 한다" 는 주석으로 이어 뒀는데, 그 주석은 아무것도 안
    // 막는다 — 측정 장치가 조용히 다른 질의를 재게 된다. `LuceneQuery` 가 정의처다.
    private fun analyzer(): Analyzer = LuceneQuery.analyzerFor(AnalyzerKind.KOREAN)

    private fun query(text: String, a: Analyzer) =
        LuceneQuery.textQuery(text, a) ?: error("질의 파싱 실패: $text")

    @Test
    fun `b 를 낮추면 순위가 어떻게 되나`() {
        val dir = Path.of(System.getProperty("user.dir")).parent.resolve("bench/srv-index")
        assumeTrue(Files.isDirectory(dir) && Files.list(dir).use { it.findAny().isPresent },
            "색인 없음 — bench/setup.sh up 후에 돈다")

        val a = analyzer()
        MMapDirectory(dir).use { d ->
            DirectoryReader.open(d).use { r ->
                val searcher = IndexSearcher(r)
                println("\n  문서 ${r.numDocs()}건 · b 를 낮출수록 길이 벌점이 약해진다\n")
                println("  %-6s %s".format("b", "그룹별 정답 순위 (0 = 30위 밖)"))
                val summary = LinkedHashMap<Float, Pair<Int, Double>>()
                for (b in listOf(0.75f, 0.5f, 0.25f, 0.0f)) {
                    searcher.similarity = BM25Similarity(1.2f, b)
                    val ranks = cases.map { c ->
                        val top = searcher.search(query(c.query, a), 30)
                        val ids = top.scoreDocs.map { searcher.storedFields().document(it.doc).get(Fields.ID) }
                        ids.indexOf(c.gold) + 1
                    }
                    val hits = ranks.count { it > 0 }
                    val mrr = ranks.sumOf { if (it > 0) 1.0 / it else 0.0 } / ranks.size
                    summary[b] = hits to mrr
                    println("  b=%-4s %s".format(b, ranks.joinToString(" ") { "%2d".format(it) }))
                }
                println("\n  %-6s %-8s %s".format("b", "도달", "MRR (높을수록 위쪽에서 찾음)"))
                summary.forEach { (b, v) ->
                    println("  b=%-4s %2d/30    %.4f".format(b, v.first, v.second))
                }

                // **개선만 보고 채택하면 안 된다.** 길이 벌점을 빼면 긴 문서가 위로 올라오는데,
                // 이 30개 정답이 평균보다 크면 "정답을 잘 찾은 것" 과 "긴 것을 올린 것" 이
                // 구별되지 않는다. 둘을 갈라 보려고 상위권의 길이 분포를 함께 낸다.
                // `BODY` 는 색인만 되고 저장은 안 되므로 길이를 stored 에서 못 꺼낸다.
                // 대신 **BM25 가 실제로 쓰는 값**인 norm 을 읽는다 — 근사지만 정의상 맞다.
                val lenOfDoc = { docId: Int ->
                    val leaf = r.leaves().last { docId >= it.docBase }
                    val nv = leaf.reader().getNormValues(Fields.BODY)
                    if (nv != null && nv.advanceExact(docId - leaf.docBase)) {
                        org.apache.lucene.util.SmallFloat.byte4ToInt(nv.longValue().toByte())
                    } else 0
                }
                val docIdOf = { id: String ->
                    searcher.search(
                        org.apache.lucene.search.TermQuery(org.apache.lucene.index.Term(Fields.ID, id)), 1
                    ).scoreDocs.firstOrNull()?.doc
                }

                val corpus = (0 until r.numDocs()).map(lenOfDoc).sorted()
                val golds = cases.map { it.gold }.distinct().mapNotNull(docIdOf).map(lenOfDoc).sorted()
                println("\n  본문 항 수 중앙값 — 코퍼스 ${corpus[corpus.size / 2]} · 정답 10건 ${golds[golds.size / 2]}")
                println("  → 정답이 코퍼스보다 길면 b 를 낮춘 이득에 **선택 편향**이 섞인다")

                println("\n  %-6s %-14s %s".format("b", "상위5 중앙길이", "1위가 아주 짧은 문서(항 20 미만)"))
                val topMedian = LinkedHashMap<Float, Int>()
                for (b in listOf(0.75f, 0.5f, 0.25f, 0.0f)) {
                    searcher.similarity = BM25Similarity(1.2f, b)
                    val top5 = ArrayList<Int>()
                    var tiny = 0
                    for (c in cases) {
                        val docs = searcher.search(query(c.query, a), 5).scoreDocs.map { it.doc }
                        docs.forEach { top5.add(lenOfDoc(it)) }
                        if (docs.isNotEmpty() && lenOfDoc(docs[0]) < 20) tiny++
                    }
                    top5.sort()
                    topMedian[b] = top5[top5.size / 2]
                    println("  b=%-4s %-14s %d/30".format(b, "${top5[top5.size / 2]}", tiny))
                }
                println()

                // **위 개선표를 "b 를 낮추면 좋다" 로 읽지 못하게 막는 두 단언.**
                // 하나라도 깨지면 편향이나 기전이 달라진 것이고, 그때는 다시 판단할 자리다.
                assertTrue(
                    golds[golds.size / 2] > corpus[corpus.size / 2] * 2,
                    "정답 집합이 더는 코퍼스보다 뚜렷이 길지 않다 — 편향이 사라졌으니 개선표를 다시 읽을 것",
                )
                assertTrue(
                    topMedian[0.0f]!! > topMedian[0.75f]!!,
                    "b 를 낮췄는데 상위권이 길어지지 않았다 — 기전 자체가 이 코퍼스에서 성립하지 않는다",
                )
            }
        }
    }

    /**
     * **질의가 순수 OR 이라 7낱말 중 1개만 맞아도 후보가 된다.** 흔한 낱말로만 물었을 때
     * 정답이 수천 건에 묻히는 것이 그 결과이므로, "몇 낱말 이상 맞아야 후보" 를 거는 것이
     * 길이 정규화보다 원인에 가까운 레버다.
     *
     * `b` 와 달리 **길이 편향이 개선을 설명하지 못한다** — 조건이 "낱말을 몇 개 맞췄나"
     * 이지 "문서가 기냐" 가 아니다. 다만 긴 문서가 우연히 더 많은 낱말을 담으므로 완전히
     * 무관하지는 않아, 여기서도 상위권 길이를 함께 낸다.
     */
    @Test
    fun `몇 낱말 이상 맞아야 후보로 걸면 순위가 어떻게 되나`() {
        val dir = Path.of(System.getProperty("user.dir")).parent.resolve("bench/srv-index")
        assumeTrue(Files.isDirectory(dir) && Files.list(dir).use { it.findAny().isPresent },
            "색인 없음 — bench/setup.sh up 후에 돈다")

        val a = analyzer()
        MMapDirectory(dir).use { d ->
            DirectoryReader.open(d).use { r ->
                val searcher = IndexSearcher(r)

                // 파서가 낸 최상위 OR 의 절 수가 곧 "낱말 수" 다. 그 비율만큼을 필수로 만든다.
                fun tighten(q: org.apache.lucene.search.Query, frac: Double): org.apache.lucene.search.Query {
                    if (frac <= 0.0 || q !is org.apache.lucene.search.BooleanQuery) return q
                    val should = q.clauses().filter { it.occur == org.apache.lucene.search.BooleanClause.Occur.SHOULD }
                    if (should.size < 2) return q
                    val need = Math.ceil(should.size * frac).toInt().coerceIn(1, should.size)
                    val b = org.apache.lucene.search.BooleanQuery.Builder()
                    q.clauses().forEach { b.add(it) }
                    return b.setMinimumNumberShouldMatch(need).build()
                }

                val lenOfDoc = { docId: Int ->
                    val leaf = r.leaves().last { docId >= it.docBase }
                    val nv = leaf.reader().getNormValues(Fields.BODY)
                    if (nv != null && nv.advanceExact(docId - leaf.docBase)) {
                        org.apache.lucene.util.SmallFloat.byte4ToInt(nv.longValue().toByte())
                    } else 0
                }

                println("\n  낱말의 몇 할이 맞아야 후보인가 (0% = 지금)\n")
                println("  %-6s %s".format("필수", "그룹별 정답 순위 (0 = 30위 밖)"))
                val res = LinkedHashMap<Int, Triple<Int, Double, Int>>()
                for (frac in listOf(0.0, 0.34, 0.5, 0.67)) {
                    val ranks = ArrayList<Int>()
                    val top5 = ArrayList<Int>()
                    for (c in cases) {
                        val top = searcher.search(tighten(query(c.query, a), frac), 30)
                        val ids = top.scoreDocs.map { searcher.storedFields().document(it.doc).get(Fields.ID) }
                        ranks.add(ids.indexOf(c.gold) + 1)
                        top.scoreDocs.take(5).forEach { top5.add(lenOfDoc(it.doc)) }
                    }
                    top5.sort()
                    res[(frac * 100).toInt()] = Triple(
                        ranks.count { it > 0 },
                        ranks.sumOf { if (it > 0) 1.0 / it else 0.0 } / ranks.size,
                        if (top5.isEmpty()) 0 else top5[top5.size / 2],
                    )
                    println("  %3d%%   %s".format((frac * 100).toInt(), ranks.joinToString(" ") { "%2d".format(it) }))
                }
                println("\n  %-6s %-8s %-9s %s".format("필수", "도달", "MRR", "상위5 중앙길이"))
                res.forEach { (p, v) -> println("  %3d%%   %2d/30    %.4f    %d".format(p, v.first, v.second, v.third)) }
                println()

                // **실측: 조이면 오히려 못 찾는다** (23 → 24 → 19 → 13/30). 구어체 질의의
                // 절반이 `어떻게`·`있나`·`문서` 같은 군더더기라, 정답이 그것들을 안 담고 있어
                // 필수 낱말 수를 올리면 **정답부터 탈락한다.** 이 단언이 그 방향을 잠근다 —
                // 깨지면 질의 집합이나 분석기가 달라진 것이니 다시 잴 자리다.
                assertTrue(
                    res[67]!!.first < res[0]!!.first,
                    "낱말을 많이 요구하는 쪽이 더 잘 찾는다 — 군더더기 가정이 깨졌으니 다시 판단할 것",
                )
            }
        }
    }
}
