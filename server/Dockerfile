# WikiLens 서버 이미지.
#
# ### 무엇이 이미지에 들어가고 무엇이 안 들어가나
#
# **볼트는 이미지에 굽지 않는다.** 위키 본문은 데이터이고, 이미지에 구우면 그 사본을
# 회수할 수 없다 — 레지스트리에 올라간 순간 권한 취소가 불가능해진다. 볼트·색인·상태는
# 전부 마운트다(아래 VOLUME 참고).
#
# **ripgrep 은 넣는다.** 서버는 rg 를 `PATH` 에서 이름으로만 찾는데(셸을 안 거치므로
# 별칭·셸 함수가 안 보인다), 컨테이너에서는 우리가 PATH 를 정할 수 있어 그 불확실성이
# 사라진다. 없어도 JVM 스캔으로 동작하지만 문서당 비용이 3~4배다.
#
# ### 왜 두 단계인가
#
# Gradle 과 JDK 전체(약 700MB)를 런타임에 들고 있을 이유가 없다. 빌드 산출물은
# fat jar 하나뿐이라 JRE 만 있으면 된다.

# ---------- 1단계: 빌드 ----------
FROM eclipse-temurin:25-jdk AS build
WORKDIR /src

# 래퍼와 빌드 스크립트를 먼저 복사해 **의존성 다운로드를 레이어로 굳힌다** —
# 소스만 바뀔 때 매번 다시 받지 않는다.
COPY gradlew settings.gradle.kts build.gradle.kts gradle.properties ./
COPY gradle ./gradle
# **`|| true` 를 안 붙인다.** 붙이면 의존성 해석 실패(네트워크·저장소 문제)가 조용히
# 지나가고 다음 단계에서 엉뚱한 모습으로 터진다 — 이 저장소가 `check.sh` 에서 지운
# 바로 그 형태다. stdout 만 버리고 **stderr 와 종료 코드는 살린다.**
RUN ./gradlew --no-daemon dependencies > /dev/null

COPY src ./src
# 테스트는 여기서 안 돌린다. `check.sh` 가 정본이고, 그것은 Python·계약까지 함께
# 보는데 이 이미지에는 그 둘이 없다 — 반쪽 검증을 통과 신호로 만들지 않는다.
RUN ./gradlew --no-daemon bootJar -x test

# ---------- 2단계: 실행 ----------
FROM eclipse-temurin:25-jre
RUN apt-get update \
 && apt-get install -y --no-install-recommends ripgrep curl \
 && rm -rf /var/lib/apt/lists/*

# 루트로 안 돈다. 마운트한 볼륨의 소유자를 이 uid 로 맞춰야 한다(compose 참고).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin wikilens
USER wikilens
WORKDIR /app
# 글롭이지만 **하나만 맞는 것이 보장된다** — `build.gradle.kts` 가 `-plain.jar` 를
# 끈다(확인: 컨테이너 안 build/libs 에 파일 하나). 둘이 되면 Docker 가 여기서
# 소리내며 실패하므로 조용히 엉뚱한 것을 집을 일은 없다.
COPY --from=build --chown=wikilens:wikilens /src/build/libs/*.jar /app/wikilens.jar

# **경로를 절대경로로 못 박는다.** 기본값이 상대경로라 실행 디렉터리에 매달리는데,
# 컨테이너에서 그건 조용히 어긋나기 좋은 자리다.
#
#   /vault  볼트 (읽기 전용으로 마운트해도 된다 — 서버는 읽기만 한다)
#   /state  궤적 로그·사용자 등록. **유일한 복구 불가 자산이라 반드시 영속 볼륨.**
#   /index  Lucene 색인. 지워도 재색인으로 복구되지만 두면 기동이 빨라진다.
#
# **`VOLUME` 선언은 일부러 안 한다.** 하면 마운트를 안 준 `docker run` 이 **익명 볼륨
# 셋을 조용히 만든다**(실측). `/state` 가 그렇게 만들어지면 운영자는 상태가 없는 줄
# 아는데 실제로는 어딘가에 쌓이고, `docker rm -v` 로 같이 사라진다 — 유일한 복구 불가
# 자산에 그런 모호함을 두면 안 된다. 마운트는 `compose.yml` 이 명시한다.
ENV WIKILENS_VAULT_ROOT=/vault \
    WIKILENS_INDEX_DIR=/index \
    WIKILENS_STATE_DIR=/state

EXPOSE 8787

# `--enable-native-access` 는 성능이 아니라 **로그** 때문이다. 없으면 Lucene 의 FFM
# 호출에 경고 네 줄이 찍혀, 진단에 쓰는 기동 로그(볼트 경로·궤적 재생·적재 완료)를
# 가린다. 근거는 `build.gradle.kts` 의 같은 상수 옆에.
#
# 컨테이너 메모리를 힙 상한에 반영한다 — 안 주면 JVM 이 호스트 RAM 을 기준으로 잡아
# cgroup 한도를 넘어 OOMKill 된다.
ENTRYPOINT ["java", \
            "--enable-native-access=ALL-UNNAMED", \
            "-XX:MaxRAMPercentage=75", \
            "-jar", "/app/wikilens.jar"]

# 컨테이너가 "떴다" 와 "쓸 수 있다" 는 다르다. `/api/health` 는 포트가 열렸는지만
# 말하므로, 볼트를 못 읽어 색인이 빈 상태도 healthy 로 보인다 — 그 구별은
# `--status` 나 `/api/stats` 의 `indexedDocs`·`aclPages` 가 한다.
#
# **`start-period` 가 넉넉한 이유:** 기동 적재가 웹 서버보다 **먼저** 끝난다(빈 생성
# 시점에 돈다 — 그래야 포트가 열린 뒤 read 가 404 나는 상태가 없다). 즉 색인하는 동안
# 포트가 안 열려 health 가 실패하는데, 그건 고장이 아니라 정상이다. 코퍼스가 크면
# 그만큼 길어지므로 짧게 잡으면 **멀쩡한 기동이 unhealthy 로 보인다.**
HEALTHCHECK --interval=30s --timeout=3s --start-period=5m \
    CMD curl -fsS http://localhost:8787/api/health || exit 1
