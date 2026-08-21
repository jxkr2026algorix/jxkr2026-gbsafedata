"""GB SafeData 표준 API.

기존 행정시스템과 살길 Ops가 연계하는 표면이다. 설계 원칙 셋:

1. **모든 데이터 응답이 같은 봉투를 쓴다.** 값만 오는 응답은 없다.
2. **부작용이 없다.** 전화 발신·대피명령·상태변경은 이 API의 책임이 아니다.
   POST/PUT/DELETE가 존재하지 않는다.
3. **실패를 숨기지 않는다.** 원천 장애는 200 응답의 `degradations`로 온다.
   조회 실패를 빈 배열로 돌려주면 '위험 없음'으로 읽히기 때문이다.

인증은 이 계층에 없다. 공개 데이터만 다루고 개인정보를 취급하지 않기 때문이며,
운영 배치에서는 앞단에 게이트웨이를 두는 것을 전제한다(README에 명시).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from gbsafe_core.licensing import TERMS, Operation
from gbsafe_core.regions import SIGUNGU, HazardDomain

from .envelope import ApiEnvelope, envelope
from .service import HAZARD_PLAYBOOK, SafeDataService

TITLE = "GB SafeData API"
DESCRIPTION = """
경북 재난대피에 필요한 공공데이터를 **출처·시점·라이선스와 함께** 제공하는 표준 API.

## 이 API가 하지 않는 것

- 전화 발신, 대피명령, 주민 상태 변경 — 운영 플랫폼(살길 Ops)의 책임입니다
- 개인정보 취급 — 공개 집계 데이터만 다룹니다
- 대피 여부 결정 — 근거와 후보를 제시하며 결정은 담당 공무원이 합니다

## 응답을 읽는 방법

모든 데이터 응답은 `records` + `citations` + `degradations` + `complete`를 함께 담습니다.

**`complete`가 `false`면 `records`만 보고 판단하면 안 됩니다.** 일부 원천 조회에
실패한 상태이고, 결과가 비어 있는 것이 '해당 없음'을 의미하지 않습니다.
사유는 `degradations[].detail`에 있습니다.

각 레코드의 `freshness.usable_for_decision`이 `false`면 그 값은 오래된 자료입니다.
`source.mode`가 `synthetic`이면 훈련용 데이터이므로 실제 상황으로 표시하면 안 됩니다.
"""


#: region 없이는 조회할 수 없는 커넥터. 기상 격자 좌표가 필요하다.
REGION_REQUIRED = frozenset({"weather_now", "weather_forecast"})


def _connector_kwargs(connector: str, region: str | None, rows: int) -> dict[str, Any]:
    """커넥터별 지역 파라미터 이름이 다르므로 여기서 맞춘다."""
    kwargs: dict[str, Any] = {"rows": rows}
    if not region:
        return kwargs
    if connector in REGION_REQUIRED:
        kwargs["location"] = region
    elif connector == "emergency_beds":
        kwargs["sigungu"] = region
    else:
        kwargs["region"] = region
    return kwargs


def create_app(service: SafeDataService | None = None) -> FastAPI:
    resolved = service or SafeDataService()

    app = FastAPI(
        title=TITLE,
        description=DESCRIPTION,
        version="0.1.0",
        license_info={"name": "Apache-2.0"},
        openapi_tags=[
            {"name": "catalog", "description": "데이터셋 검색·검증·인용"},
            {"name": "hazard", "description": "지역별 위험 상황 조회"},
            {"name": "reference", "description": "지역 코드·좌표 변환"},
            {"name": "ops", "description": "원천 상태와 데이터 품질"},
        ],
    )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "name": TITLE,
            "version": "0.1.0",
            "docs": "/docs",
            "openapi": "/openapi.json",
            "read_only": True,
            "note": "이 API는 조회만 제공합니다. 전화·대피명령·상태변경 기능이 없습니다.",
        }

    @app.get("/v1/health", tags=["ops"], summary="원천 상태와 인증 정보 현황")
    async def health() -> dict[str, Any]:
        """어떤 데이터 원천이 지금 쓸 수 있는지, 못 쓰면 왜인지.

        키가 없거나 심의 대기 중인 상태는 오류가 아니라 정상적인 운영 상태로
        보고됩니다.
        """
        return resolved.data_health()

    @app.get("/v1/datasets", tags=["catalog"], summary="데이터셋 검색")
    async def search_datasets(
        q: Annotated[str, Query(description="검색어 (예: 산사태 대피소)")] = "",
        hazard: Annotated[
            str | None, Query(description="재난 유형 (heavy_rain, landslide, wildfire ...)")
        ] = None,
        dev_ready_only: Annotated[
            bool, Query(description="개발계정으로 지금 착수 가능한 것만")
        ] = False,
        usable_only: Annotated[
            bool, Query(description="빈 등록물·기계판독 불가를 제외")
        ] = True,
        must_allow: Annotated[
            Literal["read", "derive", "redistribute", "commercial"] | None,
            Query(description="이 연산이 라이선스상 허용되는 것만 (derive = 재투영·클리핑 등)"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """자연어로 데이터셋을 찾는다.

        `must_allow=derive`를 주면 변경금지(KOGL 3·4) 데이터가 결과에서 빠집니다.
        재투영이나 파생 지표 생성이 필요한 작업에서 미리 걸러내는 데 씁니다.
        """
        return resolved.search_datasets(
            q,
            hazard=hazard,
            dev_ready_only=dev_ready_only,
            usable_only=usable_only,
            must_allow=must_allow,
            limit=limit,
        )

    @app.get(
        "/v1/datasets/{dataset_id}",
        tags=["catalog"],
        summary="데이터셋 상세 — 취득 방법·라이선스·결함",
    )
    async def describe_dataset(dataset_id: str) -> dict[str, Any]:
        result = resolved.describe_dataset(dataset_id)
        if not result.get("found"):
            raise HTTPException(status_code=404, detail=result)
        return result

    @app.get(
        "/v1/datasets/{dataset_id}/verify",
        tags=["catalog"],
        summary="이 용도로 써도 되는지 판정",
    )
    async def verify_dataset(
        dataset_id: str,
        operation: Annotated[
            Literal["read", "derive", "redistribute", "commercial"],
            Query(description="확인할 연산. derive는 재투영·클리핑·조인·파생라벨을 포함"),
        ] = "read",
    ) -> dict[str, Any]:
        """라이선스와 심의 상태, 데이터 품질을 함께 보고 판정합니다.

        라이선스가 허용해도 개발단계 심의 대기 중이면 `allowed`가 false입니다.
        지금 호출할 수 없기 때문입니다.
        """
        return resolved.verify_dataset(dataset_id, operation).to_dict()

    @app.get("/v1/quality", tags=["ops"], summary="검증으로 확인된 데이터 품질 결함")
    async def quality_report() -> dict[str, Any]:
        """포털 메타데이터가 틀린 사례 목록.

        행 수 과소 표기, 빈 등록물, 확장자 불일치 등 실제 다운로드·호출로
        확인된 것만 담습니다.
        """
        return resolved.quality_report()

    @app.get("/v1/regions", tags=["reference"], summary="경북 시군 목록")
    async def list_regions() -> dict[str, Any]:
        return {
            "count": len(SIGUNGU),
            "regions": [
                {
                    "code": item.code,
                    "name": item.name,
                    "center": {"lat": item.center.lat, "lon": item.center.lon},
                }
                for item in SIGUNGU.values()
            ],
            "caveat": "대표 좌표는 청사 기준 근사값이며 경계 판정에 쓸 수 없습니다",
        }

    @app.get(
        "/v1/regions/resolve",
        tags=["reference"],
        summary="지역명 → 코드·좌표·기상격자",
    )
    async def resolve_region(
        q: Annotated[str, Query(description="시군명, 시군구 코드, 또는 '문경시 산북면'")],
    ) -> dict[str, Any]:
        """기관마다 지역 식별자가 달라서 필요한 변환입니다.

        기상청은 격자(nx/ny), ASOS는 지점번호, 다른 API는 시군구 코드를 씁니다.
        """
        result = resolved.resolve_region(q)
        if not result.get("found"):
            raise HTTPException(status_code=404, detail=result)
        return result

    @app.get(
        "/v1/hazards/context",
        tags=["hazard"],
        summary="특정 지역의 현재 위험 상황",
        response_model=ApiEnvelope,
    )
    async def hazard_context(
        region: Annotated[str, Query(description="경북 시군 (예: 문경시)")],
        hazard: Annotated[
            str, Query(description="재난 유형 (heavy_rain, landslide, wildfire, flood)")
        ] = "heavy_rain",
    ) -> ApiEnvelope:
        """재난 유형에 맞는 여러 원천을 병렬로 조회해 합칩니다.

        일부 원천이 실패해도 나머지를 돌려주고 `complete=false`로 알립니다.
        """
        answer = await resolved.hazard_context(region, hazard=hazard)
        return envelope(answer, {"region": region, "hazard": hazard})

    @app.get(
        "/v1/sources/{connector}",
        tags=["hazard"],
        summary="원천 하나를 직접 조회",
        response_model=ApiEnvelope,
    )
    async def fetch_source(
        connector: str,
        region: Annotated[str | None, Query(description="경북 시군")] = None,
        rows: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> ApiEnvelope:
        """커넥터 이름으로 특정 원천을 조회합니다.

        사용 가능한 이름은 `/v1/health`의 `connectors[].name`에 있습니다.
        """
        known = resolved.registry.names()
        if connector not in known:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"'{connector}' 커넥터가 없습니다",
                    "available": list(known),
                },
            )

        if connector in REGION_REQUIRED and not region:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": f"'{connector}'는 region 파라미터가 필요합니다",
                    "reason": "기상 격자 좌표를 계산할 지역이 있어야 조회할 수 있습니다",
                    "example": f"/v1/sources/{connector}?region=문경시",
                },
            )

        answer = await resolved.fetch_connector(
            connector, **_connector_kwargs(connector, region, rows)
        )
        return envelope(answer, {"connector": connector, "region": region})

    @app.get("/v1/hazard-types", tags=["reference"], summary="지원하는 재난 유형")
    async def hazard_types() -> dict[str, Any]:
        return {
            "hazards": [
                {
                    "value": hazard.value,
                    "connectors": list(HAZARD_PLAYBOOK.get(hazard, ())),
                }
                for hazard in HazardDomain
            ]
        }

    @app.get("/v1/licenses", tags=["catalog"], summary="라이선스별 허용 연산")
    async def licenses() -> dict[str, Any]:
        """어떤 라이선스에서 무엇이 금지되는지.

        변경금지(KOGL 3·4)가 이 프로젝트에서 특히 중요합니다. 재투영·클리핑·
        래스터화·파생 라벨 생성이 모두 여기 걸립니다.
        """
        return {
            "licenses": [
                {
                    "code": code.value,
                    "summary": terms.summary,
                    "attribution_required": terms.attribution_required,
                    "share_alike": terms.share_alike,
                    "allows": {
                        operation.value: terms.permits(operation) for operation in Operation
                    },
                }
                for code, terms in TERMS.items()
            ]
        }

    return app


app = create_app()

__all__ = ["app", "create_app"]
