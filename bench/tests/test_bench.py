"""
`bench/` 의 순수 함수들.

**이 디렉터리에 테스트가 하나도 없었고, 하루에 결함 넷이 여기서 나왔다** — cold 가
warm 에 덮이던 키 충돌 · `mode_split` 이 다른 시험지를 비교하던 것 · `transfer`
가 학습 0회로 돌던 것 · read 실패 시 세션 미종료. 앞의 셋은 전부 순수 함수라 여기서
싸게 잡힌다.

**서버가 필요한 것은 안 덮는다** — `probe`·`run_once` 는 통합 경로이고, 테스트가
서버를 요구하면 `check.sh` 가 환경에 매달린다.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

BENCH = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))

import agent  # noqa: E402
import harness  # noqa: E402
import learn  # noqa: E402
import report  # noqa: E402
from harness import Record, Writer, load, overlaps, record, summarize, too_few, wilson  # noqa: E402


# --------------------------------------------------------------- 키

def test_mode_가_키를_가른다():
    """
    **cold 와 warm 이 같은 키면 두 곳에서 깨진다** — `done_keys` 가 warm 을 통째로
    건너뛰고, 파일을 나눠도 `report.collect` 가 cold 를 warm 으로 덮는다(먼저 잰 쪽이
    리포트에서 사라진다). 되돌려 확인했다: 2건 → 1건, warm 만 남았다.
    """
    base = dict(harness="agent", case="C 서버판", group="G04", qi=0,
                query="q", gold="1", rep=0)
    assert record(**base, mode="cold").key() != record(**base, mode="warm").key()
    assert record(**base, mode="cold").key() == record(**base, mode="cold").key()


def test_이어받기와_리포트가_두_모드를_따로_센다(tmp_path):
    p = tmp_path / "a.jsonl"
    with Writer(p) as w:
        for m in ("cold", "warm"):
            w.write(record(harness="agent", case="C 서버판", group="G04", qi=0,
                           query="q", gold="1", rep=0, mode=m, tokens=100))
    assert len(harness.done_keys(p)) == 2
    assert len({r.key() for r in load(p)}) == 2


# --------------------------------------------------------------- 통계

def test_표본이_적으면_겹침을_판정하지_않는다():
    """"모른다" 와 "겹친다" 는 다른 상태다 — 뭉치면 리포트가 늘 같은 문장을 뱉는다."""
    assert too_few(summarize([1.0, 2.0, 3.0]))
    assert not too_few(summarize([1.0, 2.0, 3.0, 4.0]))


def test_사분위를_손으로_인덱싱하지_않는다():
    """`[1,2,3,4]` 의 q3 가 최댓값이면 IQR 이 전 범위가 되어 [overlaps] 가 늘 참이다."""
    m = summarize([1.0, 2.0, 3.0, 4.0])
    assert m["q3"] < m["max"]


def test_겹침_판정():
    a = summarize([1.0, 2.0, 3.0, 4.0, 5.0])
    far = summarize([100.0, 101.0, 102.0, 103.0, 104.0])
    assert not overlaps(a, far)
    assert overlaps(a, summarize([2.0, 3.0, 4.0, 5.0, 6.0]))


def test_wilson_은_0과_1을_넘지_않는다():
    for h, n in ((0, 3), (3, 3), (1, 5), (0, 1)):
        lo, hi = wilson(h, n)
        assert 0.0 <= lo <= hi <= 1.0


# --------------------------------------------------------------- 계획 생성

def _g(name="G04", n=3):
    return (name, "167175834", "제목", [f"q{i}" for i in range(n)])


def test_per_group_이_질의를_자른다():
    gs = [_g()]
    assert len(agent.plan(gs, 1, "spread", 1)) == 1
    assert len(agent.plan(gs, 1, "spread", 0)) == 3


def test_transfer_는_질의를_자르지_않는다():
    """
    q0 로 학습해 q1·q2 로 시험하는 것이 그 순서의 **정의**다 — 질의를 줄이면 재려던
    것 자체가 사라진다.
    """
    got = agent.plan([_g()], 3, "transfer", 1)
    assert {qi for _, qi, _ in got} == {0, 1, 2}


def test_repeat_은_같은_질의를_연속으로():
    got = agent.plan([_g()], 3, "repeat", 1)
    assert [rep for _, _, rep in got] == [0, 1, 2]
    assert {qi for _, qi, _ in got} == {0}


# --------------------------------------------------------------- learn 단계

def test_repeat_단계는_전부_학습이다():
    assert learn.steps("repeat", 3) == [(0, 0, True), (0, 1, True), (0, 2, True)]


def test_transfer_는_학습_뒤에_시험_둘이다():
    got = learn.steps("transfer", 4)
    assert [l for *_, l in got] == [True, True, False, False]
    assert [qi for qi, *_ in got] == [0, 0, 1, 2]


@pytest.mark.parametrize("reps", [3, 4, 6])
def test_회차가_음수가_되지_않는다(reps):
    """음수면 워밍(`rep=-1`)과 이어받기 키가 충돌한다. `main` 이 reps>=3 을 요구한다."""
    assert all(r >= 0 for _, r, _ in learn.steps("transfer", reps))


def test_transfer_는_학습_단계가_있어야_한다():
    """
    `--reps 2` 면 학습이 0회고, 그러면 시험이 당연히 0 이라 판정이 **"예측대로"** 라고
    찍는다 — 아무것도 안 재고 예측을 확인한 셈이 된다. `main` 이 막는 조건이 이것이다.
    """
    assert sum(1 for *_, l in learn.steps("transfer", 2) if l) == 0
    assert sum(1 for *_, l in learn.steps("transfer", 3) if l) == 1


# --------------------------------------------------------------- 리포트

def _rows(tmp_path, warm_groups, warm_tokens):
    """cold 는 세 그룹, warm 은 주어진 그룹만 — 권장 흐름과 같은 모양."""
    cost = {"G01": 900000, "G04": 200000, "G09": 400000}
    p = tmp_path / "a.jsonl"
    with Writer(p) as w:
        for case in ("A 원시grep", "B 로컬판", "C 서버판"):
            for g in ("G01", "G04", "G09"):
                w.write(record(harness="agent", case=case, group=g, qi=0, query="q",
                               gold="1", rep=0, mode="cold", tokens=cost[g], cost=0.5,
                               extra={"tools": ["x"]}))
            for g in warm_groups:
                for rep in range(3):
                    w.write(record(harness="agent", case=case, group=g, qi=0, query="q",
                                   gold="1", rep=rep, mode="warm",
                                   tokens=warm_tokens(case, g, cost[g]), cost=0.5,
                                   extra={"tools": ["x"]}))
    return load(p)


def test_학습기여분은_두_모드에_다_있는_질의만_센다(tmp_path, capsys, monkeypatch):
    """
    **권장 흐름이 cold 는 여러 그룹, warm 은 한 그룹이다**(warm 에서 힌트가 서빙되는
    그룹이 하나뿐이라 그렇다). 그냥 비교하면 학습이 아니라 **질의 구성의 차이**가 나온다 —
    학습 효과를 0 으로 둔 이 데이터에서 예전 코드는 세 케이스 전부 -50% 를 냈다.
    """
    rows = _rows(tmp_path, ["G04"], lambda c, g, base: base)
    monkeypatch.setattr(report, "RESULTS", tmp_path)
    report.mode_split([r for r in rows if r.harness == "agent"], False)
    out = capsys.readouterr().out
    assert "공통 질의 1개만 센다" in out
    assert "+0%" in out and "-50%" not in out


def test_학습기여분이_대조군보다_클_때만_학습으로_읽는다(tmp_path, capsys):
    rows = _rows(tmp_path, ["G04"],
                 lambda c, g, base: int(base * 0.6) if c.startswith("C") else base)
    report.mode_split([r for r in rows if r.harness == "agent"], False)
    out = capsys.readouterr().out
    assert "학습 기여로 읽을 여지가 있다" in out


def test_대조군이_더_움직이면_잡음이라고_말한다(tmp_path, capsys):
    rows = _rows(tmp_path, ["G04"],
                 lambda c, g, base: int(base * 1.5) if c.startswith("A") else
                 (int(base * 0.9) if c.startswith("C") else base))
    report.mode_split([r for r in rows if r.harness == "agent"], False)
    assert "학습 덕으로 읽을 수 없다" in capsys.readouterr().out


def test_collect_는_하위디렉터리를_안_걷는다(tmp_path, monkeypatch):
    """
    옛 형상 결과는 `stale-*/` 로 치운다. 그것이 다시 걷히면 **코드가 바뀐 전후를 한
    표에 넣는다.**
    """
    (tmp_path / "stale-0813").mkdir()
    with Writer(tmp_path / "stale-0813" / "old.jsonl") as w:
        w.write(record(harness="agent", case="C 서버판", group="G99", qi=0,
                       query="q", gold="1", rep=0, tokens=1))
    with Writer(tmp_path / "now.jsonl") as w:
        w.write(record(harness="agent", case="C 서버판", group="G04", qi=0,
                       query="q", gold="1", rep=0, tokens=1))
    monkeypatch.setattr(report, "RESULTS", tmp_path)
    assert {r.group for r in report.collect()} == {"G04"}


def test_같은_키는_마지막_것만_센다(tmp_path, monkeypatch):
    """`--no-resume` 로 다시 돌리면 같은 키가 여러 줄이 된다. 그대로 세면 중앙값이 흔들린다."""
    with Writer(tmp_path / "a.jsonl") as w:
        for tok in (100, 200):
            w.write(record(harness="agent", case="C 서버판", group="G04", qi=0,
                           query="q", gold="1", rep=0, tokens=tok))
    monkeypatch.setattr(report, "RESULTS", tmp_path)
    got = report.collect()
    assert len(got) == 1 and got[0].tokens == 200


# --------------------------------------------------------------- 하네스 공유

def test_세_하네스가_같은_서버를_본다():
    """
    각자 들면 한쪽만 고쳤을 때 다른 서버를 재고 그것이 결과에 안 보인다 — 실제로
    겪었다(`df048e9c`). 이제 `harness` 가 정본이다.
    """
    assert agent.SERVER == harness.SERVER == learn.SERVER
    import rank
    assert rank.SERVER == harness.SERVER


def test_운영_서버를_재려_하면_막는다(monkeypatch):
    """이 스크립트들은 궤적을 *만든다* — 주소를 잘못 주면 되돌릴 수 없다."""
    monkeypatch.setattr(harness, "SERVER", "http://127.0.0.1:8787")
    assert "되돌릴 수 없다" in harness.require_server(need_plugins=False)


def test_안내가_필요한_준비를_가른다(monkeypatch):
    """
    `agent` 만 플러그인 격리가 필요하다. 한 문장으로 두면 $0 측정 하나 때문에
    사용자의 플러그인이 내려간다.
    """
    monkeypatch.setattr(harness, "api_get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    assert "setup.sh up" in harness.require_server(need_plugins=True)
    assert "setup.sh server" in harness.require_server(need_plugins=False)


def test_그룹은_접두어로_고른다():
    gs = [_g("G01"), _g("G04"), _g("G09")]
    assert len(harness.select_groups(gs, None)) == 3
    assert [g[0] for g in harness.select_groups(gs, ["G04"])] == ["G04"]
    assert harness.select_groups(gs, ["ZZ"]) == []


# --------------------------------------------------------------- 스키마

def test_모르는_키는_조용히_버린다():
    """옛 결과 파일에 지금 없는 필드가 있어도 읽혀야 한다 — 죽은 필드가 데이터를 막았다."""
    r = harness.make(harness="agent", case="C 서버판", group="G04", qi=0,
                     query="q", gold="1", rep=0, 없는필드=123)
    assert isinstance(r, Record) and r.group == "G04"
