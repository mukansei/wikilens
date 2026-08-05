---
description: WikiLens 로컬판을 설정합니다 — 볼트 위치 지정, CLI 설치, 첫 싱크
---

WikiLens 로컬판 설정을 진행한다.

절차 정본은 `${CLAUDE_PLUGIN_ROOT}/skills/wikilens/references/setup.md` 에 있다.
**그 파일을 먼저 Read 하고 거기 적힌 순서를 그대로 따를 것** — 절차를 여기 옮겨 적으면
두 곳이 갈라진다.

시작 전 현재 상태부터 확인한다:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault_status.py"
```

지켜야 할 것:

- **각 단계를 실행하기 전에 사용자에게 보여주고 승낙을 받는다.** 싱크는 Confluence에
  수천 건을 요청하고 수십 MB를 쓴다. `pip install` 과 전역 설정 변경도 마찬가지다.
- 이미 볼트가 있으면 **옮기라고 하지 말고** 그 경로를 등록하기만 한다.
- 마지막 `stats` 의 어휘 격차 비율은 **포장하지 말고 그대로 보고**한다. 낮으면 이 도구가
  값어치 없다는 뜻이고, 그 판정도 사용자에게 전달한다.
