"""오답을 받은 세션이 그 뒤에 몇 개를 더 읽는가 — D2 의 `1+n` 가정을 잰다.

    python3 bench/cost_model.py bench/results/cost-model.jsonl

## 왜

손익분기 `p_hit(n−1) > p_wrong` (D2)는 **오답이면 `1+n` 읽기**를 전제한다. `n` 은 기본
`limit=8` 이고, 이 값은 잰 적이 없다. `pWrong` 이 게이트가 아니라는 것이 확인되면서
(`pWrong < (n−1)/n = 0.875`) 판정의 무게가 이 가정으로 옮겨왔다 —
`docs/design/design-2026-08-28-1x-gate.md`.

## 어떻게

`agent.py` 가 `extra.tools` 와 `extra.args` 에 도구 호출을 **순서대로** 남긴다.
세션마다 이렇게 읽는다:

    reads          read 호출의 pageId 순서
    gold_pos       정답을 읽은 위치(0-based). 없으면 못 찾은 세션
    wrong_first    첫 읽기가 오답인가
    extra_reads    첫 오답 뒤에 더 읽은 개수  ← 이것이 D2 의 `n` 에 해당
    searches_after 첫 오답 뒤의 search 재호출 수

**`extra_reads` 의 분포가 답이다.** `n=8` 근처면 D2 가 맞고, 훨씬 작으면 모델을 다시
써야 한다.

## 못 재는 것

- **정답이 하나라고 본다.** `gold` 가 한 페이지라 여러 문서에 걸친 답은 "오답 읽기" 로
  잘못 센다. 그 세션은 `multi_gold_risk` 로 따로 표시한다(참고용).
- **읽기와 재질의의 비용이 같다고 안 본다.** 둘을 따로 세고 합치지 않는다 — D2 의
  모델에는 재질의 항이 아예 없어서, 그것이 지배적이면 모델 자체를 다시 봐야 한다.
"""
import collections
import json
import pathlib
import statistics
import sys

READ_TOOLS = ("read",)
SEARCH_TOOLS = ("search",)


def _tool(name: str) -> str:
    """`mcp__plugin_wikilens-client_librarian__read` → `read`."""
    return name.rsplit("__", 1)[-1].lower()


def analyze(row: dict) -> dict | None:
    extra = row.get("extra") or {}
    tools, args = extra.get("tools") or [], extra.get("args") or []
    if not tools or len(tools) != len(args):
        return None

    gold = str(row.get("gold", ""))
    reads: list[str] = []
    first_wrong_at = None          # 첫 오답 read 의 호출 색인
    searches_after = 0

    for i, (t, a) in enumerate(zip(tools, args)):
        n = _tool(t)
        if n in READ_TOOLS:
            pid = str(a.get("pageId", ""))
            reads.append(pid)
            if first_wrong_at is None and pid != gold:
                first_wrong_at = i
        elif n in SEARCH_TOOLS and first_wrong_at is not None:
            searches_after += 1

    gold_pos = reads.index(gold) if gold in reads else None
    return {
        "group": row.get("group"), "rep": row.get("rep"),
        "reads": len(reads),
        "gold_pos": gold_pos,
        "found": gold_pos is not None,
        "wrong_first": bool(reads) and reads[0] != gold,
        # **첫 오답 뒤 추가 읽기.** D2 의 `n` 과 견줄 값이다.
        "extra_reads": (len(reads) - 1 - reads.index(reads[0])) if (reads and reads[0] != gold) else None,
        "searches_after": searches_after,
        "turns": row.get("turns"), "calls": row.get("calls"),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    rows = [json.loads(l) for l in pathlib.Path(sys.argv[1]).read_text().splitlines() if l.strip()]
    got = [a for a in (analyze(r) for r in rows) if a]

    if not got:
        print("도구 기록이 있는 세션이 없다 — extra.tools 가 비었나?", file=sys.stderr)
        return 1

    print(f"세션 {len(got)} (파일 {len(rows)}행)")
    print(f"  정답 도달   {sum(a['found'] for a in got)}/{len(got)}")
    print(f"  첫 읽기가 오답 {sum(a['wrong_first'] for a in got)}")
    print()

    extra = [a["extra_reads"] for a in got if a["extra_reads"] is not None]
    if extra:
        print("D2 의 `1+n` 에서 n 에 해당하는 값 — 첫 오답 뒤 추가 읽기")
        print(f"  n = {len(extra)} · 중앙값 {statistics.median(extra):.1f} · 평균 {statistics.mean(extra):.2f}")
        print(f"  분포 {dict(sorted(collections.Counter(extra).items()))}")
        print(f"  **D2 가정은 8** — 실측 중앙값이 그보다 훨씬 작으면 문턱을 다시 유도한다")
    else:
        print("첫 읽기가 오답인 세션이 없다 — 이 코퍼스·질의에서는 잴 수 없다")

    print()
    sa = [a["searches_after"] for a in got if a["wrong_first"]]
    if sa:
        print("오답 뒤 재질의 (D2 모델에 없는 항)")
        print(f"  중앙값 {statistics.median(sa):.1f} · 0 이 아닌 세션 {sum(1 for x in sa if x)}/{len(sa)}")

    print()
    print("정답을 읽은 위치 (0 = 첫 읽기에 바로)")
    pos = [a["gold_pos"] for a in got if a["found"]]
    if pos:
        print(f"  분포 {dict(sorted(collections.Counter(pos).items()))} · 중앙값 {statistics.median(pos):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
