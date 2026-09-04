# 서버판 운영 — 권한 시행 · 정기 갱신

> 이 문서는 `README.md` 에서 옮겨 왔습니다(2026-08-27). README 는 세우는 절차까지만
> 두고, 운영에 들어간 뒤의 것은 여기 둡니다.

## 권한 시행을 켜려면

이 서버에 닿는 사람들이 **서비스 계정의 권한 범위를 공유하면 안 될 때** 켭니다.
켜면 등록 전까지 전원이 0건입니다 (fail-closed).

**순서를 뒤집으면 전원이 0건이 됩니다.**

시행을 먼저 켜고 `["@public"]` 로 등록해 두면, 나중에 `wikilens acl` 이 페이지 토큰을
`@space:<KEY>` 로 바꾸는 순간 겹치는 토큰이 없어집니다.

`--status` 가 `ACL_TOKEN_OVERLAP=0` 으로 짚습니다.

```bash
$WL acl --root ~/.wikilens/vault        # 권한 수집. sync 와 주기가 다릅니다
WIKILENS_ACL_ENFORCED=true docker compose up -d --build

# 사용자 등록 — 어떤 토큰을 줄지는 `wikilens acl` 출력의 토큰 목록이 알려줍니다
curl -XPOST -H "X-WikiLens-Admin: $WIKILENS_ADMIN_TOKEN" \
  'localhost:8787/api/admin/acl/user?userKey=alice@corp' \
  -H 'Content-Type: application/json' -d '["@space:PLATFORM"]'
```

`wikilens acl` 은 시행이 꺼져 있어도 한 가지를 바꿉니다.

수집이 권한을 확정하지 못한 페이지는 빈 토큰이 되어 **아무에게도 안 보입니다**.
파이썬 쪽 fail-closed 를 여기서 뒤집지 않으려는 의도된 동작입니다.

돌린 뒤 문서가 줄었다면 원인은 시행이 아니라 수집 실패입니다.
기동 로그의 `unresolved` 가 그 수를 냅니다.

## 정기 갱신은 스크립트 하나입니다

```bash
# 저장소가 없다면 이미지에서 꺼냅니다 — clone 이 필요 없습니다
docker run --rm IMAGE cat /app/wikilens-refresh.sh > ~/.wikilens/wikilens-refresh.sh
chmod +x ~/.wikilens/wikilens-refresh.sh

crontab -e
# 0 9 * * 1 WIKILENS_ADMIN_TOKEN=… WIKILENS_IMAGE=… ~/.wikilens/wikilens-refresh.sh --space PLATFORM
```

`wikilens-refresh.sh` 가 **싱크 → 재색인 → 확인**을 한 번에 합니다. 첫 구축에도
같은 것을 씁니다 — 절차가 하나뿐이어야 손으로 옮겨 적다 빠뜨리지 않습니다.

**호스트에서 돕니다**(자격증명을 쥐고 `docker run` 을 부릅니다). 이미지 이름은
`WIKILENS_IMAGE` 로 줍니다 — 기본값은 `compose` 가 붙이는 `wikilens-wikilens` 라
`docker run` 으로 직접 띄웠다면 다릅니다. **없는 이름이면 있는 것을 찍어 말합니다**
(`docker run` 이 Hub 에서 받으려다 내는 `pull access denied` 는 이름 문제로 안 읽힙니다).

**무엇을 대신 지켜주나:**

- **싱크가 실패하면 재색인하지 않습니다.** 반쪽 상태가 반영되는 것을 막습니다
- **싱크 후 볼트가 비면 멈춥니다.** 종료 코드만으로는 못 봅니다 — 컨테이너가 다른
  자리에 쓰면 싱크는 성공하고 볼트는 빈 채로 남는데, 뒤의 재색인은 서버가 보는 **옛
  볼트**를 다시 색인해 통과합니다. 실제로 겪었습니다(2026-08-27)
- **자격증명을 `sync` 컨테이너에만 넘깁니다** — 서버에는 안 들어갑니다
- **`~/.wikilens/env.sh` 를 읽어 넘깁니다** — 그 파일은 `export KEY=VAL` 형식이라
  `docker --env-file` 이 거부합니다(실측)
- **끝나고 색인이 0건이면 실패로 끊습니다** — 볼트를 못 읽어도 재색인 자체는
  HTTP 200 입니다

```
WIKILENS_IMAGE   기본 wikilens-wikilens
WIKILENS_VAULT   기본 ~/.wikilens/vault
WIKILENS_SERVER  기본 http://localhost:8787
WIKILENS_ENV     기본 ~/.wikilens/env.sh
```

CLI 를 호스트에 설치했다면 그것을 직접 불러도 됩니다 — 다만 `&&` 로 이어야 합니다.
실패했는데 재색인하면 못 받은 상태가 그대로 반영됩니다.

시행을 켰다면 `acl` 도 사이에 넣으세요 — `sync` 보다 자주 돌려야 합니다.

자격증명도 `export` 가 아니라 `~/.wikilens/env.sh` (600) 에서 읽습니다.

첫 싱크가 끝나면 `$WL stats` 도 한 번 보세요. 어느 랭킹 층이 값어치를 하는지
알려줍니다 (→ [끝나면 값어치부터 재세요](#끝나면-값어치부터-재세요)).

배포·운영 절차 전체는 [`server/README.md`](../server/README.md) 에 있습니다 —
분석기 선택 · 백업 대상 · 상태 디렉터리 락 · Docker 볼륨.

