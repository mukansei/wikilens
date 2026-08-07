# WikiLens

**사내 위키를 "사람들이 실제로 부르는 이름"으로 찾습니다.**

문서 제목은 `OAuth 2.0 인가 코드 흐름`인데 다들 "로그인 붙이는 법"이라고 부릅니다.
그 표현은 제목에도 본문에도 없어서, 보통의 검색으로는 못 찾습니다.

WikiLens 는 **다른 문서들이 그 페이지를 링크할 때 쓴 표현**(앵커 텍스트)을 모아
대상 기준으로 뒤집어 색인합니다:

```console
$ grep "로그인" ALIASES.md
PLATFORM | OAuth 2.0 인가 코드 흐름 | 로그인 붙이는 법 · 인증 붙이기 | 2 | mirror/pages/01/200000001.md
```

본문만 뒤졌다면 **엉뚱한 문서**가 나옵니다 — 그 표현으로 *링크한* 온보딩 페이지지,
찾으려는 문서가 아닙니다. 이 차이가 이 프로젝트의 출발점입니다.

Claude Code 플러그인으로 설치하면 **어느 프로젝트에서든** "우리 회사 배포 절차 문서
어디 있어?" 라고 물어볼 수 있습니다.

---

**쓰려는 분은** [어느 것을 쓰나요](#어느-것을-쓰나요) → [시작하기](#시작하기) 두 절이면
충분합니다. 그 아래는 만드는 사람을 위한 것입니다.

| | |
|---|---|
| [어느 것을 쓰나요](#어느-것을-쓰나요) | 로컬판과 서버판 — 어느 쪽이 내 상황인가 |
| [어떻게 동작하나](#어떻게-동작하나) | Confluence → 볼트 → 두 판으로 갈리는 흐름 |
| [시작하기](#시작하기) | [로컬판 5분](#로컬판--5분) · [서버판 운영자](#서버판--운영자) · [사이에 둘 한 단계](#사이에-한-단계를-두세요) |
| [학습 레이어](#학습-레이어-서버판만) | 서버판만 — 팀의 탐색 이력이 랭킹을 보정하는 방식 |
| [저장소 구조](#저장소-구조) | 어느 디렉터리가 무엇을 하나 |
| [적용 범위](#적용-범위) | Cloud·Server/DC 양쪽 · 언어는 한국어 기본 |
| [설계 결정 요약](#설계-결정-요약) | 왜 이렇게 만들었나 |
| [만드는 사람을 위해](#만드는-사람을-위해) | [검증 절차](#변경-후-이-넷이-통과해야-합니다) · [검증 상태](#검증-상태) · [측정 지표](#측정-지표-우선순위-순) |
| [미해결](#미해결) | 아직 못 푼 것 — 배포 전에 읽으세요 |

---

## 어느 것을 쓰나요

두 가지 배포 형태가 있습니다. **업그레이드 경로가 아니라 서로 다른 물건입니다.**

| | **로컬판** | **서버판** |
|---|---|---|
| 누구에게 | 혼자 · 소규모 팀 | 팀 배포 |
| 준비 | 각자 5분 싱크 | 관리자가 서버 운영 |
| 코퍼스 | 내 노트북 (수십 MB) | 서버 |
| 검색 | grep + 별칭 색인 | BM25 + 형태소 분석 + 학습 |
| 권한 | 내 토큰 = 내 범위 (**자동**) | 서버가 요청마다 확인 |
| 오프라인 | 가능 | 불가 |
| 학습 | 없음 | 팀의 탐색 이력이 쌓임 |

**지금 팀에 배포한다면 로컬판입니다.** 서버판은 ACL 수집 전까지 제3자를 붙이면 안 됩니다
(→ [미해결](#미해결)). 대가는 Confluence 부하가 사용자 수에 비례하는 것이라
소규모 팀에 한합니다.

설치해서 쓰기만 한다면 여기서 멈추고 사용 안내로 가세요 —
[**로컬판**](plugin/local/README.md) · [**서버판**](plugin/client/README.md).
아래부터는 만드는 사람을 위한 것입니다.

---

## 어떻게 동작하나

```mermaid
flowchart LR
    subgraph CONF["Confluence — 배포 형태를 가리지 않습니다"]
        direction TB
        CLOUD[("Cloud<br/>경로 접두사 /wiki")]
        DC[("Server / Data Center<br/>접두사 없음")]
    end

    NET["네트워크 · 인증<br/>doctor 가 배포 형태 판별<br/>PAT · Cloud 토큰 · IAM · 프록시"]
    CLI["cli · Python<br/>sync → build<br/>유일한 싱커"]
    VAULT[("볼트 · 파일<br/>두 판이 같은 포맷")]

    subgraph L["로컬판 — 각자 자기 볼트"]
        LSKILL["스킬 + 커맨드<br/>ALIASES → TREE → 본문 grep<br/>런타임 의존성 0"]
    end

    subgraph S["서버판 — 팀 공용 · 상주"]
        IDX["Lucene/Nori 색인<br/>+ 학습 레이어<br/>질의 시점 ACL"]
        MCP["MCP 도구 4개<br/>search · read · grep · tree"]
    end

    CLOUD --> NET
    DC --> NET
    NET -->|CQL · 읽기 전용| CLI --> VAULT
    VAULT -->|파일 직접 읽기| LSKILL
    VAULT -->|VaultReader| IDX --> MCP

    classDef ext fill:#e5e7eb,stroke:#6b7280,color:#111827
    classDef net fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef py fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef kt fill:#d1fae5,stroke:#059669,color:#064e3b
    classDef st fill:#fef3c7,stroke:#d97706,color:#78350f
    class CLOUD,DC ext
    class NET net
    class CLI,LSKILL py
    class IDX,MCP kt
    class VAULT st
```

읽는 법이 셋입니다.

- **색이 곧 구현 언어** — 파랑 Python(`cli`·로컬판), 초록 Kotlin(서버판), 노랑 파일.
- **Confluence 는 Cloud 든 Server/DC 든** 상관없습니다. `doctor` 가 배포 형태를 판별하고
  인증도 넷을 지원합니다 — PAT · Cloud API 토큰 · IAM OAuth2 · 리버스 프록시.
- **볼트를 만드는 것은 `cli` 하나**입니다. 서버는 Confluence 를 크롤하지 않고
  그 결과를 읽기만 합니다. 두 판은 볼트에서 갈라져 그 뒤로 만나지 않습니다.

```mermaid
flowchart TB
    subgraph S1["① sync — 네트워크 · 증분"]
        RAW["mirror/raw/…/{id}.xhtml<br/>원본 XHTML · 무손실"]
        STATE["mirror/.sync-state.json<br/>cursor · version · ancestors"]
    end

    subgraph S2["② build — 로컬 · 멱등"]
        PAGES["mirror/pages/<br/>마크다운 본문"]
        ALIAS["★ ALIASES.md<br/>별칭 색인 · 사람과 grep 용<br/>한 줄에 제목·별칭·인링크·경로"]
        TREE["TREE.md<br/>계층 목차 · 고아 문서용"]
        ANCH["derived/anchors.jsonl<br/>앵커 원자료 · 프로그램용"]
    end

    subgraph S3L["③ 검색 — 로컬판"]
        LQ["스킬<br/>ALIASES → TREE → 본문 순 grep<br/>인링크 많은 쪽이 정본"]
    end

    subgraph S3S["③ 검색 — 서버판"]
        SQ["/api/search<br/>Nori → BM25(앵커4 : 제목3 : 본문1)<br/>+ 학습 힌트 RRF 융합"]
        ST["/api/tree<br/>TreeIndex · depth · rootId"]
    end

    RAW -->|파싱| PAGES
    RAW -->|링크 전치| ALIAS
    RAW -->|링크 전치| ANCH
    STATE -->|ancestors| TREE

    ALIAS --> LQ
    TREE --> LQ
    PAGES --> LQ

    ANCH --> SQ
    PAGES --> SQ
    STATE --> ST

    classDef st fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef key fill:#fde68a,stroke:#b45309,color:#78350f
    classDef py fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef kt fill:#d1fae5,stroke:#059669,color:#064e3b
    class RAW,STATE,PAGES,TREE st
    class ALIAS,ANCH key
    class LQ py
    class SQ,ST kt
```

**`build` 는 같은 원자료로 두 벌을 냅니다** — 마크다운은 사람이 grep 하라고, JSON 계열은
프로그램이 색인하라고:

| 산출물 | 누가 읽나 |
|---|---|
| `ALIASES.md` · `TREE.md` | **로컬판만** — 사람과 grep 을 위한 형식 |
| `derived/anchors.jsonl` · `.sync-state.json` 의 `ancestors` | **서버판만** — 색인을 위한 형식 |
| `mirror/pages/` 의 본문 | 양쪽 다 |

그래서 **서버판은 두 마크다운 파일을 읽지 않습니다.** 앵커도 계층도 양쪽 다 갖고 있고,
같은 원본에서 나온 다른 산출물을 볼 뿐입니다.

`sync` 와 `build` 를 나눈 이유는 **제목→ID 해석 완전성**입니다. Confluence 링크는 대개
제목으로 대상을 가리키는데, 전부 받은 뒤 한 번에 파싱해야 해석이 완전해집니다(실측 94%).

서버판은 여기에 **학습 레이어**를 얹습니다 — 검색하고 읽은 궤적이 쌓여, 남들이 같은
질문으로 찾아간 문서가 위로 올라옵니다. 훅은 없습니다. 읽기가 서버를 거치므로
서버가 궤적을 직접 관측합니다.

---

## 시작하기

### 로컬판 — 5분

```
/plugin marketplace add ./
/plugin install wikilens-local@wikilens
/wikilens-local:setup
```

`setup` 이 전부 합니다 — 자격증명을 `~/.wikilens/env.sh`(600)에 고정하고, 볼트 위치를
`~/.wikilens/config.json` 에 적고, CLI 를 `~/.wikilens/venv` 에 설치하고, 첫 싱크까지.
준비할 것은 **Confluence 주소와 개인 액세스 토큰(PAT)** 둘뿐입니다.
**이미 볼트가 있으면 옮기지 말고 그 경로를 등록**하면 됩니다.

싱크가 끝나면 `stats` 가 "제목과 어휘가 안 겹치는 별칭을 가진 페이지" 비율을 냅니다.
**여기서 판단하세요 — 낮으면 어휘 격차가 없다는 뜻이고, 이 도구 전체가 값어치가 없습니다.**

<details>
<summary><b>플러그인 없이 CLI 만</b> — cron·서버 운영자용</summary>

```bash
python3 -m venv ~/.wikilens/venv && ~/.wikilens/venv/bin/pip install ./cli

mkdir -p ~/.wikilens && chmod 700 ~/.wikilens
cat > ~/.wikilens/env.sh <<'EOF'
export CONFLUENCE_URL=https://confluence.mycompany.com
export CONFLUENCE_TOKEN=<개인 액세스 토큰>
EOF
chmod 600 ~/.wikilens/env.sh

~/.wikilens/venv/bin/wikilens doctor                       # 연결·인증·스페이스 확인
~/.wikilens/venv/bin/wikilens sync --space PLATFORM --root ~/wiki   # 수 분
~/.wikilens/venv/bin/wikilens stats --root ~/wiki
```

**자격증명은 `export` 가 아니라 파일에 둡니다.** `export` 는 그 셸에서만 살아서
Claude Code 를 앱으로 띄우면 없는 것과 같고, 실제로 **검색은 되는데 `sync` 만 조용히
죽는** 상태를 오래 겪었습니다(→ `DECISIONS.md` D10). CLI 는 환경변수가 없으면
이 파일을 읽습니다 — cron 에서도 그냥 동작합니다.

`--root` 는 서브커맨드 앞뒤 어디에 와도 되고, 플러그인을 통해 부르면 래퍼가
`~/.wikilens/config.json` 의 볼트 경로로 자동으로 채웁니다.

</details>

<details>
<summary><b>인증이 안 되면</b> — SSO · 게이트웨이 · 접두사 자동판별</summary>

**SSO 환경이어도 대개 PAT 가 따로 동작합니다.** Server/DC 의 PAT 를 먼저 시도하세요.
그것이 막혀 있을 때만 IAM OAuth2 가 필요합니다. 인증은 넷을 지원하고
(`CONFLUENCE_TOKEN` PAT · Cloud API 토큰 + `CONFLUENCE_EMAIL` · 사내 IAM OAuth2 ·
리버스 프록시 헤더 주입) 전부 `cli/wikilens/auth.py` 한 곳에 격리돼 있습니다.

**배포 형태를 잘못 판별하면** — `detect_prefix()` 가 Cloud(`/wiki`)와 Server/DC(빈 접두사)를
자동 판별합니다. 사내 리버스 프록시가 일부 엔드포인트만 허용하는 구성에 속은 적이 있어
이중 검증을 넣었지만, 게이트웨이 구성은 회사마다 달라 또 속을 수 있습니다:

```bash
export CONFLUENCE_PREFIX=""        # Server/DC 강제
# export CONFLUENCE_PREFIX="/wiki" # Cloud 강제
```

**첫 번째로 의심할 자리는 아닙니다** — 이중 검증 이후로는 강제 지정 없이 동작하는 것을
실측했습니다(2026-08-06). 자격증명이 파일에 제대로 있는지를 먼저 보세요.

</details>

### 서버판 — 운영자

```bash
# 서비스 계정으로 1회 싱크 (사용자별 싱크 없음 → Confluence 부하 1배)
CONFLUENCE_TOKEN=<서비스계정> wikilens sync --space PLATFORM --root ~/wiki

cd server && ./gradlew bootRun          # 기동 시 볼트를 찾아 전량 색인
```

**볼트 경로를 서버에 따로 알려주지 않아도 됩니다.** 기본 자리(`server/mirror-root`)가
비어 있으면 `~/.wikilens/config.json` 의 `vault` 를 읽습니다 — CLI 가 만든 볼트를
그대로 찾습니다. 다른 자리에 두려면 `--wikilens.vault-root=/절대/경로` 로 명시하세요
(명시가 항상 이깁니다). 운영 배포는 절대경로를 주는 쪽을 권합니다.

사용자는 볼트를 받지 않습니다. MCP 플러그인만 설치하고 값 둘을 넣으면 됩니다:

```
/plugin install wikilens-client@wikilens
/wikilens-client:setup                  # 서버 주소 · 본인 식별자
```

**`user` 를 빼면 서버가 요청자를 식별하지 못해 결과가 항상 빕니다** — 그게 "문서가 없다"
처럼 보입니다. 배포·운영 절차는 [`server/README.md`](server/README.md) 에 있습니다
(cron 자동화, ACL 등록, 분석기 선택, 백업 대상).

### 사이에 한 단계를 두세요

로컬판에서 서버판으로 바로 가지 마세요. 팀에 배포하기 전에 **서버를 혼자 띄워
궤적이 실제로 쌓이는지만** 먼저 봅니다 — 3단계와 같은 서버지만 플러그인 배포가 없습니다:

```bash
cd server && ./gradlew bootRun
curl -XPOST localhost:8787/api/search -H 'Content-Type: application/json' \
  -d '{"query":"...","userKey":"me","sessionId":"t1"}'
curl localhost:8787/api/stats     # trajectories · termPagePairs 가 느는지
```

**측정할 것: 비링크 도달 비율과 질의 중복률.** 낮으면 학습 레이어가 링크 그래프의
복사본으로 퇴화하는 중이라 배포해봐야 얻는 게 없습니다.

이 순서는 의도적으로 **실패해도 산출물이 남게** 짜여 있습니다 — 1단계에서 멈춰도
볼트와 별칭 색인은 그대로 쓸 수 있습니다.

---

## 학습 레이어 (서버판만)

**"남들이 같은 질문으로 찾아간 문서"를 다음 사람에게 밀어 올립니다.** 어휘 검색이
못 찾은 것을 사람들의 탐색이 대신 찾아준 셈이니, 그 경로를 기억해 두는 것입니다.

1인 사용에는 없습니다 — 혼자서는 통계가 안 쌓여 어차피 무의미하고,
로컬판에는 관측할 서버 자체가 없습니다.

### 관측 — 도구 호출이 곧 궤적입니다

```mermaid
flowchart LR
    Q["/api/search<br/>onQuery · onServed"]
    R["/api/read<br/>onRead"]
    E["세션 종료<br/>onEnd"]
    SW["SessionSweeper<br/>5분마다 · idle 30분"]
    FIN["finalize<br/>유용했나 판정"]
    LOG[("trajectories.jsonl<br/>append-only<br/>유일한 복구 불가 자산")]
    POST[("postings<br/>항 → 페이지 → hits/misses")]

    Q --> R --> E --> FIN
    SW -.->|종료를 놓치면| FIN
    FIN --> LOG
    FIN --> POST
    POST -->|hints| Q

    classDef api fill:#d1fae5,stroke:#059669,color:#064e3b
    classDef st fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef j fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    class Q,R,E api
    class SW,FIN j
    class LOG,POST st
```

**별도의 보고 도구가 없습니다.** 에이전트가 `record_trajectory` 같은 것을 안정적으로
불러줄 리 없어서(D6), 검색과 읽기 **자체를 관측**합니다. 훅도 없습니다 — 읽기가 서버를
거치므로 서버가 직접 봅니다.

세션은 MCP 프록시 프로세스 하나입니다. 종료 신호를 놓치면(SIGKILL·크래시)
`SessionSweeper` 가 거둡니다 — **안 거두면 그 세션이 배운 것이 통째로 사라집니다.**

### 무엇을 기억하나

| | 값 | 왜 |
|---|---|---|
| 키 단위 | **항(term)** — 키워드 집합이 아님 | "로그인 붙이는 법 어디"와 "로그인 붙이는 법"이 다른 키가 되면 카운트가 흩어집니다 |
| 저장 형태 | `항 → 페이지 → (hits, misses)` | 같은 항에 목적지가 여럿인 건 경쟁이 아니라 **질의가 모호한 것**이라 분포로 둡니다 |
| 캐싱 대상 | `LOCALIZATION` 질의만 | "이 데이터가 어떻게 흐르나"에 목적지만 주면 답한 게 아니라 **답을 지운** 겁니다 |

`Gate.classify` 가 질의를 넷으로 나눕니다 — `LOCALIZATION`(목적지가 곧 답) ·
`TRACING`(경로가 곧 답) · `RATIONALE`(근거가 경유지에 분산) · `UNKNOWN`(판단 보류).
**첫째만 간선을 만듭니다.**

### 얼마나 믿나 — Empirical Bayes

Wilson 하한이 아닙니다. Wilson 은 카운트만 봐서 **같은 4승 3패라도 검색이 강하게
가리키는 후보와 겨우 걸린 후보를 구분하지 못합니다.**

```
prior = Beta(s·κ, (1−s)·κ)          s = 정규화된 Lucene 점수 · κ = 5
post  = Beta(s·κ + hits, (1−s)·κ + misses)
신뢰도 = 그 사후분포의 5% 하한
```

Wilson 은 **균등 사전분포의 특수한 경우**라, 검색 점수를 사전분포로 주면 그 정보가
살아납니다. 표본이 쌓이면 사전분포 영향은 저절로 사라집니다(400승 300패에서 격차 0.005).

사전확률은 `[0.05, 0.85]` 로 자릅니다 — 1.0 이면 Beta 한쪽 모수가 0이 되어
**관측 한 번에 신뢰도 1.0** 이 됩니다.

신뢰도가 **0.45 미만이면 서빙하지 않습니다.** 확신 없을 때 침묵하는 것이 이 설계의
전제입니다:

```
손익분기    p_hit · (n−1) > p_wrong
```

기준이 절대 적중률이 아니라 **적중 대 오답 비율**입니다. 캐시가 침묵하면 `p_wrong → 0`
이라 거의 항상 이득입니다 — `1/n` 은 틀린 공식입니다(D2).

### "유용했다"는 어떻게 아나 — 이 설계의 최대 미해결

서버는 무엇을 읽었는지만 보고 그게 답이었는지는 모릅니다. 신호 다섯을 씁니다:

| 신호 | 판정 |
|---|---|
| 마지막으로 읽은 페이지 | 답일 확률이 높다 — 탐색은 성공에서 멈춥니다 |
| 키워드가 겹치는 재질의 | 앞 시도는 실패였다 |
| **서빙했는데 끝내 안 읽힌 힌트** | 그 힌트가 틀렸다 — 유일하게 학습을 **되돌리는** 신호 |
| 마지막 읽기 앞의 것들 | 열어보고 지나쳤다 (1건이면 미스 0, 6건이면 5) |
| `dest` 의 검색 순위 | 깊을수록 강한 증거 (1위 ×1 · 2~3위 ×2 · 그 아래 ×3) |

셋째가 중요합니다. 그전에는 미스가 나는 경로가 재질의 하나뿐이라 **엉뚱한 힌트도
벌점이 없었고**, `pWrong` 이 늘 0.0 이었습니다.

**클릭 없는 종료를 실패로 세지 않습니다.** 결과 제목만 보고 답했을 수 있어서요 —
IR 에서 [good abandonment](https://dl.acm.org/doi/10.1145/1571941.1571951) 로 알려진
오독입니다. `abandonedWithHints` 로 따로 셉니다.

> **아직 재지 못한 것:** 순위 가중의 문턱(3·6)과 3배 상한은 **손으로 정한 값**입니다.
> 깊을수록 강하다는 방향만 웹 검색 클릭 모델에서 확립된 것이고, 이 코퍼스에서
> 측정하지 않았습니다. `dest = reads.last()` 라는 전제 자체도 그대로입니다.
> `/api/stats` 의 `pWrong` 과 `sinceStart` 로 모니터링하세요.

### 검색 결과에 어떻게 섞이나

어휘 순위와 학습 힌트를 RRF 로 융합하되, **힌트는 순위가 아니라 신뢰도로 가중**합니다 —
EB 하한은 이미 확률이라 순위로 뭉개면 보정된 정보를 버리게 됩니다.

```
어휘   1 / (60 + rank + 1)
학습   1.6 × 신뢰도 / (60 + rank + 1)
```

결과에 붙는 표시가 그것입니다 — `*` 는 양쪽에서, `S` 는 학습에서만 나온 것입니다.
`S` 가 곧 **어휘 검색이 못 찾은 문서를 학습이 찾아낸 경우**이고, 이 레이어의 존재 이유입니다.

**힌트도 ACL 을 다시 통과합니다.** 학습 레이어는 권한을 모르므로 융합 시점에 한 번 더
거릅니다 — 이중 방어선입니다.

---

## 저장소 구조

| 경로 | 내용 | 언어 |
|---|---|---|
| **`cli/`** | Confluence 싱크 · 파싱 · 앵커 전치 — **볼트를 만드는 유일한 곳** | Python |
| **`server/`** | Lucene/Nori 색인 · 학습 레이어 · HTTP API | Kotlin |
| **`plugin/local/`** | 스킬 + 커맨드 2개 ([사용 안내](plugin/local/README.md)) | Python |
| **`plugin/client/`** | MCP 도구 4개 + 스킬 ([사용 안내](plugin/client/README.md)) | Python |
| `contract/` | 교차 언어 계약 검사 + 공유 골든 픽스처 | — |
| `docs/` | [아키텍처](docs/architecture.md) · 임베딩 설계 제안 | — |
| `.claude-plugin/` | 마켓플레이스 매니페스트 (**반드시 저장소 루트**) | — |

Python 과 Kotlin 은 **파일로만** 연결됩니다 — 볼트 포맷이 곧 인터페이스입니다.
그래서 한쪽만 바꾸면 에러 없이 조용히 틀어지고, `contract/shared_contract.sh` 가
그것을 막습니다.

[`DECISIONS.md`](DECISIONS.md) — 뒤집힌 결정과 지우면 안 되는 것들. **되돌리기 전에 읽으세요.**

---

## 적용 범위

**특정 회사에 묶여 있지 않습니다.** Confluence **Cloud** 와 **Server/Data Center** 양쪽,
인증 4방식을 지원합니다. Confluence 고유 개념(`ac:link` storage format · `ancestors` · CQL)
에만 의존하므로 어느 조직의 인스턴스든 같은 코드가 돕니다.

> 저장소 곳곳의 `Coway 2,383건` 같은 표기는 **측정 출처 라벨**입니다 — 그 수치가 어느
> 코퍼스에서 나왔는지 밝히는 것이지 종속이 아닙니다. 배포되는 플러그인·CLI 에는
> 회사 고유값이 없습니다.

**언어는 한국어를 기본으로 합니다.** 서버판이 Lucene **Nori** 형태소 분석기를 쓰는데,
교착어라 조사가 붙어서('로그인을/로그인은/로그인이') 형태소 분석 없이는 BM25 가
무너지기 때문입니다 — 그것이 서버를 JVM 으로 고른 유일한 이유입니다.

Nori 는 **영문을 깨뜨리지 않습니다** — 공백·구두점으로 자르고 소문자화합니다. 다만
**어간을 안 줄여서** 문서 `production servers` 에 질의 `production server` 가 안 맞습니다.
한국어 문서에 `Jenkins`·`DEPLOY_TOKEN` 같은 영문 식별자가 섞인 코퍼스에서는 그것들이
굴절하지 않으므로 문제가 안 됩니다.

영어가 주된 코퍼스라면 **색인할 때** 분석기를 고르세요 —
`--wikilens.analyzer=korean|english|standard`. 로컬판은 grep 이라 언어와 무관합니다.
자세한 것은 [`server/README.md`](server/README.md#분석기는-색인-시점에-고른다).

---

## 설계 결정 요약

**앵커 텍스트가 가치의 대부분입니다.** 실데이터로 베이스라인(순수 MD 검색)과 비교했을 때
`ALIASES.md` 가 정확도·토큰 사용량 둘 다 크게 앞섰습니다. 신규 기법이 아니라
(Brin & Page 1998) 신뢰도가 높습니다.

**위키에 쓰지 않습니다.** 쓰면 사람 링크와 기계 링크가 섞여 정답 신호가 영구히
오염됩니다. 읽기 전용은 규율이 아니라 설계로 얻는 보장입니다.

**서버가 볼트를 배포하지 않습니다.** 배포된 사본은 **회수할 수 없어** 권한 취소가
불가능해집니다. 서버가 서빙하면 매 요청 ACL 이 걸리고, 부수적으로 훅도 불필요해집니다.

**서버가 색인을 갖습니다.** 클라이언트 분산 색인은 Confluence 부하가 사용자 수에
비례하고(200명이면 200배), 무엇보다 사용자마다 랭킹 척도가 달라져 학습 레이어에
이질적 관측이 섞입니다. 대가는 질의 시점 ACL 시행입니다.

**경로 의존 질의는 캐싱하지 않습니다.** "이 데이터가 어떻게 흐르나"에 목적지만 주면
답한 게 아니라 답을 지운 겁니다.

**압축이 아니라 가산입니다.** 원본 궤적을 보존합니다 — 무효화·폴백·출처 추적이 전부
여기 의존하고, 궤적은 유일하게 복구 불가능한 자산입니다.

자세한 근거는 [`docs/architecture.md`](docs/architecture.md), 기각된 안과 뒤집힌 결정의
이력은 [`DECISIONS.md`](DECISIONS.md).

---

## 만드는 사람을 위해

### 변경 후 이 넷이 통과해야 합니다

```bash
./contract/shared_contract.sh          # 교차 언어 계약
python -m pytest -q                    # cli/tests + plugin/tests
cd server && ./gradlew test            # JUnit
python3 plugin/tests/test_mcp_proxy.py # pytest 가 안 걷는 독립 스크립트
```

IntelliJ 를 쓴다면 `.idea/runConfigurations/` 의 **"3. 전체 검증"** 이 넷을 한 번에
돌립니다. 서버를 띄울 때는 **"1. 서버 실행 (bootRun)"** 을 쓰세요 — `fun main` 옆
화살표로 띄우면 작업 디렉터리가 달라져 볼트·색인·**궤적**이 다른 자리를 씁니다.

작업 규율은 [`CLAUDE.md`](CLAUDE.md) 에 있습니다 — 절대 깨면 안 되는 계약,
조용히 실패하는 것들, 의도적으로 이상해 보이는 것.

### 검증 상태

| | 검증 |
|---|---|
| Python CLI · 앵커 전치 · 파서 | 통과 — 공유 골든 픽스처로 Kotlin 과 같은 산출물 대조 |
| Confluence 클라이언트 | 통과 — 가짜 서버로 Cloud/Server·429·페이지네이션·재개 |
| 인증 계층 (SSO/IAM) | 통과 — 가짜 IAM 으로 OAuth2·만료 갱신·401 재시도 |
| Kotlin 학습 레이어 (`learn/`) | JUnit 통과. 기대값 6개가 `scoring_reference.py` 산출과 1e-6 일치 |
| Kotlin 서비스 계층 | JUnit 통과 |
| Lucene/Spring 배선 | 빌드·bootRun·재색인 검증됨 (실데이터 2,383건) |
| **검색 랭킹 품질** | **미검증** — 배선이 도는 것과 랭킹이 좋은 것은 다른 문제입니다 |
| **실사용 적중률** | **측정된 바 없음** |

**개수는 일부러 안 적습니다** — 늘 때마다 낡습니다. 위 네 스위트를 돌리면 나옵니다.

### 측정 지표 (우선순위 순)

1. **제목과 다른 별칭 비율** — 낮으면 여기서 중단
2. **비링크 도달 비율** — 낮으면 학습 레이어가 링크 그래프의 복사본으로 퇴화
3. **`pWrong`** — 손익분기 `p_hit > p_wrong/(n−1)` 의 분자. 적중률보다 이게 기준
4. 적중당 절약 대 미스당 검증 오버헤드

---

## 미해결

**서버판 인증 — 다중 사용자 배포의 선결 조건입니다.** `sync` 가 Confluence 권한을
안 가져와 모든 페이지가 공개로 들어오고, `/api/admin/*` 에는 인증이 아예 없어 서버에
닿는 누구나 스스로 권한을 부여할 수 있습니다. 게다가 권한 변경은 `lastModified` 를
건드리지 않아 콘텐츠 증분 싱크가 놓칩니다. **셋이 한 묶음입니다**
(로컬판에는 해당 없음 — 개인 토큰이 곧 권한 범위).

**"유용했다"의 판정.** 서버는 무엇을 읽었는지만 보고 그게 답이었는지는 모릅니다.
신호 다섯을 씁니다 — 마지막 읽기, 질의 재구성, **서빙했는데 끝내 안 읽힌 힌트**,
지나친 읽기, `dest` 의 검색 순위. 셋째가 유일하게 학습을 **되돌리는** 신호입니다.
`dest = reads.last()` 라는 전제 자체는 그대로입니다.

**질의 원문이 서버로 갑니다.** 콘텐츠는 아니지만 질의어 자체가 민감할 수 있습니다.
