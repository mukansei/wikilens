#!/usr/bin/env python3
"""
세 방식을 **각각 독립된 Claude Code 세션**으로 돌려 비교한다.

세션 분리가 이 벤치의 핵심이다 — 한 세션에서 세 방식을 다 하면 첫 방식에서 답을
알아버려 나머지가 무효가 된다. `claude -p` 는 매번 새 프로세스라 컨텍스트가 안 샌다.

    eval/setup.sh up
    python3 eval/agent.py --groups G01 G05 --reps 3 --budget 20
    eval/setup.sh down

**비싸다.** 파일럿이 세션당 평균 $0.53 이었다. `--budget` 이 상한이고, 넘으면 멈춘다.
중단돼도 `--resume`(기본)이 끝난 조합을 건너뛴다.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
NOHINT = HERE / "vault-nohint"
VAULT = pathlib.Path.home() / ".wikilens" / "vault"

#: `setup.sh` 가 띄우는 전용 서버. **운영(:8787)으로 돌리면 궤적이 오염된다** —
#: MCP 프록시는 항상 sessionId 를 보내므로 벤치 질의가 그대로 학습으로 쌓인다.
SERVER = "http://127.0.0.1:8790"

sys.path.insert(0, str(HERE))
from harness import Writer, done_keys, make  # noqa: E402
from queries import GROUPS  # noqa: E402

#: 셋 다 막는다 — 벤치가 재려는 것은 **볼트 검색**이지 웹 검색이나 위임이 아니다.
COMMON_DENY = "WebFetch,WebSearch,Task,Edit,Write,NotebookEdit"

ASK = ("찾은 문서의 페이지 ID(숫자)만 마지막 줄에 `ANSWER=<id>` 형식으로 답하세요. "
       "못 찾으면 `ANSWER=none`.")


def case_a(q: str) -> list[str]:
    """플러그인 없음 + **힌트 파일이 없는 볼트**(setup.sh 가 만든다)."""
    return ["claude", "-p",
            f"위키 볼트가 {NOHINT} 에 있습니다. 마크다운 문서 13,933개가 "
            f"mirror/pages/ 아래 샤딩돼 있습니다.\n\n질문: {q}\n\n{ASK}",
            "--output-format", "json", "--disallowed-tools", COMMON_DENY,
            "--add-dir", str(NOHINT)]


def case_b(q: str) -> list[str]:
    """로컬판 — 스킬 + 네이티브 grep. **MCP 가 아니다**(D8)."""
    return ["claude", "-p", f"질문: {q}\n\n{ASK}",
            "--output-format", "json", "--disallowed-tools", COMMON_DENY,
            "--plugin-dir", str(REPO / "plugin" / "local"), "--add-dir", str(VAULT)]


def case_c(q: str) -> list[str]:
    """서버판 — MCP 도구 4개."""
    return ["claude", "-p", f"질문: {q}\n\n{ASK}",
            "--output-format", "json", "--disallowed-tools", COMMON_DENY,
            "--plugin-dir", str(REPO / "plugin" / "client")]


CASES = [("A 원시grep", case_a), ("B 로컬판", case_b), ("C 서버판", case_c)]


def run_once(argv: list[str]) -> dict:
    env = dict(os.environ, WIKILENS_SERVER=SERVER, WIKILENS_USER="eval")
    t = time.perf_counter()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=900)
    except subprocess.TimeoutExpired:
        return {"error": "timeout 900s", "seconds": time.perf_counter() - t}
    wall = time.perf_counter() - t
    try:
        d = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"error": (p.stderr or p.stdout or "빈 출력")[:200], "seconds": wall}

    u = d.get("usage", {})
    text = str(d.get("result", ""))
    ans = ""
    for line in reversed(text.splitlines()):
        if "ANSWER=" in line:
            ans = line.split("ANSWER=")[-1].strip().strip("`.*_ ")
            break
    return {
        "answer": ans,
        # 캐시 읽기까지 더한다 — 그것도 청구되고, 캐시가 잘 붙었는지가 케이스마다 다르다.
        "tokens": (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                   + u.get("cache_read_input_tokens", 0)),
        "cost": d.get("total_cost_usd", 0.0),
        "turns": d.get("num_turns", 0),
        "seconds": wall,
        "error": "" if not d.get("is_error") else str(d.get("result", ""))[:120],
        "extra": {"out_tokens": u.get("output_tokens", 0),
                  "cache_read": u.get("cache_read_input_tokens", 0)},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", nargs="*", default=None, help="예: G01 G05 (기본 전체)")
    ap.add_argument("--reps", type=int, default=3,
                    help="같은 조건 반복. **1 이면 통계를 못 낸다** — 변동이 7배다")
    ap.add_argument("--budget", type=float, default=20.0, help="USD 상한. 넘으면 멈춘다")
    ap.add_argument("--warmup", action="store_true",
                    help="케이스마다 버리는 1회를 먼저 돈다(MCP 첫 호출 오버헤드 분리)")
    ap.add_argument("--out", default="agent.jsonl")
    ap.add_argument("--no-resume", action="store_true")
    a = ap.parse_args()

    out = HERE / "results" / a.out
    done = set() if a.no_resume else done_keys(out)
    if done:
        print(f"  이어받기: 끝난 조합 {len(done)}개 건너뜀")

    groups = [g for g in GROUPS
              if a.groups is None or any(g[0].startswith(p) for p in a.groups)]
    if not groups:
        print("  해당 그룹 없음", file=sys.stderr)
        return 2

    total = len(groups) * 3 * len(CASES) * a.reps
    print(f"  대상 {len(groups)}그룹 × 3질의 × {len(CASES)}케이스 × {a.reps}회 = {total}세션")
    print(f"  예산 ${a.budget:.0f} (세션당 파일럿 평균 $0.53 → 예상 ${total*0.53:.0f})\n")

    spent = 0.0
    with Writer(out) as w:
        if a.warmup:
            # **첫 호출만 비싼 경우를 분리한다.** 파일럿에서 C 의 첫 질의가 603K 토큰,
            # 이후 183K·259K 였다. MCP 서버 기동이 첫 세션에만 붙는 것으로 보이는데
            # 확정된 것은 아니다 — 버리는 1회를 따로 기록해 두면 나중에 확인할 수 있다.
            g0 = groups[0]
            for cname, builder in CASES:
                # **워밍도 예산을 쓴다.** 안 보면 `--budget 0.9` 인데 워밍만 $1.41 을
                # 쓰고 본측정이 0건이 된다(실측). 돈을 쓰는 모든 자리가 같은 가드를
                # 지나야 한다.
                if spent >= a.budget:
                    print(f"  ★ 예산 ${a.budget:.2f} 도달 — 워밍 중 멈춘다")
                    return 0
                r = run_once(builder(g0[3][0]))
                spent += r.get("cost", 0.0)
                w.write(make(harness="agent", case=cname, group=g0[0], qi=0,
                             query=g0[3][0], gold=g0[1], rep=-1, warmup=True,
                             hit=(r.get("answer") == g0[1]), **r))
                print(f"  [워밍] {cname:10} ${r.get('cost',0):.3f}")
            print()

        for rep in range(a.reps):
            for name, gold, _title, queries in groups:
                for qi, q in enumerate(queries):
                    for cname, builder in CASES:
                        k = ("agent", cname, name, qi, rep)
                        if k in done:
                            continue
                        if spent >= a.budget:
                            print(f"\n  ★ 예산 ${a.budget:.0f} 도달 — 여기서 멈춘다 "
                                  f"(이어받으려면 같은 명령을 다시)")
                            return 0
                        r = run_once(builder(q))
                        spent += r.get("cost", 0.0)
                        rec = make(harness="agent", case=cname, group=name, qi=qi,
                                   query=q, gold=gold, rep=rep,
                                   hit=(r.get("answer") == gold), **r)
                        w.write(rec)
                        print(f"  {name[:3]} q{qi} r{rep} {cname:10} "
                              f"{'○' if rec.hit else '✗'} {rec.tokens:>8,}tok · "
                              f"{rec.turns:>2}턴 · ${rec.cost:.3f} · {rec.seconds:>5.1f}s "
                              f"→ {rec.answer or rec.error[:28]}   [누적 ${spent:.2f}]")
    print(f"\n  총 ${spent:.2f} · 결과 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
