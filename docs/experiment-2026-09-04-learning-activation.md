# 학습 층 발동 실측 — 막은 것은 EB 가 아니라 게이트였다 (2026-09-04)

설계와 예측은 [`design/design-2026-08-28-learning-activation.md`](design/design-2026-08-28-learning-activation.md)
에 **실행 전에** 적었다. 단계 1(비용 0)만 돌렸고 단계 2는 안 돌렸다 — 아래가 그 이유다.

## 결과 요약

    ONAP 15,499건 · analyzer=english · 벤치 :8790 · 궤적 0 에서 시작

    1-R (repeat, 같은 표현 4회)   서빙 3 · 5그룹 중 1
    1-T (transfer, 학습 2회→다른 표현 2회)   서빙 0 · 5그룹 중 0

| 가설 | 예측 | 결과 |
|---|---|---|
| H1 반복하면 서빙된다 | `r1` 부터 | **N05 만 `r1` 부터 — 예측 그대로.** 나머지 넷은 끝까지 0 |
| H2 다른 표현으로는 안 옮는다 | 끝까지 0 | **참.** 5/5 가 0 |

## H1 이 4/5 에서 실패한 이유 — 설계가 게이트를 모형에 안 넣었다

설계 3장은 서빙 조건을 `rel = ebLower(hits, misses, prior) × c ≥ θ` 로 분해하고
prior·c 를 실측해 `r1` 발동을 예측했다. **그 앞에 이진 필터가 하나 더 있다.**

    Gate.classify(원문)  →  9낱말 이상이고 마커에 안 걸리면  UNKNOWN
    QueryKind.UNKNOWN.cacheable = false
    TrajectoryStore.kt:331   if (!t.kind.cacheable) return      ← 간선이 여기서 죽는다
    →  포스팅 0  →  몇 번을 반복하든 힌트 0

서버가 궤적에 `kind` 를 직접 기록하므로 재현이 아니라 **관측**이다:

    N01  UNKNOWN       10낱말   「how do I get recording rights for the project call」
    N02  UNKNOWN       10낱말   「how do we tell if a committer is still active」
    N03  UNKNOWN       13낱말   「i wrote a test, how do I get it into the ci chain」
    N04  UNKNOWN       10낱말   「a daily test failed, how do I run it again」
    N05  LOCALIZATION   5낱말   「run dmaap locally for testing」          ← 유일하게 통과

**EB 수식은 틀리지 않았다.** 게이트를 통과한 유일한 그룹에서 예측이 정확히 맞았다
(`c=1.0`, `ebLower(1,0,0.85)=0.62 > 0.45` → `r1` 부터). 나머지는 그 계산에 닿지도 못했다.

## 게이트가 언어에 따라 다르게 작동한다 — 두 서버 대조

`/api/stats` 의 `byKind` 를 같은 시각에 양쪽에서 읽었다:

    벤치 (english, ONAP 자연어 질의)     LOCALIZATION 2 · UNKNOWN 8    → UNKNOWN 80%
    운영 (korean, 실사용 질의)           LOCALIZATION 8 · UNKNOWN 0    → UNKNOWN  0%

`Gate` 의 마커는 한국어가 17개(`어디`·`문서`·`가이드`·`알려줘`…)이고 영어는 9개인데,
영어 쪽은 전부 **검색창에 치는 말투**(`where is`·`show me`·`look up`)다. 사람이 실제로
묻는 `how do I …` 는 어느 마커에도 안 걸리고, 그런 문장은 대개 9낱말을 넘는다.

    한국어  마커가 넓어 거의 전부 통과 — 게이트가 사실상 항등함수다
    영어    마커가 안 걸려 길이 폴백만 남고, 자연어 질문은 대부분 그 문턱을 넘는다

`TrajectoryStore` 의 KDoc 이 정확히 이 불확실성을 적어뒀다 — "마커가 넓고 8토큰 이하는
전부 LOCALIZATION 이라 **게이트가 항등함수인지 아닌지를 알 방법이 없었다.** UNKNOWN
비율이 그 답이다." **답이 나왔다: 언어에 따라 다르다.**

같은 그룹 안에서도 갈린다. N03 은 **같은 정답을 향한 같은 의도**인데 길이만으로 갈렸다:

    q0  13낱말  UNKNOWN        「i wrote a test, how do I get it into the ci chain」
    q2   7낱말  LOCALIZATION   「how do test suites get run automatically」

`Gate.kt` 주석이 "마커에 안 걸리는 긴 질의를 오분류할 잔여 위험" 이라고 예고한 자리다.
**위험이 아니라 지배적 경로였다** — 영어에서는.

## H2 — 이전은 일어나지 않는다 (예측대로)

유일하게 학습이 된 N05 조차 표현을 바꾸자 0으로 떨어졌다.

    q0 「run dmaap locally for testing」   → 항 {dmaap, local, run, test}   r1 에 힌트 1
    q1 「local DMaaP in docker, how」      → 겹침 2개, c ≈ 0.5
                                            rel = 0.62 × 0.5 = 0.31 < 0.45   힌트 0

`rel` 이 **곱**이라 한쪽이 작으면 다른 쪽이 못 메운다(설계 3.1). 관측이 그대로 따랐다.

## 1.x 판정에 주는 답

관문은 `served > 0` 하나였다([1x-gate](design/design-2026-08-28-1x-gate.md)).
**넘었다** — 1-R 에서 3회, 1-T 에서 1회 서빙됐고 거부는 0이었다.

그런데 넘은 방식이 판정을 바꾼다. 학습 층이 말하려면 **조건 둘이 동시에** 서야 한다:

    ① 게이트 통과   — 마커에 걸리거나 8낱말 이하
    ② 표현 반복     — 같은 항 집합으로 다시 물어야 한다 (H2 가 확인한 대로 이전은 없다)

한국어 실사용에서는 ①이 사실상 무료이므로 **②만 남는다.** 영어에서는 ①이 80% 를
자른다. `--wikilens.analyzer=english` 가 지원되는 설정인데 **학습 층은 그 설정에서
대부분 침묵한다** — 이것이 이 측정의 가장 큰 소득이다.

## 안 한 것과 그 이유

**단계 2(45세션 · $18~29)를 안 돌렸다.** 단계 1이 예측을 뒤집은 것이 아니라 **예측이
닿는 범위를 좁혔기** 때문이다. 5그룹 중 4그룹이 구조적으로 힌트를 못 받으므로, 그
위에서 모델 행동을 재면 "학습 효과 없음" 이 나오는데 그것은 층에 대한 판정이 아니라
게이트에 대한 판정이다. **돈을 쓰기 전에 게이트를 어떻게 할지부터 정해야 한다.**

**게이트를 고쳐서 다시 돌리지 않았다.** 예측이 빗나간 뒤에 통과하도록 조건을 바꾸면
그것은 측정이 아니라 맞추기다. 고칠지 말지는 별도 결정이고, 고친다면 **그 결정을
먼저 적고 나서** 다시 잰다.

## 이 측정의 한계

- **영어 코퍼스 하나다.** 한국어 쪽 `byKind` 는 운영 서버의 실사용 궤적 8건뿐이라
  표본이 작다. "한국어는 게이트가 항등함수" 는 마커 목록에서 온 추론이 절반이다.
- **질의 15개 · 그룹 5개.** ONAP 에서 손으로 고른 것이라 코퍼스를 대표하지 않는다.
- **`c` 를 직접 못 쟀다.** `rel` 은 힌트가 서빙될 때만 응답에 실리는데 `learn.py` 의
  `probe` 가 그것을 기록하지 않는다. 위의 `c ≈ 0.5` 는 항 집합에서 손으로 센 값이다.
- **N01 은 정답이 어휘 24건 안에 아예 없다.** 그 그룹은 게이트를 통과했더라도 다른
  이유로 못 배웠을 수 있다 — 게이트만이 원인이라고 말할 수 있는 것은 N02·N03·N04 다.
