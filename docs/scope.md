# 적용 범위 — 배포 형태 · 언어 · 플랫폼

> 이 문서는 `README.md` 에서 옮겨 왔습니다(2026-08-27).

특정 회사에 묶여 있지 않습니다.

Confluence Cloud 와 Server/Data Center 양쪽, 인증 4방식을 지원합니다.

Confluence 고유 개념 (`ac:link` storage format · `ancestors` · CQL) 에만 의존하므로,
어느 조직의 인스턴스든 같은 코드가 돕니다.

> 저장소 곳곳의 `Acme 2,383건` 같은 표기는 **측정 출처 라벨**입니다. 그 수치가 어느
> 코퍼스에서 나왔는지 밝히는 것이지 종속이 아닙니다. 배포되는 플러그인·CLI 에는
> 회사 고유값이 없습니다.

언어는 한국어를 기본으로 합니다.

교착어라 조사가 붙습니다 — '로그인을 / 로그인은 / 로그인이'. 형태소 분석 없이는
BM25 가 무너집니다.

그것이 서버를 JVM 으로 고른 유일한 이유입니다.

하나로 다 되지 않습니다. 어느 쪽을 고르든 반대쪽을 잃습니다.

| | `korean`(Nori, 기본) | `english` | `standard` |
|---|---|---|---|
| 한국어 조사를 뗀다 | ✅ | ❌ | ❌ |
| 영문 어간을 줄인다 (`servers`→`server`) | ❌ | ✅ | ❌ |
| 영문을 깨뜨리지 않는다 | ✅ 자르고 소문자화 | ✅ | ✅ |
| 어느 언어도 망가뜨리지 않는다 | — | — | ✅ 대신 어느 쪽에도 최선은 아님 |

실측은 굴절 쌍 5개에서 Nori 0 · English 5 였습니다.

다만 **한국어 문서에 `Jenkins`·`DEPLOY_TOKEN` 같은 영문 식별자가 섞인 코퍼스**라면
그것들이 굴절하지 않으므로 Nori 의 약점이 안 드러납니다.

영어가 주된 코퍼스라면 **색인할 때** 고르세요 — `--wikilens.analyzer=korean|english|standard`.

질의는 설정이 아니라 색인에 기록된 분석기를 따릅니다. 안 그러면 설정이 낡았을 때
에러 없이 0건이 됩니다.

로컬판은 grep 이라 언어와 무관합니다. 자세한 것은
[`server/README.md`](server/README.md#분석기는-색인-시점에-고른다) 에 있습니다.

### 플랫폼

| | macOS · Linux · WSL | Windows |
|---|---|---|
| **서버판 사용자**(검색만) | 됩니다 | **됩니다** — 프록시가 순수 파이썬이라 셸이 필요 없습니다 |
| 서버 운영(sync·acl) | 됩니다 | **Git for Windows 가 필요합니다** |
| 로컬판 | 됩니다 | **Git for Windows 가 필요합니다** |

Claude Code 는 Git for Windows 가 있으면 **Git Bash 로 Bash 도구**를 쓰고, 없으면
PowerShell 도구를 씁니다 (https://code.claude.com/docs/en/setup).

볼트를 *만드는* 경로가 셸 스크립트입니다 — `wikilens_cli.sh` 래퍼, 자격증명
`~/.wikilens/env.sh`. 그래서 **볼트를 만드는 쪽은 Git for Windows 를 요구사항으로
둡니다**(2026-08-28 결정). PowerShell 전용 경로를 따로 만들면 같은 일을 하는 두 번째
경로가 생기고, 그때부터 둘이 갈립니다 — 이 저장소가 반복해서 지워온 실패 모양입니다.

**검색만 하는 서버판 사용자는 해당 없습니다** — 프록시가 순수 파이썬이라 셸을 안 씁니다.
선은 "볼트를 만드는가" 이고, 그 한 줄로 갈립니다.

### 리눅스 호스트에서 갈리는 것 하나

**서버는 리눅스에서 검증됩니다** — 이미지가 `eclipse-temurin`(Debian) 이라 서버·Lucene·
ripgrep·CLI 가 컨테이너 안에서 리눅스로 돕니다. 갈리는 것은 **호스트의 bind mount
소유권** 하나입니다.

리눅스는 bind mount 가 호스트 소유권을 그대로 씁니다. 이미지는 uid 10001 로 도는데
`~/.wikilens` 는 700 이라 **컨테이너가 traverse 조차 못 하고, 그러면 색인이 0건**이
됩니다 — 에러가 아니라 0건이라 "문서가 없다" 와 구별되지 않습니다.

`wikilens-setup.sh` 는 호스트 uid 를 그대로 넘겨 이 차이를 없앱니다. 손으로 띄운다면
같은 값을 주세요:

```bash
WIKILENS_UID=$(id -u) WIKILENS_GID=$(id -g) docker compose up -d
```

**macOS 는 Docker Desktop 이 uid 를 가려 이 문제가 안 보입니다** — 그래서 macOS 에서의
통과가 리눅스를 보증하지 않습니다. 700 을 푸는 쪽은 택하지 않았습니다: 공용 서버에서
다른 로컬 사용자가 위키 본문을 읽으면 안 됩니다.

<details>
<summary><b>Windows 준비</b> — Git for Windows 설치부터</summary>

```powershell
# 1. Git for Windows — Claude Code 가 Bash 도구를 쓰게 해줍니다
winget install Git.Git

# 2. 파이썬 — 이름을 확인해 두세요 (python / python3 / py 중 무엇인지)
winget install Python.Python.3.12
python --version

# 3. MCP 프록시의 인터프리터 이름 (서버판만. `python3` 로 안 뜰 때)
setx WIKILENS_PYTHON python
```

설정 후 **Claude Code 를 재시작**하세요. 이후는 macOS·리눅스와 같습니다 —
래퍼가 `python3 → python → py -3` 순으로 알아서 찾습니다.

**알아둘 것 셋:**

- **줄바꿈** — `.gitattributes` 가 `.sh` 를 LF 로 고정합니다. 그전에 clone 한 저장소가
  있다면 `git rm --cached -r . && git reset --hard` 로 한 번 정규화하세요.
  CRLF 로 받은 셸 스크립트는 `$'\r': command not found` 로 죽습니다
- **홈 디렉터리** — 래퍼는 `~/.wikilens/env.sh` 를 직접 조립하지 않고 파이썬에게
  물어봅니다. Git Bash 의 `$HOME` 과 파이썬의 `Path.home()` (=`USERPROFILE`) 이 다를 수
  있기 때문입니다. 회사 환경에서 홈 드라이브를 따로 잡아두면 실제로 갈립니다
- **파일 권한** — `env.sh` 는 토큰이 들어 `600` 으로 만들지만, NTFS 에서는 그 제한이
  실질적으로 걸리지 않습니다. 공용 PC 에서는 그 점을 감안하세요

> **Windows 에서 실제로 돌려본 적은 없습니다.**
>
> 위는 코드가 무엇을 요구하는지와 Claude Code 문서
> (https://code.claude.com/docs/en/setup) 에서 온 것이지 실행 확인이 아닙니다.
> 이 저장소의 다른 검증과 같은 급으로 읽지 마세요.

</details>

---
