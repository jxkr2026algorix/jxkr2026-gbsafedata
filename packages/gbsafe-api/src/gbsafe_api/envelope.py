"""표준 API 응답 봉투.

외부 시스템(기존 경북 주민대피시스템, 시군 행정시스템)이 연계할 때 필요한 것은
값이 아니라 **값과 그것을 신뢰할 수 있는 근거**다. 그래서 모든 응답이 같은
모양을 갖는다.

```json
{
  "query": {...},
  "records": [{"payload": ..., "source": ..., "freshness": ...}],
  "citations": [...],
  "degradations": [...],
  "caveats": [...],
  "complete": true
}
```

`complete`가 false이면 일부 원천을 조회하지 못한 것이고, 그 이유가
`degradations`에 있다. 이 필드를 무시하고 `records`만 읽으면 조회 실패를
'해당 없음'으로 오해할 수 있으므로 문서에 명시한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gbsafe_core.licensing import Operation, attribution_notice, permits, terms_for
from gbsafe_core.models import Answer, Citation, Degradation, Record
from pydantic import BaseModel, ConfigDict, Field


class SourceInfo(BaseModel):
    """레코드 하나의 출처. 인용에 필요한 최소 정보를 모두 담는다."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    dataset_name: str
    provider: str
    license: str
    license_summary: str
    attribution: str | None = Field(
        default=None, description="출처표시 의무가 있는 경우 표시할 문구"
    )
    source_url: str | None = None
    endpoint: str | None = None
    mode: str = Field(description="real | snapshot | synthetic — 훈련 데이터 구별용")
    upstream_status: str
    retrieved_at: datetime
    observed_at: datetime | None = None
    published_at: datetime | None = None
    snapshot_id: str | None = Field(
        default=None, description="원본이 보존된 스냅샷 해시 — 사후 검증용"
    )
    may_modify: bool = Field(
        description="파생 가공(재투영·클리핑·조인)이 라이선스상 허용되는지"
    )
    may_redistribute: bool


class FreshnessInfo(BaseModel):
    """이 값을 얼마나 신뢰할 수 있는지."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="fresh | aging | stale | unknown")
    age_seconds: int | None
    expected_cycle_seconds: int | None
    as_of: datetime
    reason: str
    usable_for_decision: bool = Field(
        description="판단 근거로 제시해도 되는 상태인지. false면 최신성을 함께 표시해야 한다"
    )


class RecordEnvelope(BaseModel):
    """값 + 출처 + 신선도. 이 세 가지는 분리되지 않는다."""

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]
    source: SourceInfo
    freshness: FreshnessInfo
    quality_flags: list[str] = Field(
        default_factory=list, description="검증으로 확인된 결함 (좌표 누락, CP949 등)"
    )
    notes: list[str] = Field(default_factory=list)
    fingerprint: str = Field(description="같은 값·출처·시점이면 동일 — 멱등 처리용")


class DegradationInfo(BaseModel):
    """조회하지 못한 원천과 그 이유."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    status: str = Field(description="degraded | unavailable | not_authorized")
    detail: str
    occurred_at: datetime
    last_known_good_at: datetime | None = Field(
        default=None, description="마지막 정상 수집 시각 — 이 시점 이후 자료가 없다"
    )
    blocks_interpretation: bool = Field(
        description="true면 결과가 비어 있는 것이 '해당 없음'을 의미하지 않는다"
    )


class CitationInfo(BaseModel):
    """AI·문서가 그대로 인용할 수 있는 형태."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    dataset_name: str
    provider: str
    license: str
    source_url: str | None
    as_of: datetime
    mode: str
    text: str = Field(description="사람이 읽을 수 있는 완성된 인용 문구")


class ApiEnvelope(BaseModel):
    """모든 데이터 응답의 공통 봉투."""

    model_config = ConfigDict(extra="forbid")

    query: dict[str, Any]
    records: list[RecordEnvelope]
    citations: list[CitationInfo]
    degradations: list[DegradationInfo] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    complete: bool = Field(
        description="false면 일부 원천 조회에 실패했다. records만 보고 판단하면 안 된다"
    )
    record_count: int
    generated_at: datetime
    modes: list[str] = Field(
        description="포함된 데이터 종류. synthetic이 섞여 있으면 훈련 데이터다"
    )


def to_source_info(record: Record[Any]) -> SourceInfo:
    provenance = record.provenance
    terms = terms_for(provenance.license)
    return SourceInfo(
        dataset_id=provenance.dataset_id,
        dataset_name=provenance.dataset_name,
        provider=provenance.provider,
        license=provenance.license.value,
        license_summary=terms.summary,
        attribution=attribution_notice(
            provenance.license, provenance.provider, provenance.dataset_name
        ),
        source_url=provenance.source_url,
        endpoint=provenance.endpoint,
        mode=provenance.mode.value,
        upstream_status=provenance.upstream_status.value,
        retrieved_at=provenance.retrieved_at,
        observed_at=provenance.observed_at,
        published_at=provenance.published_at,
        snapshot_id=provenance.snapshot_id,
        may_modify=permits(provenance.license, Operation.DERIVE),
        may_redistribute=permits(provenance.license, Operation.REDISTRIBUTE),
    )


def to_record_envelope(record: Record[Any]) -> RecordEnvelope:
    payload = record.payload
    body = (
        payload.model_dump(mode="json")
        if isinstance(payload, BaseModel)
        else {"value": payload}
    )
    freshness = record.freshness
    return RecordEnvelope(
        payload=body,
        source=to_source_info(record),
        freshness=FreshnessInfo(
            status=freshness.status.value,
            age_seconds=freshness.age_seconds,
            expected_cycle_seconds=freshness.expected_cycle_seconds,
            as_of=freshness.as_of,
            reason=freshness.reason,
            usable_for_decision=freshness.is_usable_for_decision,
        ),
        quality_flags=[flag.value for flag in record.quality_flags],
        notes=list(record.notes),
        fingerprint=record.fingerprint(),
    )


def to_citation_info(citation: Citation) -> CitationInfo:
    return CitationInfo(
        dataset_id=citation.dataset_id,
        dataset_name=citation.dataset_name,
        provider=citation.provider,
        license=citation.license.value,
        source_url=citation.source_url,
        as_of=citation.as_of,
        mode=citation.mode.value,
        text=citation.to_text(),
    )


def to_degradation_info(degradation: Degradation) -> DegradationInfo:
    return DegradationInfo(
        dataset_id=degradation.dataset_id,
        status=degradation.status.value,
        detail=degradation.detail,
        occurred_at=degradation.occurred_at,
        last_known_good_at=degradation.last_known_good_at,
        blocks_interpretation=degradation.blocks_interpretation,
    )


def envelope(answer: Answer[Any], query: dict[str, Any]) -> ApiEnvelope:
    """`Answer`를 API 응답 봉투로 변환한다."""
    return ApiEnvelope(
        query=query,
        records=[to_record_envelope(record) for record in answer.records],
        citations=[to_citation_info(citation) for citation in answer.citations],
        degradations=[to_degradation_info(item) for item in answer.degradations],
        caveats=list(answer.caveats),
        complete=answer.is_complete,
        record_count=len(answer.records),
        generated_at=answer.generated_at,
        modes=[mode.value for mode in answer.modes()],
    )


__all__ = [
    "ApiEnvelope",
    "CitationInfo",
    "DegradationInfo",
    "FreshnessInfo",
    "RecordEnvelope",
    "SourceInfo",
    "envelope",
    "to_citation_info",
    "to_degradation_info",
    "to_record_envelope",
    "to_source_info",
]
