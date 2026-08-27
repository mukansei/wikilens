plugins {
    kotlin("jvm") version "2.4.10"
    kotlin("plugin.spring") version "2.4.10"
    id("org.springframework.boot") version "3.5.16"
    id("io.spring.dependency-management") version "1.1.7"
}

group = "io.wikilens"
version = "0.18.2"

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

/**
 * Lucene `MMapDirectory` 가 `posix_madvise` 를 FFM API 로 부른다
 * (`org.apache.lucene.store.PosixNativeAccess`). Java 22 부터 네이티브 호출은
 * "restricted" 라, 승인하지 않으면 기동할 때마다 경고 네 줄이 찍힌다.
 *
 * **성능 때문이 아니라 로그 때문에 켠다.** 차단돼도 Lucene 은 폴백한다 —
 * `NativeAccess.getImplementation()` 이 `Optional` 이고, 실측으로
 * `--illegal-native-access=deny` 에서도 색인 2,383건이 정상이었다
 * (2,279ms 대 허용 시 2,171ms — 노이즈 수준). madvise 는 커널 readahead
 * 힌트일 뿐이고 이 규모(색인 3MB)는 어차피 페이지 캐시에 들어간다.
 *
 * 그런데 이 프로젝트는 **기동 로그를 진단에 쓴다** — 볼트·색인·상태 경로,
 * `궤적 N건 재생`, `기동 적재 완료` 를 눈으로 확인하는 구조다. 무해한 경고가
 * 그 사이에 끼면 정작 봐야 할 줄을 가린다.
 *
 * `ALL-UNNAMED` 는 클래스패스 전체를 승인하는 넓은 범위다. 모듈로 나뉘어 있지
 * 않아 더 좁힐 수단이 없다 — Lucene 만 지정하려면 모듈 경로로 옮겨야 한다.
 */
val nativeAccess = "--enable-native-access=ALL-UNNAMED"

tasks.withType<Test> {
    useJUnitPlatform()
    jvmArgs(nativeAccess)
}

tasks.named<org.springframework.boot.gradle.tasks.run.BootRun>("bootRun") {
    jvmArgs(nativeAccess)
}

/**
 * 산출물을 **하나로** 만든다.
 *
 * Spring Boot 플러그인은 기본으로 둘을 낸다 — 실행 가능한 fat jar 와 클래스만 든
 * `-plain.jar`. 후자는 아무도 안 쓰는데, `build/libs/*.jar` 로 집는 배포 스크립트를
 * **조용히 애매하게** 만든다. `Dockerfile` 의 `COPY` 가 정확히 그 형태다.
 *
 * 이 한 줄이 그것을 보장하고, Dockerfile 의 글롭은 그 위에 서 있다. 확인은 이미지
 * 빌드로 했다 — 컨테이너 안 `build/libs` 에 파일이 하나다.
 *
 * **jar 이름은 고정하지 못했다.** `archiveFileName` 도 `archiveBaseName`+
 * `archiveVersion` 도 이 조합(Gradle 9.6.1 · Boot 3.5.16)에서 `bootJar` 에 안 먹었다 —
 * 둘 다 깨끗한 컨테이너 빌드로 확인했고 산출물은 계속 `server-<버전>.jar` 다.
 * 원인을 못 찾았고, 글롭이 하나만 맞으면 문제가 없어 더 파지 않았다. 이름이 필요해지면
 * 여기서부터 다시 볼 것.
 */
tasks.named<Jar>("jar") { enabled = false }
