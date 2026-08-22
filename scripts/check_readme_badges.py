#!/usr/bin/env python3
"""README 뱃지의 숫자가 실제 값과 같은지 검사한다.

뱃지에 테스트 수나 도구 수를 적어두면 코드가 변할 때마다 낡는다. CI가 실제
값과 비교해 어긋나면 실패하고, `--write`로 갱신한다.

사용법:
    uv run python scripts/check_readme_badges.py          # 검사만 (CI)
    uv run python scripts/check_readme_badges.py --write  # 갱신
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 뱃지를 실은 문서 전부. 영문만 검사하다가 국문 README가 501에 멈춰 있었고,
#: 두 문서가 서로 다른 테스트 수를 광고했다.
READMES = (REPO_ROOT / "README.md", REPO_ROOT / "README.ko.md")

TEST_BADGE = re.compile(r"(tests-)(\d+)(%20passing)")
TOOL_BADGE = re.compile(r"(MCP-)(\d+)(%20read--only%20tools)")


def count_tests() -> int:
    """수집된 테스트 수. 실행하지 않고 세기만 한다."""
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(r"(\d+) tests? collected", result.stdout)
    if match:
        return int(match.group(1))
    # 구버전 pytest는 마지막 줄에 개수만 출력한다
    return sum(1 for line in result.stdout.splitlines() if "::" in line)


def count_tools() -> int:
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-c",
            "from gbsafe_mcp.tools import validated_tools; print(len(validated_tools()))",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


def main() -> int:
    write = "--write" in sys.argv
    tests, tools = count_tests(), count_tools()

    stale: list[str] = []
    for readme in READMES:
        content = readme.read_text(encoding="utf-8")
        updated = TEST_BADGE.sub(rf"\g<1>{tests}\g<3>", content)
        updated = TOOL_BADGE.sub(rf"\g<1>{tools}\g<3>", updated)
        if updated == content:
            continue
        stale.append(readme.name)
        if write:
            readme.write_text(updated, encoding="utf-8")

    if write:
        if stale:
            print(f"updated badges in {', '.join(stale)}: tests={tests} tools={tools}")
        else:
            print(f"badges already current: tests={tests} tools={tools}")
        return 0

    if stale:
        print(
            f"{', '.join(stale)} 뱃지가 실제 값과 다릅니다 (tests={tests}, tools={tools}).\n"
            "uv run python scripts/check_readme_badges.py --write 로 갱신하세요.",
            file=sys.stderr,
        )
        return 1
    print(f"badges match: tests={tests} tools={tools}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
