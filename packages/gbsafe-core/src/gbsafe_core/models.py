"""GB SafeData 정규 스키마의 기반 타입.

여기 있는 타입들은 하나의 원칙을 강제한다: **출처와 시점 없이는 값을 전달할 수 없다.**
모든 데이터는 `Record`로 감싸여 이동하며, `Record`는 `Provenance`를 반드시 갖는다.
AI나 외부 시스템이 값만 뽑아 쓰는 것은 가능하지만, 그렇게 하려면 인용을 의도적으로
버려야 하므로 실수로 출처를 잃는 일이 없다.

도메인 엔티티(대피소·위험구역 등)는 `domain.py`에 있다.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

KST_OFFSET_HOURS = 9


class Frozen(BaseModel):
    """모든 GB SafeData 모델의 기반. 불변이며 미지정 필드를 거부한다."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class LicenseCode(StrEnum):
    """공공누리 유형과 이 프로젝트가 취급하는 그 외 라이선스.

    유형 번호가 아니라 **무엇이 금지되는가**로 판단해야 하므로
    권한 해석은 `gbsafe_core.licensing`에 둔다.
    """

    UNRESTRICTED = "unrestricted"
    KOGL_1 = "KOGL-1"
    KOGL_2 = "KOGL-2"
    KOGL_3 = "KOGL-3"
    KOGL_4 = "KOGL-4"
    ODBL = "ODbL"
    PROPRIETARY = "proprietary"
    UNKNOWN = "unknown"


class DataMode(StrEnum):
    """실제 운영 데이터와 훈련용 데이터를 시스템 차원에서 분리한다.

    화면·API·MCP 응답 어디에서도 이 값이 사라지지 않아야 한다.
    `SYNTHETIC`이 `REAL`처럼 보이는 순간이 이 프로젝트에서 가장 위험한 실패다.
    """

    REAL = "real"
    SNAPSHOT = "snapshot"
    SYNTHETIC = "synthetic"


class UpstreamStatus(StrEnum):
    """원천 API의 상태. 장애를 숨기지 않기 위해 응답에 항상 포함된다."""

    OK = "ok"
    CACHED = "cached"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_AUTHORIZED = "not_authorized"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    UNKNOWN = "unknown"


class QualityFlag(StrEnum):
    """검증으로 실제 확인된 결함 유형.

    값은 ../jxkr2026-datasets/docs/data-quality-defects.md 에서 관측된 것만 둔다.
    """

    ROW_COUNT_MISMATCH = "row_count_mismatch"
    EMPTY_DATASET = "empty_dataset"
    FORMAT_MISMATCH = "format_mismatch"
    PROVIDER_MISMATCH = "provider_mismatch"
    UPDATE_CLAIM_MISMATCH = "update_claim_mismatch"
    NOT_MACHINE_READABLE = "not_machine_readable"
    ADMIN_BOUNDARY_DRIFT = "admin_boundary_drift"
    MISSING_COORDINATES = "missing_coordinates"
    COORDINATE_OUT_OF_RANGE = "coordinate_out_of_range"
    ENCODING_CP949 = "encoding_cp949"
    PARTIAL_RESPONSE = "partial_response"
    NO_DATA_RETURNED = "no_data_returned"


Latitude = Annotated[float, Field(ge=33.0, le=39.0)]
Longitude = Annotated[float, Field(ge=124.0, le=132.0)]


class GeoPoint(Frozen):
    """WGS84 위경도. 한반도 범위를 벗어나면 스키마 단계에서 거부된다.

    좌표계 혼동(EPSG:5179/5186 값을 위경도로 넣는 실수)이 실제로 자주 나므로
    범위 제약을 타입에 박아 둔다.
    """

    lat: Latitude
    lon: Longitude

    def as_geojson(self) -> dict[str, Any]:
        return {"type": "Point", "coordinates": [self.lon, self.lat]}


class BBox(Frozen):
    min_lon: Longitude
    min_lat: Latitude
    max_lon: Longitude
    max_lat: Latitude

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min_lon >= self.max_lon or self.min_lat >= self.max_lat:
            raise ValueError("BBox의 min 값은 max 값보다 작아야 합니다")
        return self

    def contains(self, point: GeoPoint) -> bool:
        return (
            self.min_lon <= point.lon <= self.max_lon
            and self.min_lat <= point.lat <= self.max_lat
        )


class Provenance(Frozen):
    """이 값이 어디서 언제 왔는지. 모든 Record에 필수다."""

    dataset_id: str = Field(description="data.go.kr pk 또는 GB SafeData 내부 데이터셋 id")
    dataset_name: str
    provider: str = Field(description="원천 기관명")
    source_url: str | None = Field(default=None, description="사람이 확인할 수 있는 원본 페이지")
    endpoint: str | None = Field(default=None, description="실제 호출한 엔드포인트")
    license: LicenseCode = LicenseCode.UNKNOWN
    mode: DataMode = DataMode.REAL
    upstream_status: UpstreamStatus = UpstreamStatus.OK

    retrieved_at: datetime = Field(description="GB SafeData가 수집한 시각")
    observed_at: datetime | None = Field(default=None, description="원천의 관측·측정 시각")
    published_at: datetime | None = Field(default=None, description="원천의 발표·갱신 시각")
    valid_until: datetime | None = Field(default=None, description="유효기간이 명시된 경우")
    expected_cycle_seconds: int | None = Field(
        default=None, ge=0, description="데이터셋의 예상 갱신주기(초)"
    )
    snapshot_id: str | None = Field(default=None, description="원본이 보존된 스냅샷 해시")

    @model_validator(mode="after")
    def _tz_aware(self) -> Self:
        for name in ("retrieved_at", "observed_at", "published_at", "valid_until"):
            value: datetime | None = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name}은 시간대를 포함해야 합니다 (naive datetime 거부)")
        return self

    @property
    def effective_time(self) -> datetime:
        """신선도 판단의 기준 시각. 관측 > 발표 > 수집 순으로 신뢰한다."""
        return self.observed_at or self.published_at or self.retrieved_at

    def citation(self) -> Citation:
        return Citation(
            dataset_id=self.dataset_id,
            dataset_name=self.dataset_name,
            provider=self.provider,
            license=self.license,
            source_url=self.source_url,
            as_of=self.effective_time,
            mode=self.mode,
        )


class Citation(Frozen):
    """AI가 답변에 그대로 붙일 수 있는 최소 인용 단위."""

    dataset_id: str
    dataset_name: str
    provider: str
    license: LicenseCode
    source_url: str | None
    as_of: datetime
    mode: DataMode

    def to_text(self) -> str:
        stamp = self.as_of.astimezone(UTC).isoformat(timespec="seconds")
        parts = [f"{self.provider} 「{self.dataset_name}」", f"기준 {stamp}", self.license.value]
        if self.mode is not DataMode.REAL:
            parts.append(f"[{self.mode.value.upper()}]")
        if self.source_url:
            parts.append(self.source_url)
        return " · ".join(parts)


class Freshness(Frozen):
    """수집 시각과 갱신주기로 계산한 신선도 판정."""

    status: FreshnessStatus
    age_seconds: int | None
    expected_cycle_seconds: int | None
    as_of: datetime
    evaluated_at: datetime
    reason: str

    @property
    def is_usable_for_decision(self) -> bool:
        """대피 판단 근거로 제시해도 되는 상태인지."""
        return self.status in (FreshnessStatus.FRESH, FreshnessStatus.AGING)


class Record[PayloadT](BaseModel):
    """정규화된 값 하나 + 출처 + 신선도 + 품질 플래그.

    `payload`만 꺼내 쓰는 것은 호출자의 선택이지만, 기본 직렬화에는 항상
    인용이 함께 나간다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload: PayloadT
    provenance: Provenance
    freshness: Freshness
    quality_flags: tuple[QualityFlag, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def citation(self) -> Citation:
        return self.provenance.citation()

    def fingerprint(self) -> str:
        """같은 값·같은 출처·같은 시점이면 동일한 지문. 중복 제거와 멱등성에 쓴다."""
        body = self.payload.model_dump_json() if isinstance(self.payload, BaseModel) else str(
            self.payload
        )
        seed = "|".join(
            [
                self.provenance.dataset_id,
                self.provenance.effective_time.isoformat(),
                body,
            ]
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


class SourceOutcome(StrEnum):
    """한 원천을 조회한 결과의 종류.

    빈 결과의 의미를 추정하지 않기 위해 존재한다. `CONFIRMED_EMPTY`는 원천이
    정상 응답으로 "해당 없음"을 명시한 경우이고, `FAILED`는 응답을 해석하지
    못한 경우다. 둘을 구별하지 않으면 파싱 실패가 '위험 없음'이 된다.
    """

    RECORDS = "records"
    CONFIRMED_EMPTY = "confirmed_empty"
    FAILED = "failed"

    @property
    def is_trustworthy_absence(self) -> bool:
        """결과가 비어 있을 때 그것을 '해당 없음'으로 읽어도 되는지."""
        return self is SourceOutcome.CONFIRMED_EMPTY


class SourceReceipt(Frozen):
    """어떤 원천을 언제 조회해서 무엇을 얻었는지에 대한 영수증.

    실패만 기록하면 "조회하지 않은 원천"과 "조회해서 비어 있던 원천"을 구별할
    수 없다. 성공한 조회도 남겨야 빈 결과의 근거가 생긴다.
    """

    connector: str
    dataset_id: str
    outcome: SourceOutcome
    record_count: int = Field(ge=0)
    checked_at: datetime
    upstream_status: UpstreamStatus
    detail: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.outcome is SourceOutcome.RECORDS and self.record_count == 0:
            raise ValueError("RECORDS는 레코드가 하나 이상이어야 합니다")
        if self.outcome is not SourceOutcome.RECORDS and self.record_count:
            raise ValueError(f"{self.outcome.value}는 레코드를 가질 수 없습니다")
        if self.checked_at.tzinfo is None:
            raise ValueError("checked_at은 시간대를 포함해야 합니다")
        return self


class Degradation(Frozen):
    """원천 장애나 권한 부재를 응답에 실어 보내는 구조.

    비어 있는 결과를 '위험 없음'으로 오해하게 만드는 것이 이 시스템에서
    가장 치명적인 오류이므로, 실패는 조용히 사라지지 않고 여기에 남는다.
    """

    dataset_id: str
    status: UpstreamStatus
    detail: str
    occurred_at: datetime
    last_known_good_at: datetime | None = None

    @property
    def blocks_interpretation(self) -> bool:
        """결과를 '해당 없음'으로 해석할 수 없게 만드는지.

        모든 실패가 여기 해당한다. 파싱 실패나 호출 한도 초과를 '부분 장애'로
        분류해 통과시키면, 응답을 만들지 못한 원천이 있는데도 complete=true가
        되어 빈 결과가 '위험 없음'으로 읽힌다.
        """
        return self.status in (
            UpstreamStatus.UNAVAILABLE,
            UpstreamStatus.NOT_AUTHORIZED,
            UpstreamStatus.DEGRADED,
        )


class Answer[PayloadT](BaseModel):
    """API·MCP가 밖으로 내보내는 최종 봉투.

    결과가 비었을 때 그것이 '해당 없음'인지 '조회 실패'인지 구별할 수 있어야 하므로
    `degradations`와 `records`를 함께 싣는다.
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    records: tuple[Record[PayloadT], ...] = ()
    degradations: tuple[Degradation, ...] = ()
    receipts: tuple[SourceReceipt, ...] = Field(
        default=(), description="조회한 원천별 결과. 빈 결과의 근거가 된다"
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    caveats: tuple[str, ...] = ()

    @property
    def citations(self) -> tuple[Citation, ...]:
        seen: dict[str, Citation] = {}
        for record in self.records:
            citation = record.citation
            seen.setdefault(f"{citation.dataset_id}@{citation.as_of.isoformat()}", citation)
        return tuple(seen.values())

    @property
    def is_complete(self) -> bool:
        """모든 원천을 조회했고 해석을 막는 실패가 없는지."""
        if any(item.blocks_interpretation for item in self.degradations):
            return False
        return all(
            receipt.outcome is not SourceOutcome.FAILED for receipt in self.receipts
        )

    @property
    def absence_is_confirmed(self) -> bool:
        """결과가 비어 있는 것을 '해당 없음'으로 읽어도 되는지.

        조회한 원천이 하나도 없으면 확인된 것이 없으므로 False다.
        """
        if not self.receipts:
            return False
        return self.is_complete and all(
            receipt.outcome.is_trustworthy_absence or receipt.record_count
            for receipt in self.receipts
        )

    def failed_sources(self) -> tuple[str, ...]:
        return tuple(
            receipt.connector
            for receipt in self.receipts
            if receipt.outcome is SourceOutcome.FAILED
        )

    def modes(self) -> tuple[DataMode, ...]:
        return tuple({record.provenance.mode for record in self.records})
