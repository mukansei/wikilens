package io.wikilens.index

import org.apache.lucene.analysis.Analyzer
import org.apache.lucene.analysis.ko.KoreanAnalyzer
import org.apache.lucene.analysis.miscellaneous.PerFieldAnalyzerWrapper
import org.apache.lucene.queryparser.classic.MultiFieldQueryParser
import org.apache.lucene.queryparser.classic.QueryParser
import org.apache.lucene.queryparser.classic.QueryParserBase
import org.apache.lucene.search.Query

/**
 * **분석기와 질의를 짓는 유일한 자리.** [LuceneIndex] 와 측정 테스트가 함께 쓴다.
 *
 * 나눈 이유가 하나다: `Bm25LengthNormTest` 가 유사도를 갈아끼우려면 자기 `IndexSearcher`
 * 를 열어야 하고, 그러면 질의도 자기가 지어야 한다. 그것을 손으로 다시 만들어 두면
 * **측정 장치가 조용히 프로덕션과 다른 질의를 재게 된다** — 실제로 그 상태였고, 둘을
 * 잇는 것이 "같은 구성이어야 한다" 는 주석 한 줄뿐이라 아무것도 안 막았다.
 *
 * 부스트를 바꾸면 `FieldBoostTest` 와 `Bm25LengthNormTest` 가 **함께** 빨개진다.
 */
internal object LuceneQuery {

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

    /** 세 필드에 대한 가중 OR. 파싱 실패 시 null 을 반환해 호출부가 조용히 빈 결과를 내게 한다. */
    fun textQuery(text: String, analyzer: Analyzer): Query? {
        val parser = MultiFieldQueryParser(
            arrayOf(Fields.ANCHOR, Fields.TITLE, Fields.BODY),
            analyzer,
            mapOf(
                Fields.ANCHOR to FieldBoost.ANCHOR,
                Fields.TITLE to FieldBoost.TITLE,
                Fields.BODY to FieldBoost.BODY,
            ),
        )
        parser.defaultOperator = QueryParser.Operator.OR
        // 사용자 질의를 그대로 넣으면 특수문자로 예외가 난다.
        return runCatching { parser.parse(QueryParserBase.escape(text)) }.getOrNull()
    }
}
