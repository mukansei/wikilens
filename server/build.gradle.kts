plugins {
    kotlin("jvm") version "2.4.10"
    kotlin("plugin.spring") version "2.4.10"
    id("org.springframework.boot") version "3.5.16"
    id("io.spring.dependency-management") version "1.1.7"
}

group = "dev.wikilens"
version = "0.1.0"

kotlin {
    jvmToolchain(25)
    compilerOptions { freeCompilerArgs.addAll("-Xjsr305=strict") }
}

repositories { mavenCentral() }

// Lucene 9.x. 10.x는 API가 바뀌어 storedFields/TermInSetQuery 호출부를 손봐야 한다.
val luceneVersion = "9.11.1"

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("com.fasterxml.jackson.module:jackson-module-kotlin")
    implementation("org.jetbrains.kotlin:kotlin-reflect")

    implementation("org.apache.lucene:lucene-core:$luceneVersion")
    implementation("org.apache.lucene:lucene-queryparser:$luceneVersion")
    implementation("org.apache.lucene:lucene-analysis-common:$luceneVersion")
    // 한국어 형태소 분석. JVM을 고른 유일하고 결정적인 이유다.
    implementation("org.apache.lucene:lucene-analysis-nori:$luceneVersion")

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("org.jetbrains.kotlin:kotlin-test-junit5")
}

tasks.withType<Test> { useJUnitPlatform() }

// 순수 로직 검증을 빌드에 연결한다. Lucene/Spring 없이도 돌아간다.
tasks.register<Exec>("verifyCore") {
    group = "verification"
    description = "kotlinc만으로 학습 레이어 순수 로직을 검증합니다"
    commandLine("./verify.sh")
}
