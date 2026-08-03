#!/usr/bin/env bash
# 순수 로직 검증. Spring도 Lucene도 Gradle도 필요 없다.
# 알고리즘 핵심이 프레임워크 없이 컴파일·실행되는지 확인한다.
set -euo pipefail
KOTLINC="${KOTLINC:-kotlinc}"
command -v "$KOTLINC" >/dev/null || { echo "kotlinc가 필요합니다 (sdk install kotlin)"; exit 1; }
SRC=src/main/kotlin/dev/wikilens/learn
"$KOTLINC" -language-version 2.1 \
  "$SRC/Scoring.kt" "$SRC/TrajectoryStore.kt" verify/Verify.kt \
  -include-runtime -d build/verify.jar
java -Dfile.encoding=UTF-8 -Dstdout.encoding=UTF-8 -jar build/verify.jar
