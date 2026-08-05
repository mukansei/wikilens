# 로컬판 설정 절차 (정본)

이 문서가 설정 절차의 **유일한 정본**이다. `/wikilens-local:setup` 커맨드도 이걸 읽고
수행한다 — 절차를 다른 곳에 복제하지 말 것.

로컬판은 **각자 자기 볼트를 만든다.** 관리자가 만든 볼트를 받는 게 아니라 본인 토큰으로
싱크하므로, 볼 수 없는 문서는 애초에 볼트에 들어오지 않는다(권한이 설계상 해결된다).

> **아무것도 자동으로 실행하지 말 것.** 싱크는 Confluence에 수천 건을 요청하고 수십 MB를
> 쓰고 수 분이 걸린다. 각 단계마다 사용자에게 보여주고 승낙을 받은 뒤 실행한다.

## 1. 연결 정보 확인

`CONFLUENCE_URL`이 필수다. 인증은 넷 중 하나이고 자동 판별된다:

| 방식 | 환경변수 |
|---|---|
| Server/DC PAT (대개 이것) | `CONFLUENCE_TOKEN` |
| Cloud API 토큰 | `CONFLUENCE_EMAIL` + `CONFLUENCE_TOKEN` |
| 사내 IAM OAuth2 | `IAM_TOKEN_URL`, `IAM_CLIENT_ID`, `IAM_CLIENT_SECRET` |
| 리버스 프록시 | `CONFLUENCE_HEADERS='X-Forwarded-User: me@corp'` |

SSO 환경이어도 대개 PAT가 동작한다. **PAT를 먼저 시도할 것.**

**Acme(`wiki.example.com`)에서는 `CONFLUENCE_PREFIX=""` 가 필수다.** 사내 게이트웨이가
`/wiki/rest/api/space`만 열어두고 나머지는 로그인 페이지로 리다이렉트해서, 자동 판별이
`/wiki` 접두사를 잘못 고른다. 빈 문자열로 강제하면 해결된다.

## 2. 볼트 위치 정하기

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup_vault.py" --vault <경로>
```

기본은 `~/.wikilens/vault`. **이미 볼트가 있으면 그 경로를 주면 된다** — 옮길 필요 없다.
기록된 값은 `~/.wikilens/config.json`에 남아 다음 세션에도 유지된다(환경변수는 세션이
끝나면 사라지므로 config가 정본이다).

## 3. CLI 설치

볼트를 **만드는 데만** 필요하다(검색은 파일만 읽으므로 의존성이 없다).

`vault_status.py`의 `CLI_SOURCE`가 설치 대상을 알려준다. 순서대로 확인한다:

1. `CLI=` 에 값이 있으면 이미 설치된 것 — 그대로 쓴다
2. `CLI_SOURCE=` 에 경로가 있으면 `pip install "<CLI_SOURCE>"`
3. 둘 다 비어 있으면 **사내 git URL을 사용자에게 요청**한 뒤
   `setup_vault.py --cli-source "git+<URL>#subdirectory=cli"` 로 기록

설치는 사용자 승낙 후 실행한다.

## 4. 접근 가능한 스페이스 확인

```
wikilens doctor
```

배포 형태·인증 방식·접근 가능한 스페이스 목록을 실행 전에 보여준다. 여기서 막히면
싱크는 어차피 실패한다. 출력의 스페이스 목록을 사용자에게 제시하고 **어느 것을 받을지
고르게 한다** — 스페이스는 필수 인자이고 기본값이 없다.

## 5. 싱크

```
wikilens --root <VAULT> sync --space <KEY>
```

**`--root` 는 서브커맨드 앞에 와야 한다.** 최상위 파서에 있어서 `sync --root ...` 로 쓰면
파싱 에러가 난다. 스페이스가 여럿이면 `--space A --space B` 로 반복한다.

**`sync` 한 번이 sync + build 를 다 한다.** 따로 `build`를 부를 필요 없다.

수 분 걸린다. 중단돼도 다음 실행이 이어받는다.

## 6. 값어치 판정 — 건너뛰지 말 것

```
wikilens --root <VAULT> stats
```

출력의 **"제목과 어휘가 안 겹치는 별칭을 가진 페이지"** 비율을 사용자에게 그대로 보고한다.

이 비율이 낮으면 어휘 격차가 없다는 뜻이고, **그러면 이 도구 전체가 값어치가 없다.**
그 판정까지 정직하게 전달할 것 — 숫자를 좋게 포장하지 말 것.

## 7. (선택) 권한 등록

볼트는 어느 프로젝트 밖에 있어서, 등록하지 않으면 프로젝트마다 읽기 승인을 다시 받아야
한다.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup_vault.py" --register-permissions
```

`~/.claude/settings.json`의 `permissions.additionalDirectories`에 볼트를 추가한다.
**전역 설정을 바꾸는 것이므로 반드시 사용자에게 묻고 승낙을 받은 뒤 실행한다.**
원치 않으면 건너뛰어도 되고, 그 경우 프로젝트마다 한 번씩 승인하면 된다.
