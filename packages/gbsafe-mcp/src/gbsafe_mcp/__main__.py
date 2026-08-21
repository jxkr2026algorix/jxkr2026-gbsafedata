"""`gbsafe-mcp` 실행 진입점.

MCP 클라이언트 설정에서 `uv run gbsafe-mcp` 또는
`python -m gbsafe_mcp`로 기동한다.
"""

from __future__ import annotations

import asyncio
import sys


def main() -> int:
    from .server import run_stdio

    try:
        asyncio.run(run_stdio())
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"gbsafe-mcp 기동 실패: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
