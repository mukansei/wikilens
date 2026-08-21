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
  docs/report-2026-08-21-learning-effect.md                     # 문서 제목·URL 가림
  server/src/test/kotlin/io/wikilens/index/Bm25LengthNormTest.kt  # 픽스처 비움
)

echo "master 를 가져옵니다…"
git checkout master -- .
echo "oss 전용 파일 ${#OSS_ONLY[@]}개를 되돌립니다:"
missing=()
for f in "${OSS_ONLY[@]}"; do
  # **oss 쪽 판이 아직 없는 파일**은 master 에서 새로 생긴 것이다. 그냥
  # `git checkout HEAD` 하면 `set -e` 에서 pathspec 오류로 죽는데, 그 메시지는
  # 무엇을 해야 하는지 말해주지 않는다 — 여기서 모아 뒤에 안내한다.
  if git cat-file -e "HEAD:$f" 2>/dev/null; then
    git checkout HEAD -- "$f"
    echo "  $f"
  else
    missing+=("$f")
    echo "  $f  ← oss 판이 아직 없음"
  fi
done

if [ ${#missing[@]} -gt 0 ]; then
  echo
  echo "다음 ${#missing[@]}개는 master 판이 그대로 들어와 있습니다. **가린 판을 만들고 커밋하세요.**" >&2
  for f in "${missing[@]}"; do echo "  $f" >&2; done
  echo "  계약이 이 상태를 잡습니다 — 문서 제목·조직 식별자가 남아 있으면 빨개집니다." >&2
  exit 1
fi

echo
if git diff --cached --quiet && git diff --quiet; then
  echo "반영할 변경이 없습니다."
  exit 0
fi
git status --short
echo
echo "다음: ./check.sh 로 확인한 뒤 커밋하세요."
echo "  계약이 조직 정보 유입과 커밋 저자를 검사합니다."
