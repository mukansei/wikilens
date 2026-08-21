#!/usr/bin/env bash
#
# master 의 변경을 공개판(oss)에 반영한다. **oss 워크트리에서 실행한다.**
#
# ### 왜 스크립트인가
#
# 두 판은 merge 로 잇지 못한다(공개 쪽 이력을 세탁해 공통 조상이 없다). 그래서
# 동기화가 "master 것을 가져온 뒤 oss 전용 파일만 되돌리기" 인데, **되돌릴 목록을
# 손으로 기억하는 한 빠뜨린다** — 2026-08-20 에 두 번 빠뜨렸고 둘 다 커밋까지 갔다
# (실험 기록의 익명화가 풀렸고, 계약 주석의 실제 제목+ID 가 되살아났다).
#
# 목록을 여기 한 곳에 둔다. 계약(`shared_contract.sh`)이 결과를 검사하지만 그것은
# **사후**이고, 이 스크립트는 사고 자체를 막는다.
set -euo pipefail
cd "$(dirname "$0")/.."

[ "$(git branch --show-current)" = "oss" ] || {
  echo "oss 브랜치에서 실행하세요 (지금: $(git branch --show-current))." >&2
  echo "  워크트리: cd ~/Desktop/wikilens-oss" >&2
  exit 1
}

# **이 목록이 정본이다.** 새 oss 전용 파일이 생기면 여기 더한다.
OSS_ONLY=(
  README.md                                                     # 마켓플레이스 URL
  bench/queries.py                                              # 실제 질의 → 템플릿
  docs/experiment-2026-08-14-answer.md                          # 문서 제목 가림
  server/src/test/kotlin/io/wikilens/index/Bm25LengthNormTest.kt  # 픽스처 비움
)

echo "master 를 가져옵니다…"
git checkout master -- .
echo "oss 전용 파일 ${#OSS_ONLY[@]}개를 되돌립니다:"
for f in "${OSS_ONLY[@]}"; do
  git checkout HEAD -- "$f"
  echo "  $f"
done

echo
if git diff --cached --quiet && git diff --quiet; then
  echo "반영할 변경이 없습니다."
  exit 0
fi
git status --short
echo
echo "다음: ./check.sh 로 확인한 뒤 커밋하세요."
echo "  계약이 조직 정보 유입과 커밋 저자를 검사합니다."
