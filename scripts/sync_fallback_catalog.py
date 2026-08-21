#!/usr/bin/env python3
"""동봉 폴백 카탈로그를 jxkr2026-datasets에서 재생성한다.

데이터셋 저장소가 계속 갱신되므로 이 스크립트를 다시 돌려 폴백을 맞춘다.
폴백은 데이터셋 저장소 없이 설치한 사용자를 위한 최소 동작 보장이며,
실제 운영에서는 저장소를 나란히 두는 것이 정상 경로다.

라이선스 제약이 있는 원본 데이터는 담지 않는다. 여기 들어가는 것은
메타데이터(이름·기관·라이선스·갱신주기·검증 결과)뿐이다.

사용법:
    uv run python scripts/sync_fallback_catalog.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT.parent / "jxkr2026-datasets" / "catalog"
TARGET = (
    REPO_ROOT
    / "packages"
    / "gbsafe-core"
    / "src"
    / "gbsafe_core"
    / "data"
    / "catalog-fallback.json"
)

#: 폴백에 남길 필드. 원본 데이터가 아니라 메타데이터만 담는다.
KEEP_FIELDS = (
    "pk",
    "catalog_name",
    "portal_title",
    "category",
    "provider",
    "dept",
    "access_route",
    "external_portal",
    "api_type",
    "format",
    "license",
    "license_raw",
    "review_dev",
    "review_prod",
    "dev_traffic",
    "update_cycle",
    "rows",
    "modified",
    "url",
    "endpoint",
    "keywords",
    "apply_status",
    "note",
)


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    main_file = source / "datago-datasets.json"
    if not main_file.is_file():
        print(f"카탈로그를 찾을 수 없습니다: {main_file}", file=sys.stderr)
        print("jxkr2026-datasets 저장소 경로를 인자로 주세요.", file=sys.stderr)
        return 1

    rows = json.loads(main_file.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("datago-datasets.json이 배열이 아닙니다", file=sys.stderr)
        return 1

    overrides: dict[str, dict[str, object]] = {}
    override_file = source / "verified-overrides.json"
    if override_file.is_file():
        payload = json.loads(override_file.read_text(encoding="utf-8"))
        raw = payload.get("overrides", {}) if isinstance(payload, dict) else {}
        overrides = {k: v for k, v in raw.items() if isinstance(v, dict)}

    routing: dict[str, str] = {}
    routing_file = source / "link-routing.json"
    if routing_file.is_file():
        payload = json.loads(routing_file.read_text(encoding="utf-8"))
        raw = payload.get("routing", {}) if isinstance(payload, dict) else {}
        routing = {k: str(v) for k, v in raw.items()}

    datasets: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pk = str(row.get("pk") or "").strip()
        if not pk:
            continue
        merged = dict(row)
        override = overrides.get(pk)
        if override:
            merged.update(override)
        if pk in routing and not merged.get("external_portal"):
            merged["external_portal"] = routing[pk]
        entry = {key: merged[key] for key in KEEP_FIELDS if merged.get(key) not in (None, "")}
        entry["verified"] = bool(override)
        datasets.append(entry)

    datasets.sort(key=lambda item: str(item.get("pk")))
    payload = {
        "_comment": (
            "jxkr2026-datasets/catalog에서 생성한 폴백 스냅샷. "
            "데이터셋 저장소가 없을 때만 사용된다. "
            "scripts/sync_fallback_catalog.py로 재생성한다."
        ),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": str(source),
        "datasets": datasets,
    }

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    verified = sum(1 for item in datasets if item.get("verified"))
    print(f"{TARGET.relative_to(REPO_ROOT)} — {len(datasets)}건 (검증 {verified}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
