package dev.wikilens

import com.fasterxml.jackson.databind.ObjectMapper
import dev.wikilens.acl.AclRegistry
import dev.wikilens.config.WikiLensProperties
import dev.wikilens.index.AnalyzerKind
import dev.wikilens.index.LuceneIndex
import dev.wikilens.service.IndexingService
import dev.wikilens.learn.FileTrajectorySink
import dev.wikilens.learn.TrajectoryStore
import dev.wikilens.vault.VaultReader
import org.slf4j.LoggerFactory
import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.boot.runApplication
import org.springframework.context.annotation.Bean
import org.springframework.scheduling.annotation.EnableScheduling
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import java.nio.file.Path

/** 기동 적재 결과. 빈으로 두는 이유는 적재가 실제로 일어났음을 테스트가 확인하기 위해서다. */
data class VaultBootstrap(val indexed: Int, val aclPages: Int)

fun main(args: Array<String>) {
    runApplication<WikiLensApplication>(*args)
}
