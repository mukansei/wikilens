#!/usr/bin/env python3
"""
세 방식을 **각각 독립된 Claude Code 세션**으로 돌려 비교한다.

세션 분리가 이 벤치의 핵심이다 — 한 세션에서 세 방식을 다 하면 첫 방식에서 답을
알아버려 나머지가 무효가 된다. `claude -p` 는 매번 새 프로세스라 컨텍스트가 안 샌다.

    bench/setup.sh up
    python3 bench/agent.py          # 기본이 최소 집합 — 9세션 약 $5
    bench/setup.sh down

**비싸다.** 세션당 $0.5~1.6 이라 기본값을 최소로 잡아 뒀다 — `MINIMAL` 세 그룹의
q0 만, 반복 없이 9세션이다. 전량(90세션)은 손으로 켜야 한다:

    python3 bench/agent.py --groups G01 G02 … G10 --per-group 0 --reps 3

**순위는 여기서 재지 말 것.** `rank.py` 가 30질의 전량을 $0 에 재고, 세션이 유일하게
주는 것은 **에이전트가 실제로 얼마나 일하나**다. 그건 세 그룹이면 모양이 다 나온다.

`--budget` 이 상한이고 넘으면 멈춘다. 중단돼도 `--resume`(기본)이 끝난 조합을 건너뛴다.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
NOHINT = HERE / "vault-nohint"
VAULT = pathlib.Path.home() / ".wikilens" / "vault"

sys.path.insert(0, str(HERE))
# 주소·사용자는 `harness` 가 정본이다 — 각자 들면 세 하네스가 다른 서버를 잰다.
from harness import (BENCH_USER, COMMIT, DIRTY, SERVER, Writer,  # noqa: E402
                     done_keys, record, require_server, select_groups,
                     trajectory_count)
from queries import GROUPS, MINIMAL  # noqa: E402

#: 벤치가 재려는 것은 **볼트 검색**이지 웹 검색이나 위임이 아니다.
#:
#: **케이스 격리는 여기가 아니라 `setup.sh` 가 한다.** 처음에는 도구 이름으로 막으려
#: 했는데(`mcp__…` 목록 + `Skill`) 둘 다 나쁘다: 이름은 플러그인을 고치면 낡고,
#: `Skill` 통째 차단은 B·C 가 자기 스킬을 못 쓰게 만든다. `Skill(이름)` 형식은
#: 아예 안 먹는다(실측). 설치본을 내리고 `--plugin-dir` 만 남기는 것이 유일하게
#: 깨끗하다 — 그러면 각 세션이 자기 플러그인만 본다(실측 확인).
DENY = "WebFetch,WebSearch,Task,Edit,Write,NotebookEdit"


#: MCP 프록시가 `search` 결과 첫 줄에 찍는 `학습 힌트 N`. 형식이 바뀌면 여기도 바꿔야
#: 하는데, 안 바꾸면 **힌트를 0 으로 읽어 warm 측정이 통째로 제외된다** — 조용하지 않게
#: `report.py` 가 "힌트를 받은 측정이 없다" 로 지목한다.
_HINTS = re.compile(r"학습 힌트 (\d+)")

ASK = ("찾은 문서의 페이지 ID(숫자)만 마지막 줄에 `ANSWER=<id>` 형식으로 답하세요. "
       "못 찾으면 `ANSWER=none`.")


def case_a(q: str) -> list[str]:
    """플러그인 없음 + **힌트 파일이 없는 볼트**(setup.sh 가 만든다)."""
    return ["claude", "-p",
            f"위키 볼트가 {NOHINT} 에 있습니다. 마크다운 문서 13,933개가 "
            f"mirror/pages/ 아래 샤딩돼 있습니다.\n\n질문: {q}\n\n{ASK}",
            "--output-format", "stream-json", "--verbose",
            "--disallowed-tools", DENY,
            "--add-dir", str(NOHINT)]


def case_b(q: str) -> list[str]:
    """로컬판 — 스킬 + 네이티브 grep. **MCP 가 아니다**(D8)."""
    return ["claude", "-p", f"질문: {q}\n\n{ASK}",
            "--output-format", "stream-json", "--verbose",
            "--disallowed-tools", DENY,
            "--plugin-dir", str(REPO / "plugin" / "local"), "--add-dir", str(VAULT)]


def case_c(q: str) -> list[str]:
    """서버판 — MCP 도구 4개."""
    return ["claude", "-p", f"질문: {q}\n\n{ASK}",
            "--output-format", "stream-json", "--verbose",
            "--disallowed-tools", DENY,
            "--plugin-dir", str(REPO / "plugin" / "client")]


CASES = [("A 원시grep", case_a), ("B 로컬판", case_b), ("C 서버판", case_c)]


def server_image() -> str:
    """
    C 케이스 서버가 **실제로 도는 이미지**. 소스 커밋만으로는 부족하다 — 고치고 다시
    안 지으면 옛 이미지가 돈다(이 저장소에서 실제로 겪었다). 결과에 남겨야 나중에
    "그 측정은 어느 서버였나" 를 답할 수 있다.
    """
    try:
        out = subprocess.run(
            ["docker", "inspect", "wikilens-bench", "--format", "{{.Image}}"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        return out.replace("sha256:", "")[:12] or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def run_once(argv: list[str]) -> dict:
    env = dict(os.environ, WIKILENS_SERVER=SERVER, WIKILENS_USER=BENCH_USER)
    t = time.perf_counter()
    # **프로세스 그룹으로 띄운다.** `subprocess.run(timeout=)` 은 `claude` 만 죽이고
    # 그 자식(MCP 프록시 등)은 남긴다(실측: 타임아웃 뒤 자식 2개 잔존). 90세션에서
    # 몇 번만 나도 프록시가 쌓여 다음 세션의 측정에 섞인다.
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        # 그룹 전체에 신호를 보낸다 — 자식까지 정리된다.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.communicate()
        return {"error": "timeout 900s", "seconds": time.perf_counter() - t}
    wall = time.perf_counter() - t
    # **도구 호출을 세려면 스트림을 봐야 한다.** 최종 JSON 에는 `num_turns` 밖에 없고
    # 그것은 도구 호출 수가 아니다(실측: 도구 2회인데 num_turns 는 3). 무엇보다
    # **어느 도구를 썼는지**가 격리 검증이다 — C 가 MCP 를 정말 쓰는지, A 가 힌트
    # 파일을 안 보는지는 도구 이름으로만 확인된다.
    tools: list[str] = []
    hints = 0
    d = None
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    tools.append(c.get("name", "?"))
        elif ev.get("type") == "user":
            # **이 세션이 힌트를 실제로 받았나.** warm 이라고 다 받는 것이 아니다 —
            # 학습된 질의에서만 서빙된다. 그 구별 없이 cold↔warm 을 비교하면 학습이
            # 안 닿은 그룹의 잡음이 중앙값을 지배한다(실측: G04 는 -3% 인데 학습이
            # 없는 G01·G09 가 +81%·+63% 라 전체가 +63% 로 나왔다).
            #
            # 서버 응답을 직접 못 보므로 MCP 프록시가 찍는 문장에서 읽는다 —
            # `search` 도구의 첫 줄이 `N건 (어휘 후보 X · 학습 힌트 Y)` 다.
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") != "tool_result":
                    continue
                body = c.get("content")
                text = body if isinstance(body, str) else "".join(
                    b.get("text", "") for b in body or [] if isinstance(b, dict))
                m = _HINTS.search(text)
                if m:
                    hints += int(m.group(1))
        elif ev.get("type") == "result":
            d = ev
    if d is None:
        return {"error": (err or out or "빈 출력")[:200], "seconds": wall}

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
        # **비용의 최대 변수.** 모델이 바뀌면 토큰·턴이 통째로 달라져 비교가 무효다.
        "model": ",".join(sorted(d.get("modelUsage", {}))) or "unknown",
        "seconds": wall,
        "error": "" if not d.get("is_error") else str(d.get("result", ""))[:120],
        # **질의 연산 수와 그 종류.** 왕복 하나하나가 지연과 토큰을 함께 쓰므로
        # 이것이 비용의 실제 동인이다. 이름까지 남기는 것은 격리 검증용이다.
        "calls": len(tools),
        # **이 세션이 받은 학습 힌트 총합.** 0 이면 warm 이어도 학습이 안 닿은 것이다.
        "hints": hints,
        "extra": {"out_tokens": u.get("output_tokens", 0),
                  "cache_read": u.get("cache_read_input_tokens", 0),
                  "tools": tools},
    }


def plan(groups: list, reps: int, pattern: str, per_group: int = 0) -> list[tuple]:
    """
    측정 순서를 만든다. **warm 실험에서 이 순서가 곧 가설이다.**

    학습 발동 조건을 먼저 계산해 뒀다(2026-08-12, 이 코퍼스):

      - 정답의 **사전확률이 전부 상한 0.85 에 붙는다.** BM25 점수가 13% 폭으로
        압축돼 있어 `score/top` 정규화가 변별을 못 한다 — 10위 문서도 0.85 다.
        그래서 `ebLower(1,0,0.85)=0.62` 로 **1회 관측이면 문턱(0.45)을 넘는다.**
      - 다만 서빙되려면 `rel = 0.62 × c >= 0.45`, 즉 **커버리지 c >= 0.73** 이다.
      - 그룹 안 세 표현의 **항 겹침이 0~3개**(질의 항은 4~8개)라 c 가 0.17~0.43 이다.

    **예측: 정확 반복만 발동하고 표현 전이는 안 된다.** 세 순서가 그것을 가른다.

      spread    회차마다 전 질의를 한 번씩 — 현재 기본. 섞여서 원인 분리가 안 된다
      repeat    같은 질의를 연속 N회 — **c=1.0** 이라 2회차부터 힌트가 서빙돼야 한다
      transfer  q0 를 N-1회 학습시킨 뒤 q1·q2 를 한 번씩 — **c 가 낮아 안 될 것이다**

    transfer 가 예측을 깨고 성공하면 그것대로 중요하다(항이 적은 질의는 겹침 하나가
    c 를 크게 올린다 — G08 은 q0 항이 4개뿐이다).
    """
    # **`transfer` 만은 자르지 않는다** — q0 로 학습해 q1·q2 로 시험하는 것이 그 순서의
    # 정의라, 질의를 줄이면 재려던 것 자체가 사라진다.
    def qs(g):
        n = len(g[3])
        return range(n if (per_group <= 0 or pattern == "transfer") else min(per_group, n))

    out = []
    if pattern == "spread":
        for rep in range(reps):
            for g in groups:
                for qi in qs(g):
                    out.append((g, qi, rep))
    elif pattern == "repeat":
        for g in groups:
            for qi in qs(g):
                for rep in range(reps):
                    out.append((g, qi, rep))
    else:  # transfer
        for g in groups:
            for rep in range(reps - 1):      # q0 로 학습
                out.append((g, 0, rep))
            for qi in (1, 2):                # 다른 표현으로 시험
                out.append((g, qi, reps - 1))
    return out


def warmup(w: Writer, g0: tuple, mode: str, budget: float) -> float:
    """
    케이스마다 **버리는 1회**를 먼저 돈다. 쓴 비용을 반환하고, 예산에 걸리면 음수.

    **첫 호출만 비싼 경우를 분리한다.** 파일럿에서 C 의 첫 질의가 603K 토큰, 이후
    183K·259K 였다. MCP 서버 기동이 첫 세션에만 붙는 것으로 보이는데 확정된 것은
    아니다 — 버리는 1회를 따로 기록해 두면 나중에 확인할 수 있다.
    """
    spent = 0.0
    for cname, builder in CASES:
        # **워밍도 예산을 쓴다.** 안 보면 `--budget 0.9` 인데 워밍만 $1.41 을 쓰고
        # 본측정이 0건이 된다(실측). 돈을 쓰는 모든 자리가 같은 가드를 지나야 한다.
        if spent >= budget:
            print(f"  ★ 예산 ${budget:.2f} 도달 — 워밍 중 멈춘다")
            return -1.0
        r = run_once(builder(g0[3][0]))
        spent += r.get("cost", 0.0)
        w.write(record(harness="agent", case=cname, group=g0[0], qi=0,
                       query=g0[3][0], gold=g0[1], rep=-1, warmup=True,
                       mode=mode, hit=(r.get("answer") == g0[1]), **r))
        print(f"  [워밍] {cname:10} ${r.get('cost',0):.3f}")
    print()
    return spent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", nargs="*", default=list(MINIMAL),
                    help=f"기본 {' '.join(MINIMAL)} (모양이 다른 셋). 전량은 --groups G01 … G10")
    ap.add_argument("--per-group", type=int, default=1,
                    help="그룹당 질의 수. 0 이면 전부. transfer 는 무시한다")
    ap.add_argument("--reps", type=int, default=1,
                    help="같은 조건 반복. **1 이면 통계를 못 낸다** — 변동이 7배다")
    ap.add_argument("--budget", type=float, default=10.0, help="USD 상한. 넘으면 멈춘다")
    ap.add_argument("--pattern", choices=["spread", "repeat", "transfer"],
                    default="spread",
                    help="질의 순서. warm 실험에서 무엇을 재는지가 이것으로 갈린다")
    ap.add_argument("--warmup", action="store_true",
                    help="케이스마다 버리는 1회를 먼저 돈다(MCP 첫 호출 오버헤드 분리)")
    ap.add_argument("--out", default="agent.jsonl")
    ap.add_argument("--no-resume", action="store_true")
    a = ap.parse_args()

    out = HERE / "results" / a.out
    done = set() if a.no_resume else done_keys(out)
    if done:
        print(f"  이어받기: 끝난 조합 {len(done)}개 건너뜀")

    groups = select_groups(GROUPS, a.groups)
    if not groups:
        print("  해당 그룹 없음", file=sys.stderr)
        return 2
    # **transfer 는 학습 단계가 있어야 성립한다.** `--reps 1` 이면 q0 를 0회 학습하고
    # q1·q2 를 재는데, 그것은 그냥 cold 측정이지 전이 실험이 아니다. 조용히 돌면
    # 결과 파일에 `pattern=transfer` 로 남아 나중에 전이를 잰 것처럼 읽힌다.
    if a.pattern == "transfer" and a.reps < 2:
        print("  ✗ --pattern transfer 는 --reps 2 이상이 필요하다 "
              f"(지금 {a.reps} → 학습 0회)", file=sys.stderr)
        return 2

    total = len(plan(groups, a.reps, a.pattern, a.per_group)) * len(CASES)
    print(f"  대상 {len(groups)}그룹 · {a.pattern} 순서 · {len(CASES)}케이스 = {total}세션")
    # **서버가 없으면 시작하지 않는다.** 그냥 두면 C 세션이 전부 실패하면서
    # 세션당 $0.53 을 태운다 — 30세션이면 $16 을 버리고 나서야 안다.
    # 이쪽만 플러그인 격리가 필요하므로 `up` 을 안내한다.
    bad = require_server(need_plugins=True)
    if bad:
        print(f"  ✗ {bad}", file=sys.stderr)
        return 2
    t0 = trajectory_count()
    mode = "warm" if t0 > 0 else "cold"
    print(f"  예산 ${a.budget:.0f} (세션당 파일럿 평균 $0.53 → 예상 ${total*0.53:.0f})")
    print(f"  서버 학습량 {t0}건 → **{mode}** 실험"
          + ("  ← C 만 이전 회차를 물려받는다(비대칭)" if mode == "warm" else "") + "\n")

    image = server_image()
    print(f"  형상: {COMMIT}{' (더러운 트리 — 재현 불가)' if DIRTY else ''}"
          f" · 서버 이미지 {image}\n")

    spent = 0.0
    with Writer(out) as w:
        if a.warmup:
            spent = warmup(w, groups[0], mode, a.budget)
            if spent < 0:                      # 워밍 중 예산 도달
                return 0

        for g, qi, rep in plan(groups, a.reps, a.pattern, a.per_group):
            name, gold, _title, queries = g
            q = queries[qi]
            for cname, builder in CASES:
                k = ("agent", cname, name, qi, rep, mode)
                if k in done:
                    continue
                if spent >= a.budget:
                    print(f"\n  ★ 예산 ${a.budget:.0f} 도달 — 여기서 멈춘다 "
                          f"(이어받으려면 같은 명령을 다시)")
                    return 0
                before = trajectory_count() if cname.startswith("C") else -1
                r = run_once(builder(q))
                spent += r.get("cost", 0.0)
                rec = record(harness="agent", case=cname, group=name, qi=qi,
                           query=q, gold=gold, rep=rep, mode=mode,
                           hit=(r.get("answer") == gold),
                           # **이 측정 시점에 서버가 들고 있던 학습량.**
                           # warm 에서 회차가 갈수록 늘고, cold 면 0 근처다.
                           trajectories=before, pattern=a.pattern,
                           server_image=image, **r)
                w.write(rec)
                print(f"  {name[:3]} q{qi} r{rep} {cname:10} "
                      f"{'○' if rec.hit else '✗'} {rec.tokens:>8,}tok · "
                      f"{rec.turns:>2}턴 · ${rec.cost:.3f} · {rec.seconds:>5.1f}s "
                      f"→ {rec.answer or rec.error[:28]}   [누적 ${spent:.2f}]")
    print(f"\n  총 ${spent:.2f} · 결과 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
