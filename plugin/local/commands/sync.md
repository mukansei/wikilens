---
description: WikiLens 로컬 볼트를 다시 싱크합니다 (인자로 스페이스 키를 줄 수 있음)
argument-hint: "[SPACE_KEY ...]"
---

WikiLens 로컬 볼트를 갱신한다. 인자: `$ARGUMENTS` (비어 있을 수 있음)

CLI 실행은 **반드시 래퍼를 거친다.** 래퍼가 `~/.wikilens/env.sh` 에서 자격증명을 싣고
CLI 위치까지 찾아준다. 맨 `wikilens` 를 직접 부르면 Claude Code 를 띄운 셸에 export 가
없어 `CONFLUENCE_URL 환경변수가 필요합니다` 로 죽는다.

```
CLI="${CLAUDE_PLUGIN_ROOT}/scripts/wikilens_cli.sh"
```

## 1. 상태 확인

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault_status.py"
```

- `STATUS=missing` → 아직 설정 전이다. `/wikilens-local:setup` 을 안내하고 멈춘다.
- `CREDS=none` → 자격증명이 없어 싱크가 불가능하다. 역시 setup 으로 보낸다.
- `CREDS=shell` → 지금은 되지만 **다음 세션엔 안 된다.** 싱크를 마친 뒤
  `setup_vault.py --capture-env` 로 고정할 것을 권한다.

## 2. 스페이스 결정

인자가 있으면 그것을 쓴다. 없으면 **이미 받아둔 스페이스 전부**를 재사용한다:

```
Bash: python3 -c "import json,sys; d=json.load(open(sys.argv[1]))['pages']; print(' '.join(sorted({p.get('space','') for p in d.values()} - {''})))" "<VAULT>/mirror/.sync-state.json"
```

## 3. 싱크 (사용자 승낙 후)

```
bash "$CLI" --root <VAULT> sync --space <KEY>
```

- **`--root` 는 반드시 서브커맨드 앞.** 최상위 파서에 있어서 뒤에 두면 파싱 에러다.
- 스페이스가 여럿이면 `--space A --space B` 로 반복한다.
- `sync` 가 build 까지 한 번에 한다. 따로 `build` 를 부르지 말 것.
- 수 분 걸리고 Confluence 에 수천 건을 요청하므로 **실행 전에 승낙을 받는다.**

### 삭제 감지가 필요하면 `--full`

`--full` 은 **2단계에서 구한 전체 스페이스 목록**과 함께 써야 한다. 사용자가 인자로
일부만 줬다면 `--full` 을 붙이지 말고, 붙이려면 목록을 전체로 되돌린다 —
목록에서 빠진 스페이스의 페이지가 통째로 삭제된 것으로 잡힌다.

## 4. 결과 보고

받음/변경없음/실패/삭제 건수를 전달한다.

- **삭제가 0이 아니면** 근거를 함께 말한다: state 에는 있는데 Confluence 조회 결과에
  없는 페이지다(볼트 파일이 아니라 페이지 ID 기준). 스페이스 목록이 온전했는지도 확인한다.
- 링크 해석률이 70% 미만이면 링크 대상 스페이스가 싱크 범위 밖일 수 있다고 알린다.
