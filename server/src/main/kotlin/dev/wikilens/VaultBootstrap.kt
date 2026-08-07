package dev.wikilens

import org.springframework.boot.runApplication

/** 기동 적재 결과. 빈으로 두는 이유는 적재가 실제로 일어났음을 테스트가 확인하기 위해서다. */
data class VaultBootstrap(val indexed: Int, val aclPages: Int)

fun main(args: Array<String>) {
    runApplication<WikiLensApplication>(*args)
}
