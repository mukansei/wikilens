#!/usr/bin/env python3
"""
정답이 결과 **몇 위**에 오나. 그리고 틀렸을 때 **무엇을 대신 골랐나**.

    python3 eval/rank.py

**비용은 안 잰다.** 그건 `agent.py` 가 실제 세션에서 실제 토큰으로 재고, 여기서
흉내내면 시뮬레이션을 측정처럼 보이게 할 뿐이다(예전에 그렇게 만들었다가 두 하네스가
같은 표 모양으로 나와 4자리 차이 나는 지연을 비교로 읽게 됐다).

**A 원시 grep 은 순위가 없다 — 대신 "후보에 들어는 있나" 를 잰다.** 랭커가 없으니
몇 위인지는 물을 수 없지만, 그 문서가 grep 결과에 **포함되기는 하는지**는 같은 30개
질의로 잴 수 있다. 그것이 A 의 도달률이고, 함께 남기는 **후보 수**가 그 도달의 값을
말한다 — 정답이 400건 중 하나로 섞여 있으면 닿았다고 보기 어렵다.

싸고($0) 결정적이라 **랭킹을 건드릴 때마다 돌릴 자리**다. 오답 열이 진단을 낸다 —
G01 에서 서버가 정답(41KB) 대신 2KB·0KB·6KB 를 고르는 것이 그렇게 드러났고,
그것이 BM25 길이 정규화 가설의 근거가 됐다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
VAULT = pathlib.Path.home() / ".wikilens" / "vault"
#: **`agent.py` 와 같은 격리 서버를 본다.** 운영(:8787)을 보면 그쪽 궤적이 섞여
#: 두 하네스가 다른 조건의 C 를 재게 된다(실측: 운영에 궤적 4건이 있었다).
#: `setup.sh up` 이 띄운다.
SERVER = "http://127.0.0.1:8790"

sys.path.insert(0, str(HERE))
from harness import Writer, record  # noqa: E402
from queries import GROUPS  # noqa: E402

#: 모델이 질의에서 버리는 말. **없으면 로컬판에 불공정하다** — 구어체는 앞 두 낱말이
#: `화면에서`·`어떻게` 같은 군더더기라 그대로 패턴에 쓰면 못 찾는다(실측 4/30 → 6/30).
#: 스킬이 "여러 단어로 물으면 그대로 grep 하지 마세요" 라고 가르치는 단계다.
STOP = set("""어떻게 어디 어디서 어디에 어디로 어디지 뭐 무엇 무슨 있어 있나 있지 있는
있었는데 정리된 관련해서 관련 대해 대한 그 이 저 좀 해 봐야 보고싶어 보고싶은데 알고싶어
찾아줘 알 수 거 것 등 및 하는 하기로 했지 한다는 되는지 되는 타는지 쌓이는지 돌아가는지
나눠 쪼개는 들어가야 받지 보려면 언제 왜 방법 문서 자료 내용 목록 같은 이슈 사전 조사한
검토한 처리해 접수 그거 이거 지난 지금 이번 저번 이랑 관해""".split())

_PID = re.compile(r"/(\d{4,})\.md")


def picks(q: str) -> list[str]:
    ws = [w.strip("?,.·") for w in q.replace("?", " ").split()]
    return [w for w in ws if len(w) > 1 and w not in STOP][:2]


def local(q: str, gold: str) -> tuple[int, str, str]:
    """
    로컬판 스킬 절차: ALIASES → TREE → 본문. (순위, 단계, 오답).

    **낙관 편향이 있다 — 고칠 수 없으므로 드러낸다.** 여기서는 정답을 찾을 때까지
    단계를 내려가는데, 실제 에이전트는 **뭔가 찾으면 거기서 멈춘다.** 이 코퍼스에서
    `ALIASES.md` 에 결과는 있는데 정답이 없는 경우가 **7/30** 이고(실측), 그때 실제
    에이전트는 틀린 답을 내고 끝날 수 있다.

    에이전트가 언제 멈출지는 알 수 없으므로 흉내낼 수 없다. 대신 **어느 단계에서
    찾았는지**를 남긴다 — `BODY` 단계 적중은 그만큼 약한 증거다. 실사용은
    `agent.py` 가 잰다.
    """
    ts = picks(q)
    if not ts:
        return -1, "none", "none"
    pat = f"{ts[0]}.*{ts[1]}|{ts[1]}.*{ts[0]}" if len(ts) > 1 else ts[0]
    first = ""
    for stage, path, flags in (("ALIASES", VAULT / "ALIASES.md", ()),
                               ("TREE", VAULT / "TREE.md", ()),
                               ("BODY", VAULT / "mirror" / "pages", ("-l",))):
        out = subprocess.run(["rg", "-i", *flags, "--", pat, str(path)],
                             capture_output=True, text=True, errors="replace").stdout
        lines = [ln for ln in out.splitlines() if ln.strip()]
        if lines and not first:
            m = _PID.search(lines[0])
            first = m.group(1) if m else ""
        for i, ln in enumerate(lines, 1):
            if gold in ln:
                return i, stage, gold
    return -1, "none", first or "none"


def rawgrep(q: str, gold: str) -> tuple[int, str, str]:
    """
    원시 grep 의 도달 — **순위가 아니라 포함 여부**다.

    반환하는 `rank` 는 순위가 아니라 **후보 수**다(찾았을 때만, 못 찾으면 -1).
    리포트가 그것을 그대로 순위처럼 쓰면 안 되므로 `stage` 에 `GREP` 을 남겨
    구별한다 — 400건 중 하나로 섞인 것과 1위는 전혀 다른 상태다.
    """
    ts = picks(q) or [q.split()[0]]
    pat = f"{ts[0]}.*{ts[1]}|{ts[1]}.*{ts[0]}" if len(ts) > 1 else ts[0]
    out = subprocess.run(["rg", "-l", "-i", "--", pat, str(VAULT / "mirror" / "pages")],
                         capture_output=True, text=True, errors="replace").stdout.split()
    if not out:
        # 교집합이 0 이면 한 낱말로 후퇴한다 — 스킬이 가르치는 것과 같은 순서다.
        out = subprocess.run(["rg", "-l", "-i", "--", ts[0], str(VAULT / "mirror" / "pages")],
                             capture_output=True, text=True, errors="replace").stdout.split()
    for f in out:
        if gold in f:
            return len(out), "GREP", gold
    if not out:
        return -1, "GREP", "none"
    m = _PID.search(out[0])
    return -1, "GREP", (m.group(1) if m else "none")


def server(q: str, gold: str) -> tuple[int, str, str]:
    """
    서버 검색. **궤적을 안 남긴다** — `sessionId` 를 안 보내므로 이 측정 자체가
    학습을 오염시키지 않는다.
    """
    req = urllib.request.Request(
        SERVER + "/api/search",
        data=json.dumps({"query": q, "userKey": "eval", "limit": 20}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        ids = [h["pageId"] for h in json.loads(r.read())["hits"]]
    if gold in ids:
        return ids.index(gold) + 1, "search", gold
    return -1, "search", (ids[0] if ids else "none")


CASES = [("A 원시grep", rawgrep), ("B 로컬판", local), ("C 서버판", server)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", nargs="*", default=None)
    ap.add_argument("--out", default="rank.jsonl")
    a = ap.parse_args()

    groups = [g for g in GROUPS
              if a.groups is None or any(g[0].startswith(p) for p in a.groups)]

    # **도달 못 하면 여기서 멈춘다.** 그냥 두면 첫 질의에서 raw URLError 로 죽어
    # "서버를 안 띄웠다" 를 알기 어렵다.
    try:
        urllib.request.urlopen(SERVER + "/api/health", timeout=5).read()
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {SERVER} 에 못 닿는다 ({e}) — `eval/setup.sh up` 을 먼저 돌릴 것",
              file=sys.stderr)
        return 2

    out = HERE / "results" / a.out
    # **`--groups` 를 줬을 때 파일을 통째로 지우면 안 된다** — 다른 그룹의 앞선 측정이
    # 조용히 사라진다. 이번에 다시 잴 그룹의 줄만 걷어낸다.
    if out.exists():
        names = {g[0] for g in groups}
        kept = [ln for ln in out.read_text(encoding="utf-8").splitlines()
                if ln.strip() and json.loads(ln).get("group") not in names]
        out.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")

    with Writer(out) as w:
        for name, gold, _title, queries in groups:
            print(f"\n{name}  정답 {gold}")
            for qi, q in enumerate(queries):
                cells = []
                for cname, fn in CASES:
                    # **결정적이라 한 번만 잰다.** 반복해도 같은 값이고, 복제하면
                    # 3건이 9건처럼 보여 표본 수를 부풀린다.
                    r, stage, ans = fn(q, gold)
                    w.write(record(harness="rank", case=cname, group=name, qi=qi,
                                 query=q, gold=gold, rep=0, hit=(r > 0),
                                 rank=r, answer=ans, stage=stage))
                    cells.append(f"{cname} {'%2d위' % r if r > 0 else ' 밖 '}"
                                 + (f"({stage})" if stage not in ("search", "none") else ""))
                print(f"  q{qi} {q[:44]:46} " + " · ".join(cells))
    print(f"\n  결과 {out} — 표는 `python3 eval/report.py`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
