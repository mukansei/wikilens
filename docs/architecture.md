# WikiLens High-Level Architecture

스택 확정: 싱크 = Python · 서버 = Kotlin + Lucene/Nori · 평가 = Python · 인터페이스 = 파일

## 컨테이너 다이어그램

```mermaid
flowchart TB
    CONF[("Confluence<br/>읽기 전용")]
    SESS(["Claude 세션 × N"])
    EMB["임베딩 API"]

    subgraph SYNC["싱크 배치 · Python · 15~60분 · 단일 라이터"]
        EXP["confluence-markdown-exporter<br/>+ 구조 서명 추출"]
        GIT["git commit → diff structure/"]
    end

    subgraph STORE["파일 스토리지 · 언어 중립"]
        MIR["mirror/ · git 추적<br/>pages · structure · acl"]
        DER["derived/ · gitignore<br/>anchors.jsonl · vectors"]
        STA["state/ · 백업 필수<br/>trajectories.jsonl · edges.json"]
        REP["reports/"]
    end

    subgraph SRV["서버 · Kotlin + Lucene/Nori · 공유 · 원격"]
        MCPT["MCP over HTTP<br/>search · read · grep"]
        QRY["분류 → IDF 트리아지 → RRF 융합"]
        IDX["Lucene 인덱스<br/>Nori · BM25 · 앵커 필드 · kNN"]
        L2["ShortcutStore<br/>+ 호출 관측기"]
        MNT["suspect 마킹 · 응고화"]
    end

    EVAL["평가 하니스 · Python<br/>시점분할 홀드아웃 · 비링크 도달"]

    CONF -->|"CQL lastModified"| EXP
    EXP --> MIR
    MIR --> GIT
    GIT -->|"구조 변경 목록"| MNT
    MIR -->|"앵커 전치"| DER
    EMB -.->|"증분 임베딩"| DER

    SESS <-->|"질의 · 읽기"| MCPT
    MCPT --> QRY
    QRY --> IDX
    QRY --> L2
    IDX --> QRY
    L2 --> QRY
    DER --> IDX
    MIR --> IDX

    MCPT -.->|"호출 로그 관측"| L2
    L2 --> STA
    STA --> L2
    MNT --> L2
    MNT --> REP
    STA --> EVAL
    MIR --> EVAL

    classDef ext fill:#e5e7eb,stroke:#6b7280,color:#111827
    classDef py fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef kt fill:#d1fae5,stroke:#059669,color:#064e3b
    classDef st fill:#fef3c7,stroke:#d97706,color:#78350f
    class CONF,SESS,EMB ext
    class EXP,GIT,EVAL py
    class MCPT,QRY,IDX,L2,MNT kt
    class MIR,DER,STA,REP st
```

## 배포 형태

```mermaid
flowchart LR
    subgraph HOST["단일 호스트"]
        CRON["cron"] --> SYNCP["싱크 프로세스<br/>Python · 주기 실행"]
        SYNCP --> VOL[("공유 볼륨<br/>mirror derived state")]
        VOL --> SRVP["서버 프로세스<br/>Kotlin · 상주"]
    end
    C1(["세션"]) -->|HTTPS| SRVP
    C2(["세션"]) -->|HTTPS| SRVP
    BAK["백업"] -.->|"state/ 만"| VOL
```

## 책임 분리

| 프로세스 | 언어 | 쓰기 권한 | 실행 |
|---|---|---|---|
| 싱크 배치 | Python | `mirror/` `derived/` **배타적** | 주기적 |
| 서버 | Kotlin | `state/` **가산 전용** | 상주 |
| 평가 하니스 | Python | 없음 (읽기 전용) | 수동 |

**파괴적 쓰기를 하는 프로세스는 싱커 하나뿐입니다.** 서버는 추가만 하므로 세션 간 조율이 필요 없고, 인덱스 교체는 참조 원자적 스왑으로 처리합니다.

## 불변식

| 불변식 | 강제 수단 |
|---|---|
| 페이지 ID가 권위, 제목은 렌더링 | `mirror/pages/{id}.md` |
| 구조 변경만 무효화 트리거 | `structure/` 분리 + `git diff` |
| 궤적만 복구 불가 | `state/` 격리, 백업 대상 = 이 디렉터리 |
| 위키에 쓰지 않음 | Confluence 토큰 = 읽기 전용 스코프 |
| 궤적은 요청이 아니라 관측 | 서버가 자기 호출 로그에서 추출 |
| 세션 간 학습 공유 | 서버 원격 · 단일 인스턴스 |

## 데이터 흐름 요약

**싱크** — `CQL lastModified` → 마크다운 + 구조 서명 → git commit → `git diff -- structure/` → 변경분 숏컷 `suspect` 마킹 (삭제 아님) → 앵커 전치 재구축 → 벡터 증분

**질의** — 경로 의존성 분류 → IDF 트리아지 (여기서 `Diffuse`면 비용 0으로 종료) → Lucene BM25 + 앵커 필드 + kNN + 숏컷 포스팅 → RRF → 좌표 + 폴백 경로

**학습** — MCP 호출 로그 관측 → `trajectories.jsonl` append → 목적지 분포별 Wilson 갱신 → `edges.json` 스냅샷

**보고** — 반복 적중 숏컷 승격 → `reports/` → 누락 링크·고아 페이지

## 이 아키텍처가 결정하지 않은 것

- **ACL 시행** — 파일럿은 권한 균일 스페이스로 회피. 공유 서버라 미룰 수 있는 기간이 짧음
- **L2 착수 여부** — 궤적이 쌓이고 비링크 도달 비율을 잰 뒤 판단
- **suspect 임계** — 무효화 후 검증 통과율로 사후 튜닝
