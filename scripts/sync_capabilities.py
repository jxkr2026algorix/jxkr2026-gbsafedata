#!/usr/bin/env python3
"""재난별 가용성 매트릭스를 jxkr2026-datasets에서 가져온다.

`capabilities.json`은 "지금 무엇이 되는가"의 단일 출처다. 재난 13종을 탐지·
위험도·대피소 세 축으로 나누고, 축이 다 있으면 `ready`, 탐지만 되면 `partial`,
탐지조차 없으면 `blocked`으로 표시한다.

이 파일을 동봉하는 이유가 있다. 배포 대상에는 형제 저장소가 없다. 그런데 이
매트릭스가 없으면 `partial`인 재난(지진처럼 발생은 알지만 대피소를 모르는 것)이
`ready`와 구별되지 않고, 그것은 이 저장소가 막으려는 실패와 같은 종류다 —
확인하지 못한 것을 확인한 것처럼 보이게 만드는 것.

사용법:
    uv run python scripts/sync_capabilities.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT.parent / "jxkr2026-datasets" / "catalog" / "capabilities.json"
TARGET = (
    REPO_ROOT
    / "packages"
    / "gbsafe-core"
    / "src"
    / "gbsafe_core"
    / "data"
    / "capabilities.json"
)

REQUIRED_KEYS = ("hazards", "sources", "axes", "status_legend")
REQUIRED_AXES = ("detection", "risk", "shelter")
VALID_STATUS = {"ready", "partial", "blocked"}


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    if not source.is_file():
        print(f"가용성 매트릭스를 찾을 수 없습니다: {source}", file=sys.stderr)
        print("jxkr2026-datasets/catalog/capabilities.json 경로를 인자로 주세요.", file=sys.stderr)
        return 1

    payload = json.loads(source.read_text(encoding="utf-8"))

    # 형태를 확인하고 복사한다. 형태가 바뀐 것을 모른 채 복사하면 로더가
    # 조용히 빈 매트릭스를 읽고, 모든 재난이 '가용성 미상'이 된다.
    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        print(f"필수 키가 없습니다: {missing}", file=sys.stderr)
        return 1

    hazards = payload["hazards"]
    if not hazards:
        print("hazards가 비어 있습니다", file=sys.stderr)
        return 1

    for name, entry in hazards.items():
        for axis in REQUIRED_AXES:
            if axis not in entry:
                print(f"{name}: '{axis}' 축이 없습니다", file=sys.stderr)
                return 1
        status = entry.get("status")
        if status not in VALID_STATUS:
            print(f"{name}: 알 수 없는 status {status!r}", file=sys.stderr)
            return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, TARGET)

    counts: dict[str, int] = {}
    for entry in hazards.values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    summary = " / ".join(f"{key} {counts.get(key, 0)}" for key in ("ready", "partial", "blocked"))
    print(f"{TARGET.relative_to(REPO_ROOT)} — 재난 {len(hazards)}종 ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
