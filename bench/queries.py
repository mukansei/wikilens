"""
같은 답을 내야 하는 유사 질의 3개 × N그룹.

아래 `GROUPS` 는 **누구나 재현할 수 있는 공개 코퍼스**로 채워져 있다 — 리눅스
재단 ONAP 프로젝트의 공개 위키다. 자격증명 없이 받을 수 있으므로, clone 한
사람이 자기 위키 없이도 이 저장소의 주장을 직접 검사할 수 있다.

    CONFLUENCE_URL=https://lf-onap.atlassian.net
    CONFLUENCE_AUTH=none            # 익명 읽기 — 명시해야 켜진다
    wikilens sync --space Meetings --space DW --root ~/.wikilens/vault-onap

    15,494건 · 링크 해석 97.2% · 인링크 보유 37% (2026-08-23 실측)
    색인은 `--wikilens.analyzer=english` 로 — 영어 코퍼스다.
    벤치는 `WIKILENS_VAULT` 로 그 볼트를 가리킨다.

**당신의 위키를 재려면 이 `GROUPS` 를 통째로 갈아야 한다.** 남의 코퍼스에서 만든
질의는 당신의 색인을 재지 못하고, 여기 적힌 pageId 는 당신의 위키에 없다. 그때
전 그룹이 `못 찾음` 인데 그것은 **"이 도구의 검색이 나쁘다" 와 구별되지 않는다** —
`harness.require_queries` 가 그 조합을 서버에 물어 끊는다.

그래서 이 파일에서 오래 남는 것은 데이터가 아니라 **질의를 만드는 규칙**이다.
아래 넷은 실제로 한 번씩 틀려보고 얻은 것들이다.

## 1. 사람이 치는 말로 쓴다 — 문서의 소제목을 옮기지 않는다

첫판은 문서 소제목을 거의 그대로 옮긴 키워드 나열이었다. 그건 **시험지를 답안지에서
베낀 것**이라 실사용을 안 닮는다. 실제 사용자는 이렇게 안 친다:

    베낀 것   "OAuth2 인가 코드 흐름 토큰 갱신 정책"
    사람 말   "로그인 붙이는 거 어떻게 하더라?"

바꿀 성질 넷:

  - **구어체·불완전한 문장** — 조사 생략, 물음표, "~있나", "~어디지"
  - **키워드가 아니라 의도** — 무엇을 알고 싶은지를 말하지 문서 제목을 말하지 않는다
  - **표기가 흔들린다** — 대소문자와 한글/영문 표기가 사람마다 다르다
  - **문서에 없는 말이 섞인다** — 사용자 어휘로 부른다

**그래도 도메인 명사는 남긴다.** 완전한 동의어로만 만들면 어휘 검색이 원리적으로 못
찾아 벤치마크가 조작이 된다. 사람도 보통 제품명·기능명은 그대로 쓴다 — 그것을 구어체
껍질에 싸는 것이 현실에 가깝다.

## 2. 순위로 질의를 고르지 않는다

"전 변형 5위 이내" 를 기준으로 질의를 고치면 **랭커에 맞춰 시험지를 고치는 순환
논리**가 되어 벤치마크가 무효가 된다. 기준은 **의미**여야 한다 — 사람이 그 문서를
답으로 인정하는가. 측정된 순위는 결과이지 합격선이 아니다.

## 3. 낮은 점수가 곧 질의의 결함은 아니다

순위를 가르는 것은 대개 **질의에 드문 항이 있느냐**다. 코퍼스의 41% 에 나오는 낱말로
물으면 정답이 수천 건과 같은 조건에 놓인다. 문서 크기(BM25 길이 정규화)도 작용하지만
다른 조건이 같을 때 갈리는 2차 효과다 — 표본 하나를 보고 크기 탓으로 돌리면 오귀인이
된다(실제로 그렇게 적었다가 고쳤다).

## 4. 하네스가 어느 판에 불공정하지 않은지 본다

구어체 질의는 앞 두 낱말이 군더더기(`화면에서`·`어떻게`)인 경우가 많다. 그것을 그대로
grep 패턴에 쓰면 파일 기반 경로에 불공정하다 — 모델은 불용어를 걸러 명사를 고르기
때문이다. 실측에서 그 차이가 4/30 대 6/30 이었다.

## 채우는 법

1. `wikilens stats` 로 어휘 격차가 있는지 먼저 본다. 없으면 벤치할 것도 없다.
2. 답이 분명한 문서를 고르고 그 `pageId` 를 적는다(볼트의 front matter `id`).
3. 그 문서가 실제로 답하는 물음을 **세 가지 표현으로** 쓴다.
4. `python3 bench/rank.py` 로 순위를 본다($0, 모델 없음). 그 결과로 `MINIMAL` 을 고른다.

`MINIMAL` 은 비싼 에이전트 벤치(`agent.py`)가 쓰는 기본 그룹이다. **결과의 모양이
서로 다른 것**을 골라야 한다 — 셋 다 맞히는 그룹만 넣으면 돈만 쓰고 정보가 없다.
쉬운 것 하나, 두 판이 갈리는 것 하나, 깊이 파야 나오는 것 하나가 좋은 조합이다.
"""
from __future__ import annotations

#: 형식: (그룹명, 정답 pageId, 정답 제목, [질의 셋])
#:
#: 변형 셋은 모호함을 단계로 가른다:
#:   a) 도메인 명사 + 구어체   b) 의도만 말함   c) 사용자 어휘·표기 흔들림
#:
#: **정답을 회의록이 아니라 절차·정책 문서에서 골랐다.** 이 코퍼스는 `TSC 2024-05-23`
#: 류 회의록이 절반 가까이라, "TSC 가 뭘 정했나" 는 정답이 유일하지 않다. 다섯 다
#: 제목이 유일하고 본문이 실제로 그 물음에 답하는 것을 읽어서 확인했다.
#:
#: **모델이 기억으로 답할 수 없는 것을 골랐다.** ONAP 이 무엇인지는 널리 알려져
#: 있지만 이 문서들의 내용 — 브리지 권한 절차, 커미터 활동 판정 기준, 랩 접속
#: 방법 — 은 조직 고유의 기록이다. 어휘는 공유하고(`TSC`·`committer`·`CI`) 사실은
#: 모르는 상태가 사용자가 처한 상황과 같다.
GROUPS = [
    ("N01 회의 브리지 권한", "15471028", "Calendar and Bridge FAQ", [
        "how do I get recording rights for the project call",
        "who can be zoom host for our meetings",
        "meeting bridge policy — can I record it",
    ]),
    ("N02 커미터 활동 판정", "15468014", "Committer Status Review", [
        "how do we tell if a committer is still active",
        "what does registered but inactive mean",
        "someone never approved a commit, do they keep permissions",
    ]),
    ("N03 CI 체인에 테스트 넣기", "16409707", "How can I include my test(s) in CI chains?", [
        "i wrote a test, how do I get it into the ci chain",
        "adding my use case to the gates",
        "how do test suites get run automatically",
    ]),
    ("N04 데일리 랩 테스트 재실행", "16500285", "How to re-run a test on a daily platform?", [
        "a daily test failed, how do I run it again",
        "troubleshooting errors on the daily chain",
        "ssh into the daily lab and rerun",
    ]),
    ("N05 로컬 DMaaP 설치", "16275767",
     "How to set up a local DMaaP installation in Docker for testing", [
        "run dmaap locally for testing",
        "local DMaaP in docker, how",
        "i need a message bus on my laptop to test against",
    ]),
]

#: **측정 2026-08-23** (`rank.py`, 상위 25까지, `-1` = 밖):
#:
#:     N01  -1  -1   8      대체로 밖
#:     N02   3  18  -1      넓게 퍼짐
#:     N03   1  12  15      넓게 퍼짐
#:     N04   2   3   2      천장 — 검색만으로 된다
#:     N05   1   1  -1      거의 천장
#:
#: 띠로는 1~2위 5 · 3~7위 2 · 8~25위 4 · 밖 4. **이 수치는 결과이지 합격선이 아니다**
#: (위 규칙 2번). 순위를 올리려고 질의를 고치면 벤치마크가 무효가 된다.
#:
#: 딸려 나온 관찰: 틀린 질의 셋에서 `How do I run xtesting tests on my ONAP lab?` 이
#: 나란히 1위였다. 서로 무관한 물음에 같은 문서가 올라오는 **허브**이고, 다른
#: 코퍼스에서도 같은 현상이 관측됐다(`DECISIONS.md` D26 의 "확신에 찬 오답" 재료).

#: 비싼 벤치(`agent.py`·`learn.py`)의 기본 그룹. `rank.py` 로 순위를 본 뒤 고른다 —
#: 근거는 위 "채우는 법" 4번.
#: 측정된 모양이 서로 다른 셋을 골랐다 — 셋 다 맞히는 그룹만 넣으면 돈만 쓰고
#: 정보가 없다. N04 는 천장(비용만 갈린다) · N02 는 넓게 퍼짐(층이 갈리는 자리) ·
#: N01 은 대체로 밖(파야 나온다).
MINIMAL = ("N01", "N02", "N04")
