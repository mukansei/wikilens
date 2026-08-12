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
import subprocess
from datetime import datetime
import statistics
from dataclasses import asdict, dataclass, field, fields

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE / "results"


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(HERE.parent), *args],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def provenance() -> tuple[str, bool]:
    """
    **어느 형상에서 잰 값인가.** 결과만 남고 코드가 바뀌면 그 수치는 복원 불가가 된다 —
    이 저장소가 `13,921건 2.44초` 로 한 번 겪은 일이다(`CLAUDE.md` 의 grep 예산 항목).

    태그가 붙어 있으면 태그를, 아니면 짧은 커밋을 쓴다. **더러운 트리는 표시한다** —
    커밋 해시만 남기면 "그 커밋에서 나온 값" 처럼 읽히는데 사실이 아니다.
    """
    tag = _git("describe", "--tags", "--exact-match")
    ref = tag or _git("rev-parse", "--short", "HEAD") or "unknown"
    dirty = bool(_git("status", "--porcelain"))
    return ref, dirty


#: 프로세스당 한 번만 푼다 — 측정마다 `git` 을 부르면 그것이 측정에 섞인다.
COMMIT, DIRTY = provenance()

#: 볼트 문서 수. 코퍼스가 달라지면 grep 비용도 랭킹도 달라지므로 결과에 남긴다.
#: 세는 데 1초쯤 걸리므로 여기서 한 번만 한다.
CORPUS = len(list((pathlib.Path.home() / ".wikilens" / "vault" / "mirror" / "pages")
                  .rglob("*.md"))) if (pathlib.Path.home() / ".wikilens" / "vault").exists() else 0


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

    tokens: int = 0         # 실제 토큰(에이전트)
    cost: float = 0.0       # USD(에이전트)
    turns: int = 0
    calls: int = 0          # 도구 호출 수(정적)
    seconds: float = 0.0

    #: 측정 당시의 코드 형상. **더러운 트리면 재현 불가**이므로 함께 남긴다.
    commit: str = ""
    dirty: bool = False
    #: **언제 쟀나.** 형상이 같아도 서버·모델·코퍼스는 그 사이에 바뀔 수 있고,
    #: 그때 시각만이 두 측정의 선후를 말해준다.
    ts: str = ""
    #: **어느 모델이었나.** 비용의 최대 변수인데 응답의 `modelUsage` 에 있는 것을
    #: 안 쓰고 있었다. 모델이 바뀌면 토큰·턴이 통째로 달라져 비교가 무효다.
    model: str = ""
    #: **볼트가 어떤 상태였나.** 문서 수가 다르면 grep 비용도 랭킹도 달라진다.
    corpus: int = 0

    warmup: bool = False    # 참이면 통계에서 뺀다 — 아래 참고
    error: str = ""
    extra: dict = field(default_factory=dict)

    def key(self) -> tuple:
        """이어받기용 식별자. 이 조합이 이미 있으면 다시 안 돈다."""
        return (self.harness, self.case, self.group, self.qi, self.rep)


def record(**kw) -> Record:
    """
    **새 측정 하나.** 지금 형상을 박아 넣는다 — 이 값이 결과의 재현 근거다.
    """
    kw.setdefault("commit", COMMIT)
    kw.setdefault("dirty", DIRTY)
    kw.setdefault("ts", datetime.now().astimezone().isoformat(timespec="seconds"))
    kw.setdefault("corpus", CORPUS)
    return make(**kw)


def make(**kw) -> Record:
    """
    **모르는 키를 조용히 버리고 만든다.**

    예전에는 하네스가 `Record(**측정결과)` 로 만들었는데, 측정 함수에 필드를 하나
    더하면 `unexpected keyword argument` 로 **돈을 쓴 뒤 중간에 터졌다**(에이전트
    벤치는 세션당 $0.53 이다). 모르는 것은 [Record.extra] 로 밀어 넣어 잃지 않는다.
    """
    known = {f.name for f in fields(Record)}
    extra = dict(kw.pop("extra", {}) or {})
    unknown = {k: kw.pop(k) for k in list(kw) if k not in known}
    if unknown:
        extra.update(unknown)
    return Record(extra=extra, **kw)


def load(path: pathlib.Path) -> list[Record]:
    """
    **옛 스키마를 읽을 수 있어야 한다.** 필드를 하나 지우자 예전 결과가 통째로
    `unexpected keyword argument` 로 안 읽혔다 — 돈을 쓴 측정이 스키마 변화에
    인질이 되면 안 된다. `make()` 와 같은 규칙으로 모르는 키는 `extra` 로 보낸다.
    """
    if not path.exists():
        return []
    # **읽을 때는 형상을 채우지 않는다.** `record()` 로 안 만들어진 옛 줄에 지금
    # 형상을 넣으면 **그때 잰 것처럼 보인다** — 실제로 그렇게 찍혀 60건이 지금
    # 태그를 달았다. 기록이 없으면 없다고 말해야 한다.
    return [make(**{"commit": "(기록 없음)", **json.loads(line)})
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def done_keys(path: pathlib.Path) -> set[tuple]:
    """
    **이어받기.** 90세션이 세션당 $0.53 이라, 중간에 죽었을 때 처음부터 다시 도는 것은
    돈으로 물어야 하는 실수다. 이미 끝난 조합은 건너뛴다.

    **워밍은 세지 않는다.** 그것은 버리는 측정이고 `rep=-1` 로 들어오는데, 포함하면
    그 키가 "끝난 것" 이 되어 나중에 같은 자리를 재려 할 때 조용히 건너뛴다.
    실패한 것도 세지 않는다 — 다시 돌려야 하는 자리다.
    """
    return {r.key() for r in load(path) if not r.error and not r.warmup}


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
    평균 하나를 적으면 그 폭이 사라져서, 없는 우열을 있다고 말하게 된다.

    **`statistics.quantiles` 를 쓴다 — 손으로 인덱싱하면 안 된다.** 처음에
    `vs[n//4]`·`vs[3*n//4]` 로 짰는데 `[1,2,3,4]` 의 q3 가 **최댓값**이 나왔다.
    그러면 IQR 이 전 범위가 되어 [overlaps] 가 늘 참이고, 가드가 항상 발동하면
    정보가 0 이다.
    """
    if not values:
        return {"n": 0}
    vs = sorted(values)
    n = len(vs)
    if n < 4:
        # 사분위를 낼 표본이 아니다. **범위로 두되 그 사실을 남긴다** — 아래
        # [overlaps] 가 이것을 보고 "모른다" 와 "겹친다" 를 구별한다.
        return {"n": n, "median": statistics.median(vs), "min": vs[0], "max": vs[-1],
                "q1": vs[0], "q3": vs[-1], "too_few": True}
    q1, _, q3 = statistics.quantiles(vs, n=4, method="inclusive")
    return {"n": n, "median": statistics.median(vs), "min": vs[0], "max": vs[-1],
            "q1": q1, "q3": q3, "too_few": False}


def overlaps(a: dict, b: dict) -> bool:
    """두 분포의 [q1,q3] 가 겹치면 **차이를 주장할 수 없다.**"""
    if not a.get("n") or not b.get("n"):
        return True
    return not (a["q3"] < b["q1"] or b["q3"] < a["q1"])


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """
    적중률의 95% 신뢰구간 (Wilson score).

    **적중률은 비율이라 IQR 이 안 맞는다** — 토큰처럼 분포가 아니라 성공/실패 카운트다.
    맨눈으로 `69/90` 대 `74/90` 을 보면 차이가 있어 보이는데, 구간을 그려보면 크게
    겹친다. 이 저장소가 `learn/Reliability.kt` 에서 Wilson 대신 EB 를 쓴 것은 사전
    정보를 살리려는 것이었고, 여기서는 사전 정보가 없으므로 Wilson 이 맞다.

    정규 근사가 아니라 Wilson 인 이유: n 이 작거나 비율이 0·1 에 가까울 때 정규
    근사는 구간이 [0,1] 을 벗어난다.
    """
    if n == 0:
        return 0.0, 1.0
    p_hat = hits / n
    d = 1 + z * z / n
    centre = (p_hat + z * z / (2 * n)) / d
    half = z * ((p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) ** 0.5) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def too_few(*dists: dict) -> bool:
    """
    표본이 4 미만이면 **겹침 여부를 따지는 것 자체가 무의미하다.**

    "겹쳐서 말할 수 없다" 와 "표본이 없어서 말할 수 없다" 는 다른 상태인데, 구별하지
    않으면 리포트가 늘 같은 문장을 뱉어 아무 정보를 안 준다.
    """
    return any(d.get("too_few") or d.get("n", 0) < 4 for d in dists)
