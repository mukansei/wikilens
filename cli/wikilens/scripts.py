"""
문서를 어느 문자 집합으로 썼는지 보고 **볼트에 편입할지 정한다.**

다국어 코퍼스용이다. 같은 내용이 여러 언어로 있으면 **읽지 못하는 언어의 문서가
어휘 순위에서 이길 수 있다** — 실측(한·베 혼재 13,933건, 2026-08-15): 한국어 질의에
베트남어 번역본이 1위이고 한국어 원본이 10위 밖이었다. 두 문서가 같은 영문 식별자
(`ga`·URL)를 공유하는데 번역본이 두 배 길어 tf 가 높았다. 앵커 층도 번역본을 올렸다
(원본의 인링크 19개 중 18개가 `SDK 접속 가이드` 라고 불러 `ga` 가 안 들어 있었다).
**어휘 층은 정상 동작한 것이라 언어 말고는 가를 신호가 없다.**

### 판정을 여기서 하는 이유

`build` 는 이미 본문을 파싱하고 있어 계산이 공짜다. 그리고 **결정이 한 곳이어야
두 판이 같은 답을 낸다** — 서버에 두면 로컬판이 아무것도 못 받는다(그쪽은 서버가
없다). `sync`·`build` 가 볼트를 만들고 서버는 읽기만 한다는 규칙 그대로다.

### 문자 집합이지 언어가 아니다

라틴 하나에 100개 넘는 언어가 있어 `french` 같은 이름은 원리적으로 못 만든다
(`é è à ç` 를 스페인어·포르투갈어가 똑같이 쓴다). 갈리는 것은 **자기만 쓰는 범위가
있는 언어**뿐이고 [VIETNAMESE](U+1EA0~1EF9)가 그 예다. 이름을 다 갖출 수 없으므로
**범위를 직접 적는 길**을 연다(`"U+0100-017F"`).

### 낱말 단위로 센다 — 글자가 아니라

베트남어는 글자 대부분이 평범한 라틴이고 성조부호만 다르다(`Sử dụng` 에서 이질적인
글자는 2/7). 글자로 세면 신호가 묽어진다. 실측(같은 문서):

    글자 단위   한국어 0.00% · 베트남어 4.17% · 베트남어(성조 많음) 24.26%
    낱말 단위   한국어 0.00% · 베트남어 16.6% · 베트남어(성조 많음) 76.6%

낱말에 선언 밖 글자가 **하나라도** 있으면 그 낱말을 밖으로 세면, 글자마다 다른
한자·태국어와 부호만 다른 베트남어가 **같은 척도**로 잡힌다 — 문턱 하나가 모든
문자 집합에 통한다.
"""
from __future__ import annotations

import re

#: 이름 → 코드포인트 범위. **`ascii` 를 빼면 코드·URL·영문이 전부 밖이 된다** —
#: 실측으로 이 코퍼스는 `[hangul]` 만 선언하면 84%가 걸린다(본문의 46%가 ASCII).
SETS: dict[str, list[tuple[int, int]]] = {
    "ascii":      [(0x41, 0x5A), (0x61, 0x7A)],
    # 라틴 전체. ASCII 를 포함하고, 확장 영역에 유럽어와 베트남어가 함께 들어 있다.
    "latin":      [(0x41, 0x5A), (0x61, 0x7A), (0xC0, 0x24F), (0x1E00, 0x1EFF),
                   (0x2C60, 0x2C7F), (0xA720, 0xA7FF)],
    # 베트남어 전용. U+1EA0~1EF9 는 다른 언어가 쓰지 않는다. 낮은 영역 몇 자는
    # 유럽어와 겹치지만, 베트남어 문장은 U+1EXX 를 거의 반드시 포함한다.
    "vietnamese": [(0x1EA0, 0x1EF9), (0xC0, 0xC3), (0xC8, 0xCA), (0xCC, 0xCD),
                   (0xD2, 0xD5), (0xD9, 0xDA), (0xDD, 0xDD), (0xE0, 0xE3),
                   (0xE8, 0xEA), (0xEC, 0xED), (0xF2, 0xF5), (0xF9, 0xFA),
                   (0xFD, 0xFD), (0x102, 0x103), (0x110, 0x111), (0x128, 0x129),
                   (0x168, 0x169), (0x1A0, 0x1B0)],
    "hangul":     [(0xAC00, 0xD7A3), (0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F)],
    "han":        [(0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF)],
    "kana":       [(0x3040, 0x309F), (0x30A0, 0x30FF), (0x31F0, 0x31FF)],
    "cyrillic":   [(0x400, 0x4FF), (0x500, 0x52F), (0x2DE0, 0x2DFF)],
    "arabic":     [(0x600, 0x6FF), (0x750, 0x77F), (0x8A0, 0x8FF), (0xFB50, 0xFDFF)],
    "devanagari": [(0x900, 0x97F), (0xA8E0, 0xA8FF)],
    "thai":       [(0xE00, 0xE7F)],
    "hebrew":     [(0x590, 0x5FF), (0xFB1D, 0xFB4F)],
    "greek":      [(0x370, 0x3FF), (0x1F00, 0x1FFF)],
    "armenian":   [(0x530, 0x58F)],
    "georgian":   [(0x10A0, 0x10FF), (0x2D00, 0x2D2F)],
    "bengali":    [(0x980, 0x9FF)],
    "tamil":      [(0xB80, 0xBFF)],
    "telugu":     [(0xC00, 0xC7F)],
    "kannada":    [(0xC80, 0xCFF)],
    "malayalam":  [(0xD00, 0xD7F)],
    "sinhala":    [(0xD80, 0xDFF)],
    "khmer":      [(0x1780, 0x17FF)],
    "lao":        [(0xE80, 0xEFF)],
    "myanmar":    [(0x1000, 0x109F)],
    "ethiopic":   [(0x1200, 0x137F)],
    "tibetan":    [(0xF00, 0xFFF)],
}

_RANGE = re.compile(r"^U\+([0-9A-Fa-f]{4,6})-([0-9A-Fa-f]{4,6})$")


def resolve(specs: list[str]) -> list[tuple[int, int]]:
    """
    설정 문자열들을 코드포인트 범위 목록으로. 이름이거나 `U+0100-017F` 꼴이다.

    **모르는 이름은 예외를 던진다** — 조용히 무시하면 사용자는 필터가 걸린 줄 알고
    쓴다. 분석기 이름을 틀렸을 때와 같은 규칙이다(`DECISIONS.md` D14).
    """
    out: list[tuple[int, int]] = []
    for spec in specs:
        s = spec.strip()
        if not s:
            continue
        if s.lower() in SETS:
            out += SETS[s.lower()]
            continue
        m = _RANGE.match(s)
        if not m:
            raise ValueError(
                f"알 수 없는 문자 집합 '{s}'. 가능한 이름: {'·'.join(sorted(SETS))} "
                f"— 또는 범위를 직접 적으세요(예: U+0100-017F)"
            )
        a, b = int(m.group(1), 16), int(m.group(2), 16)
        if a > b:
            raise ValueError(f"범위가 뒤집혔습니다: {s}")
        out.append((a, b))
    return out


def foreign_word_ratio(text: str, ranges: list[tuple[int, int]]) -> float:
    """
    선언 밖 **낱말**의 비율. 선언이 비면 0(=전부 통과).

    **글자가 없는 낱말은 안 센다** — 숫자·기호만 있는 토큰(`2026`·`v1.2`)은 어느
    언어에도 속하지 않아서, 분모에 넣으면 코드가 많은 문서가 무조건 통과한다.
    """
    if not ranges:
        return 0.0
    total = foreign = 0
    has_letter = is_foreign = False
    for ch in text:
        if ch.isalpha():
            has_letter = True
            o = ord(ch)
            if not any(a <= o <= b for a, b in ranges):
                is_foreign = True
        elif not (ch.isdigit() or ch == "_"):
            if has_letter:
                total += 1
                foreign += is_foreign
            has_letter = is_foreign = False
    if has_letter:
        total += 1
        foreign += is_foreign
    return foreign / total if total else 0.0
