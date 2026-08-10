# WikiLens CLI — 싱크 + 로컬판

> 프로젝트 전체 안내는 [../README.md](../README.md) 참조

Confluence 위키를 로컬 마크다운으로 미러링하고, **다른 문서들이 각 페이지를 실제로
부르는 이름**을 모은 별칭 색인을 만듭니다. 서버 없음, 인덱스 서버 없음, 그냥 파일과 grep.

## 왜

문서 제목과 사람들이 쓰는 말이 다릅니다. 제목이 `OAuth 2.0 인가 코드 흐름`인데
다들 "로그인 붙이는 법"이라고 부르죠. 그 표현은 제목에도 본문에도 없습니다.

그런데 **다른 문서들이 그 페이지로 링크할 때 쓴 앵커 텍스트**에는 있습니다.
WikiLens는 그 링크를 대상 기준으로 뒤집어(전치) 색인합니다.

```console
$ grep "로그인" ALIASES.md
PLATFORM | OAuth 2.0 인가 코드 흐름 | 로그인 붙이는 법 · 인증 붙이기 | 2 | mirror/pages/01/200000001.md
```

본문만 뒤졌다면 **엉뚱한 문서**가 나옵니다:

```console
$ grep -rl "로그인" mirror/pages/
mirror/pages/04/200000004.md       ← "온보딩 체크리스트". OAuth 문서를 그렇게 부르며 링크한 페이지
```

그 표현으로 *링크한* 페이지지, 찾으려는 문서가 아닙니다. 이 차이가 이 도구의 전부입니다.

## 설치

```bash
pip install -e .
```

## 사용

```bash
export CONFLUENCE_URL=https://mycompany.atlassian.net
export CONFLUENCE_EMAIL=me@mycompany.com     # Cloud만. Server/DC는 생략
export CONFLUENCE_TOKEN=...                   # 개인 API 토큰 또는 PAT

wikilens --root ~/wiki sync --space PLATFORM --space ENG
wikilens --root ~/wiki stats
```

**`export` 는 그 셸에서만 삽니다.** 그래서 CLI 는 환경변수가 없으면
`~/.wikilens/env.sh`(권한 600)를 읽습니다 — cron 이나 Claude Code 처럼 `export` 가
없는 환경에서도 그냥 동작합니다. 환경변수가 있으면 그쪽이 이깁니다(일회성 재정의).

```bash
mkdir -p ~/.wikilens && chmod 700 ~/.wikilens
printf 'export CONFLUENCE_URL=%s\nexport CONFLUENCE_TOKEN=%s\n' "$CONFLUENCE_URL" "$CONFLUENCE_TOKEN" \
  > ~/.wikilens/env.sh && chmod 600 ~/.wikilens/env.sh
```

플러그인을 쓴다면 `/wikilens-local:setup` 이 만들어 줍니다
(`setup_vault.py --capture-env` 가 지금 셸의 값을 화면에 안 찍고 옮깁니다).
셸 스크립트로 두는 이유는 자기 터미널에서 `source ~/.wikilens/env.sh` 로 그대로
재사용하기 위해서입니다.

`sync`는 원본만 받고 자동으로 `build`를 이어서 실행합니다.
`build`는 순수 로컬이라 네트워크 없이 몇 번이든 다시 돌릴 수 있습니다.

`--full`은 전체 재싱크 + 삭제 감지입니다. 증분 싱크(`lastModified > cursor`)로는
삭제된 페이지가 원리적으로 잡히지 않으니 가끔 돌려주세요.

### `acl` — 서버판을 쓸 때만

```bash
wikilens --root ~/wiki acl
```

페이지별 읽기 권한을 `mirror/acl/acl.json` 에 모읍니다. **`sync` 와 분리돼 있고 더
자주 돌려야 합니다** — 권한 변경은 `lastModified` 를 안 건드려 증분 싱크가 영영 못
잡습니다. 로컬판은 개인 토큰이 곧 권한 범위라 필요 없습니다.

`byOperation` 은 **직접 제한만** 주므로 상속을 `ancestors` 로 직접 풉니다. 제한이
없는 페이지는 `@public` 이 아니라 `@space:<KEY>` 를 받습니다 — 여러 스페이스를 한
볼트에 모으면 사용자마다 볼 수 있는 스페이스가 다르기 때문입니다(`DECISIONS.md` D19).

출력에서 볼 것:

| | 뜻 |
|---|---|
| `실패` | 조회를 못 한 페이지. **공개로 바뀌지 않습니다** — 옛 값을 지키고, 처음 보는 페이지면 빠집니다 |
| `미확정` | 자신은 읽었지만 **조상을 못 읽어** 확정 못 한 페이지. 다시 돌리면 대개 해소됩니다 |

둘 중 하나라도 0 이 아니면 종료 코드가 1 입니다. 조회가 **전부** 실패하면 파일을
아예 쓰지 않습니다 — 배운 게 없는데 덮으면 옛 값까지 사라지고, 서버는 빈 파일을
"전 페이지 비공개" 로 읽습니다.

```bash
wikilens --root ~/wiki acl && curl -XPOST -H "X-WikiLens-Admin: $TOKEN" .../api/admin/reindex
```

`&&` 가 중요합니다 — 실패했는데 재색인하면 반쪽 권한이 반영됩니다.

### 도입 판단

```console
$ wikilens --root ~/wiki stats
페이지 2383개
  별칭 보유 403 (17%)
제목과 어휘가 안 겹치는 별칭을 가진 페이지: 42 (2%)
```

**이 비율이 낮으면 이 도구는 값어치가 없습니다.** 어휘 격차가 없다는 뜻이니까요.
먼저 재보고 판단하세요.

위 출력은 지어낸 예시가 아니라 **실제 코퍼스(Acme CWDOMESTICDT)** 입니다 —
그리고 2% 는 낮은 쪽입니다. 이 위키는 문서끼리 링크를 거의 안 걸어서(인링크 중앙값 0)
앵커라는 신호 자체가 얇습니다. 그렇다고 `ALIASES.md` 가 쓸모없다는 뜻은 아닙니다:
그 42개는 **다른 방법으로는 못 찾는** 페이지고, 파일 자체는 전체 2,383개를 담습니다
(별칭 없는 것은 제목만). 2% 는 "부가 정보의 커버리지"지 도달 범위가 아닙니다.

## Claude Code 플러그인

```bash
/plugin marketplace add <저장소 경로 또는 비공개 git URL>
/plugin install wikilens-local@wikilens
/reload-plugins
```

스킬만 담긴 플러그인입니다. MCP 서버도 훅도 없습니다 — 에이전트가 네이티브
grep과 Read로 볼트를 쓰고, 스킬은 "본문보다 `ALIASES.md`를 먼저 보라"는 순서만 알려줍니다.

## 구조

```
ALIASES.md                        별칭 색인. 한 줄에 스페이스·제목·별칭·인링크수·경로
mirror/raw/{id뒤2}/{id}.xhtml     Confluence 원본. 무손실. 권위
mirror/pages/{id뒤2}/{id}.md      마크다운. 사람·grep용
mirror/structure/{id뒤2}/{id}.json  구조 서명 (제목·헤딩·링크집합)
mirror/.sync-state.json           커서 + 페이지 메타데이터
derived/anchors.jsonl             별칭 원자료
```

### 설계 결정

**페이지 ID가 식별자입니다.** 제목이 아닙니다. 제목으로 경로를 잡으면 이름 변경이
삭제+생성으로 보입니다.

**원본을 버리지 않습니다.** `raw/`에 Confluence storage format 원본이 그대로 있습니다.
마크다운 변환은 매크로를 일부 잃지만, 원본이 있으니 되돌릴 수 있고 변환기가
개선되면 재크롤 없이 재변환할 수 있습니다.

**앵커는 원본 XHTML에서 뽑습니다.** storage format은 링크가 구조화돼 있어
(`ac:link` + `ri:page` + `ac:plain-text-link-body`) 앵커와 대상의 대응이 정확합니다.
마크다운으로 내린 뒤에는 그 구조가 평평해집니다.

**구조 서명을 본문과 분리합니다.** 오타 수정은 `pages/`만 바꾸고 `structure/`는
그대로여야 합니다. **지금은 아무도 읽지 않습니다** — 증분 build 를 도입하는 순간
`git diff -- structure/` 로 무효화 대상을 뽑는 데 쓰입니다(`version` 은 오탈자에도
오르니 신호로 너무 거칩니다). 포맷을 나중에 고치면 전체 재크롤이 필요하므로 지금
고정합니다. 근거는 `DECISIONS.md` D7.

**결정적 직렬화.** 키 정렬 · 배열 정렬 · 고정 구분자 · `ensure_ascii=False`.
아니면 아무것도 안 바뀌었는데 전체 파일이 변경으로 잡힙니다.

**모호한 제목은 해석하지 않습니다.** 같은 제목이 여러 스페이스에 있으면 링크를
미해결로 남깁니다. 틀린 간선보다 없는 편이 낫습니다.

**ACL은 개인 토큰으로 해결됩니다.** 각자 자기 권한으로 싱크하니 볼 수 있는 것만
볼트에 들어옵니다. 권한 필터링 코드가 필요 없습니다.

## 하지 않는 것

- **위키에 쓰지 않습니다.** 읽기 전용. 볼트 수정은 다음 싱크에 덮어써집니다
- **랭킹하지 않습니다.** grep 순서가 전부입니다. IDF도 BM25도 없습니다
- **세션 간 학습이 없습니다.** 1인 사용에서는 통계가 쌓이지 않아 어차피 무의미합니다

이 셋이 필요하면 서버판 영역입니다 → **[../server/](../server/)**.
로컬판이 실제로 쓰이는지 먼저 확인하세요.

## 테스트

```bash
python -m pytest -q        # 이 디렉터리에서는 CLI 테스트만
```

멱등성 테스트가 중요합니다 — 두 번 빌드해서 바이트가 달라지면 서버판에서
`git diff` 기반 무효화가 매번 전체 발화합니다.
