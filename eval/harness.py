"""
두 하네스가 공유하는 것 — 결과 스키마 · 이어받기 · 통계.

**출력 모양을 하나로 묶는 것이 이 파일의 요점이다.** 예전에는 `static.py` 와
`agent.py` 가 각자 다른 모양으로 찍어서 나란히 놓고 볼 수가 없었고, 결과를 손으로
문서에 옮기다 보니 질의를 바꾼 순간 그 문서가 조용히 낡았다(실제로 겪었다).
이제 둘 다 `Record` 로 내고 `report.py` 가 그것만 읽는다.
"""
from __future__ import annotations

import json
import pathlib
import statistics
from dataclasses import asdict, dataclass, field

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE / "results"


@dataclass
class Record:
    """한 측정 = 한 줄. 두 하네스가 같은 모양으로 낸다."""

    harness: str            # "static" | "agent"
    case: str               # "A 원시grep" | "B 로컬판" | "C 서버판"
    group: str              # "G01 …"
    qi: int                 # 그룹 안 몇 번째 변형(0~2)
    query: str
    gold: str
    rep: int                # 같은 조건의 몇 번째 반복

    hit: bool = False
    answer: str = ""        # 실제로 답한 것 — 틀렸을 때 무엇과 헷갈렸는지가 신호다
    rank: int = -1          # 정답이 결과 몇 위였나(정적 측정만). 못 찾으면 -1

    chars: int = 0          # 컨텍스트에 들어간 문자 수(정적)
    tokens: int = 0         # 실제 토큰(에이전트)
    cost: float = 0.0       # USD(에이전트)
    turns: int = 0
    calls: int = 0          # 도구 호출 수(정적)
    seconds: float = 0.0

    warmup: bool = False    # 참이면 통계에서 뺀다 — 아래 참고
    error: str = ""
    extra: dict = field(default_factory=dict)

    def key(self) -> tuple:
        """이어받기용 식별자. 이 조합이 이미 있으면 다시 안 돈다."""
        return (self.harness, self.case, self.group, self.qi, self.rep)


def load(path: pathlib.Path) -> list[Record]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out.append(Record(**d))
    return out


def done_keys(path: pathlib.Path) -> set[tuple]:
    """
    **이어받기.** 90세션이 세션당 $0.53 이라, 중간에 죽었을 때 처음부터 다시 도는 것은
    돈으로 물어야 하는 실수다. 이미 끝난 조합은 건너뛴다.
    """
    return {r.key() for r in load(path) if not r.error}


class Writer:
    """줄 단위 append. **매 줄 flush 한다** — 죽어도 거기까지는 남아야 한다."""

    def __init__(self, path: pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.fh = path.open("a", encoding="utf-8")

    def write(self, r: Record) -> None:
        self.fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        self.fh.flush()

    def close(self) -> None:
        self.fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def summarize(values: list[float]) -> dict:
    """
    **중앙값과 사분위폭으로 낸다 — 평균이 아니다.**

    파일럿에서 같은 조건이 96K~704K 토큰으로 **7배** 흔들렸다(케이스 간 차이는 11%).
    평균 하나를 적으면 그 폭이 사라져서, 없는 우열을 있다고 말하게 된다. IQR 을 함께
    내면 **두 케이스의 폭이 겹치는지**를 보고 판단할 수 있다.
    """
    if not values:
        return {"n": 0}
    vs = sorted(values)
    n = len(vs)
    q1 = vs[n // 4] if n >= 4 else vs[0]
    q3 = vs[(3 * n) // 4] if n >= 4 else vs[-1]
    return {"n": n, "median": statistics.median(vs), "min": vs[0], "max": vs[-1],
            "q1": q1, "q3": q3}


def overlaps(a: dict, b: dict) -> bool:
    """두 분포의 [q1,q3] 가 겹치면 **차이를 주장할 수 없다.**"""
    if not a.get("n") or not b.get("n"):
        return True
    return not (a["q3"] < b["q1"] or b["q3"] < a["q1"])
