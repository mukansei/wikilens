#!/usr/bin/env bash
#
# 버전을 한 번에 올린다. **정본은 `VERSION` 파일 하나이고 나머지는 사본이다.**
#
#   ./contract/bump-version.sh 0.19.0
#
# ### 왜 사본을 두나
#
# 다섯 다 자기 형식이 강제한다 — `pyproject.toml`·`plugin.json`×2·`build.gradle.kts`·`marketplace.json`.
# 생성으로 없앨 수 있는 것은 gradle 뿐이고, `plugin.json` 은 정적 JSON 이라 방법이
# 없다. 그래서 **사본을 두되 계약이 정본과 대조한다** — README 배지와 같은 방식이다.
#
# ### 내릴 수 없다
#
# 플러그인 설치본은 **버전별 캐시**로 복사되고 계약이 "설치 ≥ 소스" 를 본다.
# 낮추면 기존 설치본이 더 높은 버전으로 남아 재설치가 안 되고 계약이 빨개진다.
set -euo pipefail
cd "$(dirname "$0")/.."

NEW="${1:?사용법: $0 <새 버전>}"
OLD="$(cat VERSION)"

# **낮추기를 막는다.** 실수 한 번이 사용자 설치본을 못 고치는 상태로 만든다.
[ "$(printf '%s\n%s\n' "$OLD" "$NEW" | sort -V | tail -1)" = "$NEW" ] && [ "$OLD" != "$NEW" ] || {
  echo "✗ $OLD → $NEW 는 올라가는 방향이 아닙니다." >&2
  echo "  플러그인 버전은 못 내립니다 — 설치본 캐시가 버전별입니다." >&2
  exit 1
}

echo "$NEW" > VERSION
python3 - "$NEW" <<'PY'
import json, pathlib, re, sys
v = sys.argv[1]
p = pathlib.Path("cli/pyproject.toml"); s = p.read_text(encoding="utf-8")
p.write_text(re.sub(r'^version = ".*"$', f'version = "{v}"', s, count=1, flags=re.M), encoding="utf-8")
p = pathlib.Path("server/build.gradle.kts"); s = p.read_text(encoding="utf-8")
p.write_text(re.sub(r'^version = ".*"$', f'version = "{v}"', s, count=1, flags=re.M), encoding="utf-8")
for f in ("plugin/local/.claude-plugin/plugin.json", "plugin/client/.claude-plugin/plugin.json"):
    p = pathlib.Path(f); d = json.loads(p.read_text(encoding="utf-8"))
    d["version"] = v
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
# **마켓플레이스 매니페스트가 다섯째 사본이다.** 여기 버전이 `plugin.json` 과
# 어긋나면 계약이 빨개진다 — 실제로 첫 시도에서 이것을 빠뜨렸다.
p = pathlib.Path(".claude-plugin/marketplace.json"); d = json.loads(p.read_text(encoding="utf-8"))
for e in d["plugins"]:
    e["version"] = v
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"  {v} 로 다섯을 맞췄습니다")
PY
echo
echo "다음: ./check.sh 로 확인한 뒤 커밋하세요."
echo "  설치본까지 가려면 push → marketplace update → /plugin install 이 남습니다."
