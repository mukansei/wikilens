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

    // grep 의 정규식 엔진. java.util.regex 는 재귀 백트래킹이라 사용자 패턴 하나로
    // CPU 를 태우거나(ReDoS) 스택을 넘긴다 — 둘 다 실측으로 겪었다. RE2 는 유한
    // 오토마타라 입력 길이에 선형이고 그 두 실패가 원리적으로 없다. ripgrep 과
    // 같은 문법 계열이라, 나중에 rg 를 속도 가속기로 붙여도 답이 갈리지 않는다.
    implementation("com.google.re2j:re2j:1.7")

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("org.jetbrains.kotlin:kotlin-test-junit5")
}

tasks.withType<Test> { useJUnitPlatform() }
