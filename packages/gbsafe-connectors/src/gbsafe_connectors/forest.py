"""산림청 커넥터 — 산불위험예보, 산사태 예측·취약정보.

산사태 3종은 **개발단계가 심의승인**이어서 신청이 승인되기 전까지 호출이
막힌다(../jxkr2026-datasets/docs/api-operations.md). 이것은 오류가 아니라
정상적인 운영 상태이므로, 커넥터는 예외를 던지지 않고 `NOT_AUTHORIZED`
degradation을 돌려준다. 그러면 API·MCP 응답에 "왜 산사태 정보가 없는지"가
사유와 함께 남는다.

베이스 URL이 API마다 다르다. 산불위험예보만 `/1400377/`이고 나머지는
`/1400000/`이다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from gbsafe_core.domain import (
    AlertAction,
    HazardAlert,
    RiskZone,
    Severity,
    parse_severity,
)
from gbsafe_core.models import GeoPoint, QualityFlag
from gbsafe_core.regions import SIDO_CODE, HazardDomain, find_sigungu

from .base import KST, Connector, FetchOutcome, RawResponse, confirmed_empty

#: 산불위험 등급 개수 필드 → 등급명.
FIRE_GRADE_FIELDS: dict[str, str] = {
    "d1": "낮음",
    "d2": "다소높음",
    "d3": "높음",
    "d4": "매우높음",
}


def _is_success_envelope(payload: Any) -> bool:
    """응답 봉투가 정상인지. 산림청은 resultCode를 주지 않는 경우가 있어
    구조 자체(response/body/items 또는 result)를 확인한다."""
    if not isinstance(payload, dict):
        return False
    header = payload.get("response", {}).get("header", {})
    if isinstance(header, dict) and "resultCode" in header:
        return str(header["resultCode"]) in ("00", "0")
    node: Any = payload
    for key in ("response", "body"):
        if not isinstance(node, dict):
            return False
        node = node.get(key, {})
    if isinstance(node, dict) and node:
        return True
    # `result`만 있는 응답은 목록이 실제로 들어 있을 때만 정상으로 본다
    result = payload.get("result")
    return isinstance(result, dict) or (isinstance(result, list) and bool(result))


def _rows(payload: Any) -> list[dict[str, Any]]:
    """산림청 응답에서 행 목록을 꺼낸다. 응답 구조가 API마다 다르다.

    알려진 경로 중 어느 것도 없으면 **예외**다. 빈 목록으로 돌려주면 구조를
    알아보지 못한 응답이 '자료 없음'으로 보고된다.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"응답이 객체가 아닙니다 ({type(payload).__name__})")

    paths = (
        ("response", "body", "items", "item"),
        ("response", "body", "items"),
        ("body", "items", "item"),
        ("items", "item"),
        ("result",),
    )
    for path in paths:
        node: Any = payload
        found = True
        for key in path:
            if not isinstance(node, dict) or key not in node:
                found = False
                break
            node = node[key]
        if not found:
            continue
        if isinstance(node, dict):
            return [node]
        if isinstance(node, list):
            return [row for row in node if isinstance(row, dict)]
        # 키는 있지만 값이 목록·객체가 아니다 — 빈 문자열만 자료 없음으로 본다.
        # null은 원천 오류와 구별할 수 없으므로 실패로 다룬다.
        if node == "":
            return []
        if node is None:
            raise ValueError(f"{'.'.join(path)}가 null입니다 — 원천 오류일 수 있습니다")
        raise ValueError(f"{'.'.join(path)}가 객체·배열이 아닙니다 ({type(node).__name__})")
    raise ValueError("응답에서 알려진 데이터 경로를 찾을 수 없습니다")


def _parse_date(raw: str | None) -> datetime | None:
    """YYYYMMDD 또는 YYYY-MM-DD를 KST datetime으로."""
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    for width, pattern in ((14, "%Y%m%d%H%M%S"), (12, "%Y%m%d%H%M"), (8, "%Y%m%d")):
        if len(digits) >= width:
            try:
                return datetime.strptime(digits[:width], pattern).replace(tzinfo=KST)
            except ValueError:
                continue
    return None


def _coord(row: dict[str, Any], *names: str) -> float | None:
    for name in names:
        raw = row.get(name)
        if raw in (None, "", "-"):
            continue
        try:
            return float(str(raw).strip())
        except ValueError:
            continue
    return None


def _point(row: dict[str, Any]) -> GeoPoint | None:
    """행에서 좌표를 뽑는다. 범위를 벗어나면 None (다른 좌표계일 수 있다)."""
    lat = _coord(row, "lat", "latitude", "yCoord", "ycrd", "y")
    lon = _coord(row, "lon", "lng", "longitude", "xCoord", "xcrd", "x")
    if lat is None or lon is None:
        return None
    try:
        return GeoPoint(lat=lat, lon=lon)
    except ValueError:
        return None


class WildfireRiskConnector(Connector[HazardAlert]):
    """산불위험예보 — 시도·시군구별 위험지수와 등급 분포.

    개발계정 한도가 일 1,000건으로 낮으므로 캐시가 특히 중요하다.
    """

    dataset_id: ClassVar[str] = "15084817"
    service_key_param: ClassVar[str] = "serviceKey"
    update_cycle_seconds: ClassVar[int] = 86400

    def base_url(self) -> str:
        return (
            "https://apis.data.go.kr/1400377/forestPointV2/"
            "forestPointListSidoSearchV2"
        )

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        return {
            "pageNo": "1",
            "numOfRows": "30",
            "_type": "json",
            "localAreas": str(kwargs.get("sido_code", SIDO_CODE)),
            "excludeForecast": "0",
        }

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[HazardAlert]:
        payload = response.json()
        if not _is_success_envelope(payload):
            raise ValueError("응답이 정상 봉투가 아닙니다")
        rows = _rows(payload)
        if not rows:
            return confirmed_empty("산불위험예보 자료가 조회되지 않았습니다")

        records = []
        for row in rows:
            analyzed = _parse_date(row.get("analdate"))
            mean = _coord(row, "meanavg")
            peak = _coord(row, "maxi")
            grades = {
                label: row.get(field)
                for field, label in FIRE_GRADE_FIELDS.items()
                if row.get(field) not in (None, "", "0")
            }
            severity = _fire_severity(peak if peak is not None else mean)
            area = str(row.get("sido") or row.get("sigun") or "경상북도").strip()

            records.append(
                self.record(
                    HazardAlert(
                        hazard=HazardDomain.WILDFIRE,
                        severity=severity,
                        headline=f"산불위험지수 평균 {mean if mean is not None else '미확인'}",
                        area_name=area,
                        action=AlertAction.CURRENT_STATE,
                        issued_at=analyzed,
                        raw_level=str(peak) if peak is not None else None,
                    ),
                    response,
                    observed_at=analyzed,
                    notes=(
                        tuple(f"{label} {count}개 지역" for label, count in grades.items())
                        if grades
                        else ()
                    ),
                )
            )
        return FetchOutcome(
            records=tuple(records),
            caveats=("시도·시군 단위 지수입니다 — 특정 마을의 발생 확률이 아닙니다",),
            confirmed_absence=not records,
        )


def _fire_severity(index: float | None) -> Severity:
    """산불위험지수를 정규화 단계로. 산림청 등급 구간을 따른다."""
    if index is None:
        return Severity.UNKNOWN
    if index >= 86:
        return Severity.EMERGENCY
    if index >= 66:
        return Severity.WARNING
    if index >= 51:
        return Severity.ADVISORY
    return Severity.INFO


class LandslidePredictionConnector(Connector[HazardAlert]):
    """산사태 예측정보 — 시군구별 예보단계.

    개발단계 심의승인 대기 중이면 `NOT_AUTHORIZED`가 나온다. 그것이 정상이며
    응답에 사유가 남는다.
    """

    dataset_id: ClassVar[str] = "15074800"
    service_key_param: ClassVar[str] = "serviceKey"
    region_param: ClassVar[str] = "sigungu"
    update_cycle_seconds: ClassVar[int] = 3600

    def base_url(self) -> str:
        return (
            "https://apis.data.go.kr/1400000/predictionInfoService/predictionInfoList"
        )

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        params = {"pageNo": "1", "numOfRows": "50", "_type": "json"}
        if sgg := kwargs.get("sigungu"):
            params["sgg"] = str(sgg)
        if level := kwargs.get("level"):
            params["lndslFrcstNm"] = str(level)
        return params

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[HazardAlert]:
        payload = response.json()
        if not _is_success_envelope(payload):
            raise ValueError("응답이 정상 봉투가 아닙니다")
        rows = _rows(payload)
        if not rows:
            return confirmed_empty("현재 발효된 산사태 예보단계가 없습니다 (조회는 정상)")

        records = []
        for row in rows:
            level = str(row.get("lndslFrcstNm") or row.get("frcstNm") or "").strip()
            area = str(row.get("sgg") or row.get("sggNm") or "미확인").strip()
            analyzed = _parse_date(row.get("anlsDt") or row.get("frcstDt"))
            records.append(
                self.record(
                    HazardAlert(
                        hazard=HazardDomain.LANDSLIDE,
                        severity=parse_severity(level),
                        headline=f"산사태 {level or '예보'}",
                        area_name=area,
                        # 이 API는 통보문이 아니라 현재 예보단계를 준다.
                        # 발표·해제 개념이 없어 UNKNOWN으로 두면 경보가 묻힌다.
                        action=AlertAction.CURRENT_STATE,
                        issued_at=analyzed,
                        raw_level=level or None,
                    ),
                    response,
                    observed_at=analyzed,
                )
            )
        return FetchOutcome(
            records=tuple(records),
            caveats=(
                "시군구 단위 예보입니다 — 특정 가구의 산사태 발생 확률로 표현하면 안 됩니다",
            ),
            confirmed_absence=not records,
        )


class RoadsideLandslideConnector(Connector[RiskZone]):
    """도로변 산사태 정보 — 대피경로가 끊길 수 있는 구간.

    호우 시 어느 도로가 단절될 수 있는지 사전에 파악하는 데 쓴다.
    """

    dataset_id: ClassVar[str] = "15074812"
    service_key_param: ClassVar[str] = "serviceKey"
    region_param: ClassVar[str] = "address"

    def base_url(self) -> str:
        return (
            "https://apis.data.go.kr/1400000/roadsideLndslInfoService/"
            "roadsideLndslInfoList"
        )

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        params = {"pageNo": "1", "numOfRows": str(kwargs.get("rows", 100)), "_type": "json"}
        if address := kwargs.get("address"):
            params["poflcAddr"] = str(address)
        return params

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[RiskZone]:
        payload = response.json()
        if not _is_success_envelope(payload):
            raise ValueError("응답이 정상 봉투가 아닙니다")
        rows = _rows(payload)
        if not rows:
            return confirmed_empty("도로변 산사태 취약구간 자료가 없습니다")

        area = kwargs.get("address")
        records = []
        for index, row in enumerate(rows):
            address = str(row.get("poflcAddr") or row.get("addr") or "").strip()
            if area and area not in address:
                continue
            point = _point(row)
            flags: tuple[QualityFlag, ...] = (
                () if point else (QualityFlag.MISSING_COORDINATES,)
            )
            records.append(
                self.record(
                    RiskZone(
                        zone_id=str(row.get("id") or row.get("sn") or f"roadside-{index}"),
                        hazard=HazardDomain.LANDSLIDE,
                        name=str(row.get("ecninPssssSctinNm") or "도로변 취약구간").strip(),
                        address=address or None,
                        location=point,
                        grade=str(row.get("vnaraTpeNm") or "").strip() or None,
                        managing_agency=str(row.get("lndslMnagnNm") or "").strip() or None,
                    ),
                    response,
                    quality_flags=flags,
                )
            )
        return FetchOutcome(records=tuple(records), confirmed_absence=not records)


class PastLandslideConnector(Connector[RiskZone]):
    """과거 산사태 발생 이력 — 반복 발생지 분석과 훈련 시나리오 작성용."""

    dataset_id: ClassVar[str] = "15074816"
    service_key_param: ClassVar[str] = "serviceKey"
    region_param: ClassVar[str] = "sigungu"

    def base_url(self) -> str:
        return (
            "https://apis.data.go.kr/1400000/pastLndslInfoService/pastLndslInfoList"
        )

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        params = {"pageNo": "1", "numOfRows": str(kwargs.get("rows", 100)), "_type": "json"}
        params["ctprvNm"] = str(kwargs.get("sido", "경상북도"))
        if year := kwargs.get("year"):
            params["lndslDsstrYr"] = str(year)
        if sigungu := kwargs.get("sigungu"):
            resolved = find_sigungu(str(sigungu))
            params["sgngNm"] = resolved.name if resolved else str(sigungu)
        return params

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[RiskZone]:
        payload = response.json()
        if not _is_success_envelope(payload):
            raise ValueError("응답이 정상 봉투가 아닙니다")
        rows = _rows(payload)
        if not rows:
            return confirmed_empty("조건에 해당하는 과거 산사태 기록이 없습니다")

        records = []
        for index, row in enumerate(rows):
            year = str(row.get("lndslDsstrYr") or "").strip()
            point = _point(row)
            records.append(
                self.record(
                    RiskZone(
                        zone_id=str(row.get("sn") or f"past-{index}"),
                        hazard=HazardDomain.LANDSLIDE,
                        name=f"{year}년 산사태 발생지" if year else "산사태 발생지",
                        address=" ".join(
                            part
                            for part in (
                                str(row.get("ctprvNm") or "").strip(),
                                str(row.get("sgngNm") or "").strip(),
                                str(row.get("epmnNm") or "").strip(),
                            )
                            if part
                        )
                        or None,
                        location=point,
                        designated_on=year or None,
                    ),
                    response,
                    quality_flags=() if point else (QualityFlag.MISSING_COORDINATES,),
                )
            )
        return FetchOutcome(
            records=tuple(records),
            caveats=("과거 이력입니다 — 현재 위험 상태를 나타내지 않습니다",),
            confirmed_absence=not records,
        )


__all__ = [
    "FIRE_GRADE_FIELDS",
    "LandslidePredictionConnector",
    "PastLandslideConnector",
    "RoadsideLandslideConnector",
    "WildfireRiskConnector",
]
