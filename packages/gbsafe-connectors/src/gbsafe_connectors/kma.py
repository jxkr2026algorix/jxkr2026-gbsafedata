"""기상청 커넥터 — 강우·기온·풍속과 기상특보.

메인 시나리오(극한호우 + 산사태)의 1차 입력이다. 검증된 개발계정 키 하나로
동작한다.

두 가지를 정확히 다룬다.

1. **격자 좌표.** API가 위경도를 받지 않으므로 `gbsafe_core.regions`의 LCC
   변환을 쓴다.
2. **관측과 예보의 구별.** 초단기실황은 `obsrValue`, 예보 계열은 `fcstValue`와
   대상시각을 준다. 예보를 현재 상황으로 표시하면 판단을 왜곡하므로
   `Observation.is_forecast`로 분리한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from gbsafe_core.domain import (
    AlertAction,
    HazardAlert,
    Observation,
    parse_alert_action,
    parse_severity,
)
from gbsafe_core.models import GeoPoint, QualityFlag
from gbsafe_core.regions import HazardDomain, KmaGrid, find_sigungu, to_kma_grid

from .base import KST, Connector, FetchOutcome, RawResponse, confirmed_empty
from .stations import GYEONGBUK_STATIONS, describe_station, serves_gyeongbuk

#: 기상청 카테고리 코드 → (정규화 이름, 단위).
#: 공개 페이지에 코드표가 인쇄되지 않아 활용가이드 기준으로 정리했다.
CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "T1H": ("temperature", "℃"),
    "TMP": ("temperature", "℃"),
    "RN1": ("rainfall_1h", "mm"),
    "PCP": ("rainfall_1h", "mm"),
    "REH": ("humidity", "%"),
    "WSD": ("wind_speed", "m/s"),
    "VEC": ("wind_direction", "deg"),
    "UUU": ("wind_u", "m/s"),
    "VVV": ("wind_v", "m/s"),
    "PTY": ("precipitation_type", "code"),
    "SKY": ("sky_condition", "code"),
    "POP": ("precipitation_probability", "%"),
    "SNO": ("snowfall", "cm"),
    "TMN": ("temperature_min", "℃"),
    "TMX": ("temperature_max", "℃"),
    "WAV": ("wave_height", "m"),
}

#: 강수형태(PTY) 코드. 값이 0이면 강수 없음이다.
PTY_CODES: dict[str, str] = {
    "0": "없음",
    "1": "비",
    "2": "비/눈",
    "3": "눈",
    "4": "소나기",
    "5": "빗방울",
    "6": "빗방울눈날림",
    "7": "눈날림",
}

#: 강수·적설에서만 0을 의미하는 표기. 다른 카테고리에는 적용하지 않는다.
_ZERO_VALUES = frozenset({"강수없음", "적설없음"})

#: 결측 표기. **0이 아니라 None이다.**
_MISSING_VALUES = frozenset({"-", "", "null", "none", "nan"})

#: 값이 0을 의미할 수 있는 카테고리. 강수·적설만 해당한다.
#: 기온·습도·풍속에서 결측을 0으로 바꾸면 0℃·습도 0%가 실측처럼 보인다.
_ZEROABLE_CATEGORIES = frozenset({"RN1", "PCP", "SNO", "POP", "PTY"})


def _parse_measure(raw: str, category: str = "") -> float | None:
    """기상청 값 문자열을 숫자로.

    **결측은 None이다.** 이전에는 빈 문자열·`-`·`null`을 모두 0으로 바꿨는데,
    강수량에서는 맞지만 기온에서는 결측이 0℃로 보이는 문제가 있었다.
    '강수없음'류 표기는 강수·적설 카테고리에서만 0으로 해석한다.
    """
    text = raw.strip()
    lowered = text.casefold()
    if lowered in _MISSING_VALUES:
        return None
    if text in _ZERO_VALUES:
        return 0.0 if not category or category in _ZEROABLE_CATEGORIES else None
    if "미만" in text:
        return 0.5
    cleaned = text.replace("mm", "").replace("cm", "").replace("이상", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_stamp(date_text: str, time_text: str) -> datetime | None:
    """YYYYMMDD + HHMM을 KST datetime으로."""
    try:
        stamp = datetime.strptime(f"{date_text}{time_text.zfill(4)}", "%Y%m%d%H%M")
    except ValueError:
        return None
    return stamp.replace(tzinfo=KST)


def _resolve_grid(location: str | GeoPoint | KmaGrid) -> KmaGrid:
    if isinstance(location, KmaGrid):
        return location
    if isinstance(location, GeoPoint):
        return to_kma_grid(location)
    sigungu = find_sigungu(location)
    if sigungu is None:
        raise ValueError(
            f"'{location}'을 경북 시군으로 해석할 수 없습니다. "
            "시군명(예: 문경시), 시군구 코드(예: 47280), 또는 좌표를 주세요."
        )
    return to_kma_grid(sigungu.center)


def _is_success_envelope(payload: Any) -> bool:
    """응답이 정상 봉투인지 확인한다.

    이것을 확인하지 않고 빈 목록을 '해당 없음'으로 보고하면, 구조를 알아보지
    못한 응답이 '위험 없음'이 된다.
    """
    if not isinstance(payload, dict):
        return False
    header = payload.get("response", {}).get("header", {})
    return isinstance(header, dict) and str(header.get("resultCode", "")) in ("00", "0")


def _items(payload: Any) -> list[dict[str, Any]]:
    """기상청 응답에서 item 목록을 꺼낸다. 단일 항목이 dict로 오는 경우도 있다."""
    body = payload.get("response", {}).get("body", {})
    items = body.get("items")
    if items in (None, "", []):
        return []
    if isinstance(items, dict):
        item = items.get("item")
        if isinstance(item, dict):
            return [item]
        if isinstance(item, list):
            return [entry for entry in item if isinstance(entry, dict)]
    if isinstance(items, list):
        return [entry for entry in items if isinstance(entry, dict)]
    return []


class UltraShortNowcastConnector(Connector[Observation]):
    """초단기실황 — 현재 강우·기온·풍속. 정시 발표."""

    dataset_id: ClassVar[str] = "15084084"
    service_key_param: ClassVar[str] = "serviceKey"
    region_param: ClassVar[str] = "location"
    update_cycle_seconds: ClassVar[int] = 3600
    max_decision_age_seconds: ClassVar[int] = 5400

    def base_url(self) -> str:
        return (
            "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
        )

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        grid = _resolve_grid(kwargs["location"])
        base = kwargs.get("base_time") or _latest_nowcast_base()
        return {
            "pageNo": "1",
            "numOfRows": "60",
            "dataType": "JSON",
            "base_date": base.strftime("%Y%m%d"),
            "base_time": base.strftime("%H00"),
            "nx": str(grid.nx),
            "ny": str(grid.ny),
        }

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[Observation]:
        payload = response.json()
        if not _is_success_envelope(payload):
            raise ValueError("응답이 정상 봉투가 아닙니다 (resultCode 확인 실패)")
        items = _items(payload)
        if not items:
            return confirmed_empty("해당 시각·격자에 실황 자료가 없습니다 (발표 지연 가능)")

        grid = _resolve_grid(kwargs["location"])
        point = None
        if isinstance(kwargs["location"], GeoPoint):
            point = kwargs["location"]
        elif (sigungu := find_sigungu(str(kwargs["location"]))) is not None:
            point = sigungu.center

        records = []
        for item in items:
            category = str(item.get("category", ""))
            mapped = CATEGORY_MAP.get(category)
            if mapped is None:
                continue
            kind, unit = mapped
            raw_value = str(item.get("obsrValue", ""))
            observed_at = _parse_stamp(
                str(item.get("baseDate", "")), str(item.get("baseTime", ""))
            )
            flags: tuple[QualityFlag, ...] = ()
            value = _parse_measure(raw_value, category)
            if value is None:
                flags = (QualityFlag.PARTIAL_RESPONSE,)

            records.append(
                self.record(
                    Observation(
                        kind=kind,
                        value=value,
                        unit=unit,
                        station=f"격자 {grid.nx},{grid.ny}",
                        location=point,
                        target_time=observed_at or response.retrieved_at,
                        is_forecast=False,
                        raw_code=category,
                    ),
                    response,
                    observed_at=observed_at,
                    quality_flags=flags,
                    notes=(
                        (f"강수형태: {PTY_CODES.get(raw_value, raw_value)}",)
                        if category == "PTY"
                        else ()
                    ),
                )
            )
        return FetchOutcome(records=tuple(records), confirmed_absence=not records)


class ShortTermForecastConnector(Connector[Observation]):
    """단기예보 — 향후 강우·기온 추이. 예보값임을 명시한다."""

    dataset_id: ClassVar[str] = "15084084"
    service_key_param: ClassVar[str] = "serviceKey"
    region_param: ClassVar[str] = "location"
    update_cycle_seconds: ClassVar[int] = 10800

    def base_url(self) -> str:
        return (
            "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        )

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        grid = _resolve_grid(kwargs["location"])
        base = kwargs.get("base_time") or _latest_forecast_base()
        return {
            "pageNo": "1",
            "numOfRows": str(kwargs.get("rows", 300)),
            "dataType": "JSON",
            "base_date": base.strftime("%Y%m%d"),
            "base_time": base.strftime("%H%M"),
            "nx": str(grid.nx),
            "ny": str(grid.ny),
        }

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[Observation]:
        payload = response.json()
        if not _is_success_envelope(payload):
            raise ValueError("응답이 정상 봉투가 아닙니다 (resultCode 확인 실패)")
        items = _items(payload)
        if not items:
            return confirmed_empty("해당 발표시각의 예보 자료가 없습니다")

        grid = _resolve_grid(kwargs["location"])
        wanted = kwargs.get("categories") or {"TMP", "PCP", "POP", "REH", "WSD", "SKY"}
        records = []
        for item in items:
            category = str(item.get("category", ""))
            if category not in wanted:
                continue
            mapped = CATEGORY_MAP.get(category)
            if mapped is None:
                continue
            kind, unit = mapped
            target = _parse_stamp(str(item.get("fcstDate", "")), str(item.get("fcstTime", "")))
            published = _parse_stamp(
                str(item.get("baseDate", "")), str(item.get("baseTime", ""))
            )
            if target is None:
                continue
            records.append(
                self.record(
                    Observation(
                        kind=kind,
                        value=_parse_measure(str(item.get("fcstValue", "")), category),
                        unit=unit,
                        station=f"격자 {grid.nx},{grid.ny}",
                        target_time=target,
                        is_forecast=True,
                        raw_code=category,
                    ),
                    response,
                    published_at=published,
                )
            )
        return FetchOutcome(
            records=tuple(records),
            caveats=("예보값입니다 — 현재 관측 상황과 구별해야 합니다",),
            confirmed_absence=not records,
        )


class WeatherWarningConnector(Connector[HazardAlert]):
    """기상특보 — 호우·대설·강풍 주의보·경보.

    특보 발효 여부는 대피 검토 착수의 1차 신호다. 다만 특보 자체가 대피명령은
    아니며 `HazardAlert.is_actionable`은 검토 우선순위 힌트일 뿐이다.
    """

    dataset_id: ClassVar[str] = "15000415"
    service_key_param: ClassVar[str] = "serviceKey"
    update_cycle_seconds: ClassVar[int] = 3600
    max_decision_age_seconds: ClassVar[int] = 10800

    def base_url(self) -> str:
        return "https://apis.data.go.kr/1360000/WthrWrnInfoService/getWthrWrnList"

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        now = datetime.now(KST)
        days = int(kwargs.get("days", 2))
        params = {
            "pageNo": "1",
            "numOfRows": str(kwargs.get("rows", 100)),
            "dataType": "JSON",
            "fromTmFc": (now - timedelta(days=days)).strftime("%Y%m%d"),
            "toTmFc": now.strftime("%Y%m%d"),
        }
        if station := kwargs.get("station_id"):
            params["stnId"] = str(station)
        return params

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[HazardAlert]:
        payload = response.json()
        if not _is_success_envelope(payload):
            raise ValueError("응답이 정상 봉투가 아닙니다 (resultCode 확인 실패)")
        items = _items(payload)
        if not items:
            return confirmed_empty("조회 기간에 발효된 기상특보가 없습니다 (조회는 정상)")

        # 이 API는 지역명이 아니라 발표관서 번호를 준다. 필터하지 않으면
        # 전국 특보가 경북 특보처럼 섞인다.
        gyeongbuk_only = bool(kwargs.get("gyeongbuk_only", True))
        active_only = bool(kwargs.get("active_only", True))
        wanted = kwargs.get("station_id")
        records = []
        skipped_region = 0
        skipped_cancelled = 0

        for item in items:
            title = str(item.get("title", "")).strip()
            station_id = str(item.get("stnId", "")).strip() or None
            if wanted and station_id != str(wanted):
                skipped_region += 1
                continue
            if gyeongbuk_only and not serves_gyeongbuk(station_id):
                skipped_region += 1
                continue

            action = parse_alert_action(title)
            if active_only and action is AlertAction.CANCELLED:
                skipped_cancelled += 1
                continue

            issued = _parse_stamp(str(item.get("tmFc", ""))[:8], str(item.get("tmFc", ""))[8:12])
            records.append(
                self.record(
                    HazardAlert(
                        hazard=_hazard_from_title(title),
                        severity=parse_severity(title),
                        headline=title or "기상특보",
                        area_name=describe_station(station_id),
                        action=action,
                        area_code=station_id,
                        issued_at=issued,
                        raw_level=title,
                    ),
                    response,
                    published_at=issued,
                )
            )

        caveats = [
            "발표관서 단위 특보입니다 — 관할 구역 전체가 대상이며 특정 마을 상태가 아닙니다",
        ]
        if gyeongbuk_only:
            covered = ", ".join(describe_station(sid) for sid in GYEONGBUK_STATIONS)
            caveats.append(f"경북 관할 관서만 필터했습니다 ({covered})")
            if skipped_region:
                caveats.append(f"타 지역 특보 {skipped_region}건을 제외했습니다")
        if skipped_cancelled:
            caveats.append(f"해제 통보문 {skipped_cancelled}건을 제외했습니다 (이미 종료된 위험)")
        if not records:
            caveats.append("경북 관할 구역에 발효 중인 특보가 없습니다 (조회는 정상)")
        return FetchOutcome(
            records=tuple(records),
            caveats=tuple(caveats),
            confirmed_absence=not records,
        )


def _hazard_from_title(title: str) -> HazardDomain:
    """특보 제목에서 재난 유형을 추정한다."""
    for needle, domain in (
        ("호우", HazardDomain.HEAVY_RAIN),
        ("대설", HazardDomain.HEAVY_RAIN),
        ("태풍", HazardDomain.HEAVY_RAIN),
        ("홍수", HazardDomain.FLOOD),
        ("건조", HazardDomain.WILDFIRE),
        ("폭염", HazardDomain.HEATWAVE),
        ("한파", HazardDomain.HEATWAVE),
        ("지진", HazardDomain.EARTHQUAKE),
    ):
        if needle in title:
            return domain
    return HazardDomain.OTHER


def _latest_nowcast_base() -> datetime:
    """가장 최근 실황 발표 시각. 매시 40분 이후에 당시 정시 자료가 올라온다."""
    now = datetime.now(KST)
    candidate = now.replace(minute=0, second=0, microsecond=0)
    if now.minute < 45:
        candidate -= timedelta(hours=1)
    return candidate


#: 단기예보 발표 시각 (KST). 이 시각 이후에 해당 회차 자료가 제공된다.
_FORECAST_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)


def _latest_forecast_base() -> datetime:
    """가장 최근 단기예보 발표 회차. 발표 후 약 10분 뒤부터 조회된다."""
    now = datetime.now(KST) - timedelta(minutes=15)
    for hour in reversed(_FORECAST_HOURS):
        if now.hour >= hour:
            return now.replace(hour=hour, minute=0, second=0, microsecond=0)
    previous = now - timedelta(days=1)
    return previous.replace(hour=23, minute=0, second=0, microsecond=0)


__all__ = [
    "CATEGORY_MAP",
    "PTY_CODES",
    "ShortTermForecastConnector",
    "UltraShortNowcastConnector",
    "WeatherWarningConnector",
]
