"""공공데이터 검색·검증·인용 서비스.

API와 MCP가 공유하는 실제 로직이다. 두 표면이 같은 답을 주어야 하므로 여기
한 곳에만 구현한다.

제공하는 능력:

- `search_datasets` — 자연어로 데이터셋 찾기
- `describe_dataset` — 한 데이터셋의 취득 방법·라이선스·결함
- `verify_dataset` — 이 데이터셋을 이 용도로 써도 되는지 판정
- `hazard_context` — 특정 지역의 현재 위험 상황 (여러 원천 통합)
- `shelter_candidates` / `risk_zones` — 대피소·위험구역 후보
- `data_health` — 원천 상태와 인증 정보 현황
- `citations_for` — 특정 조회에 사용된 출처 목록
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from gbsafe_connectors import FetchOutcome, Registry, get_registry
from gbsafe_connectors.filedata import local_response
from gbsafe_core.catalog import AccessRoute, DatasetEntry
from gbsafe_core.domain import DatasetDescriptor
from gbsafe_core.licensing import LicenseViolation, Operation, require, terms_for
from gbsafe_core.models import Answer, Degradation, Record, UpstreamStatus
from gbsafe_core.regions import (
    SIGUNGU,
    HazardDomain,
    asos_station_for,
    find_sigungu,
    grid_for,
    resolve_transferred,
)
from gbsafe_core.safety import SafetyViolation, assert_not_individual_inference

#: 재난 유형별로 먼저 확인해야 할 커넥터 순서.
#: 근거: 메인 시나리오(극한호우 + 산사태 + 도로통제)에서 강우가 선행 조건이다.
HAZARD_PLAYBOOK: dict[HazardDomain, tuple[str, ...]] = {
    HazardDomain.HEAVY_RAIN: ("weather_warning", "weather_now", "weather_forecast"),
    HazardDomain.LANDSLIDE: (
        "landslide_forecast",
        "weather_warning",
        "weather_now",
        "landslide_roadside",
    ),
    HazardDomain.WILDFIRE: ("wildfire_risk", "weather_now", "air_quality"),
    HazardDomain.FLOOD: ("weather_warning", "weather_now", "weather_forecast"),
    HazardDomain.EARTHQUAKE: ("weather_warning",),
    HazardDomain.HEATWAVE: ("weather_warning", "weather_now"),
    HazardDomain.OTHER: ("weather_warning", "weather_now"),
}


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """데이터셋을 특정 용도로 쓸 수 있는지에 대한 판정."""

    dataset_id: str
    dataset_name: str
    operation: str
    allowed: bool
    license: str
    license_summary: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    obtain_via: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_name": self.dataset_name,
            "operation": self.operation,
            "allowed": self.allowed,
            "license": self.license,
            "license_summary": self.license_summary,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "obtain_via": self.obtain_via,
        }


#: 취득 경로별 설명. LINK 유형이 가장 오해를 많이 만든다.
_ROUTE_GUIDE: dict[AccessRoute, str] = {
    AccessRoute.DATA_GO_KR_REST: "data.go.kr 개발계정 인증키로 직접 호출",
    AccessRoute.EXTERNAL_PORTAL: (
        "data.go.kr은 카탈로그 역할만 하며 인증키는 원천기관 포털에서 별도 발급"
    ),
    AccessRoute.PORTAL_DOWNLOAD: "data.go.kr에서 파일 직접 다운로드 (인증키 불필요)",
    AccessRoute.AGENCY_DOWNLOAD: "기관 자체 사이트에서 다운로드",
    AccessRoute.UNKNOWN: "취득 경로가 확인되지 않음",
}


def _describe(entry: DatasetEntry) -> DatasetDescriptor:
    obtain = _ROUTE_GUIDE[entry.access_route]
    if entry.external_portal:
        obtain = f"{obtain} — {entry.external_portal}"

    caveat_parts: list[str] = []
    if not entry.dev_ready:
        caveat_parts.append("개발단계 심의승인 대상 — 승인 전 호출 불가")
    if not entry.usable_now:
        caveat_parts.append("빈 등록물이거나 기계판독 불가")
    if entry.note:
        caveat_parts.append(entry.note)

    return DatasetDescriptor(
        dataset_id=entry.dataset_id,
        name=entry.name,
        provider=entry.provider or "미확인",
        license=entry.license.value,
        access_route=entry.access_route.value,
        dev_ready=entry.dev_ready,
        usable_now=entry.usable_now,
        update_cycle=entry.update_cycle_raw or None,
        rows=entry.rows,
        hazard_domains=tuple(domain.value for domain in entry.hazard_domains),
        quality_flags=tuple(flag.value for flag in entry.quality_flags),
        source_url=entry.url or None,
        how_to_obtain=obtain,
        caveat=" / ".join(caveat_parts) if caveat_parts else None,
    )


class SafeDataService:
    """GB SafeData의 조회 기능. 부작용이 없다."""

    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry or get_registry()

    @property
    def registry(self) -> Registry:
        return self._registry

    # ── 검색 ────────────────────────────────────────────────────
    def search_datasets(
        self,
        query: str = "",
        *,
        hazard: str | None = None,
        dev_ready_only: bool = False,
        usable_only: bool = True,
        must_allow: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """자연어로 데이터셋을 찾는다.

        `must_allow`에 `derive`를 주면 변경금지 데이터가 결과에서 빠진다.
        재투영·클리핑이 필요한 작업에서 미리 걸러내는 데 쓴다.
        """
        hazard_domain = _parse_hazard(hazard)
        operation = Operation(must_allow) if must_allow else None
        results = self._registry.catalog.search(
            query,
            hazard=hazard_domain,
            dev_ready_only=dev_ready_only,
            usable_only=usable_only,
            permits_operation=operation,
            limit=limit,
        )
        return {
            "query": query,
            "filters": {
                "hazard": hazard_domain.value if hazard_domain else None,
                "dev_ready_only": dev_ready_only,
                "usable_only": usable_only,
                "must_allow": must_allow,
            },
            "count": len(results),
            "catalog_source": self._registry.catalog.source.describe(),
            "datasets": [_describe(entry).model_dump(mode="json") for entry in results],
        }

    def describe_dataset(self, dataset_id: str) -> dict[str, Any]:
        """데이터셋 하나의 상세 정보."""
        entry = self._registry.catalog.get(dataset_id)
        if entry is None:
            suggestions = self._registry.catalog.search(dataset_id, limit=5)
            return {
                "found": False,
                "dataset_id": dataset_id,
                "message": f"카탈로그에 '{dataset_id}'가 없습니다",
                "suggestions": [
                    {"dataset_id": item.dataset_id, "name": item.name} for item in suggestions
                ],
            }

        terms = terms_for(entry.license)
        connectors = [spec.name for spec in self._registry.specs_for_dataset(dataset_id)]
        return {
            "found": True,
            **_describe(entry).model_dump(mode="json"),
            "portal_title": entry.portal_title,
            "department": entry.department,
            "formats": list(entry.formats),
            "external_portal": entry.external_portal or None,
            "review_dev": entry.review_dev.value,
            "review_prod": entry.review_prod.value,
            "dev_traffic_per_day": entry.dev_traffic,
            "modified": entry.modified or None,
            "verified": entry.verified,
            "keywords": list(entry.keywords),
            "apply_status": entry.apply_status or None,
            "license_terms": {
                "summary": terms.summary,
                "attribution_required": terms.attribution_required,
                "share_alike": terms.share_alike,
                "may_read": terms.permits(Operation.READ),
                "may_derive": terms.permits(Operation.DERIVE),
                "may_redistribute": terms.permits(Operation.REDISTRIBUTE),
                "may_use_commercially": terms.permits(Operation.COMMERCIAL),
            },
            "connectors": connectors,
        }

    def verify_dataset(self, dataset_id: str, operation: str = "read") -> VerificationResult:
        """이 데이터셋을 이 용도로 써도 되는지 판정한다.

        라이선스뿐 아니라 심의 상태와 데이터 품질까지 함께 본다. 승인되지 않은
        데이터셋은 라이선스가 허용해도 지금 호출할 수 없기 때문이다.
        """
        entry = self._registry.catalog.get(dataset_id)
        if entry is None:
            return VerificationResult(
                dataset_id=dataset_id,
                dataset_name="미확인",
                operation=operation,
                allowed=False,
                license="unknown",
                license_summary="카탈로그에 없는 데이터셋",
                reasons=(f"'{dataset_id}'를 카탈로그에서 찾을 수 없습니다",),
                warnings=(),
                obtain_via="확인 불가",
            )

        target = Operation(operation)
        reasons: list[str] = []
        warnings: list[str] = []

        try:
            require(entry.license, target, f"「{entry.name}」")
            allowed = True
            reasons.append(f"{terms_for(entry.license).summary} 조건에서 허용됩니다")
        except LicenseViolation as violation:
            allowed = False
            reasons.append(str(violation))

        if not entry.dev_ready:
            allowed = False
            reasons.append(
                "개발단계가 심의승인 대상이어서 활용신청 승인 전까지 호출할 수 없습니다"
            )
        if not entry.usable_now:
            allowed = False
            reasons.append("빈 등록물이거나 기계판독이 불가한 데이터셋입니다")

        for flag in entry.quality_flags:
            warnings.append(f"확인된 결함: {flag.value}")
        if entry.access_route is AccessRoute.EXTERNAL_PORTAL:
            warnings.append(
                f"인증키를 {entry.external_portal or '원천기관 포털'}에서 별도로 받아야 합니다"
            )
        if terms_for(entry.license).share_alike:
            warnings.append(
                "share-alike 라이선스입니다 — 다른 라이선스 데이터와 병합해 배포하면 전염됩니다"
            )
        if entry.dev_traffic and entry.dev_traffic <= 1000:
            warnings.append(f"개발계정 한도가 일 {entry.dev_traffic}건으로 낮습니다")

        obtain = _ROUTE_GUIDE[entry.access_route]
        return VerificationResult(
            dataset_id=entry.dataset_id,
            dataset_name=entry.name,
            operation=operation,
            allowed=allowed,
            license=entry.license.value,
            license_summary=terms_for(entry.license).summary,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
            obtain_via=obtain,
        )

    # ── 지역 ────────────────────────────────────────────────────
    def resolve_region(self, query: str) -> dict[str, Any]:
        """지역명을 코드·좌표·격자로 변환한다.

        기관마다 지역 식별자가 달라서(시도명 문자열, 시군구 코드, 기상 격자,
        ASOS 지점번호) 이 변환이 조회의 전제 조건이다.
        """
        transferred = resolve_transferred(query.strip())
        if transferred:
            return {"found": False, "query": query, "message": transferred}

        sigungu = find_sigungu(query)
        if sigungu is None:
            return {
                "found": False,
                "query": query,
                "message": f"'{query}'를 경북 시군으로 해석할 수 없습니다",
                "available": [item.name for item in SIGUNGU.values()],
            }

        grid = grid_for(sigungu.code)
        return {
            "found": True,
            "code": sigungu.code,
            "name": sigungu.name,
            "full_name": sigungu.full_name,
            "center": {"lat": sigungu.center.lat, "lon": sigungu.center.lon},
            "kma_grid": {"nx": grid.nx, "ny": grid.ny} if grid else None,
            "asos_station": asos_station_for(sigungu.code),
            "caveat": (
                "대표 좌표는 시군 청사 기준 근사값입니다 — 경계 판정이나 "
                "거리 계산의 근거로 쓰면 안 됩니다"
            ),
        }

    # ── 위험 상황 ───────────────────────────────────────────────
    async def hazard_context(
        self,
        region: str,
        *,
        hazard: str | None = None,
        include: tuple[str, ...] | None = None,
    ) -> Answer[Any]:
        """특정 지역의 현재 위험 상황을 여러 원천에서 모아 온다.

        일부 원천이 실패해도 나머지를 돌려주고, 실패는 degradation으로 남긴다.
        """
        hazard_domain = _parse_hazard(hazard) or HazardDomain.HEAVY_RAIN
        names = include or HAZARD_PLAYBOOK.get(hazard_domain, ("weather_warning",))

        sigungu = find_sigungu(region)
        if sigungu is None:
            return Answer(
                query=f"{region} {hazard_domain.value}",
                degradations=(
                    Degradation(
                        dataset_id="region",
                        status=UpstreamStatus.UNAVAILABLE,
                        detail=f"'{region}'을 경북 시군으로 해석할 수 없습니다",
                        occurred_at=datetime.now(UTC),
                    ),
                ),
            )

        outcomes = await asyncio.gather(
            *(self._fetch(name, sigungu.code) for name in names), return_exceptions=True
        )

        records: list[Record[Any]] = []
        degradations: list[Degradation] = []
        caveats: list[str] = []
        for name, outcome in zip(names, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                degradations.append(
                    Degradation(
                        dataset_id=name,
                        status=UpstreamStatus.UNAVAILABLE,
                        detail=f"{type(outcome).__name__}: {outcome}",
                        occurred_at=datetime.now(UTC),
                    )
                )
                continue
            records.extend(outcome.records)
            degradations.extend(outcome.degradations)
            caveats.extend(outcome.caveats)

        return Answer(
            query=f"{sigungu.full_name} {hazard_domain.value}",
            records=tuple(records),
            degradations=tuple(degradations),
            caveats=tuple(dict.fromkeys(caveats)),
        )

    async def _fetch(self, name: str, region_code: str) -> FetchOutcome[Any]:
        connector = self._registry.create(name)
        kwargs: dict[str, Any] = {}
        if name in ("weather_now", "weather_forecast"):
            kwargs["location"] = region_code
        elif name == "emergency_beds":
            kwargs["sigungu"] = region_code
        elif name in ("landslide_forecast", "landslide_history"):
            resolved = find_sigungu(region_code)
            if resolved:
                kwargs["sigungu"] = resolved.name
        return await connector.fetch(**kwargs)

    async def fetch_connector(self, name: str, **kwargs: Any) -> Answer[Any]:
        """커넥터 하나를 직접 조회한다."""
        try:
            connector = self._registry.create(name)
        except KeyError as error:
            return Answer(
                query=name,
                degradations=(
                    Degradation(
                        dataset_id=name,
                        status=UpstreamStatus.UNAVAILABLE,
                        detail=str(error),
                        occurred_at=datetime.now(UTC),
                    ),
                ),
            )
        outcome = await connector.fetch(**kwargs)
        return Answer(
            query=f"{name} {kwargs}",
            records=outcome.records,
            degradations=outcome.degradations,
            caveats=outcome.caveats,
        )

    def normalize_csv(
        self, connector_name: str, path_or_bytes: Any, **kwargs: Any
    ) -> Answer[Any]:
        """사용자가 받아둔 파일데이터 CSV를 정규화한다.

        파일데이터는 세션 의존 때문에 자동 다운로드가 어려워, 취득은 사용자가
        하고 정규화는 이 경로로 처리한다.
        """
        connector = self._registry.create(connector_name)
        response = local_response(path_or_bytes)
        outcome = connector.parse(response, **kwargs)
        return Answer(
            query=f"{connector_name} (local file)",
            records=outcome.records,
            degradations=outcome.degradations,
            caveats=outcome.caveats,
        )

    # ── 상태 ────────────────────────────────────────────────────
    def data_health(self) -> dict[str, Any]:
        """원천 상태와 인증 정보 현황."""
        return {
            "summary": self._registry.summary(),
            "connectors": [item.to_dict() for item in self._registry.health()],
            "credentials": self._registry.credential_status(),
            "checked_at": datetime.now(UTC).isoformat(),
        }

    def quality_report(self) -> dict[str, Any]:
        """검증으로 확인된 데이터 품질 결함 목록."""
        defects = self._registry.catalog.defects()
        return {
            "count": len(defects),
            "catalog_source": self._registry.catalog.source.describe(),
            "datasets": [
                {
                    "dataset_id": entry.dataset_id,
                    "name": entry.name,
                    "flags": [flag.value for flag in entry.quality_flags],
                    "rows": entry.rows,
                    "usable_now": entry.usable_now,
                    "note": entry.note or None,
                }
                for entry in defects
            ],
        }

    def population_guidance(self, purpose: str) -> dict[str, Any]:
        """인구 데이터 사용 목적이 안전 경계를 넘지 않는지 확인한다."""
        try:
            assert_not_individual_inference(purpose)
        except SafetyViolation as violation:
            return {"allowed": False, "reason": str(violation)}
        return {
            "allowed": True,
            "guidance": (
                "집계 인구통계는 지역 단위 취약성 지표로만 사용하세요. "
                "개인별 지원 필요 여부는 기관의 주민 명부에서 확인해야 합니다."
            ),
        }


def _parse_hazard(raw: str | None) -> HazardDomain | None:
    if not raw:
        return None
    text = raw.strip().lower()
    try:
        return HazardDomain(text)
    except ValueError:
        pass
    aliases = {
        "호우": HazardDomain.HEAVY_RAIN,
        "폭우": HazardDomain.HEAVY_RAIN,
        "rain": HazardDomain.HEAVY_RAIN,
        "산사태": HazardDomain.LANDSLIDE,
        "landslide": HazardDomain.LANDSLIDE,
        "산불": HazardDomain.WILDFIRE,
        "wildfire": HazardDomain.WILDFIRE,
        "fire": HazardDomain.WILDFIRE,
        "침수": HazardDomain.FLOOD,
        "홍수": HazardDomain.FLOOD,
        "flood": HazardDomain.FLOOD,
        "지진": HazardDomain.EARTHQUAKE,
        "earthquake": HazardDomain.EARTHQUAKE,
        "폭염": HazardDomain.HEATWAVE,
        "heatwave": HazardDomain.HEATWAVE,
    }
    return aliases.get(text)


__all__ = ["HAZARD_PLAYBOOK", "SafeDataService", "VerificationResult"]
