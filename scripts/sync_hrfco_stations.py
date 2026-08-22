#!/usr/bin/env python3
"""홍수통제소 수위관측소 제원을 내려받아 경북 관측소 참조표를 만든다.

수위 자체는 10분마다 바뀌지만 **경보 임계수위는 거의 바뀌지 않는다.** 그래서
임계값은 참조데이터로 동봉하고, 실시간 조회는 관측값 한 번만 부른다. 매번 제원
1417건을 함께 받으면 조회가 두 배로 느려지고 실패 지점도 두 배가 된다.

임계값이 없는 관측소를 그냥 버리지 않고 표에 남기는 이유가 있다. 임계값을 모르는
관측소는 "안전하다"가 아니라 **"판단할 수 없다"**이며, 그 구분이 이 저장소의
핵심이다.

출처: 한강홍수통제소 오픈API (https://api.hrfco.go.kr)

사용법:
    uv run python scripts/sync_hrfco_stations.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = (
    REPO_ROOT
    / "packages"
    / "gbsafe-connectors"
    / "src"
    / "gbsafe_connectors"
    / "data"
    / "hrfco-stations.json"
)

BASE = "https://api.hrfco.go.kr"

#: 경북 관측소를 고르는 기준. 주소 표기가 기관마다 흔들려 둘 다 받는다.
GYEONGBUK_PREFIXES = ("경상북도", "경북")


def load_key() -> str:
    key = os.environ.get("GBSAFE_HRFCO_SERVICE_KEY", "").strip()
    if key:
        return key
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        found = re.search(
            r"^GBSAFE_HRFCO_SERVICE_KEY=(.+)$", env_file.read_text(encoding="utf-8"), re.M
        )
        if found:
            return found.group(1).strip()
    return ""


def _float(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _dms(value: object) -> float | None:
    """제원의 위경도는 `36-35-12` 같은 도분초 문자열로 온다."""
    text = str(value or "").strip()
    if not text:
        return None
    parts = [part for part in re.split(r"[-:\s]+", text) if part]
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0]
    degrees = numbers[0]
    minutes = numbers[1] if len(numbers) > 1 else 0.0
    seconds = numbers[2] if len(numbers) > 2 else 0.0
    return degrees + minutes / 60.0 + seconds / 3600.0


def main() -> int:
    key = load_key()
    if not key:
        print("GBSAFE_HRFCO_SERVICE_KEY가 없습니다.", file=sys.stderr)
        return 1

    response = httpx.get(f"{BASE}/{key}/waterlevel/info.json", timeout=90)
    response.raise_for_status()
    content = response.json().get("content") or []
    if not content:
        print("관측소 제원이 비어 있습니다 — 응답을 확인하세요.", file=sys.stderr)
        return 1

    stations: list[dict[str, object]] = []
    for row in content:
        address = str(row.get("addr") or "")
        if not address.startswith(GYEONGBUK_PREFIXES):
            continue
        stations.append(
            {
                "station_id": str(row.get("wlobscd") or "").strip(),
                "name": str(row.get("obsnm") or "").strip(),
                "address": address,
                "lat": _dms(row.get("lat")),
                "lon": _dms(row.get("lon")),
                "attention_m": _float(row.get("attwl")),
                "advisory_m": _float(row.get("wrnwl")),
                "warning_m": _float(row.get("almwl")),
                "serious_m": _float(row.get("srswl")),
                "plan_flood_m": _float(row.get("pfh")),
                "is_forecast_point": str(row.get("fstnyn") or "").strip().upper() == "Y",
            }
        )

    stations = [item for item in stations if item["station_id"]]
    stations.sort(key=lambda item: str(item["station_id"]))
    with_thresholds = sum(1 for item in stations if item["advisory_m"] is not None)

    payload = {
        "_comment": (
            "한강홍수통제소 수위관측소 제원(경북). 경보 임계수위는 거의 바뀌지 않아 "
            "참조데이터로 동봉한다. scripts/sync_hrfco_stations.py로 재생성한다."
        ),
        "source": f"{BASE}/<인증키>/waterlevel/info.json",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "stations": stations,
    }
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(
        f"{TARGET.relative_to(REPO_ROOT)} — 경북 {len(stations)}개 "
        f"(임계수위 보유 {with_thresholds}개, 미보유 {len(stations) - with_thresholds}개)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
