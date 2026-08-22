#!/usr/bin/env python3
"""기상청 지상관측(ASOS) 지점 정보를 내려받아 동봉 참조표를 만든다.

왜 필요한가. 시군마다 어느 관측지점을 쓸지는 `gbsafe_core.regions.ASOS_STATIONS`에
손으로 적혀 있었고, 그 값이 맞는지 확인할 방법이 없었다. 실제로 일곱 곳이
가장 가까운 지점을 쓰고 있지 않았다 — 영덕군은 13.7km 거리에 자기 지점(277)이
있는데도 42.6km 떨어진 포항을 읽고 있었고, 문경시는 5.6km 거리의 문경(273)
대신 20.0km 떨어진 상주를 읽고 있었다.

지점번호가 틀려도 API는 200과 그럴듯한 강우량을 돌려준다. 그래서 다른 지역의
비를 이 지역의 비로 읽으면서 성공한 것처럼 보인다. 격자 좌표에서 이미 같은
일이 있었다(문경으로 표기된 (90,95)가 71.6km 떨어진 구미였다).

출처는 기상청 기상자료개방포털의 관측지점 정보다. 지점번호·지점명·위경도·
관측개시일만 담으며 관측값은 담지 않는다.

    https://data.kma.go.kr/tmeta/stn/selectStnDetail.do?isSelectStn=Y&pgmNo=82&stdStnNo=<번호>

주의: 이 번호는 기상청 **국내 지점번호**다. WMO 국제 번호(WIS2의
`0-20000-0-47xxx`)와 번호 체계가 다르며, data.go.kr의 AsosHourlyInfoService가
받는 `stnIds`는 국내 지점번호다. 두 체계를 섞으면 조용히 다른 지점을 읽는다.

사용법:
    uv run python scripts/sync_asos_stations.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = (
    REPO_ROOT
    / "packages"
    / "gbsafe-core"
    / "src"
    / "gbsafe_core"
    / "data"
    / "asos-stations.json"
)

DETAIL_URL = "https://data.kma.go.kr/tmeta/stn/selectStnDetail.do"

#: 국내 지상관측 지점번호가 놓인 범위. 넉넉하게 훑고 없는 번호는 버린다.
STATION_NUMBER_RANGE = range(90, 301)

#: 경북 시군 중심에서 이 거리 안에 있는 지점만 담는다. 어떤 시군에 대해서도
#: "가장 가까운 지점"을 이 표 안에서 증명할 수 있을 만큼 넓게 잡은 값이다.
KEEP_WITHIN_KM = 80.0

#: 고정 관측소가 아닌 지점. 이동관측차량(`_차량`)과 레이더(`(레)`)는 시간자료
#: 조회 대상이 아닌데도 좌표가 있어 최근접 계산에 끼어든다. 실제로 대구_차량
#: (193)이 경산시에서 9.9km로 대구(143)와 같은 거리라, 정렬 순서가 바뀌면
#: 경산시가 관측차량을 가리킬 수 있었다.
NON_FIXED_MARKERS = ("_차량", "(레)")

_NAME = re.compile(r"지점명\(한글\)</th>\s*<td>([^<]*)</td>")
_ENG = re.compile(r"지점명\(영문\)</th>\s*<td>([^<]*)</td>")
_LAT = re.compile(r"위도\s*:\s*([0-9.]+)")
_LON = re.compile(r"경도\s*:\s*([0-9.]+)")
_START = re.compile(r"관측개시일</th>\s*<td>([^<]*)</td>")
_END = re.compile(r"관측종료일</th>\s*<td>([^<]*)</td>")


def _one(pattern: re.Pattern[str], text: str) -> str:
    found = pattern.search(text)
    return found.group(1).strip() if found else ""


async def fetch_station(
    client: httpx.AsyncClient, gate: asyncio.Semaphore, number: int
) -> dict[str, object] | None:
    """지점 하나의 정보. 없는 번호와 좌표 없는 번호는 None."""
    async with gate:
        for attempt in range(3):
            try:
                response = await client.get(
                    DETAIL_URL,
                    params={"isSelectStn": "Y", "pgmNo": 82, "stdStnNo": number},
                    timeout=30,
                )
            except httpx.HTTPError:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue

            body = response.text
            name, lat, lon = _one(_NAME, body), _one(_LAT, body), _one(_LON, body)
            if not (name and lat and lon):
                return None
            return {
                "station_id": number,
                "name": name,
                "name_en": _one(_ENG, body),
                "lat": float(lat),
                "lon": float(lon),
                "observing_since": _one(_START, body),
                "closed_on": _one(_END, body),
            }
    return None


async def collect() -> list[dict[str, object]]:
    gate = asyncio.Semaphore(6)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *(fetch_station(client, gate, n) for n in STATION_NUMBER_RANGE)
        )
    return [item for item in results if item is not None]


def main() -> int:
    # 시군 좌표는 라이브러리에서 가져온다. 거리 계산 기준을 한 곳에 둔다.
    sys.path.insert(0, str(REPO_ROOT / "packages" / "gbsafe-core" / "src"))
    from gbsafe_core.models import GeoPoint
    from gbsafe_core.regions import SIGUNGU, haversine_km

    stations = asyncio.run(collect())
    if len(stations) < 50:
        print(
            f"지점을 {len(stations)}개밖에 받지 못했습니다 — 포털 응답을 확인하세요.",
            file=sys.stderr,
        )
        return 1

    centers = [item.center for item in SIGUNGU.values()]
    nearby: list[dict[str, object]] = []
    for station in stations:
        if any(marker in str(station["name"]) for marker in NON_FIXED_MARKERS):
            continue
        point = GeoPoint(lat=float(station["lat"]), lon=float(station["lon"]))
        if min(haversine_km(center, point) for center in centers) <= KEEP_WITHIN_KM:
            nearby.append(station)

    nearby.sort(key=lambda item: int(item["station_id"]))
    payload = {
        "_comment": (
            "기상청 기상자료개방포털 관측지점 정보에서 생성. 지점번호는 국내 "
            "지점번호이며 WMO 번호와 다르다. scripts/sync_asos_stations.py로 재생성한다."
        ),
        "source": "https://data.kma.go.kr/tmeta/stn/selectStnDetail.do",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "kept_within_km_of_gyeongbuk": KEEP_WITHIN_KM,
        "stations": nearby,
    }

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    closed = sum(1 for item in nearby if item["closed_on"])
    print(
        f"{TARGET.relative_to(REPO_ROOT)} — 전체 {len(stations)}개 중 경북 인근 "
        f"{len(nearby)}개 (폐지 {closed}개)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
