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
