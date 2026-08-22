"""기상청 태풍 중심위치와 비대칭 바람반경 커넥터."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from gbsafe_core.models import Frozen, QualityFlag
from pydantic import Field

from .base import FetchOutcome, RawResponse, confirmed_empty
from .seismic import _DatedKmaConnector, _number, _response_items, _stamp


class TyphoonObservation(Frozen):
    """태풍 중심과 방향별 바람반경.

    공통 `Observation.location`의 `GeoPoint`는 한반도 범위만 허용해 저위도에서
    접근 중인 태풍 중심을 담을 수 없고, 단일 `value`는 방향별 반경을 보존하지
    못한다. 그래서 이 원천의 실제 비대칭 필드를 그대로 갖는 경계 모델을 쓴다.
    """

    sequence: str | None = None
    name: str | None = None
    target_time: datetime

    #: 관측시각이 원본에 없거나 깨져서 수집시각으로 대체했는지.
    #:
    #: 조용히 대체하면 몇 시간 전 태풍 위치가 방금 관측한 것처럼 보인다.
    time_is_estimated: bool = False
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    strong_wind_radius_km: float | None = Field(default=None, ge=0)
    strong_wind_extended_direction: str | None = None
    strong_wind_extended_radius_km: float | None = Field(default=None, ge=0)
    storm_radius_km: float | None = Field(default=None, ge=0)
    storm_extended_direction: str | None = None
    storm_extended_radius_km: float | None = Field(default=None, ge=0)

    @property
    def has_position(self) -> bool:
        """위도·경도가 모두 확인된 경우에만 지도 위치로 사용할 수 있다."""
        return self.latitude is not None and self.longitude is not None


def _latitude(raw: Any) -> float | None:
    """깨진 위도를 0도로 대체해 태풍을 적도에 표시하는 오류를 막는다."""
    value = _number(raw)
    return value if value is not None and -90 <= value <= 90 else None


def _longitude(raw: Any) -> float | None:
    """깨진 경도를 0도로 대체해 태풍을 본초자오선에 표시하는 오류를 막는다."""
    value = _number(raw)
    return value if value is not None and -180 <= value <= 180 else None


def _direction(raw: Any) -> str | None:
    text = str(raw if raw is not None else "").strip()
    return text or None


class TyphoonConnector(_DatedKmaConnector[TyphoonObservation]):
    """현재 태풍 정보. 중심점과 방향별 강풍·폭풍반경을 원형으로 합치지 않는다."""

    dataset_id: ClassVar[str] = "15000174"
    num_rows: ClassVar[int] = 30

    @property
    def dataset_name(self) -> str:
        return "기상청 태풍정보 조회서비스"

    def base_url(self) -> str:
        return "https://apis.data.go.kr/1360000/TyphoonInfoService/getTyphoonInfo"

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[TyphoonObservation]:
        payload = response.json()
        items = _response_items(payload)
        if items is None:
            return confirmed_empty("기상청이 조회 기간에 태풍 정보가 없다고 응답했습니다 (코드 03)")
        if not items:
            return confirmed_empty("정상 성공 봉투의 태풍 정보 목록이 비어 있습니다")

        records = []
        skipped = 0
        for item in items:
            # 태풍 번호도 이름도 시각도 위치도 없으면 태풍이 아니다.
            if not isinstance(item, dict) or not any(
                str(item.get(field) or "").strip()
                for field in ("typSeq", "typEn", "typName", "typTm", "tmFc", "typLat", "typLon")
            ):
                skipped += 1
                continue
            target_time = _stamp(item.get("typTm") or item.get("tmFc"))
            latitude = _latitude(item.get("typLat"))
            longitude = _longitude(item.get("typLon"))
            flags: list[QualityFlag] = []
            notes: list[str] = []
            if latitude is None or longitude is None:
                flags.extend(
                    (QualityFlag.MISSING_COORDINATES, QualityFlag.PARTIAL_RESPONSE)
                )
                notes.append("태풍 중심 위치가 결측입니다 — (0, 0) 좌표로 대체하지 않았습니다")

            strong_direction = _direction(item.get("typ15ed"))
            strong_extended = _number(item.get("typ15er"))
            storm_direction = _direction(item.get("typ25ed"))
            storm_extended = _number(item.get("typ25er"))
            if strong_direction or strong_extended is not None:
                notes.append(
                    f"비대칭 강풍반경: typ15ed={strong_direction or '미확인'}, "
                    f"typ15er={strong_extended if strong_extended is not None else '미확인'}km"
                )
            if storm_direction or storm_extended is not None:
                notes.append(
                    f"비대칭 폭풍반경: typ25ed={storm_direction or '미확인'}, "
                    f"typ25er={storm_extended if storm_extended is not None else '미확인'}km"
                )

            records.append(
                self.record(
                    TyphoonObservation(
                        sequence=_direction(item.get("typSeq")),
                        name=_direction(item.get("typName") or item.get("typEn")),
                        target_time=target_time or response.retrieved_at,
                        time_is_estimated=target_time is None,
                        latitude=latitude,
                        longitude=longitude,
                        strong_wind_radius_km=_number(item.get("typ15")),
                        strong_wind_extended_direction=strong_direction,
                        strong_wind_extended_radius_km=strong_extended,
                        storm_radius_km=_number(item.get("typ25")),
                        storm_extended_direction=storm_direction,
                        storm_extended_radius_km=storm_extended,
                    ),
                    response,
                    observed_at=target_time,
                    quality_flags=tuple(dict.fromkeys(flags)),
                    notes=tuple(notes),
                )
            )

        return FetchOutcome(
            records=tuple(records),
            caveats=(
                "typ15·typ25는 비대칭 반경이며 대칭 원 반경이 아닙니다. typ15er·typ25er는 각각 "
                "typ15ed·typ25ed 방향으로 더 긴 반경이므로 원형 버퍼는 실제 바람장을 "
                "왜곡하며, 경북 동해안 상륙 때 오차가 특히 커집니다",
                "태풍 탐지·추적 자료이며 대피장소·대피경로 자료를 포함하지 않습니다",
            ),
        )


__all__ = ["TyphoonConnector", "TyphoonObservation"]
