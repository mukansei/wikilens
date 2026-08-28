# 적용 범위 — 배포 형태 · 언어 · 플랫폼

> 이 문서는 `README.md` 에서 옮겨 왔습니다(2026-08-27).

특정 회사에 묶여 있지 않습니다.

Confluence Cloud 와 Server/Data Center 양쪽, 인증 4방식을 지원합니다.

Confluence 고유 개념 (`ac:link` storage format · `ancestors` · CQL) 에만 의존하므로,
어느 조직의 인스턴스든 같은 코드가 돕니다.

### 이 저장소의 숫자는 어디서 나왔나

두 코퍼스에서 나왔고, **당신이 재현할 수 있는 것은 둘째뿐입니다.**

| | 어디 | 재현 |
|---|---|---|
| **개발 코퍼스** (13,933건) | 비공개 인스턴스 (접근 불가) | **불가** — 접근 권한이 없습니다 |
| **공개 코퍼스** (15,494건) | 리눅스 재단 ONAP 위키 | **가능** — 자격증명 없이 받습니다 |

`Acme 2,383건` 같은 표기는 전자입니다. **회사명을 익명 대체했으므로 그 라벨만으로는
출처를 확인할 수 없습니다** — 숫자를 믿어 달라는 말이 아니라, 어느 코퍼스에서 나왔는지
구별해 두려는 표기입니다. 검증하려면 아래를 직접 돌리세요.

```bash
CONFLUENCE_URL=https://lf-onap.atlassian.net \
CONFLUENCE_AUTH=none \
  wikilens sync --space Meetings --space DW --root ~/.wikilens/vault-onap
```

`CONFLUENCE_AUTH=none` 은 **명시해야 켜집니다.** 자동 폴백을 안 넣은 이유는
"자격증명을 빠뜨림" 과 "공개 위키" 가 겉으로 같아 보이기 때문입니다.

영어 코퍼스라 색인은 `--wikilens.analyzer=english` 로 만듭니다.
벤치 질의는 `bench/queries.py` 에 있고 **그 위키의 실제 pageId 를 가리킵니다** —
`bench/` 를 돌리면 이 저장소의 주장을 당신 손으로 검사할 수 있습니다.

**당신의 위키를 재려면 `GROUPS` 를 통째로 갈아야 합니다.** 남의 코퍼스에서 만든 질의는
당신의 색인을 못 재고, 전 그룹 `못 찾음` 은 "이 도구가 나쁘다" 와 구별되지 않습니다.

> 배포되는 플러그인·CLI 에는 조직 고유값이 없습니다.

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
| 서버 운영(sync·acl) | 됩니다 | **Git for Windows** 가 있으면 됩니다 |
| 로컬판 | 됩니다 | **Git for Windows** 가 있으면 됩니다 |

Claude Code 는 Git for Windows 가 있으면 **Git Bash 로 Bash 도구**를 쓰고, 없으면
PowerShell 도구를 씁니다 (https://code.claude.com/docs/en/setup).

볼트를 *만드는* 경로가 셸 스크립트입니다 — `wikilens_cli.sh` 래퍼, 자격증명
`~/.wikilens/env.sh`. 그래서 Bash 도구가 있어야 로컬판과 서버 운영이 됩니다.

검색만 하는 서버판 사용자는 해당 없습니다.

<details>
<summary><b>Windows 준비</b> — Git for Windows 가 있는 경우</summary>

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
