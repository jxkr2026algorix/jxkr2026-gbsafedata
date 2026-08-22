"""API 응답 모델.

FastAPI가 OpenAPI 스키마를 만드는 근거다. 라우트가 `dict[str, Any]`를 돌려주면
스키마에 `{}`만 남고, 연동하는 쪽은 실제 응답을 눈으로 보고 추측해야 한다.
필드 하나하나에 설명을 붙이는 이유도 같다 — 이 API는 다른 팀과 LLM이 읽는다.

특히 안전에 걸리는 필드는 설명에 **읽는 법**을 함께 적는다. `complete`가
false인데 `records`만 보고 판단하는 것이 이 저장소가 막으려는 실패이고,
스키마는 그것을 경고할 수 있는 마지막 자리다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    """응답 모델 공통. 정의하지 않은 필드는 거부한다."""

    model_config = ConfigDict(extra="forbid")


# ── 지역 ────────────────────────────────────────────────────────────


class Coordinate(Model):
    lat: float = Field(description="위도 (WGS84)", examples=[36.5866])
    lon: float = Field(description="경도 (WGS84)", examples=[128.1867])


class KmaGridCell(Model):
    nx: int = Field(description="기상청 단기예보 격자 X", examples=[81])
    ny: int = Field(description="기상청 단기예보 격자 Y", examples=[106])


class AsosStationDetail(Model):
    station_id: int = Field(description="기상청 국내 지점번호", examples=[273])
    name: str = Field(description="관측지점명", examples=["문경"])
    distance_km: float = Field(
        description="시군 청사에서 관측지점까지 직선거리", examples=[5.6]
    )
    is_local: bool = Field(
        description=(
            "이 시군을 대표하는 관측으로 볼 수 있는지. false면 이웃 지역 관측값을 "
            "대신 쓰는 것이며 국지성 호우는 그 거리에서 크게 달라진다"
        )
    )


class RegionSummary(Model):
    code: str = Field(description="행정표준코드 시군구 5자리", examples=["47280"])
    name: str = Field(description="시군명", examples=["문경시"])
    center: Coordinate = Field(description="청사 기준 대표 좌표 (근사값)")


class RegionList(Model):
    count: int
    regions: list[RegionSummary]
    caveat: str = Field(description="대표 좌표를 경계 판정에 쓰면 안 되는 이유")


class RegionResolution(Model):
    found: bool = Field(description="경북 시군으로 해석됐는지")
    query: str | None = Field(default=None, description="해석하지 못한 원래 입력")
    message: str | None = Field(default=None, description="해석 실패 사유")
    available: list[str] | None = Field(default=None, description="사용 가능한 시군명")
    code: str | None = Field(default=None, examples=["47280"])
    name: str | None = Field(default=None, examples=["문경시"])
    full_name: str | None = Field(default=None, examples=["경상북도 문경시"])
    center: Coordinate | None = None
    kma_grid: KmaGridCell | None = Field(
        default=None, description="기상청 격자. 위경도가 아니므로 그대로 지도에 쓰면 안 된다"
    )
    asos_station: int | None = Field(default=None, description="대표 관측지점번호")
    asos_station_detail: AsosStationDetail | None = None
    caveat: str | None = Field(default=None, description="첫 번째 주의사항 (하위호환)")
    caveats: list[str] | None = Field(
        default=None, description="이 답과 함께 반드시 표시해야 하는 주의사항 전부"
    )
    transferred: str | None = Field(
        default=None, description="행정구역이 이관된 경우 그 사실과 시점"
    )


# ── 재난 가용성 ─────────────────────────────────────────────────────


class CapabilityAxis(Model):
    label: str = Field(description="축 이름 (탐지·위험도·대피소)")
    usable: int = Field(description="이 축에서 지금 쓸 수 있는 원천 수")
    total: int = Field(description="이 축에서 확인된 원천 수")
    covered: bool = Field(description="쓸 수 있는 원천이 하나라도 있는지")
    sources: list[str] = Field(description="원천 식별자")


class HazardCapabilityEntry(Model):
    hazard: str = Field(description="재난 식별자", examples=["earthquake"])
    korean_name: str = Field(examples=["지진"])
    readiness: str = Field(
        description=(
            "ready = 세 축 완비 / partial = 탐지는 되나 나머지가 빔 / "
            "blocked = 탐지 축 자체가 없음. **partial을 ready처럼 보이게 하면 안 된다**"
        ),
        examples=["partial"],
    )
    can_detect: bool = Field(description="발생 여부를 확인할 수 있는지")
    can_say_where_to_go: bool = Field(
        description="대피 목적지를 답할 수 있는지. 지진은 발생은 알지만 이 값이 false다"
    )
    axes: dict[str, CapabilityAxis]
    missing_axes: list[str] = Field(description="자료가 없는 축")
    caveat: str | None = Field(
        description="ready가 아닌 재난에서 답과 함께 반드시 나가야 하는 한계"
    )
    connectors: list[str] = Field(description="이 재난에 조회되는 커넥터")


class HazardCapabilities(Model):
    hazards: list[HazardCapabilityEntry]
    summary: dict[str, list[str]] = Field(
        description="상태별 재난 목록", examples=[{"ready": ["호우"], "partial": ["지진"]}]
    )
    axes: dict[str, str] = Field(description="각 축이 답하는 질문")
    how_to_read: str


class HazardTypeEntry(Model):
    value: str = Field(examples=["heavy_rain"])
    connectors: list[str]


class HazardTypeList(Model):
    hazards: list[HazardTypeEntry]


# ── 카탈로그 ────────────────────────────────────────────────────────


class DatasetSummary(Model):
    dataset_id: str = Field(examples=["15084084"])
    name: str
    provider: str
    license: str = Field(description="정규화된 라이선스 코드", examples=["KOGL-1"])
    access_route: str = Field(description="취득 경로")
    dev_ready: bool = Field(description="개발계정으로 지금 호출할 수 있는지")
    usable_now: bool = Field(description="인증키까지 갖춰 실제로 조회 가능한지")
    hazard_domains: list[str]
    quality_flags: list[str] = Field(description="검증으로 확인된 결함")
    source_url: str | None = None
    update_cycle: str | None = None
    rows: int | None = Field(default=None, description="포털이 신고한 행 수. 틀린 경우가 있다")
    how_to_obtain: str = Field(description="이 데이터를 실제로 받는 방법")
    caveat: str | None = Field(
        default=None, description="심의 대기 등 이 데이터셋을 쓰기 전 알아야 하는 것"
    )


class DatasetSearchFilters(Model):
    hazard: str | None = None
    dev_ready_only: bool
    usable_only: bool
    must_allow: str | None = None


class DatasetSearch(Model):
    query: str
    filters: DatasetSearchFilters
    count: int
    callable_now: int = Field(description="지금 바로 호출 가능한 건수")
    catalog_source: str = Field(description="카탈로그를 어디서 읽었는지")
    notes: list[str] = Field(description="심의 대기 등 결과를 읽을 때 필요한 설명")
    datasets: list[DatasetSummary]


class DatasetDetail(DatasetSummary):
    found: bool
    portal_title: str | None = Field(default=None, description="포털 표기 제목")
    department: str | None = Field(default=None, description="담당 부서")
    formats: list[str] = Field(default_factory=list, description="제공 형식")
    keywords: list[str] = Field(default_factory=list, description="포털 키워드")
    modified: str | None = Field(default=None, description="포털이 신고한 수정일")
    apply_status: str | None = Field(default=None, description="활용신청 상태")
    external_portal: str | None = Field(
        default=None, description="data.go.kr이 아닌 별도 포털에서 받아야 하는 경우 그 주소"
    )
    dev_traffic_per_day: int | None = Field(
        default=None, description="개발계정 일일 호출 한도"
    )
    review_dev: str | None = Field(default=None, description="개발단계 승인 방식")
    review_prod: str | None = Field(default=None, description="운영단계 승인 방식")
    license_terms: dict[str, Any] | None = Field(
        default=None, description="라이선스가 허용·금지하는 연산"
    )
    connectors: list[str] = Field(
        default_factory=list, description="이 데이터셋을 다루는 커넥터"
    )
    verified: bool = Field(
        default=False, description="실제 호출로 확인됐는지. false는 포털 표기만 본 것이다"
    )


class DatasetNotFound(Model):
    found: bool = Field(description="항상 false")
    dataset_id: str
    message: str


class DatasetVerification(Model):
    dataset_id: str
    dataset_name: str
    operation: str = Field(description="확인한 연산", examples=["derive"])
    allowed: bool = Field(description="라이선스가 이 연산을 허용하는지")
    license: str
    license_summary: str
    reasons: list[str] = Field(description="허용·거부 근거")
    warnings: list[str] = Field(description="허용되더라도 주의할 점 (전염성 등)")
    obtain_via: str


class DatasetCitation(Model):
    found: bool
    dataset_id: str
    text: str = Field(description="답변에 그대로 붙일 수 있는 인용문")
    attribution: str = Field(description="출처 표시 문구")
    provider: str
    dataset_name: str
    license: str
    license_summary: str
    attribution_required: bool
    share_alike: bool = Field(description="다른 데이터와 병합 시 전염되는지")
    source_url: str | None = None
    modified: str | None = None
    caveat: str | None = None


class LicenseTerm(Model):
    code: str = Field(examples=["KOGL-4"])
    summary: str
    attribution_required: bool
    share_alike: bool
    allows: dict[str, bool] = Field(description="연산별 허용 여부")


class LicenseList(Model):
    licenses: list[LicenseTerm]


class QualityDefect(Model):
    dataset_id: str
    name: str
    flags: list[str] = Field(description="확인된 결함 유형")
    rows: int | None = Field(default=None, description="실제 확인된 행 수")
    usable_now: bool = Field(description="결함이 있어도 지금 조회 가능한지")
    note: str | None = Field(default=None, description="무엇이 어떻게 틀렸는지")


class QualityReport(Model):
    count: int
    catalog_source: str
    datasets: list[QualityDefect]


# ── 운영 상태 ───────────────────────────────────────────────────────


class ConnectorHealthEntry(Model):
    name: str = Field(description="커넥터 식별자", examples=["weather_now"])
    dataset_id: str
    dataset_name: str
    provider: str = Field(description="원천 기관")
    summary: str
    available: bool = Field(description="지금 호출 가능한지")
    reason: str | None = Field(
        default=None,
        description=(
            "불가능하면 그 사유. 인증키 부재·심의 대기·파일 필요를 구별한다 — "
            "셋은 대응 방법이 다르고 추측하면 헛걸음한다"
        ),
    )
    requires_local_file: bool = Field(description="포털에서 파일을 받아야 하는 원천인지")
    license: str | None = None
    dev_review_required: bool = Field(
        description="개발단계가 심의승인 대상인지. true면 승인 전까지 403이 정상이다"
    )
    snapshot_count: int = Field(description="보존된 원본 응답 수")
    last_snapshot_at: str | None = Field(
        default=None, description="마지막 보존 시각 (ISO 8601)"
    )
    hazards: list[str]


class CredentialStatus(Model):
    present: bool = Field(description="이 인증정보를 보유하고 있는지")
    source: str = Field(description="발급처와 발급 방법")


class CatalogSummary(Model):
    total: int
    origin: str = Field(description="카탈로그를 어디서 읽었는지", examples=["sibling_repo"])
    verified: int = Field(description="실제 호출로 확인된 건수")
    dev_ready: int
    with_defects: int = Field(description="확인된 결함이 있는 건수")
    derivable: int = Field(description="라이선스가 가공을 허용하는 건수")
    by_route: dict[str, int] = Field(description="취득 경로별 건수")
    by_license: dict[str, int] = Field(description="라이선스별 건수")


class HealthSummary(Model):
    connectors: int
    available: int
    blocked_by_credentials: int
    pending_review: int = Field(description="개발단계 심의 대기 중인 원천 수")
    requires_local_file: int
    catalog: CatalogSummary
    offline_mode: bool = Field(
        description="보존된 스냅샷만 쓰는 모드인지. true면 현재 원천을 확인하지 않는다"
    )


class HealthReport(Model):
    summary: HealthSummary
    connectors: list[ConnectorHealthEntry]
    credentials: dict[str, CredentialStatus]
    checked_at: str = Field(description="이 진단을 수행한 시각 (ISO 8601)")


# ── 에이전트 연동 ───────────────────────────────────────────────────


class ToolFunction(Model):
    name: str = Field(examples=["gbsafe_hazard_context"])
    description: str
    parameters: dict[str, Any] = Field(description="JSON Schema")


class ToolDefinition(Model):
    type: str = Field(description="항상 'function'", examples=["function"])
    function: ToolFunction


class ToolCatalog(Model):
    tools: list[ToolDefinition] = Field(
        description="OpenAI·Upstage Solar가 그대로 받는 function calling 스키마"
    )
    invoke: str = Field(description="도구를 실행하는 방법")
    note: str = Field(description="도구만 붙이면 생기는 사고에 대한 경고")


class SystemPrompt(Model):
    system_prompt: str = Field(
        description="이 도구를 안전하게 쓰기 위해 반드시 함께 적용해야 하는 지침"
    )
    source: str = Field(description="원본 위치")


class ErrorDetail(Model):
    error: str
    available: list[str] | None = None
    how: str | None = None


class ApiError(Model):
    detail: str | ErrorDetail


__all__ = [
    "ApiError",
    "AsosStationDetail",
    "CapabilityAxis",
    "ConnectorHealthEntry",
    "Coordinate",
    "CredentialStatus",
    "DatasetCitation",
    "DatasetDetail",
    "DatasetNotFound",
    "DatasetSearch",
    "DatasetSummary",
    "DatasetVerification",
    "ErrorDetail",
    "HazardCapabilities",
    "HazardCapabilityEntry",
    "HazardTypeList",
    "HealthReport",
    "KmaGridCell",
    "LicenseList",
    "QualityReport",
    "RegionList",
    "RegionResolution",
    "SystemPrompt",
    "ToolCatalog",
    "ToolDefinition",
]
