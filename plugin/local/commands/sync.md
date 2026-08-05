---
description: WikiLens 로컬 볼트를 다시 싱크합니다 (인자로 스페이스 키를 줄 수 있음)
argument-hint: "[SPACE_KEY ...]"
---

WikiLens 로컬 볼트를 갱신한다. 인자: `$ARGUMENTS` (비어 있을 수 있음)

## 1. 볼트와 CLI 확인

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault_status.py"
```

- `STATUS=missing` 이면 아직 설정 전이다 → `/wikilens-local:setup` 을 안내하고 여기서 멈춘다.
- `CLI=` 가 비어 있으면 CLI가 설치돼 있지 않다 → 역시 `/wikilens-local:setup` 으로 보낸다.

## 2. 스페이스 결정

인자가 있으면 그것을 쓴다. 없으면 `<VAULT>/mirror/.sync-state.json` 의 `pages` 에서
`space` 값들을 모아 **이미 받아둔 스페이스**를 재사용한다:

```
Bash: python3 -c "import json,collections,sys; d=json.load(open(sys.argv[1]))['pages']; print(' '.join(sorted(collections.Counter(p.get('space','') for p in d.values()))))" "<VAULT>/mirror/.sync-state.json"
```

## 3. 싱크 (사용자 승낙 후)

```
wikilens --root <VAULT> sync --space <KEY>
```

- **`--root` 는 반드시 서브커맨드 앞.** 최상위 파서에 있어서 뒤에 두면 파싱 에러다.
- 스페이스가 여럿이면 `--space A --space B` 로 반복한다.
- `sync` 가 build 까지 한 번에 한다. 따로 `build` 를 부르지 말 것.
- 수 분 걸리고 Confluence에 수천 건을 요청하므로 **실행 전에 승낙을 받는다.**

전체 재싱크와 삭제 감지가 필요하면 `--full` 을 덧붙인다. 단 이때는 **받아둔 스페이스를
모두** 나열해야 한다 — 목록에서 빠진 스페이스의 페이지가 삭제된 것으로 잡힌다.

## 4. 결과 보고

싱크 출력의 받음/변경없음/실패/삭제 건수를 전달하고, 링크 해석률이 낮게 나오면
(70% 미만) 링크 대상 스페이스가 싱크 범위 밖일 수 있다는 점을 알린다.
