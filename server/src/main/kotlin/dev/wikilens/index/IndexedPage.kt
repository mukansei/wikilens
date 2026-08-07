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
