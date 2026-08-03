"""
공유 토크나이저. 표준 라이브러리만 사용한다.

클라이언트와 서버가 **반드시 같은 규칙**을 써야 한다. 다르면 항이 겹치지 않아
포스팅 조회가 조용히 0건을 반환한다. 실제로 그 버그를 겪었으므로 한 곳에 둔다.

한국어는 교착어라 조사가 붙는다('로그인을/로그인은'). 형태소 분석기 없이
문자 바이그램을 함께 색인하면 상당 부분 흡수된다. 정확도가 필요하면
kiwipiepy로 교체하되, **양쪽을 동시에** 바꿔야 한다.
"""
import re

_WORD = re.compile(r"[A-Za-z0-9_]{2,}|[가-힣]{2,}")
_HANGUL = re.compile(r"^[가-힣]+$")


def tokenize(text: str) -> list[str]:
    out = []
    for m in _WORD.finditer(text.lower()):
        w = m.group(0)
        out.append(w)
        if _HANGUL.match(w) and len(w) > 2:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
    return out
