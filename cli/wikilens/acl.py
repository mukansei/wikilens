"""
페이지별 읽기 권한을 수집해 `mirror/acl/acl.json` 에 쓴다.

**콘텐츠 싱크와 분리한 이유:** 권한 변경은 `lastModified` 를 건드리지 않는다. 증분
`sync` 는 그것을 영영 못 잡으므로, 더는 볼 수 없게 된 페이지를 계속 서빙하게 된다 —
공유 서버에서는 그것이 전 사용자에게 나간다. 그래서 별도 명령이고 더 자주 돌려야 한다.

### 상속을 직접 풀어야 한다

`/rest/api/content/{id}/restriction/byOperation` 은 **그 페이지에 직접 걸린 제한만**
준다. Confluence 의 읽기 제한은 **가장 가까운 조상**에서 상속되므로, 직접 제한만 보고
"없음 = 공개" 로 적으면 **상속으로 잠긴 문서가 통째로 노출된다.** 그래서 `ancestors`
(싱크가 이미 저장해 둔다)를 위로 훑어 가장 가까운 제한을 찾는다.

### 제한이 없어도 `@public` 이 아니다

여러 스페이스를 한 볼트에 모으면, 어느 스페이스에도 제한이 없더라도 **사용자마다 볼 수
있는 스페이스가 다르다.** 그래서 제한 없는 페이지는 `@public` 이 아니라
`@space:<KEY>` 를 받는다 — 운영자가 사용자에게 스페이스 토큰을 주는 만큼만 열린다.
`@public` 으로 적으면 등록된 누구나 12개 스페이스를 전부 보게 된다.

**스페이스 권한 자체는 가져오지 않는다.** Server/DC 의 스페이스 권한 API 는 버전마다
다르고, 여기서 필요한 것은 "이 페이지가 어느 스페이스에 속하는가" 라는 **상한**뿐이다.
누가 그 스페이스를 볼 수 있는지는 운영자가 `acl/user` 로 정한다.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import layout

#: 제한이 없는 페이지가 받는 토큰. 스페이스 키가 붙는다.
SPACE_PREFIX = "@space:"

#: 사용자·그룹 제한 토큰. 서버는 문자열 일치만 보므로 접두사로 종류를 구분해 둔다.
USER_PREFIX = "user:"
GROUP_PREFIX = "group:"


@dataclass
class AclReport:
    pages: int = 0
    restricted: int = 0
    inherited: int = 0
    failed: int = 0
    elapsed_s: float = 0.0
    tokens: set[str] = field(default_factory=set)


def _direct_tokens(client, page_id: str) -> list[str] | None:
    """
    이 페이지에 **직접** 걸린 읽기 제한. 없으면 빈 목록, 조회 실패면 `None`.

    실패와 "제한 없음" 을 구분하는 것이 중요하다 — 실패를 빈 목록으로 뭉개면
    **못 읽은 페이지가 공개로 적힌다.**
    """
    url = f"{client.base}{client.detect_prefix()}/rest/api/content/{page_id}/restriction/byOperation/read"
    r = client._get(url)
    if r.status_code != 200:
        return None
    try:
        res = r.json().get("restrictions", {})
    except ValueError:
        return None
    out = []
    for kind, prefix, key in (("user", USER_PREFIX, "username"), ("group", GROUP_PREFIX, "name")):
        for item in (res.get(kind) or {}).get("results") or []:
            name = item.get(key) or item.get("displayName")
            if name:
                out.append(prefix + name)
    return out


def collect(root: Path, client, verbose: bool = False, sleep_s: float = 0.0) -> AclReport:
    """
    싱크된 모든 페이지의 읽기 권한을 모아 `mirror/acl/acl.json` 을 쓴다.

    **부분 결과를 쓰지 않는다.** 조회에 실패한 페이지가 하나라도 있으면 그 페이지는
    기존 값을 유지해야 하는데, 파일을 통째로 새로 쓰면 그럴 수 없다. 그래서 실패분은
    **이전 파일의 값을 그대로 옮겨 담는다** — 못 읽었다고 공개로 바뀌면 안 된다.
    """
    started = time.time()
    state_path = root / "mirror" / ".sync-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pages: dict = state.get("pages") or {}

    acl_dir = root / "mirror" / "acl"
    acl_dir.mkdir(parents=True, exist_ok=True)
    out_path = acl_dir / "acl.json"
    previous: dict[str, list[str]] = {}
    if out_path.exists():
        previous = json.loads(out_path.read_text(encoding="utf-8"))

    rep = AclReport(pages=len(pages))
    direct: dict[str, list[str] | None] = {}
    for i, pid in enumerate(pages, 1):
        direct[pid] = _direct_tokens(client, pid)
        if direct[pid] is None:
            rep.failed += 1
        if sleep_s:
            time.sleep(sleep_s)
        if verbose and i % 200 == 0:
            print(f"  {i}/{len(pages)} …", flush=True)

    result: dict[str, list[str]] = {}
    for pid, meta in pages.items():
        own = direct.get(pid)
        if own is None:                       # 조회 실패 — 옛 값을 지킨다
            if pid in previous:
                result[pid] = previous[pid]
            continue
        tokens = own
        if not tokens:
            # 가장 가까운 조상의 제한을 상속한다. `ancestors` 는 루트→부모 순이라
            # 뒤에서부터 본다.
            for anc in reversed(meta.get("ancestors") or []):
                inherited = direct.get(str(anc.get("id")))
                if inherited:
                    tokens = inherited
                    rep.inherited += 1
                    break
        if not tokens:
            tokens = [SPACE_PREFIX + (meta.get("space") or "")]
        else:
            rep.restricted += 1
        result[pid] = tokens
        rep.tokens.update(tokens)

    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=0, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(out_path)
    rep.elapsed_s = time.time() - started
    return rep
