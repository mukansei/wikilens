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

**조상을 못 읽으면 자식도 확정할 수 없다.** 예전에는 조상 조회가 실패하면 그 조상을
"제한 없음" 과 똑같이 취급하고 계속 위로 올라갔다 — 실측: 부모 하나가 500 을 내자
자식이 `@space:` 를 받았다(잠긴 부모 밑의 문서가 스페이스 전체에 열린 것이다).
페이지 자신의 실패는 막고 있었는데 **조상의 실패는 안 막고 있었다.**

싱크 집합에 없는 조상도 조회한다. 안 그러면 "안 가져왔다" 와 "제한이 없다" 가
구별되지 않는다 — 실측(13,921건)에서 그런 조상은 2개뿐이라 비용은 없다시피 하다.

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
    #: 조회 실패 건수. **싱크 집합 밖의 조상도 포함한다** — 사용자에게는 그것도
    #: 실패이므로 함께 센다. 다만 "전부 실패" 판정에는 쓰면 안 된다([page_failed]).
    failed: int = 0
    #: 그중 **싱크된 페이지** 의 실패. 두 모집단을 갈라 두는 이유는 밖의 조상이
    #: 애초에 싱크가 안 가져온 페이지라 **권한이 없어 실패하기 가장 쉬운 쪽**이어서다.
    page_failed: int = 0
    #: 자신은 읽었지만 **조상을 못 읽어** 권한을 확정하지 못한 페이지
    unresolved: int = 0
    #: acl.json 을 실제로 썼는가. 전부 실패하면 안 쓴다 — 옛 파일이 그대로 남는다.
    wrote: bool = True
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


def _load_previous(out_path: Path) -> dict[str, list[str]]:
    """
    앞선 수집 결과. 깨져 있거나 dict 가 아니면 없는 것으로 친다.

    **방향이 안전하다** — 옛 값을 못 쓰면 실패분이 빠져 *안 보이게* 되지 실수로
    열리지는 않는다.
    """
    if not out_path.exists():
        return {}
    loaded = None
    try:
        loaded = json.loads(out_path.read_text(encoding="utf-8"))
    except ValueError:
        pass
    if isinstance(loaded, dict):
        return loaded
    print(f"경고: {out_path} 를 읽을 수 없어 이전 권한 없이 진행합니다.")
    return {}


def _fetch_direct(client, pages: dict, rep: AclReport,
                  verbose: bool, sleep_s: float) -> dict[str, list[str] | None]:
    """
    대상 전체의 **직접** 제한을 모은다. `None` 은 조회 실패이고 빈 목록과 다르다.

    **싱크 집합 밖의 조상도 대상에 넣는다.** 빠뜨리면 `direct` 에 없다는 사실이
    "제한 없음" 과 구별되지 않아, 상속을 푸는 쪽에서 조용히 열린다.
    """
    outside = {str(a.get("id")) for m in pages.values()
               for a in (m.get("ancestors") or [])} - set(pages)
    targets = list(pages) + sorted(outside)

    direct: dict[str, list[str] | None] = {}
    for i, pid in enumerate(targets, 1):
        direct[pid] = _direct_tokens(client, pid)
        if direct[pid] is None:
            rep.failed += 1
            if pid in pages:
                rep.page_failed += 1
        if sleep_s:
            time.sleep(sleep_s)
        if verbose and i % 200 == 0:
            print(f"  {i}/{len(targets)} …", flush=True)
    return direct


def _resolve(pages: dict, direct: dict[str, list[str] | None],
             previous: dict[str, list[str]], rep: AclReport) -> dict[str, list[str]]:
    """
    직접 제한 + 상속으로 페이지별 최종 토큰을 정한다. **계약이 지키는 규칙 셋이 여기 있다.**

      - **조회 실패는 옛 값을 유지한다** — 못 읽었다고 공개로 바뀌면 안 된다.
      - **상속은 못 읽은 조상에서 멈춘다** — 계속 올라가면 "없음" 으로 결론 나 자식이 열린다.
      - **제한 없음은 `@public` 이 아니라 `@space:<KEY>`** — 여러 스페이스를 한 볼트에
        모으면 사용자마다 볼 수 있는 스페이스가 다르다.
    """
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
            unresolved = False
            for anc in reversed(meta.get("ancestors") or []):
                inherited = direct.get(str(anc.get("id")))
                if inherited is None:
                    # 못 읽은 조상. 제한이 있었는지 없었는지 알 수 없으므로 **여기서
                    # 멈춘다** — 계속 올라가면 "없음" 으로 결론 나 자식이 열린다.
                    unresolved = True
                    break
                if inherited:
                    tokens = inherited
                    rep.inherited += 1
                    break
            if unresolved:
                rep.unresolved += 1
                if pid in previous:
                    result[pid] = previous[pid]
                continue
        if not tokens:
            tokens = [SPACE_PREFIX + (meta.get("space") or "")]
        else:
            rep.restricted += 1
        result[pid] = tokens
        rep.tokens.update(tokens)
    return result


def collect(root: Path, client, verbose: bool = False, sleep_s: float = 0.0) -> AclReport:
    """
    싱크된 모든 페이지의 읽기 권한을 모아 `mirror/acl/acl.json` 을 쓴다.

    **부분 결과를 쓰지 않는다.** 조회에 실패한 페이지가 하나라도 있으면 그 페이지는
    기존 값을 유지해야 하는데, 파일을 통째로 새로 쓰면 그럴 수 없다. 그래서 실패분은
    **이전 파일의 값을 그대로 옮겨 담는다**([_resolve]) — 못 읽었다고 공개로 바뀌면 안 된다.
    """
    started = time.time()
    state_path = root / "mirror" / ".sync-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pages: dict = state.get("pages") or {}

    acl_dir = root / "mirror" / "acl"
    acl_dir.mkdir(parents=True, exist_ok=True)
    out_path = acl_dir / "acl.json"
    previous = _load_previous(out_path)

    rep = AclReport(pages=len(pages))
    direct = _fetch_direct(client, pages, rep, verbose, sleep_s)
    result = _resolve(pages, direct, previous, rep)

    # **전부 실패했으면 쓰지 않는다.** 배운 게 없는데 파일을 덮으면 그 결과는
    # "권한을 수집했는데 아무 페이지도 없다" 가 되고, 서버는 그것을 **전 페이지가
    # 아무에게도 안 보임**으로 읽는다(fail-closed 라 맞는 해석이다). 볼트를 못 읽은
    # 재기동이 멀쩡한 색인을 지우던 것과 같은 자리다 — 못 읽은 것과 없는 것은 다르다.
    #
    # 부분 실패는 그대로 진행한다. 그건 설계된 완만한 열화다(옛 값 유지 · 새 페이지 생략).
    #
    # **세는 것은 페이지 실패뿐이다.** `rep.failed` 는 싱크 집합 밖의 조상도 함께 세는데,
    # 그쪽은 싱크가 안 가져온 페이지라 **권한이 없어 실패하기 가장 쉽다** — 두 모집단을
    # 섞으면 페이지가 전부 성공해도 문턱을 넘을 수 있다(실측: 페이지 2건 성공 · 밖의
    # 조상 3건 실패 → 안 씀). 그러면 새로 잠긴 페이지가 옛 값으로 계속 서빙된다.
    if pages and rep.page_failed >= len(pages):
        rep.elapsed_s = time.time() - started
        rep.wrote = False
        return rep

    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=0, sort_keys=True),
                   encoding="utf-8", newline="\n")
    tmp.replace(out_path)
    rep.elapsed_s = time.time() - started
    return rep
