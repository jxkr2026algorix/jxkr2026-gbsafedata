"""정규화된 도메인 엔티티.

각 기관이 서로 다른 필드명·단위·표기로 같은 것을 말하므로, AI와 외부 시스템이
기관별 원본 형식을 몰라도 이해할 수 있는 공통형을 둔다.

**안전 경계가 타입에 반영되어 있다.** 예를 들어 대피소는 `supported_hazards`를
가지며, 지진 대피소를 호우 대피소로 자동 전용하는 것은 `serves()`가 막는다.
경로는 `verified` 플래그가 있고 검증되지 않은 경로는 '공식 안전경로'가 아니라
'후보'로만 표현된다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from .models import Frozen, GeoPoint
from .regions import HazardDomain


class Severity(StrEnum):
    """경보 단계. 기관별 표기를 이 다섯 단계로 정규화한다."""

    INFO = "info"
    ADVISORY = "advisory"
    WARNING = "warning"
    EMERGENCY = "emergency"
    UNKNOWN = "unknown"


#: 기관별 경보 표기 → 정규화 단계.
_SEVERITY_TEXT: tuple[tuple[str, Severity], ...] = (
    ("예비특보", Severity.INFO),
    ("주의보", Severity.ADVISORY),
    ("경보", Severity.WARNING),
    ("특보", Severity.WARNING),
    ("심각", Severity.EMERGENCY),
    ("위험", Severity.EMERGENCY),
    ("매우높음", Severity.EMERGENCY),
    ("높음", Severity.WARNING),
    ("다소높음", Severity.ADVISORY),
    ("낮음", Severity.INFO),
    ("관심", Severity.INFO),
)


def parse_severity(raw: str | None) -> Severity:
    if not raw:
        return Severity.UNKNOWN
    text = raw.strip()
    for needle, level in _SEVERITY_TEXT:
        if needle in text:
            return level
    return Severity.UNKNOWN


class Observation(Frozen):
    """단일 관측·예보 값.

    관측인지 예보인지 구별하지 않으면 미래값을 현재 상황으로 오해하게 되므로
    `is_forecast`와 `target_time`을 분리해 둔다.
    """

    kind: str = Field(description="관측 종류 (rainfall_1h, water_level, wind_speed 등)")
    value: float | None
    unit: str
    station: str | None = None
    location: GeoPoint | None = None
    target_time: datetime = Field(description="이 값이 가리키는 시각")
    is_forecast: bool = False
    raw_code: str | None = Field(default=None, description="원천의 원본 코드 (예: KMA PTY)")

    @model_validator(mode="after")
    def _tz(self) -> Self:
        if self.target_time.tzinfo is None:
            raise ValueError("target_time은 시간대를 포함해야 합니다")
        return self


class AlertAction(StrEnum):
    """특보 통보문의 종류.

    같은 '호우주의보' 문구가 발표와 해제 양쪽에 나타난다. 해제를 발효 중으로
    읽으면 이미 끝난 위험을 현재 위험으로 표시하게 되므로 반드시 구별한다.
    """

    ISSUED = "issued"
    CANCELLED = "cancelled"
    EXTENDED = "extended"
    UNKNOWN = "unknown"


def parse_alert_action(raw: str | None) -> AlertAction:
    if not raw:
        return AlertAction.UNKNOWN
    text = raw.strip()
    if "해제" in text:
        return AlertAction.CANCELLED
    if "연장" in text or "변경" in text:
        return AlertAction.EXTENDED
    if "발표" in text or "발효" in text:
        return AlertAction.ISSUED
    return AlertAction.UNKNOWN


class HazardAlert(Frozen):
    """경보·특보·예보 통보문 하나.

    `action`이 `CANCELLED`이면 위험이 종료됐다는 뜻이다. `is_active`로
    발효 중인 것만 걸러 쓴다.
    """

    hazard: HazardDomain
    severity: Severity
    headline: str
    area_name: str
    action: AlertAction = AlertAction.UNKNOWN
    area_code: str | None = None
    location: GeoPoint | None = None
    issued_at: datetime | None = None
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    raw_level: str | None = Field(default=None, description="원천의 원본 단계 표기")

    @property
    def is_active(self) -> bool:
        """지금 발효 중이라고 **확인된** 경우에만 True.

        해제 통보문과 종류를 판별할 수 없는 통보문은 False다. 판별 실패를
        '발효 중'으로 취급하면 이미 끝난 위험이 현재 위험으로 표시된다.
        """
        return self.action in (AlertAction.ISSUED, AlertAction.EXTENDED)

    @property
    def is_actionable(self) -> bool:
        """검토를 시작할 수준인지. 명령 근거가 아니라 우선순위 힌트다."""
        return self.is_active and self.severity in (Severity.WARNING, Severity.EMERGENCY)


class RiskZone(Frozen):
    """산사태취약지역·급경사지 등 사전에 지정된 위험구역."""

    zone_id: str
    hazard: HazardDomain
    name: str
    address: str | None = None
    location: GeoPoint | None = None
    grade: str | None = Field(default=None, description="원천의 등급 표기 (A~E 등)")
    managing_agency: str | None = None
    designated_on: str | None = None
    households_at_risk: int | None = Field(default=None, ge=0)
    people_at_risk: int | None = Field(default=None, ge=0)

    @property
    def is_locatable(self) -> bool:
        """지도·공간연산에 쓸 수 있는지. 경북 취약지역 상당수가 좌표가 없다."""
        return self.location is not None


class ShelterKind(StrEnum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    UNKNOWN = "unknown"


class Shelter(Frozen):
    """대피시설 후보.

    **`supported_hazards`가 비어 있으면 어떤 재난에도 자동 배정되지 않는다.**
    지진 옥외대피장소를 호우 대피소로 쓰는 것은 실제 위험을 만든다.
    """

    shelter_id: str
    name: str
    kind: ShelterKind = ShelterKind.UNKNOWN
    address: str | None = None
    location: GeoPoint | None = None
    capacity: int | None = Field(default=None, ge=0)
    current_occupancy: int | None = Field(default=None, ge=0)
    supported_hazards: tuple[HazardDomain, ...] = ()
    wheelchair_accessible: bool | None = None
    operating: bool | None = Field(
        default=None, description="현재 운영 여부. None은 '확인되지 않음'이며 '운영중'이 아니다"
    )
    managing_agency: str | None = None
    contact: str | None = None
    designated: bool = Field(
        default=False, description="공식 지정시설인지. False는 후보시설로만 표현해야 한다"
    )
    last_verified_at: datetime | None = None

    def serves(self, hazard: HazardDomain) -> bool:
        """이 재난에 사용할 수 있다고 **확인된** 경우에만 True.

        미확인을 허용으로 바꾸지 않는다.
        """
        return hazard in self.supported_hazards

    @property
    def remaining_capacity(self) -> int | None:
        """남은 수용량. 현재 인원이 확인되지 않으면 None이며 0이 아니다."""
        if self.capacity is None or self.current_occupancy is None:
            return None
        return max(0, self.capacity - self.current_occupancy)

    @property
    def occupancy_is_trustworthy(self) -> bool:
        """현재 수용인원을 화면에 실시간처럼 표시해도 되는지."""
        return self.current_occupancy is not None and self.last_verified_at is not None


class RoadBlockCause(StrEnum):
    FLOODING = "flooding"
    LANDSLIDE = "landslide"
    ACCIDENT = "accident"
    CONSTRUCTION = "construction"
    WEATHER = "weather"
    CONTROL = "control"
    OTHER = "other"


class RoadObstruction(Frozen):
    """도로 통제·돌발 정보."""

    obstruction_id: str
    cause: RoadBlockCause
    description: str
    road_name: str | None = None
    location: GeoPoint | None = None
    started_at: datetime | None = None
    expected_clear_at: datetime | None = None
    is_full_closure: bool | None = None

    @property
    def blocks_routing(self) -> bool:
        """경로 계산에서 제외해야 하는지. 전면통제만 확실히 제외한다."""
        return self.is_full_closure is True


class PopulationProfile(Frozen):
    """지역 단위 인구 집계.

    **집계값이다.** 특정 개인의 이동능력·장애·질병을 추정하는 데 쓸 수 없으며
    그 판단은 `gbsafe_core.safety`가 막는다.
    """

    area_name: str
    area_code: str | None = None
    total_population: int | None = Field(default=None, ge=0)
    households: int | None = Field(default=None, ge=0)
    elderly_population: int | None = Field(default=None, ge=0)
    child_population: int | None = Field(default=None, ge=0)
    reference_period: str | None = Field(default=None, description="기준 시점 (예: 2026-07)")

    @property
    def elderly_ratio(self) -> float | None:
        if not self.total_population or self.elderly_population is None:
            return None
        return self.elderly_population / self.total_population


class MedicalCapacity(Frozen):
    """응급의료기관 가용 상태.

    `reported_at`(원천의 hvidate)이 실제 갱신 시각이다. 이 값 없이 실시간처럼
    표시하면 안 된다.
    """

    facility_id: str
    name: str
    address: str | None = None
    location: GeoPoint | None = None
    emergency_beds: int | None = None
    operating_rooms: int | None = None
    icu_beds: int | None = None
    reported_at: datetime | None = None
    phone: str | None = None

    @property
    def is_realtime(self) -> bool:
        return self.reported_at is not None


class DatasetDescriptor(Frozen):
    """검색 결과로 반환되는 데이터셋 설명.

    카탈로그 항목을 AI가 읽기 쉬운 형태로 축약한 것이다.
    """

    dataset_id: str
    name: str
    provider: str
    license: str
    access_route: str
    dev_ready: bool
    usable_now: bool
    update_cycle: str | None
    rows: int | None
    hazard_domains: tuple[str, ...]
    quality_flags: tuple[str, ...]
    source_url: str | None
    how_to_obtain: str
    caveat: str | None = None
