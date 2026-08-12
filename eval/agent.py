#!/usr/bin/env python3
"""
세 방식을 **각각 독립된 Claude Code 세션**으로 돌려 비교한다.

실행 전에 `eval/setup.sh up` 이 필요하다 — 격리 볼트와 전용 서버를 만든다.

세션 분리가 이 실험의 핵심이다 — 한 세션에서 세 방식을 다 하면 첫 방식에서 답을
알아버려 나머지가 무효가 된다. `claude -p` 는 매번 새 프로세스라 컨텍스트가 안 샌다.

케이스 셋:

  A 원시 grep   플러그인 없음. **ALIASES.md·TREE.md 가 아예 없는 볼트**를 준다
                (프롬프트로 금지하면 모델이 어길 수 있어 파일 자체를 뺐다)
  B 로컬판      `--plugin-dir plugin/local` — 스킬 + 네이티브 grep.
                **MCP 가 아니다**(D8: 검색 경로 런타임 의존성 0)
  C 서버판      `--plugin-dir plugin/client` — MCP 도구 4개

측정: 실제 토큰(`usage`) · 비용(`total_cost_usd`) · 턴 수 · 소요 · 정답 도달 여부.
"""
from __future__ import annotations

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
SERVER = "http://127.0.0.1:8790"      # `setup.sh` 가 띄우는 전용 서버.
                                      # **운영(:8787)으로 돌리면 궤적이 오염된다.**

sys.path.insert(0, str(HERE))
from queries import GROUPS  # noqa: E402

COMMON_DENY = "WebFetch,WebSearch,Task,Edit,Write,NotebookEdit"


def case_a(q: str) -> list[str]:
    """플러그인 없음 + 힌트 파일 없는 볼트."""
    return [
        "claude", "-p",
        f"위키 볼트가 {NOHINT} 에 있습니다. 마크다운 문서 13,933개가 "
        f"mirror/pages/ 아래 샤딩돼 있습니다.\n\n"
        f"질문: {q}\n\n"
        f"가장 알맞은 문서 하나를 찾아 그 파일명의 페이지 ID(숫자)만 마지막 줄에 "
        f"`ANSWER=<id>` 형식으로 답하세요. 못 찾으면 `ANSWER=none`.",
        "--output-format", "json",
        "--disallowed-tools", COMMON_DENY,
        "--add-dir", str(NOHINT),
    ]


def case_b(q: str) -> list[str]:
    """로컬판 플러그인 — 스킬이 ALIASES.md 부터 훑게 한다."""
    return [
        "claude", "-p",
        f"질문: {q}\n\n"
        f"찾은 문서의 페이지 ID(숫자)만 마지막 줄에 `ANSWER=<id>` 형식으로 답하세요. "
        f"못 찾으면 `ANSWER=none`.",
        "--output-format", "json",
        "--disallowed-tools", COMMON_DENY,
        "--plugin-dir", str(REPO / "plugin" / "local"),
        "--add-dir", str(VAULT),
    ]


def case_c(q: str) -> list[str]:
    """서버판 플러그인 — MCP 도구 4개."""
    return [
        "claude", "-p",
        f"질문: {q}\n\n"
        f"찾은 문서의 페이지 ID(숫자)만 마지막 줄에 `ANSWER=<id>` 형식으로 답하세요. "
        f"못 찾으면 `ANSWER=none`.",
        "--output-format", "json",
        "--disallowed-tools", COMMON_DENY,
        "--plugin-dir", str(REPO / "plugin" / "client"),
    ]


CASES = [("A 원시grep", case_a), ("B 로컬판", case_b), ("C 서버판", case_c)]


def run(argv: list[str]) -> dict:
    env = dict(os.environ, WIKILENS_SERVER=SERVER, WIKILENS_USER="bench3")
    t = time.perf_counter()
    p = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=600)
    wall = time.perf_counter() - t
    try:
        d = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"error": (p.stderr or p.stdout)[:200], "wall": wall}
    u = d.get("usage", {})
    text = str(d.get("result", ""))
    ans = ""
    for line in reversed(text.splitlines()):
        if "ANSWER=" in line:
            ans = line.split("ANSWER=")[-1].strip().strip("`.*_ ")
            break
    return {
        "answer": ans,
        "in": u.get("input_tokens", 0),
        "out": u.get("output_tokens", 0),
        "cache_w": u.get("cache_creation_input_tokens", 0),
        "cache_r": u.get("cache_read_input_tokens", 0),
        "cost": d.get("total_cost_usd", 0.0),
        "turns": d.get("num_turns", 0),
        "wall": wall,
        "err": d.get("is_error", False),
    }


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    groups = [g for g in GROUPS if only is None or g[0].startswith(only)]
    out_path = HERE / "results" / "agent.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    with out_path.open("a", encoding="utf-8") as fh:
        for name, gold, title, queries in groups:
            print(f"\n{name}  정답 {gold}")
            for qi, q in enumerate(queries):
                for cname, builder in CASES:
                    r = run(builder(q))
                    hit = r.get("answer") == gold
                    rec = {"group": name, "gold": gold, "qi": qi, "q": q,
                           "case": cname, "hit": hit, **r}
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    tok = r.get("in", 0) + r.get("cache_w", 0) + r.get("cache_r", 0)
                    print(f"    {cname:10} {'○' if hit else '✗'} "
                          f"{tok:>8,}tok · {r.get('turns',0):>2}턴 · "
                          f"${r.get('cost',0):.3f} · {r.get('wall',0):>5.1f}s "
                          f"→ {r.get('answer','') or r.get('error','')[:30]}")


if __name__ == "__main__":
    main()
