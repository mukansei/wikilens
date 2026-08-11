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

# ### 베이스 이미지를 digest 로 고정한다
#
# 태그만 쓰면 Temurin 이 패치를 올리는 순간 **같은 커밋이 다른 이미지를 낸다** — 어제
# 되던 빌드가 오늘 깨져도 저장소에는 아무 변경이 없어 원인을 찾을 데가 없다.
#
# **멀티아키텍처 인덱스의 digest 다**(플랫폼별 manifest digest 가 아니다). 후자를 박으면
# 그 아키텍처에서만 돌고 arm64 호스트에서 `no matching manifest` 로 죽는다.
#
# 갱신은 손으로 한다 — CVE 대응이 필요하면 아래로 digest 를 다시 받아 바꾸고,
# 그 커밋이 곧 "언제 무엇으로 올렸나" 의 기록이 된다:
#
#     docker buildx imagetools inspect eclipse-temurin:25-jre --format '{{.Manifest.Digest}}'

# ---------- 1단계: 빌드 ----------
# eclipse-temurin:25-jdk = 25.0.3_9 (2026-08-04)
FROM eclipse-temurin:25-jdk@sha256:12e44624adee6808a36d962717e1656e0afeeeff5a100f9cb00e0136513558f0 AS build
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
# eclipse-temurin:25-jre = 25.0.3_9 (2026-08-04)
FROM eclipse-temurin:25-jre@sha256:f19dbf0a22d0b3658fda48ce7d7181df05ad14bda151dd5ad12cc09d1451c70e
# **apt 패키지는 버전을 고정하지 않는다** — 베이스가 digest 로 고정돼 있으므로 apt
# 저장소 스냅샷도 사실상 같이 굳고, 여기에 `=버전` 을 박으면 그 버전이 저장소에서
# 내려간 날 빌드가 깨진다(우분투는 보안 업데이트로 옛 버전을 치운다).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ripgrep curl \
 && rm -rf /var/lib/apt/lists/*

# 루트로 안 돈다.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin wikilens

# **마운트 지점을 이미지에 미리 만들어 이 uid 로 넘긴다.** 없으면 `docker compose up`
# 이 **기동조차 못 한다** — Docker 는 이미지에 없는 경로의 새 named volume 을
# `root:root 0755` 로 만들고, 비루트로 도는 우리는 거기 못 쓴다(실측, 이 Dockerfile 의
# 2단계를 그대로 떼어 재현):
#
#     drwxr-xr-x 2 root root /state   ← uid=10001 로 실행
#     touch: cannot touch '/state/.lock': Permission denied
#
# 첫 쓰기가 `StateDirLock` 의 `/state/.lock` 이고, 거기서 나는 것은
# `AccessDeniedException` 이다 — `OverlappingFileLockException` 이 아니라서 그 catch 를
# 안 지나가고, `StateDirLockFailureAnalyzer` 도 `AlreadyRunning` 만 다루므로 **우리가
# 만든 진단 대신 생 스택트레이스**가 나온다.
#
# **bind mount 로 확인하면 안 걸린다** — Docker Desktop for Mac 이 bind mount 만 권한을
# 재매핑하기 때문이다. named volume 은 재매핑하지 않고, Linux 호스트에서는 둘 다 걸린다.
# 그래서 `docker run` 으로 통과한 검증이 `docker compose up` 을 보증하지 못한다.
#
# 여기서 만들어 두면 **새 볼륨이 이 소유권을 물려받는다** — 같은 방법으로 확인했고,
# 실제 `docker compose up` 으로도 확인했다(`/state/.lock` 이 uid 10001 소유로 생성,
# `/api/health` 200).
#
# **`USER` 앞이어야 한다** — 뒤면 비루트라 `chown` 이 못 돈다. 계약이 순서를 검사한다.
RUN mkdir -p /home/wikilens/.wikilens/vault \
             /home/wikilens/.wikilens/state \
             /home/wikilens/.wikilens/index \
 && chown -R wikilens:wikilens /home/wikilens/.wikilens

USER wikilens

# **`HOME` 을 명시한다 — `USER` 로 박히는 것에 기대면 안 된다.**
#
# Docker 는 `USER` 의 `/etc/passwd` 항목에서 `HOME` 을 파생시키는데, 그것은 **기본
# 사용자에 한해서만** 산다. `docker compose` 의 `user:` 로 uid 를 바꾸는 순간(리눅스
# 호스트에서 bind mount 를 쓰려면 필요하다) passwd 항목이 없어 `HOME=/` 이 되고,
# 그러면 `~/.wikilens/vault` 가 `/.wikilens/vault` 로 풀려 **경로 해석이 통째로
# 어긋난다**(실측: `--user 502:20` → `HOME=/`).
#
# ENV 로 두면 uid 와 무관하게 유지된다(실측: 같은 오버라이드에서 `/home/wikilens`).
ENV HOME=/home/wikilens

WORKDIR /app
# 글롭이지만 **하나만 맞는 것이 보장된다** — `build.gradle.kts` 가 `-plain.jar` 를
# 끈다(확인: 컨테이너 안 build/libs 에 파일 하나). 둘이 되면 Docker 가 여기서
# 소리내며 실패하므로 조용히 엉뚱한 것을 집을 일은 없다.
COPY --from=build --chown=wikilens:wikilens /src/build/libs/*.jar /app/wikilens.jar

# **경로를 환경변수로 덮지 않는다.** `application.yml` 의 기본값이 이미
# `~/.wikilens/{vault,index,state}` 이고, 컨테이너에서 `~` 는 `/home/wikilens` 로 풀린다
# (`USER wikilens` 라 이미지에 `HOME` 이 박힌다 — 실측). 호스트와 **같은 설정값**을 쓰면
# "이 배포는 경로가 다르다" 를 기억할 일이 없다.
#
#   ~/.wikilens/vault   볼트 (읽기 전용으로 마운트해도 된다 — 서버는 읽기만 한다)
#   ~/.wikilens/state   궤적 로그·사용자 등록. **유일한 복구 불가 자산이라 영속 볼륨.**
#   ~/.wikilens/index   Lucene 색인. 지워도 재색인으로 복구된다.
#
# **`VOLUME` 선언은 일부러 안 한다.** 하면 마운트를 안 준 `docker run` 이 **익명 볼륨
# 셋을 조용히 만든다**(실측). 그렇게 만들어진 상태 디렉터리는 운영자가 모르는 채 쌓이고
# `docker rm -v` 로 같이 사라진다 — 유일한 복구 불가 자산에 그런 모호함을 두면 안 된다.
# 마운트는 `compose.yml` 이 명시한다.

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
