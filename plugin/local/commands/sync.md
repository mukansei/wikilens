---
description: WikiLens 로컬 볼트를 다시 싱크합니다 (인자로 스페이스 키를 줄 수 있음)
argument-hint: "[SPACE_KEY ...]"
---

WikiLens 로컬 볼트를 갱신하세요. 인자: `$ARGUMENTS` (비어 있을 수 있음)

CLI 실행은 **반드시 래퍼를 거치세요.** 래퍼가 `~/.wikilens/env.sh` 에서 자격증명을 싣고
CLI 위치까지 찾아줍니다. 맨 `wikilens` 를 직접 부르면 Claude Code 를 띄운 셸에 export 가
없어 `CONFLUENCE_URL 환경변수가 필요합니다` 로 죽습니다.

```
CLI="${CLAUDE_PLUGIN_ROOT}/scripts/wikilens_cli.sh"
```

## 1. 상태 확인

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault_status.py"
```

- `STATUS=missing` → 아직 설정 전입니다. `/wikilens-local:setup` 을 안내하고 멈추세요.
- `CREDS=none` → 자격증명이 없어 싱크가 불가능합니다. 역시 setup 으로 보내세요.
- `CLI=(못 찾음)` → venv·pipx 안에 있을 수 있습니다. `setup_vault.py --cli-path auto` 를
  제안하세요 (setup 전체를 다시 돌릴 필요는 없습니다).
- `CREDS=shell` → 지금은 되지만 **다음 세션엔 안 됩니다.** 싱크를 마친 뒤
  `setup_vault.py --capture-env` 로 고정할 것을 권하세요.

## 2. 스페이스 결정

인자가 있으면 그것을 쓰세요. 없으면 **이미 받아둔 스페이스 전부**를 재사용합니다:

```
Bash: python3 -c "import json,sys; d=json.load(open(sys.argv[1]))['pages']; print(' '.join(sorted({p.get('space','') for p in d.values()} - {''})))" "<VAULT>/mirror/.sync-state.json"
```

## 3. 싱크 (사용자 승낙 후)

```
bash "$CLI" --root <VAULT> sync --space <KEY>
```

- **`--root` 는 반드시 서브커맨드 앞에 둡니다.** 최상위 파서에 있어서 뒤에 두면 파싱 에러입니다.
- 스페이스가 여럿이면 `--space A --space B` 로 반복하세요.
- `sync` 가 build 까지 한 번에 합니다. 따로 `build` 를 부르지 마세요.
- 수 분 걸리고 Confluence 에 수천 건을 요청하므로 **실행 전에 승낙을 받으세요.**

### 삭제 감지가 필요하면 `--full`

`--full` 은 **2단계에서 구한 전체 스페이스 목록**과 함께 써야 합니다. 사용자가 인자로
일부만 줬다면 `--full` 을 붙이지 말고, 붙이려면 목록을 전체로 되돌리세요 —
목록에서 빠진 스페이스의 페이지가 통째로 삭제된 것으로 잡힙니다.

## 4. 결과 보고

받음/변경없음/실패/삭제 건수를 전달하세요.

- **뒤따르는 `빌드 완료: … 기록 0 (변경 없음 — 멱등)` 은 정상입니다.** 내용이 같은 파일은
  안 쓰기 때문이고, 위키가 안 바뀌었으면 0 이 맞습니다. 실패로 보고하지 마세요.
  `기록` 은 실제로 디스크에 쓴 개수라 `받음` 과 다를 수 있습니다.

- **삭제가 0이 아니면** 근거를 함께 말하세요: state 에는 있는데 Confluence 조회 결과에
  없는 페이지입니다(볼트 파일이 아니라 페이지 ID 기준). 스페이스 목록이 온전했는지도 확인하세요.
- 링크 해석률이 70% 미만이면 링크 대상 스페이스가 싱크 범위 밖일 수 있다고 알리세요.
