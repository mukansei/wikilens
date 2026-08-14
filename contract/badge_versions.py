"""
README 최상단 배지의 버전이 빌드 파일과 같은지 본다.

**배지는 정적이다** — 저장소가 비공개라 shields.io 가 빌드·라이선스 상태를 읽을 수
없어서, 버전을 손으로 복제하는 것 말고 방법이 없다. 그러면 의존성을 올릴 때 배지만
옛 버전을 말한다 — 이 저장소가 반복해서 물린 "두 곳이 같아야 하는데 연결이 파일도
주석도 아닌 것" 그대로다. 그래서 검사를 붙인다.

**앞 두 자리만 본다.** 패치 버전은 배지에 안 적는다 — 올릴 때마다 배지를 고치게 되면
아무도 안 고치고 계약만 빨개진다.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _two(v: str) -> str:
    return ".".join(v.split(".")[:2])


def main() -> int:
    rd = (ROOT / "README.md").read_text(encoding="utf-8")
    gr = (ROOT / "server" / "build.gradle.kts").read_text(encoding="utf-8")
    py = (ROOT / "cli" / "pyproject.toml").read_text(encoding="utf-8")

    def grab(pattern: str, text: str, where: str) -> str:
        m = re.search(pattern, text)
        if not m:
            print(f"  정본에서 버전을 못 찾음: {where}")
            sys.exit(1)
        return m.group(1)

    want = {
        "Python": _two(grab(r'requires-python\s*=\s*">=([\d.]+)"', py, "pyproject requires-python")),
        "Kotlin": _two(grab(r'kotlin\("jvm"\) version "([\d.]+)"', gr, "gradle kotlin")),
        # 라벨이 `JVM` 이 아니라 `Java` 인 이유: 빌드는 JDK(`temurin:25-jdk`)이고
        # 런타임은 JRE(`temurin:25-jre`)다. `JDK 25` 는 런타임에 대해 틀리고,
        # `JVM 25` 는 아무도 안 쓰는 표기다 — 버전은 "Java 25" 라고 부른다.
        "Java": grab(r"jvmToolchain\((\d+)\)", gr, "gradle jvmToolchain"),
        # 배지 라벨에는 공백이 `%20` 으로 들어간다.
        "Spring%20Boot": _two(grab(r'springframework\.boot"\) version "([\d.]+)"', gr, "gradle spring boot")),
        "Lucene": _two(grab(r'luceneVersion = "([\d.]+)"', gr, "gradle lucene")),
    }

    bad = []
    for label, v in want.items():
        # **메시지의 앞 숫자만** 본다. 뒤에 무엇이 붙든(`9.11%20%28Nori%29`,
        # `3.10%2B`) 대조하려는 것은 버전 하나다 — 메시지 전체를 잡으면 배지 문구를
        # 손볼 때마다 계약이 빨개지고, 그러면 계약이 아니라 잔소리가 된다.
        m = re.search(r"img\.shields\.io/badge/" + re.escape(label) + r"-([\d.]+)", rd)
        if not m:
            bad.append(f"{label}: 배지가 없음 (빌드는 {v})")
            continue
        got = m.group(1)
        if got != v:
            bad.append(f"{label}: 배지 {got} · 빌드 {v}")

    if bad:
        print("\n".join("  " + b for b in bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
