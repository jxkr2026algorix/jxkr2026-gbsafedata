#!/usr/bin/env python3
"""시군에 배정한 ASOS 지점번호가 실제로 조회되는지 확인한다.

`TestAsosStationMapping`은 지점번호가 **가장 가까운 지점**인지 검사하지만,
그 번호로 실제 조회가 되는지는 검사하지 못한다. 둘은 다른 문제다. 기상청은
국내 지점번호와 WMO 번호를 따로 쓰고, 같은 지점이 두 체계에서 다른 번호를
갖는다. WMO 번호를 넣으면 다른 지점을 조용히 읽거나 빈 결과가 나온다.

실제로 이 표를 고치는 동안 WIS2(WMO) 목록을 국내 번호로 착각할 뻔했다.
그래서 번호가 살아 있는지는 실호출로만 확인한다.

지점당 1회, 3행만 요청한다. 인증키가 없으면 건너뛴다(실패가 아니다).

사용법:
    uv run python scripts/check_asos_stations_live.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from gbsafe_core.config import get_settings
from gbsafe_core.regions import ASOS_STATION_INFO, ASOS_STATIONS, SIGUNGU

KST = timezone(timedelta(hours=9))
ENDPOINT = "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"

#: 네트워크에 닿지 못한 것을 나타내는 예외. 원천 전체가 이 상태면 실행 위치
#: 문제이지 지점번호 결함이 아니다. 둘을 섞으면 이 검사가 신호를 잃는다.
_UNREACHABLE = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)


async def probe(client: httpx.AsyncClient, key: str, station: int, day: str) -> tuple[str, str]:
    """(결과, 설명). 결과는 ok / empty / unreachable / broken."""
    params = {
        "serviceKey": key,
        "pageNo": 1,
        "numOfRows": 3,
        "dataType": "JSON",
        "dataCd": "ASOS",
        "dateCd": "HR",
        "startDt": day,
        "startHh": "01",
        "endDt": day,
        "endHh": "03",
        "stnIds": str(station),
    }
    try:
        response = await client.get(ENDPOINT, params=params, timeout=40)
    except _UNREACHABLE as exc:
        return "unreachable", type(exc).__name__
    except httpx.HTTPError as exc:
        return "broken", f"{type(exc).__name__}: {exc}"

    if response.status_code != 200:
        return "broken", f"HTTP {response.status_code}"
    try:
        body = response.json()["response"]
    except Exception:
        return "broken", f"JSON이 아닌 응답: {response.text[:80]!r}"

    header = body.get("header") or {}
    if header.get("resultCode") != "00":
        return "broken", f'resultCode={header.get("resultCode")} {header.get("resultMsg")}'

    rows = ((body.get("body") or {}).get("items") or {}).get("item") or []
    if not rows:
        return "empty", "행이 없습니다 — 번호가 이 서비스에 없을 수 있습니다"
    return "ok", f"{len(rows)}행"


async def main() -> int:
    settings = get_settings()
    raw = settings.data_go_kr_service_key
    key = raw.get_secret_value() if hasattr(raw, "get_secret_value") else raw
    if not key:
        print("인증키가 없어 건너뜁니다. (실패가 아닙니다)")
        return 0

    day = (datetime.now(KST) - timedelta(days=1)).strftime("%Y%m%d")
    stations = sorted(set(ASOS_STATIONS.values()))

    users: dict[int, list[str]] = {}
    for code, station in ASOS_STATIONS.items():
        users.setdefault(station, []).append(SIGUNGU[code].name)

    print(f"조회일 {day} · 지점 {len(stations)}개\n")
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(probe(client, key, station, day) for station in stations)
        )

    broken: list[str] = []
    unreachable = 0
    for station, (verdict, detail) in zip(stations, results, strict=True):
        name = ASOS_STATION_INFO[station].name if station in ASOS_STATION_INFO else "?"
        served = ", ".join(users[station])
        mark = {"ok": "  ", "empty": "!!", "broken": "!!", "unreachable": ".."}[verdict]
        print(f"{mark} {station:>4} {name:<7} {verdict:<11} {detail:<28} <- {served}")
        if verdict == "unreachable":
            unreachable += 1
        elif verdict != "ok":
            broken.append(f"{station}({name}) — {detail} — 사용 시군: {served}")

    print()
    if unreachable == len(stations):
        print("모든 지점이 응답하지 않습니다 — 네트워크 문제로 보고 통과시킵니다.")
        return 0
    if broken:
        print("조회되지 않는 지점번호:")
        for item in broken:
            print(f"  - {item}")
        print("\n이 시군은 다른 지역의 관측값을 읽거나 빈 결과를 받습니다.")
        return 1

    print(f"지점 {len(stations)}개 모두 조회됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
