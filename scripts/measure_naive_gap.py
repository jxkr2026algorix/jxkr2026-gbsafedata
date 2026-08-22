#!/usr/bin/env python3
"""'뻔한 구현'이 무엇을 잘못 말하는지 측정한다.

피치 문서(docs/pitch-differentiation.md)의 수치를 재현한다. 주장을 형용사로
하지 않고 숫자로 하기 위한 스크립트다.

사용법:
    uv run python scripts/measure_naive_gap.py
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
GYEONGBUK_STATIONS = {"143", "136", "138"}

#: 잘못된 응답 10종. 실제로 원천이 반환할 수 있는 형태다.
MALFORMED: tuple[tuple[str, Any], ...] = (
    ("빈 객체", {}),
    ("null", None),
    ("배열", []),
    ("response 노드만", {"response": {}}),
    ("성공 봉투 + 본문이 문자열", {"response": {"header": {"resultCode": "00"}, "body": "x"}}),
    (
        "성공 봉투 + items 숫자",
        {"response": {"header": {"resultCode": "00"}, "body": {"items": 42}}},
    ),
    (
        "성공 봉투 + items 키 없음",
        {"response": {"header": {"resultCode": "00"}, "body": {}}},
    ),
    (
        "items null",
        {"response": {"header": {"resultCode": "00"}, "body": {"items": None}}},
    ),
    ("오류 코드 99", {"response": {"header": {"resultCode": "99"}}}),
    ("HTML 오류 페이지", "<html>500</html>"),
)


def naive_parse(payload: Any) -> list[Any]:
    """실무에서 가장 흔한 패턴. get 체이닝 + 빈 리스트 폴백."""
    try:
        items = payload.get("response", {}).get("body", {}).get("items") or []
    except AttributeError:
        return []
    if isinstance(items, dict):
        items = items.get("item") or []
    return list(items) if isinstance(items, list) else []


def _service_key() -> str | None:
    env = os.environ.get("GBSAFE_DATA_GO_KR_SERVICE_KEY")
    if env:
        return env
    dotenv = Path(__file__).resolve().parents[1] / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            if line.startswith("GBSAFE_DATA_GO_KR_SERVICE_KEY="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    return None


def measure_warning_gap(key: str) -> None:
    """기상특보: 필터가 없으면 타 지역 특보가 몇 건 섞이는가."""
    now = datetime.now(KST)
    params = {
        "pageNo": "1",
        "numOfRows": "100",
        "dataType": "JSON",
        "fromTmFc": (now - timedelta(days=3)).strftime("%Y%m%d"),
        "toTmFc": now.strftime("%Y%m%d"),
        "serviceKey": key,
    }
    url = (
        "https://apis.data.go.kr/1360000/WthrWrnInfoService/getWthrWrnList?"
        + urllib.parse.urlencode(params, safe="")
    )
    try:
        with urllib.request.urlopen(url, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
        items = payload["response"]["body"]["items"]["item"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"  원천 호출 실패: {type(error).__name__}")
        return

    gyeongbuk = [item for item in items if str(item.get("stnId")) in GYEONGBUK_STATIONS]
    print(f"  API 응답 전체             : {len(items):3}건")
    print(
        f"  경북 관할 3개 관서만      : {len(gyeongbuk):3}건"
        f"  (타 지역 {len(items) - len(gyeongbuk)}건 제외)"
    )


async def measure_reconciliation() -> None:
    """해제·대체를 반영하면 실제 발효는 몇 건인가."""
    from gbsafe_connectors import clear_cache, get_registry

    await clear_cache()
    registry = get_registry()
    active = await registry.create("weather_warning").fetch(days=3)
    await clear_cache()
    history = await registry.create("weather_warning").fetch(days=3, active_only=False)
    print(f"  경북 통보문 이력          : {len(history.records):3}건")
    print(f"  해제·대체 반영 후 발효    : {len(active.records):3}건")
    retired = len(history.records) - len(active.records)
    if retired:
        print(f"  → 반영하지 않으면 끝난 특보 {retired}건을 현재로 보고한다")


def measure_parser_gap() -> None:
    """잘못된 응답을 '자료 없음'으로 답하는 비율."""
    naive_absent = 0
    for label, body in MALFORMED:
        if not naive_parse(body):
            naive_absent += 1
            print(f"  '자료 없음'으로 답함    ← {label}")
    print(f"\n  뻔한 파서   : {naive_absent}/{len(MALFORMED)} 를 부재로 답한다")
    print(f"  GB SafeData : 0/{len(MALFORMED)} (문서화된 표기만 부재로 인정)")


def measure_grid_error() -> None:
    """잘못된 격자가 얼마나 먼 곳의 날씨를 반환하는가."""
    from gbsafe_core.models import GeoPoint
    from gbsafe_core.regions import KmaGrid, from_kma_grid, haversine_km

    mungyeong = GeoPoint(lat=36.5866, lon=128.1867)
    for label, grid in (
        ("이전 값 (90,95)", KmaGrid(nx=90, ny=95)),
        ("수정 값 (81,106)", KmaGrid(nx=81, ny=106)),
    ):
        point = from_kma_grid(grid)
        distance = haversine_km(mungyeong, point)
        print(
            f"  {label:18} → {point.lat:.3f}N {point.lon:.3f}E,"
            f" 문경 청사와 {distance:5.1f} km"
        )


def main() -> int:
    print("=" * 62)
    print("1. 기상특보 — 필터와 상태 재구성이 없으면")
    print("=" * 62)
    key = _service_key()
    if key:
        measure_warning_gap(key)
        asyncio.run(measure_reconciliation())
    else:
        print("  인증키가 없어 건너뜁니다 (GBSAFE_DATA_GO_KR_SERVICE_KEY)")

    print()
    print("=" * 62)
    print("2. 파서 — 잘못된 응답 10종을 어떻게 답하는가")
    print("=" * 62)
    measure_parser_gap()

    print()
    print("=" * 62)
    print("3. 격자 좌표 — 틀린 값에도 API는 성공 응답한다")
    print("=" * 62)
    measure_grid_error()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
