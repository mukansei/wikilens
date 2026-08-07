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

/** 색인된 문서의 메타데이터. 본문은 담지 않는다 — 콘텐츠는 미러에서 읽는다. */
data class PageMeta(val id: String, val title: String, val space: String)
