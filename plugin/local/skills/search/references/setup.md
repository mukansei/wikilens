# 로컬판 설정 절차 (정본)

이 문서가 설정 절차의 **유일한 정본**이다. `/wikilens-local:setup` 커맨드도 이걸 읽고
수행한다 — 절차를 다른 곳에 복제하지 말 것.

로컬판은 **각자 자기 볼트를 만든다.** 관리자가 만든 볼트를 받는 게 아니라 본인 토큰으로
싱크하므로, 볼 수 없는 문서는 애초에 볼트에 들어오지 않는다(권한이 설계상 해결된다).

> **아무것도 자동으로 실행하지 말 것.** 싱크는 Confluence에 수천 건을 요청하고 수십 MB를
> 쓰고 수 분이 걸린다. 각 단계마다 사용자에게 보여주고 승낙을 받은 뒤 실행한다.

전체는 네 단계다. 시작 전에 현재 상태를 확인한다:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault_status.py"
```

`CREDS=`, `CLI=`, `STATUS=` 를 보고 **이미 끝난 단계는 건너뛴다.**

---

## 1. 자격증명 고정 — 건너뛰면 다음 세션에 싱크가 죽는다

CLI는 자격증명을 환경변수로만 읽는다. 그런데 `export` 는 **그 셸에서만 산다.**
Claude Code를 재시작하면 사라져서, 검색은 되는데 갱신만 조용히 안 되는 상태가 된다.
그래서 `~/.wikilens/env.sh` 에 고정한다(볼트 경로를 `config.json` 에 두는 것과 같은 이유).

**`CREDS=shell` 이면** — 이미 셸에 있으니 그대로 옮기면 된다:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup_vault.py" --capture-env
```

값은 환경에서 파일로 곧장 가고 화면에 찍히지 않는다. 파일은 600으로 만들어진다.

**`CREDS=none` 이면** — 위 명령이 사용자가 직접 실행할 템플릿을 출력한다.
**그 명령은 사용자가 실행하게 하고, 토큰을 대신 입력하지 말 것.** 필요한 값:

| 방식 | 환경변수 |
|---|---|
| Server/DC PAT (대개 이것) | `CONFLUENCE_URL`, `CONFLUENCE_TOKEN` |
| Cloud API 토큰 | `CONFLUENCE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_TOKEN` |
| 사내 IAM OAuth2 | `IAM_TOKEN_URL`, `IAM_CLIENT_ID`, `IAM_CLIENT_SECRET` |
| 리버스 프록시 | `CONFLUENCE_HEADERS='X-Forwarded-User: me@corp'` |

SSO 환경이어도 대개 PAT가 동작한다. **PAT를 먼저 시도할 것.**

**Acme(`wiki.example.com`)에서는 `CONFLUENCE_PREFIX=""` 가 필수다.** 사내 게이트웨이가
`/wiki/rest/api/space`만 열어두고 나머지는 로그인 페이지로 리다이렉트해서, 자동 판별이
`/wiki` 접두사를 잘못 고른다. 빈 문자열로 강제하면 해결된다(빈 문자열이 유효한 값이다).

이후 CLI 호출은 **전부 래퍼를 거친다.** 래퍼가 env.sh를 싣고 CLI 위치까지 찾는다:

```
CLI="${CLAUDE_PLUGIN_ROOT}/scripts/wikilens_cli.sh"
```

## 2. 볼트 위치와 CLI — 한 번에 보여주고 승낙받기

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup_vault.py" --vault <경로>
```

기본은 `~/.wikilens/vault`. **이미 볼트가 있으면 그 경로를 주면 된다** — 옮기라고 하지 말 것.

CLI는 볼트를 **만드는 데만** 필요하다(검색은 파일만 읽으므로 의존성이 없다).
`vault_status.py` 출력으로 판단한다:

1. `CLI=` 에 값이 있으면 이미 설치된 것 — 그대로 쓴다
2. `CLI_SOURCE=` 에 경로가 있으면 `pip install "<CLI_SOURCE>"`
3. 둘 다 비어 있으면 **사내 git URL을 사용자에게 요청**한 뒤
   `setup_vault.py --cli-source "git+<URL>#subdirectory=cli"` 로 기록

## 3. 스페이스 고르고 싱크

```
bash "$CLI" doctor
```

배포 형태·인증 방식·접근 가능한 스페이스 목록을 보여준다. **여기서 인증이 실패하면
싱크는 어차피 실패하므로 1단계로 돌아간다.** 성공하면 스페이스 목록을 사용자에게
제시하고 어느 것을 받을지 고르게 한다 — 필수 인자이고 기본값이 없다.

```
bash "$CLI" --root <VAULT> sync --space <KEY>
```

- **`--root` 는 서브커맨드 앞에 와야 한다.** 최상위 파서에 있어서 뒤에 두면 파싱 에러다.
- 스페이스가 여럿이면 `--space A --space B` 로 반복한다.
- **`sync` 한 번이 sync + build 를 다 한다.** 따로 `build`를 부를 필요 없다.
- 수 분 걸린다. 중단돼도 다음 실행이 이어받는다.

## 4. 값어치 판정과 마무리

```
bash "$CLI" --root <VAULT> stats
```

출력의 **"제목과 어휘가 안 겹치는 별칭을 가진 페이지"** 비율을 그대로 보고한다.
이 비율이 낮으면 어휘 격차가 없다는 뜻이고, **그러면 이 도구 전체가 값어치가 없다.**
그 판정까지 정직하게 전달할 것 — 숫자를 좋게 포장하지 말 것.

마지막으로 권한 등록을 **묻는다**:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup_vault.py" --register-permissions
```

볼트는 어느 프로젝트 밖에 있어서, 등록하지 않으면 프로젝트마다 읽기 승인을 다시
받아야 한다. `~/.claude/settings.json` 의 `permissions.additionalDirectories` 를 바꾸는
**전역 설정 변경이므로 반드시 승낙을 받고 실행한다.** 원치 않으면 건너뛰어도 되고,
그 경우 프로젝트마다 한 번씩 승인하면 된다.

끝나면 사용자의 **원래 질문으로 돌아가 곧바로 검색해 답한다.** 설정을 마쳤다는
보고만 하고 멈추지 말 것.
