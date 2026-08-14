# WikiLens 서버 (Spring Boot + Lucene/Nori)

Confluence 미러를 **서버측에서 색인**하고, 탐색 궤적을 축적해 랭킹을 보강합니다.
읽기는 여전히 클라이언트 로컬입니다 — 서버는 좌표만 반환합니다.

## 검증 상태 (먼저 읽으세요)

| 레이어 | 검증 |
|---|---|
| `learn/` (게이트·EB·궤적·포스팅) | JUnit 통과. 기대값 6개가 `scoring_reference.py` 산출과 1e-6 일치 |
| `index/` `service/` `api/` `vault/` `acl/` (Lucene·Spring 배선) | 빌드·`bootRun`·재색인 검증됨 (Coway 실데이터 2,378건, 2026-08) |
| JUnit 전체 | 통과 (`./gradlew test`) |
| 검색 랭킹 **품질** | **미검증.** 배선이 도는 것과 랭킹이 좋은 것은 다른 문제다 |

```bash
./gradlew test       # JUnit 전체
./gradlew bootRun    # 개발용 기동 (작업 디렉터리가 server/ 로 고정된다)
```

## 목차

**처음 띄우는 것이면 [띄우기](#띄우기) 셋만 읽으면 됩니다.** 그전 배포에서 올리는
것이면 [옛 배포에서 올리기](#궤적을-먼저-옮기세요) 를 먼저 보세요 —
**궤적은 유일한 복구 불가 자산**이라 놓치면 되돌릴 수 없습니다.

**띄우기**
1. [볼트 만들기](#처음-clone-했다면-볼트부터-만드세요)
2. [Docker 로 띄우기](#docker-로-띄우기)
3. [경로·PATH](#배포할-때는-경로를-절대경로로-주세요)

**운영**
- [관리 API 잠금](#관리-api-는-기본이-잠김입니다) · [권한 수집](#권한-수집은-sync-와-따로-돌립니다) ·
  [사용자 등록](#사용자-등록은-재기동을-넘습니다) · [ACL 시행 스위치](#acl-시행을-끌-수-있습니다--다만-조건이-좁습니다)
- [정기 갱신·cron](#싱크는-python-그대로) · [분석기 고르기](#분석기는-색인-시점에-고른다) ·
  [상태 디렉터리 락](#서버는-상태-디렉터리당-하나만-뜹니다) · [세션 거두기](#버려진-세션은-백그라운드로-거둡니다)

**진단**
- [`--status` 지표](#--status-로-볼-지표) · [본문 스캔 엔진](#본문-스캔-엔진이-둘입니다) · [API](#api)

**옛 배포에서 올리기**
- [궤적 옮기기](#궤적을-먼저-옮기세요) · [Docker 볼륨](#docker-로-운영했다면-2026-08-12-변경) ·
  [작업 디렉터리](#작업-디렉터리는-이제-상관없습니다)

**왜 이렇게 만들었나**
- [서버 색인](#왜-서버가-색인을-갖는가) · [JVM](#왜-jvm인가) · [구성](#구성) ·
  [볼트 미배포](#acl--클라이언트에-배포하지-않는-이유) · [훅 없음](#훅이-없는-이유) ·
  [토크나이저](#토크나이저-정본은-서버)

**미해결**
- [여전히 미해결](#여전히-미해결) — 배포 전에 읽으세요

---

# 띄우기

## 처음 clone 했다면 볼트부터 만드세요

기본값이 전부 **홈 아래**라 저장소 위치·작업 디렉터리와 무관합니다:

| | 기본값 |
|---|---|
| 볼트 | `~/.wikilens/vault` |
| 색인 | `~/.wikilens/index` |
| 상태(궤적·사용자 등록) | `~/.wikilens/state` |

**대개는 아무것도 설정할 것이 없습니다.** 로컬판으로 볼트를 만들었다면 서버가 그대로
찾습니다. 없으면 만듭니다(`cli/README.md` 참고):

```bash
wikilens sync --space <KEY>          # --root 를 안 주면 ~/.wikilens/vault
```

다른 자리에 두려면 명시합니다 — **명시가 항상 이깁니다**:

```bash
--wikilens.vault-root=/srv/wikilens/mirror
```

`~/.wikilens/config.json` 에 `vault` 가 적혀 있으면 그것도 읽습니다
(`config/UserConfig.kt`). **사본은 만들지 마세요** — 볼트가 둘이 되면 어느 쪽을
색인했는지 알 수 없습니다. 기동 로그 첫 줄이 실제로 고른 경로를 찍습니다.

## Docker 로 띄우기

```bash
export WIKILENS_ADMIN_TOKEN=<임의의 긴 ASCII 문자열>
WIKILENS_VAULT=~/.wikilens/vault docker compose up -d --build
```

`compose.yml` 이 저장소 루트에 있습니다. **요점은 볼륨 셋입니다** — 이미지에는 코드만
있고 데이터는 전부 밖에 있습니다.

| 마운트 | 무엇 | 지우면 |
|---|---|---|
| `/vault` (`:ro`) | 위키 미러. 호스트의 `wikilens sync` 가 만듭니다 | 재싱크 |
| `/state` | 궤적 로그·사용자 등록 | **복구 불가** — 백업 대상은 여기뿐입니다 |
| `/index` | Lucene 색인 | 재색인으로 복구 |

**볼트는 이미지에 굽지 않습니다.** 위키 본문은 데이터이고, 이미지에 구우면 그 사본을
회수할 수 없습니다 — 레지스트리에 올라간 순간 권한 취소가 불가능해집니다. `:ro` 로
마운트하는 것도 같은 이유입니다. 읽기 전용을 규율이 아니라 마운트 옵션으로 얻습니다.

**싱크는 이 컨테이너가 하지 않습니다.** `sync`·`acl` 은 Confluence 자격증명이 필요한데
서버는 그걸 가질 이유가 없습니다(가지면 "위키에 쓰기 금지" 가 설계 보장에서 규율로
내려갑니다 — `DECISIONS.md` D22). 호스트 cron 이 돌리고 끝나면 재색인을 부릅니다:

```bash
wikilens sync --root ~/.wikilens/vault && wikilens acl --root ~/.wikilens/vault \
  && curl -XPOST -H "X-WikiLens-Admin: $TOKEN" localhost:8787/api/admin/reindex
```

`&&` 가 필수입니다 — 실패했는데 재색인하면 반쪽 상태가 반영됩니다.

**이미지가 ripgrep 을 갖고 있습니다.** 호스트에서는 서비스 매니저의 `PATH` 에 따라
조용히 JVM 스캔으로 떨어질 수 있는데(아래 절), 컨테이너에서는 우리가 PATH 를 정하므로
그 불확실성이 없습니다.

**한 상태 디렉터리에 서버 하나입니다.** `StateDirLock` 이 둘째 기동을 거부합니다.
스케일 아웃하려면 `state` 볼륨을 나눠야 하는데 그러면 학습이 갈립니다 — 지금은
복제하지 않는 것이 맞습니다.

**루트로 안 돕니다**(uid 10001). 마운트한 디렉터리를 그 uid 가 읽을 수 있어야 합니다.

> 확인한 것: 볼트 마운트 → 색인 2건, 등록 → 검색·읽기·grep(engine=ripgrep) 정상,
> `docker restart` 후 사용자 등록과 궤적이 그대로 남음(`/state` 볼륨).

## 배포할 때는 경로를 절대경로로 주세요

기본값은 홈 아래라(`~/.wikilens/{vault,index,state}`) 작업 디렉터리와 무관합니다 —
그래서 **아래 세 인자는 대개 필요 없습니다.** 볼트를 다른 자리에 두었거나 한 호스트에
여러 인스턴스를 띄울 때만 씁니다. 그때는 절대경로로 주세요.

> 그전에는 `./mirror-root`·`./.wikilens/state` 처럼 상대경로였고, 그것이 위
> "작업 디렉터리는 이제 상관없습니다" 절이 다루는 문제였습니다(2026-08-11 변경).

`PATH` 도 함께 보세요. rg 는 이름(`rg`)으로만 찾습니다 — 셸이 아니라 `execvp` 라
별칭·셸 함수·`~/.zshrc` 의 PATH 추가가 안 보입니다. systemd·launchd·cron 의 기본
PATH 에는 `/opt/homebrew/bin` 이나 `/usr/local/bin` 이 없는 경우가 많아, **개발
머신에서는 rg 로 돌던 서버가 서비스로 띄우면 조용히 JVM 스캔으로 떨어집니다.**
답은 같고 느려질 뿐이지만(대조 테스트가 그것을 지킵니다), 알고 그러는 것과 모르고
그러는 것은 다릅니다 — `--status` 의 `GREP_ENGINE` 이 어느 쪽인지 말합니다.
유닛 파일에 `Environment=PATH=/opt/homebrew/bin:/usr/bin:/bin` 처럼 넣으면 됩니다.
(Docker 로 띄우면 이미지가 rg 를 갖고 있어 이 문제가 없습니다.)

엉뚱한 디렉터리에서 띄우면 **에러 없이 정상 기동합니다** — 빈 볼트를 보고 문서 0개로
뜨고, `state/` 도 새로 만들어 학습 궤적이 분기됩니다(실측 2026-08-06).
궤적은 유일한 복구 불가 자산입니다.

```bash
java --enable-native-access=ALL-UNNAMED -jar wikilens-server.jar \
  --wikilens.vault-root=/srv/wikilens/mirror \
  --wikilens.index-dir=/srv/wikilens/index \
  --wikilens.state-dir=/srv/wikilens/state
```

`--enable-native-access` 는 **로그를 위한 것이지 성능을 위한 것이 아닙니다.** Lucene
`MMapDirectory` 가 `posix_madvise` 를 FFM API 로 부르는데(커널 readahead 힌트), Java 22
부터 네이티브 호출이 "restricted" 라 승인하지 않으면 기동마다 경고 네 줄이 찍힙니다.
빼도 동작합니다 — 실측으로 `--illegal-native-access=deny`(미래 JDK 동작) 에서도
색인 2,383건이 정상이고 시간도 같습니다(2,279ms 대 2,171ms, 노이즈 수준). Lucene 이
madvise 를 못 쓰면 그냥 안 씁니다.

그런데 **이 로그가 진단 도구입니다** — 볼트·색인·상태 경로, `궤적 N건 재생`,
`기동 적재 완료` 를 눈으로 확인하는 구조라, 무해한 경고가 그 사이를 가리면 곤란합니다.
`bootRun` 과 `test` 는 `build.gradle.kts` 에서 이미 켜져 있습니다.

기동 로그 첫 줄이 **실제로 쓰는 절대경로**를 찍습니다. systemd·cron 처럼 작업
디렉터리를 못 믿는 환경에서는 이 줄부터 확인하세요:

```
볼트 /srv/wikilens/mirror · 색인 /srv/wikilens/index · 상태 /srv/wikilens/state
기동 적재 완료: 문서 2377 · ACL 페이지 2377
```

문서가 0개면 `기동 적재 완료` 대신 **ERROR** 가 찍힙니다.

---

# 운영

## 관리 API 는 기본이 잠김입니다

```bash
--wikilens.admin-token=<임의의 긴 문자열>
```

**안 주면 `/api/admin` 하위가 전부 404 입니다.** 열림이 기본이면 조용히 열린 채
배포되기 때문입니다 — 그러면 서버에 닿는 누구나 `POST /api/admin/acl/user?userKey=자기자신`
으로 스스로 권한을 부여합니다. 호출할 때는 헤더를 붙이세요:

```bash
curl -XPOST -H "X-WikiLens-Admin: $TOKEN" .../api/admin/reindex
```

거부는 403 이 아니라 **404** 입니다 — 403 은 그 엔드포인트가 있다는 것을 알려줍니다.

**엔드포인트마다가 아니라 경로로 걸립니다.** `/api/admin` 하위 전체에 인터셉터가
붙으므로 새 엔드포인트를 추가하는 사람이 인증을 기억할 필요가 없습니다. 예전에는
핸들러가 각자 검사를 불렀고, 그때 계약은 `@PostMapping` 만 세고 있어서 인증 코드가
전혀 없는 `@GetMapping("/api/admin/dump")` 를 넣어도 계약 전부가 통과했습니다(실측).

> 토큰은 ASCII 로 쓰세요. 서블릿은 헤더를 ISO-8859-1 로 디코드하는데 설정값은
> UTF-8 이라, 예전에는 한글 토큰이 **영원히 404** 였습니다(실측). 지금은 바이트로
> 비교해 동작하지만, 헤더 값에 비ASCII 를 쓰는 것은 RFC 7230 밖이라 중간 프록시가
> 손대면 다시 깨집니다 — 기동 시 경고합니다.

> **`userKey` 위조는 이걸로 못 막습니다.** MCP 프록시가 설정의 `user` 를 그대로
> 보내므로 사용자가 자기 신원을 자기가 주장합니다. 그걸 막으려면 리버스 프록시(SSO)가
> 헤더로 신원을 주입해야 하고, 공유 토큰은 그 아래 깔리는 바닥입니다 —
> `DECISIONS.md` D18.

## 권한 수집은 `sync` 와 따로 돌립니다

```bash
wikilens acl --root ~/.wikilens/vault      # 그다음 POST /api/admin/reindex
```

**권한 변경은 `lastModified` 를 건드리지 않습니다.** 증분 `sync` 는 그것을 영영 못
잡으므로, 더는 볼 수 없게 된 페이지를 계속 서빙하게 됩니다 — 공유 서버에서는 그것이
전 사용자에게 나갑니다. 콘텐츠보다 자주 돌리세요.

**못 읽은 것은 공개로 바뀌지 않습니다.** 조회에 실패한 페이지는 옛 값을 지키고,
처음 보는 페이지면 아예 빠집니다. 조상을 못 읽었으면 그 자식도 확정하지 않습니다
(`미확정 N` 으로 보고합니다) — 계속 위로 올라가면 잠긴 부모 밑의 문서가 `@space` 를
받습니다. `acl.json` 에 없는 페이지는 서버에서 아무에게도 안 보입니다 — 파일이
아예 없을 때(= 수집 전)만 전 페이지 `@public` 입니다. 둘은 다릅니다.

**전부 실패하면 파일을 쓰지 않습니다.** 배운 게 없는데 덮으면 옛 값까지 사라지고,
그 결과는 전 페이지 블랙아웃입니다. 종료 코드 1 이라 `&&` 사슬이 거기서 멈춥니다.
낱개 조회는 페이지 수만큼 나가므로(13,921건이면 요청도 그만큼) 429 백오프가
모든 GET 에 걸려 있습니다.

- 제한이 없는 페이지는 `@public` 이 아니라 `@space:<KEY>` 를 받습니다. 여러
  스페이스를 한 볼트에 모으면 사용자마다 볼 수 있는 스페이스가 다르기 때문입니다.
  운영자가 `acl/user` 로 스페이스 토큰을 주는 만큼만 열립니다.
- 제한이 있으면 `user:<이름>` · `group:<이름>` 이 붙고, 상속도 풀어서 적힙니다.
- **조회에 실패한 페이지는 공개로 바뀌지 않습니다** — 이전 값을 유지하고, 처음 보는
  페이지는 아예 빠집니다(아무에게도 안 보임).

## 사용자 등록은 재기동을 넘습니다

`state/acl-users.json` 에 원자적으로 저장됩니다. 예전에는 메모리 전용이라 재기동마다
전원이 사라졌고, 그 상태가 "문서가 없다"와 구별되지 않았습니다.

## ACL 시행을 끌 수 있습니다 — 다만 조건이 좁습니다

```bash
--wikilens.acl-enforced=true       # 기본은 false (끔)
```

**끄면 등록 없이 접속한 누구나 색인된 전 문서를 봅니다.** `userKey` 도 필요 없어집니다.

왜 있느냐면, 지금의 "ACL" 이 실질적으로 사용자 허용목록이기 때문입니다 — `sync` 가
권한을 수집하지 않아 전 페이지가 `@public` 이고, 시행이 하는 일은 "등록된 사람인가"
뿐입니다. 그런데 등록을 안 하면 fail-closed 로 전원이 빈손이 되고, 그 상태가
**"문서가 없다"와 구별되지 않습니다.** 혼자 쓰거나 신뢰 경계 안에 띄우는 경우엔 그
허용목록이 얻는 것 없이 함정만 됩니다.

**끄는 것이 정당한 경우:** 볼트를 서비스 계정 하나로 싱크했고, 그 계정의 권한 범위를
이 서버에 닿는 전원이 공유해도 되는 배포 — 개인 서버 · 개발 · 신뢰 경계 안의
소규모 팀. 그 밖에서는 못 볼 문서가 그대로 나갑니다.

꺼두면 세 곳이 계속 말합니다 — 기동 로그 WARN · `/api/stats` 의 `aclEnforced` ·
`--status` 의 `ACL_ENFORCED=no`. 기동 로그만으로는 재기동 뒤 아무도 모르기 때문입니다.

> 스위치는 `AclRegistry` **한 곳**입니다. 소비자(`search`·`read`·`grep`·`tree`·학습 힌트)는
> 전부 `tokensFor`·`canSee` 만 거치므로 각자 분기할 일이 없습니다 — 각자 분기하면
> 한 곳이 빠져 반쪽으로 열립니다. 계약이 그것을 검사합니다.

## 싱크는 Python 그대로

서버가 Confluence를 직접 크롤하지 않습니다. 로컬판의 `wikilens sync`를
**서비스 계정으로 1회** 돌려 미러를 만들고, 서버는 그것을 읽습니다.
이미 동작하고 테스트된 것을 다시 만들 이유가 없습니다.

```bash
CONFLUENCE_TOKEN=<서비스계정> wikilens --root ./mirror-root sync --space PLATFORM \
  && curl -XPOST localhost:8787/api/admin/reindex
```

**`&&` 를 빼지 마세요.** 싱크가 실패했는데 재색인이 돌면 절반만 반영됩니다.

`/admin/reindex` 는 **볼트가 비면 색인을 건드리지 않고** `{"skipped":true}` 를 돌려줍니다 —
경로를 잘못 준 재색인이 마지막으로 성공한 색인을 지우면 안 되기 때문입니다. 기동 적재와
같은 코드(`IndexingService`)를 씁니다. 예전엔 둘이 따로였고, 그래서 기동 쪽에만
방어가 들어간 동안 이 엔드포인트가 색인 2,383건을 0으로 지웠습니다(실측).

### cron 으로 자동화할 때

CLI 는 자격증명을 **환경변수 → `~/.wikilens/env.sh`** 순으로 읽습니다. cron 은 환경이
최소라 `export` 가 없으므로, 서비스 계정 자격증명을 그 파일에 두면 crontab 이 깨끗해집니다:

```bash
mkdir -p ~/.wikilens && chmod 700 ~/.wikilens
cat > ~/.wikilens/env.sh <<'EOF'
export CONFLUENCE_URL=https://wiki.mycompany.com
export CONFLUENCE_TOKEN=<서비스 계정 PAT>
EOF
chmod 600 ~/.wikilens/env.sh
```
```cron
0 4 * * *  wikilens --root /srv/wikilens/mirror sync --space PLATFORM \
             && curl -sf -XPOST localhost:8787/api/admin/reindex
```

**예전에는 이게 조용히 실패했습니다** — CLI 가 환경변수만 읽어서 cron 에서는
`CONFLUENCE_URL 환경변수가 필요합니다` 로 죽었습니다(실측: `env -i` 로 재현).
`&&` 덕에 절반 반영은 막혔지만 볼트가 낡아가는 것을 알 방법이 없었습니다.
로컬판은 래퍼 스크립트로 막아뒀는데 서버판만 빠져 있었습니다.

crontab 에 직접 `CONFLUENCE_TOKEN=...` 을 쓰는 것도 됩니다(환경변수가 파일보다
우선합니다). 다만 `crontab -l` 에 토큰이 그대로 보입니다.

그리고 **사용자를 등록해야 아무거나 보입니다.** ACL 이 fail-closed 라, 등록 전에는
색인이 멀쩡해도 모든 검색이 빈손입니다 — 실측: 같은 질의가 등록 전 0건, `@public`
등록 후 3건(색인 2,377건 상태에서).

```bash
curl -XPOST -H "X-WikiLens-Admin: $TOKEN" \
  'localhost:8787/api/admin/acl/user?userKey=alice@corp' \
  -H 'Content-Type: application/json' -d '["@public"]'
```

> **헤더를 빼면 404 입니다** — 관리 API 는 기본이 잠김이고 토큰이 있어야 열립니다
> (위 "관리 API 는 기본이 잠김입니다"). 그게 없으면 서버에 닿는 누구나 이 명령을
> 자기 자신에게 실행해 **스스로 권한을 부여**할 수 있습니다.
>
> **그래도 `userKey` 위조는 못 막습니다.** 프록시가 설정의 `user` 를 그대로 보내므로
> 사용자가 자기 신원을 자기가 주장합니다 — 막으려면 리버스 프록시(SSO)가 헤더로
> 신원을 주입해야 하고, 공유 토큰은 그 아래 깔리는 바닥입니다(`DECISIONS.md` D18·D22).
> **그때까지는 신뢰 경계 안**(내부망 · 리버스 프록시 뒤 · 루프백)에만 띄우세요.

배포 직후 상태 확인은 클라이언트 플러그인의 진단이 가장 빠릅니다 —
주소·식별자·도달·색인 크기·등록 사용자 수를 한 번에 보여줍니다:

```bash
python3 plugin/client/mcp/wikilens_mcp.py --status
```

## 분석기는 색인 시점에 고른다

기본은 `korean`(Nori). 영어가 주된 코퍼스면 `english`, 다국어가 섞여 주 언어를
못 고르겠으면 `standard` 다.

설정하는 방법은 넷이고 **전부 실측으로 확인했습니다**(아래로 갈수록 우선순위가 높습니다):

```bash
# ① application.yml — 기본값이 여기 적혀 있습니다
wikilens:
  analyzer: english

# ② 환경변수 — systemd·Docker 에서 가장 흔합니다
WIKILENS_ANALYZER=english java -jar wikilens-server.jar

# ③ 시스템 속성
java -Dwikilens.analyzer=english -jar wikilens-server.jar

# ④ 앱 인자 — 일회성으로 바꿔볼 때
java --enable-native-access=ALL-UNNAMED -jar wikilens-server.jar --wikilens.analyzer=english
```

jar 안의 `application.yml` 을 고치려면 다시 빌드해야 하므로, 배포에서는 **②나 ④**를
쓰거나 jar 옆에 `application.yml` 을 두세요(그쪽이 jar 안의 것을 덮습니다).

이름을 틀리면 조용히 기본값으로 떨어지지 않고 **기동이 실패합니다** —
`알 수 없는 분석기 'korea'. 가능한 값: korean·english·standard`.

**설정은 "무엇으로 지을까"이지 "무엇으로 질의할까"가 아닙니다.** 질의는 항상 디스크
색인이 실제로 지어진 분석기를 씁니다 — 선택이 Lucene 커밋 데이터에 기록되고(색인과
원자적이라 따로 놀 수 없습니다) 서버가 그것을 읽어 씁니다. 그래서 설정을 바꿔도
재색인 전까지는 옛 분석기로 정상 동작하고, 둘은 재색인에서 만납니다:

```
색인은 'korean' 로 지어졌고 설정은 'english' 입니다. 검색은 'korean' 로 정상 동작합니다 —
설정을 적용하려면 POST /api/admin/reindex 로 다시 지으세요.
```

기록을 안 쓰고 설정을 따라가면 그 사이가 **에러 없이 0건**입니다. 색인에 답이 적혀
있는데 그것을 안 쓰고 어긋남을 재고만 있는 셈이라, 경고 대신 불일치가 성립하지 않게
바꿨습니다. 실측: 볼트 경로를 틀리게 주고 `--wikilens.analyzer=english` 로 띄워도
색인 2,383건이 그대로 살아 검색 8건이 나옵니다.

**설정을 바꾸는 절차는 재기동 하나입니다.** 값은 빈 생성 시점에 한 번 읽히므로 —
실행 중인 서버는 `application.yml` 을 고쳐도 모릅니다 — 재기동해야 하고, 재기동하면
기동 적재가 곧 재색인이라 그 자리에서 적용됩니다(포트가 열리기 전에 끝납니다):

```
색인은 'korean' 로 지어졌고 설정은 'english' 입니다…     ← 순간적으로 찍힌다
색인 재구축 2383건 · 분석기 english                       ← 곧바로 적용
```

수동 `POST /api/admin/reindex` 가 필요한 경우는 하나뿐입니다 — **볼트를 못 읽어 기동
적재가 건너뛰어졌을 때.** 그때만 불일치가 지속되고, 그 상태는 밖에서도 보입니다:

```bash
curl localhost:8787/api/stats     # analyzer · analyzerConfigured
python3 plugin/client/mcp/wikilens_mcp.py --status
#   ANALYZER=korean (설정은 english)
```

같으면 `--status` 가 한 줄만 찍고, 다를 때만 무엇을 해야 하는지 알려줍니다.


> **학습 궤적도 영향을 받습니다.** `trajectories.jsonl` 에는 분석된 항이 저장돼 있어
> 분석기를 바꾸면 옛 항과 새 질의가 안 맞습니다. 궤적은 복구 불가 자산이라 지우지
> 않지만, 그만큼 학습이 무효가 된다는 것은 알고 있어야 합니다.

## 서버는 상태 디렉터리당 하나만 뜹니다

`--wikilens.state-dir` 를 같게 주고 둘째를 띄우면 **기동을 거부합니다:**

```
***************************
APPLICATION FAILED TO START
***************************
Description:
다른 WikiLens 서버가 이미 이 상태 디렉터리를 쓰고 있습니다: …/state/.lock
```

막는 이유는 붙었을 때가 조용하기 때문입니다 — 포스팅은 각자의 힙에 있어 **서로의 학습을
재기동 전까지 못 보고**, 둘 다 같은 `trajectories.jsonl` 에 append 하며, `/api/stats` 가
두 값을 오갑니다. Lucene 의 `write.lock` 은 재색인 동안만 잡혀서 방어가 되지 못했습니다.

락은 프로세스가 어떻게 끝나든 OS 가 풀어줍니다 — 죽은 서버의 락이 남아 다음 기동을
막는 일은 없습니다. 인스턴스를 둘 띄우려면 `state-dir` 를 다르게 주세요(학습은 갈립니다).

## 버려진 세션은 백그라운드로 거둡니다

세션 종료는 MCP 프록시의 `atexit` 에 걸려 있어 프로세스가 SIGKILL 되거나 크래시하면
`/api/session/end` 가 안 옵니다. 그러면 그 세션의 궤적이 **확정되지 않아 학습에
들어가지 않습니다** — 사용자는 정상적으로 검색하고 읽었는데 배운 게 없습니다.

`SessionSweeper` 가 주기적으로 돌면서 조용해진 세션을 확정합니다. 기본은 5분마다,
30분 넘게 조용한 세션이 대상입니다:

```bash
--wikilens.learn.sweep-interval-millis=300000
--wikilens.learn.session-idle-millis=1800000
```

거둘 때 `버려진 세션에서 궤적 N건을 확정했습니다` 를 로그에 남깁니다.
즉시 돌리려면 `POST /api/admin/sweep` 입니다.

---

# 진단

## `--status` 로 볼 지표

| | 뜻 |
|---|---|
| `GREP_ENGINE` | 본문 스캔이 어느 경로였는지(`ripgrep`·`jvm`). 경로가 둘이라 답의 근거가 됩니다 — 기동 로그는 콘솔로만 나가고 응답의 `engine` 은 grep 을 던져야 보입니다. `grepEngineUsable` 이 false 면 **매 요청이 폴백**입니다 |
| `logWriteFailures` | **0 이 아니면 메모리 학습만 앞서가는 중입니다.** 검색은 정상이지만 재기동하면 그만큼이 사라집니다 — 상태 디렉터리의 여유 공간과 권한을 확인하세요 |
| `permissionScopes` | 지금까지 본 **권한 범위** 수(신원이 아닙니다). 1 이면 학습이 균질하고, 2 이상이면 권한 폭이 다른 관측이 한 포스팅에 섞이는 중입니다 |
| `trajectoryLog.replaySkipped` | 0 이 아니면 기동 시 **옛 궤적이 버려지는 중**입니다(대개 스키마 변경). 로그를 지우지 마세요 |
| `trajectoryLog.replayMillis` · `bytes` | 로그는 append-only 라 줄지 않습니다. 재생이 10초를 넘으면 기동 로그가 경고합니다 — 실측으로 100만 건이 5.3초 · 210MB 이고, 그때가 체크포인트를 설계할 시점입니다(`DECISIONS.md` D17) |

## 본문 스캔 엔진이 둘입니다

```bash
--wikilens.grep-engine=auto      # 기본. jvm · ripgrep 으로 고정할 수 있습니다
```

`auto` 는 `rg` 가 PATH 에 있으면 씁니다. 예산에 닿으면 실패가 아니라 **조용한 부분
응답**이라 사용자에게는 "없다"로 보입니다.

**언제 닿는지는 문서 수가 아니라 크기에 달려 있습니다.** 문서당 비용이
약 28us(파일 고정비) + 13us/KB 라서(JVM), 예산 3초를 그것으로 나눈 것이 한계입니다 —
평균 4KB 문서면 약 37,000건, 문서가 두꺼우면 그만큼 내려갑니다. rg 는 문서당 비용이
약 3~4배 쌉니다. 여러분의 배포에서 다시 재려면 `GrepScaleTest` 를 돌리세요
(합성 볼트로 재므로 코퍼스가 필요 없습니다).

> **D12 에 적힌 "65배" 는 이 맥락에서 틀린 수치입니다.** 그건 2,383건 시절 rg 를 셸에서
> 맨몸으로 워밍 없이 돌린 값입니다. "고정비를 두 엔진이 똑같이 내서" 라는 설명은
> 틀렸습니다 — 재보니 그 고정비는 전체의 0.18% 라 비를 전혀 안 바꿉니다. 두 값이
> 갈린 것은 측정 방법 차이(워밍 유무)입니다. 재현 가능한 값은 문서당 약 3~4배이고,
> `GrepScaleTest` 로 각자 다시 잴 수 있습니다.

**어느 엔진이 처리했는지는 grep 응답의 `engine` 에 나옵니다.** `auto` 는 머신에 rg 가
있는지에 따라 갈리므로, 답이 이상할 때 어느 경로였는지 물을 수 있어야 합니다.

두 경로가 같은 답을 내는지는 `GrepEngineParityTest` 가 지킵니다 — 14개 패턴을 두 엔진에
돌려 매치를 대조하고, rg 가 없는 머신에서는 건너뜁니다. **ACL 은 엔진 밖**에서 걸립니다
(`ContentService` 가 거른 목록만 넘깁니다) — 권한 해석이 엔진마다 갈리면 한쪽이 조용히
더 보여줍니다.

## API

```bash
# 검색 — 어휘 랭킹 + 학습 힌트 융합, ACL 필터링
curl -XPOST localhost:8787/api/search -H 'Content-Type: application/json' \
  -d '{"query":"로그인 붙이는 법","userKey":"alice@corp"}'


# 본문 읽기 — 매 요청 ACL 확인. 권한 없으면 404 (403은 존재를 알려주므로 유출)
curl -XPOST localhost:8787/api/read -H 'Content-Type: application/json' \
  -d '{"pageId":"200000001","userKey":"alice@corp","sessionId":"s1"}'

# 리터럴 검색 — 형태소 분석을 거치지 않는 정확 일치
curl -XPOST localhost:8787/api/grep -H 'Content-Type: application/json' \
  -d '{"pattern":"DEPLOY_TOKEN","userKey":"alice@corp"}'

curl localhost:8787/api/stats
```

`grep` 의 `regex=true` 는 **RE2 문법**입니다 — ripgrep 과 같은 엔진 계열입니다.
`java.util.regex` 를 쓰던 동안 사용자 패턴 하나가 서버 스레드를 영구히 묶거나
(`(.+)+@@@@`) 스택을 넘겨 HTTP 500 을 냈습니다(`(a|aa)+c`). RE2 는 유한 오토마타라
입력에 선형이고 재귀하지 않아 둘 다 원리적으로 없습니다.

**대소문자는 `regex` 와 무관하게 구분하지 않습니다.** 리터럴 경로가 `ignoreCase` 인데
RE2 기본은 구분이라, 맞춰주지 않으면 이 플래그가 문법뿐 아니라 민감도까지 바꿉니다
(실측: 본문 `Coway` 에 `coway` 가 리터럴 1건 · 정규식 0건). rg 프로세스를 붙인다면
`-i` 를 함께 넘겨야 답이 같습니다.

대가는 **역참조(`\1`)와 전방탐색(`(?=)`)** 을 못 쓰는 것입니다. 쓰면 조용히 0건이
되는 대신 `error` 필드로 이유가 옵니다. 속도를 위해 rg 프로세스를 붙이는 것은
아직 안 했고, 붙여도 문법이 같아 답이 갈리지 않습니다 — `DECISIONS.md` D12.

---

# 옛 배포에서 올리기

## 궤적을 먼저 옮기세요

기본값이 바뀌었으므로(2026-08-11) **옛 자리의 궤적을 서버가 못 읽습니다.** 색인은
재색인으로 복구되지만 궤적은 **유일한 복구 불가 자산**입니다:

```bash
# 옛 자리는 서버를 띄우던 작업 디렉터리 기준입니다 (server/ 또는 저장소 루트)
mv server/.wikilens/state/trajectories.jsonl ~/.wikilens/state/
mv server/.wikilens/state/acl-users.json     ~/.wikilens/state/   # 있으면
```

안 옮기면 기동 로그가 `기존 궤적 로그가 없어 새로 시작합니다` 로 알립니다 — 첫 배포와
구별되지 않으므로 그 줄이 보이면 옛 자리를 확인하세요.

### Docker 로 운영했다면 (2026-08-12 변경)

**상태·색인이 named volume 에서 호스트 `~/.wikilens` 아래로 옮겨졌습니다.** 그전에는
볼륨이 이름으로 붙어 아무것도 안 해도 됐지만, 이제는 **한 번 꺼내야 합니다** —
안 그러면 궤적이 옛 볼륨에 남아 고아가 됩니다:

```bash
docker compose down
mkdir -p ~/.wikilens/state
docker run --rm -v wikilens_wikilens-state:/s -v ~/.wikilens/state:/out alpine \
  sh -c 'cp -a /s/trajectories.jsonl /s/acl-users.json /out/ 2>/dev/null; true'
docker compose up -d --build
```

기동 로그의 `궤적 재생 N건` 또는 `/api/stats` 의 `trajectoryLog.replayed` 가 옮기기
전과 같으면 성공입니다. 확인한 뒤에 옛 볼륨을 지우세요:

```bash
docker volume rm wikilens_wikilens-state wikilens_wikilens-index
```

**색인은 안 옮겨도 됩니다** — 재색인으로 복구됩니다. 궤적만 복구 불가입니다.

**왜 바꿨나:** named volume 은 `rm -rf ~/.wikilens` 로 안 지워져 D15 의 "지우는 방법이
하나" 가 거짓이었고, 무엇보다 **유일한 복구 불가 자산이 안 보이는 곳**에 있었습니다 —
백업하려면 위 같은 `docker run` 을 쳐야 했고 `docker compose down -v` 한 번에
사라졌습니다.

**리눅스 호스트에서는 uid 를 줘야 합니다:**

```bash
WIKILENS_UID=$(id -u) WIKILENS_GID=$(id -g) docker compose up -d
```

bind mount 는 호스트 소유권을 그대로 쓰는데 이미지는 uid 10001 로 돌고 `~/.wikilens`
는 700 이라 **traverse 조차 안 됩니다**(실측: `Permission denied`). 그러면 색인이
0건이 되고 기동 로그에 ERROR 가 납니다. macOS 는 Docker Desktop 이 소유권을
재매핑해서 안 줘도 됩니다 — **그래서 macOS 에서의 성공은 리눅스를 보증하지 않습니다.**

### 작업 디렉터리는 이제 상관없습니다

예전에는 기본값이 `./mirror-root`·`./.wikilens/state` 처럼 **상대경로**라, IntelliJ 가
`fun main` 에서 띄우면(작업 디렉터리 = 저장소 루트) `gradlew bootRun`(= `server/`)과
**다른 자리**를 썼습니다. 볼트가 없는 건 ERROR 로 보이지만 **상태 디렉터리는 조용히
새로 만들어져서**, 옛 궤적을 못 읽은 채 두 갈래로 쌓였습니다.

기본값을 홈으로 옮겨 그 갈림이 성립하지 않습니다(실측: `/tmp`·저장소 루트·`server/`
셋에서 띄워 상태 경로가 동일). `~` 는 Spring 도 JVM 도 안 풀어주므로
`UserConfig.resolve` 한 곳에서 확장합니다.
그래서 기존 로그가 없으면 이렇게 경고합니다:

```
WARN  기존 궤적 로그가 없어 새로 시작합니다: … — 처음이면 정상이지만,
      재기동인데 이 줄이 보이면 작업 디렉터리가 달라져 **옛 궤적과 갈라진 것**입니다.
```

이미 갈렸다면 두 `trajectories.jsonl` 을 이어붙이면 복구됩니다 — append-only 라
순서만 맞으면 되고, 포스팅은 재생으로 다시 만들어집니다.

---

# 왜 이렇게 만들었나

## 왜 서버가 색인을 갖는가

클라이언트 분산 색인을 철회했습니다. 대가가 컸습니다:

| | 클라이언트 분산 | 서버 색인 |
|---|---|---|
| Confluence API 부하 | 사용자 수에 비례 (**200명이면 200배**) | 1배 |
| dense 임베딩 | 사용자마다 중복 계산 | 1회 |
| IDF | 권한 좁은 사용자는 추정 붕괴 | 전역, 안정 |
| **랭킹 척도** | **사용자마다 다름** | 동일 |
| 보고서(고아·누락 링크) | 개인 뷰라 무의미 | 전역 그래프 |

넷째 줄이 결정적입니다. 척도가 다르면 EB 사전분포로 들어오는 점수가 서로 달라
**이질적인 랭커에서 나온 관측을 하나의 카운터에 섞게** 됩니다. 학습 레이어의 전제가 깨집니다.

대가는 질의 시점 ACL 시행입니다. 이색적인 요구가 아니라 엔터프라이즈 검색의 표준이고,
공유 배포를 하는 이상 어차피 풀어야 하는 문제입니다.

## 왜 JVM인가

**한국어 형태소 분석 하나 때문입니다.** 교착어라 조사가 붙습니다 —
'로그인을/로그인은/로그인이'. 형태소 분석 없이는 BM25가 무너지고,
Lucene Nori가 그 문제의 프로덕션 검증된 해답입니다.

서버가 색인을 갖지 않던 설계에서는 이 근거가 없었고, 실제로 Python으로 만들었습니다.
색인이 돌아오면서 근거도 함께 돌아왔습니다.

## 구성

```
learn/       게이트 · EB · 궤적 · 항 단위 포스팅   ← 프레임워크 의존성 없음
             Gate · Reliability · TrajectoryStore … 파일 하나에 선언 하나
index/       Lucene + Nori, 필드 가중, ACL 필터
vault/       Python 싱크가 만든 미러 읽기
acl/         페이지·사용자 권한 토큰
service/     검색 융합(RRF) · 콘텐츠 서빙(read·grep)
api/         HTTP 표면 — Controller · Dto(= 와이어 포맷)
```

`learn/`이 Spring도 Lucene도 참조하지 않는 것은 의도입니다.
알고리즘 핵심을 프레임워크 없이 컴파일·검증할 수 있어야 하기 때문입니다.

`learn/` 에는 Spring·Lucene import 가 없습니다 — EB·게이트·궤적은 순수 알고리즘이라
프레임워크와 섞이면 단위 테스트가 통합 테스트로 변질됩니다. `shared_contract.sh` 가 강제합니다.

## ACL — 클라이언트에 배포하지 않는 이유

배포된 사본은 **회수할 수 없습니다.** 사용자가 팀에서 나가도 그 노트북의 볼트에는
전체 사본이 남습니다. 서버가 콘텐츠를 서빙하면 매 요청마다 ACL을 확인하므로
권한 취소가 즉시 반영됩니다.

시행 지점은 셋입니다:
1. `search` — Lucene 질의에 ACL 필터가 `MUST` 절로 결합
2. 학습 힌트 — 서버의 학습 레이어는 권한을 모르므로 융합 시 다시 검사
3. `read` / `grep` — 매 요청 확인. 권한 없으면 **404** (403은 존재를 알려주므로 유출)

권한 토큰이 없으면 빈 결과를 냅니다. 실수로 전체가 노출되는 것보다 낫습니다.

## 훅이 없는 이유

읽기가 서버를 거치므로 **서버가 궤적을 직접 관측합니다.** MCP 프록시 프로세스
하나가 세션 하나이고, `sessionId`가 그 경계입니다.

클라이언트 훅·버퍼링·핫패스 비용·세션 조립이 전부 사라집니다.

> **주의:** 권한 변경은 `lastModified`를 건드리지 않습니다. 콘텐츠 증분 싱크로는
> 잡히지 않으므로 ACL 싱크를 분리해 더 자주 돌려야 합니다.

## 토크나이저 정본은 서버

클라이언트는 질의 원문만 보내고 서버가 Nori로 토큰화합니다.
양쪽이 각자 토큰화하면 규칙이 달라졌을 때 **에러 없이 조용히 0건**이 되는데,
Python 프로토타입에서 실제로 겪은 버그입니다.

---

# 미해결

## 여전히 미해결

**"유용했다"의 판정.** 서버는 무엇을 읽었는지만 보고 그게 답이었는지는 모릅니다.
신호 **다섯**을 씁니다 — 모델이 `answer` 로 지정한 페이지가 답이다, 키워드가 겹치는
질의가 뒤따르면 앞 시도는 실패였다, 서빙한 힌트를 끝내 안 읽으면 그 힌트가 틀렸다,
그리고 아래 두 문단의 **읽기 개수**와 **`dest` 의 검색 순위**. 셋째가 유일하게 학습을
되돌리는 신호라, 나쁜 간선이 증거로 밀려납니다.

**첫째가 나머지의 기준점입니다.** `dest` 를 잘못 잡으면 셋이 함께 틀립니다 — 간선 ·
지나침 판정 · 순위 가중이 전부 거기 걸려 있습니다. 그래서 추정하는 대신 모델에게
묻습니다. **안 부르면 마지막 읽기로 폴백하므로 동작은 그대로이고**, 진술이 실제로
오는지는 `/api/stats` 의 `declaredDest` 로 봅니다 — **0 이면 스킬 지시가 안 먹히는
것**이고 그때는 폴백만 돌아 아무것도 안 바뀝니다.

`pWrong` = 거부된 힌트 / 서빙한 힌트. 손익분기 `p_hit > p_wrong/(n−1)` 의 그 값입니다.
세션이 실패한 비율은 `sessionFailureRate` 로 따로 봅니다 — 다른 값입니다.

읽기 개수와 순위도 씁니다. 마지막 읽기 앞의 것들은 "열어보고 지나친" 페이지이므로
미스로 charge 하고(1건이면 미스 0, 6건이면 5), `dest` 의 검색 순위가 깊을수록 강한
증거로 셉니다(1위 ×1, 2~3위 ×2, 그 아래 ×3). 위를 지나쳐 아래를 골랐다는 것은
어휘 랭킹이 틀렸다는 뜻이고, 학습 레이어가 고치라고 있는 경우가 그것입니다.

**가중치 문턱은 손으로 정했습니다** — 깊을수록 강하다는 방향만 확립된 것이고
(웹 검색 클릭 모델의 position bias), 3·6 이라는 경계와 3배 상한은 이 코퍼스에서
측정한 값이 아닙니다. `pWrong` 과 함께 모니터링하세요.

여전히 약한 곳: `dest = reads.last()` 라는 전제 자체입니다.

### 원시 계측 (`/api/stats` 의 `sinceStart`)

**궤적 수로는 검색 횟수를 역산할 수 없습니다.** 읽기도 서빙도 없는 검색은 궤적을 아예
안 남기는데, 그게 정확히 "결과가 시원찮아 다시 치는" 경우의 첫 시도라 재검색이
체계적으로 과소 계상됩니다. 웹 검색 로그 연구는 질의 자체를 남기므로 이 편향이
없습니다 — 세션의 약 40%가 질의 2개 이상이라는 수치가 그렇게 나옵니다
([Chen et al., WWW '21](https://dl.acm.org/doi/10.1145/3442381.3450127)).

```json
"sinceStart": {
  "queries": 3, "sessions": 2, "multiQuerySessions": 1,
  "queriesPerSession": 1.5, "multiQueryRate": 0.5, "unrecordedQueries": 3
}
```

**이 블록만 재기동에서 0으로 돌아갑니다.** 위쪽 값들은 궤적 로그에서 재생되지만 이건
로그에 없습니다 — 기록되지 않는 것을 세려는 지표라서요. 섞어서 빼면 재기동 직후
음수가 납니다.

`unrecordedQueries` 는 "빗나간 검색 수"가 아닙니다. **아직 안 끝난 세션의 진행 중인
스팬**도 포함하므로, `activeSessions` 가 0일 때만 그 뜻이 됩니다.

### 클릭 없는 종료를 실패로 세지 않습니다

에이전트가 검색 결과 제목만 보고 답하면 `reads` 가 빕니다. 예전에는 그것을 무조건
실패로 셌는데, IR 에서 **good abandonment** 로 알려진 오독입니다
([Li et al., SIGIR '09](https://dl.acm.org/doi/10.1145/1571941.1571951) ·
[Williams et al., WWW '16](https://www.microsoft.com/en-us/research/wp-content/uploads/2017/05/williams_www2016_good_abandonment.pdf)).
이제 `abandonedWithHints` 로 따로 세고 `sessionFailureRate` 의 분모에서 뺍니다.

**판정을 바꾸지는 않았습니다** — 만족인지 아닌지 구별할 신호가 아직 없습니다.
힌트 단위 벌점(`rejected`)은 그대로입니다. 그 힌트가 안 읽혔다는 사실은 확실하고,
`pWrong` 이 재려던 것이 그것이기 때문입니다.

**질의 원문이 서버로 갑니다.** 콘텐츠는 아니지만 질의어 자체가 민감할 수 있습니다.
