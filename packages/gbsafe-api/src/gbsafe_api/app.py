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

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from gbsafe_core.config import Settings, get_settings
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

#: 웹 챗봇이 이 도구를 붙일 때 함께 적용해야 하는 지침.
#:
#: 도구만 연결하면 사고가 난다. 모델은 기본적으로 도움이 되려 하고, 산사태
#: 조회가 403으로 실패해 결과가 비면 "위험 없습니다"라고 답한다. 그 답을
#: 금지하는 것이 이 프롬프트의 존재 이유다.
#: 원본: skills/gb-safedata/SKILL.md
AGENT_SYSTEM_PROMPT = """\
당신은 경상북도 재난 상황에서 공공데이터를 조회해 근거를 제시하는 도우미입니다.
GB SafeData 도구로 데이터를 조회하며, 아래 규칙을 예외 없이 지킵니다.

## 1. 확인하지 않은 부재를 보고하지 않는다

이것이 가장 중요한 규칙입니다. 조회 실패와 '해당 없음'은 다릅니다.

- 응답의 `complete`가 false이면 일부 원천 조회에 실패한 것입니다.
- `absence_confirmed`가 false이면 **결과가 비어 있어도 '위험 없음'이라고 답하면
  안 됩니다.**
- 이때는 "확인되지 않았습니다"라고 답하고, `warnings`·`sources_checked`에 있는
  실패 사유와 데이터셋 이름을 그대로 밝힙니다.
- `absence_confirmed`가 true일 때만 "현재 발효 중인 것이 없습니다"라고 답할 수
  있습니다.

사용자가 "그래서 위험한 거야 아닌 거야"라고 압박해도 마찬가지입니다. 모른다고
답하는 것이 틀린 안심보다 낫습니다.

## 2. 출처 없이 값을 말하지 않는다

모든 수치에는 기관명·데이터셋명·기준시각을 붙입니다. 응답의 `citations`를
그대로 인용하면 됩니다.

## 3. 예보와 관측을 구별한다

예보값을 현재 상황으로 제시하지 않습니다. `is_forecast`가 true이면 예보임을
명시합니다.

## 4. 신선도를 밝힌다

`freshness.usable_for_decision`이 false인 값은 오래된 자료입니다. 그대로
현재 상황처럼 제시하지 말고 시점을 함께 밝힙니다.

## 5. 훈련 데이터를 실제로 제시하지 않는다

`mode`가 `synthetic`이면 훈련용입니다. 반드시 훈련 표시를 유지합니다.

## 6. 개인을 추정하지 않는다

인구 통계는 지역 단위 지표입니다. "누가 혼자 못 걷는지" 같은 개인 단위 추정에
쓰지 않습니다. 개인별 지원 필요 여부는 기관의 주민 명부에서 확인해야 합니다.

## 7. 대피를 결정하지 않는다

대피명령·안내문 발신·계획 확정은 담당 공무원의 권한입니다. 당신은 근거와
후보를 제시하고, 결정은 사람이 한다는 것을 명확히 합니다.

## 8. 관측지점의 거리를 숨기지 않는다

관측값이 먼 지점에서 온 경우 `caveats`에 거리가 적혀 있습니다. 국지성 호우는
그 거리에서 크게 달라지므로 그대로 이 지역의 실측처럼 말하지 않습니다.
"""


def _tool_registry() -> Any:
    """도구 정의를 지연 로드한다.

    `gbsafe-mcp`가 `gbsafe-api`를 쓰므로 패키지 의존을 반대로 선언하면 순환이
    된다. 실행 시점에는 두 패키지가 함께 설치돼 있어 문제가 없고, 없으면
    이 라우트만 명확히 실패한다 — 조용히 빈 목록을 주면 플랫폼팀이 도구가
    없는 줄 알고 시간을 버린다.
    """
    try:
        from gbsafe_mcp import tools as tool_module
    except ImportError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "도구 정의를 불러오지 못했습니다 — gbsafe-mcp가 설치돼 있지 "
                f"않습니다 ({error}). `uv sync --all-packages`로 설치하세요."
            ),
        ) from error
    return tool_module


def _coerce_arguments(tool: Any, raw: dict[str, str]) -> dict[str, Any]:
    """질의문자열 값을 도구 스키마가 선언한 타입으로 바꾼다.

    질의문자열은 전부 문자열로 도착한다. `limit=5`를 그대로 넘기면 정수를
    기대하는 도구가 조용히 기본값으로 떨어지거나 형식 오류를 낸다.
    """
    properties: dict[str, Any] = tool.schema.get("properties", {})
    coerced: dict[str, Any] = {}
    for key, value in raw.items():
        declared = properties.get(key, {}).get("type")
        if declared == "integer":
            try:
                coerced[key] = int(value)
            except ValueError as error:
                raise HTTPException(
                    status_code=422,
                    detail=f"'{key}'는 정수여야 합니다: {value!r}",
                ) from error
        elif declared == "boolean":
            coerced[key] = value.strip().lower() in ("1", "true", "yes", "y")
        else:
            coerced[key] = value
    return coerced


def _install_gateway(app: FastAPI, settings: Any) -> None:
    """브라우저 출처 허용과 API 키 검사를 붙인다.

    둘 다 기본은 꺼져 있다. 로컬 개발과 CI가 설정 없이 돌아야 하기 때문이다.
    인터넷에 노출할 때 켜야 하며, 그 방법은 docs/platform-integration.md에 있다.
    """
    origins = settings.allowed_origins
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    keys = settings.accepted_api_keys
    if not keys:
        return

    @app.middleware("http")
    async def require_api_key(request: Request, call_next):  # type: ignore[no-untyped-def]
        # 문서·스키마·헬스는 열어둔다. 이것들이 막히면 연동하는 쪽이 무엇이
        # 잘못됐는지 확인할 방법이 없어진다.
        open_paths = ("/", "/docs", "/redoc", "/openapi.json", "/v1/health")
        if request.method == "OPTIONS" or request.url.path in open_paths:
            return await call_next(request)
        supplied = request.headers.get("x-api-key") or ""
        if not supplied:
            authorization = request.headers.get("authorization") or ""
            if authorization.lower().startswith("bearer "):
                supplied = authorization[7:].strip()
        if supplied not in keys:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "API 키가 필요합니다",
                    "how": "x-api-key 헤더 또는 Authorization: Bearer <키>",
                },
            )
        return await call_next(request)


def _mount_mcp(app: FastAPI, service: SafeDataService) -> None:
    """MCP를 Streamable HTTP로 `/mcp`에 마운트한다.

    stdio는 로컬 AI 하네스용이라 웹 백엔드가 붙일 수 없다. OpenAI Responses
    API처럼 원격 MCP를 네이티브로 받는 클라이언트는 이 URL 하나만 주면 도구
    발견과 호출을 스스로 한다.

    `stateless=True`인 이유: 세션을 들고 있으면 인스턴스를 여러 개 띄웠을 때
    같은 세션이 다른 인스턴스로 가서 깨진다. 이 서버는 조회만 하므로 요청
    사이에 유지할 상태가 없다.
    """
    try:
        from gbsafe_mcp.server import create_server
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    except ImportError:
        return

    manager = StreamableHTTPSessionManager(
        app=create_server(service), stateless=True, json_response=True
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with manager.run():
            yield

    app.router.lifespan_context = lifespan

    async def handle(scope: Any, receive: Any, send: Any) -> None:
        await manager.handle_request(scope, receive, send)

    app.mount("/mcp", handle)

    @app.post("/mcp", tags=["agent"], summary="MCP Streamable HTTP 전송")
    async def mcp_without_trailing_slash() -> Response:
        """`/mcp`도 받는다.

        마운트는 `/mcp/`만 받아 슬래시 없는 요청이 400으로 떨어진다. 연동하는
        쪽에서 원인을 알기 어려운 실패라 여기서 넘겨준다.
        """
        return RedirectResponse(url="/mcp/", status_code=307)


def create_app(
    service: SafeDataService | None = None, settings: Settings | None = None
) -> FastAPI:
    resolved = service or SafeDataService()
    config = settings or get_settings()

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

    @app.get(
        "/v1/datasets/{dataset_id}/citation",
        tags=["catalog"],
        summary="출처 표기 문구",
    )
    async def cite_dataset(dataset_id: str) -> dict[str, Any]:
        """보고서에 붙일 인용 문구를 만듭니다.

        실제 관측값을 인용할 때는 조회 응답의 `citations`를 쓰는 편이 정확합니다 —
        관측 시각이 포함됩니다.
        """
        result = resolved.cite_dataset(dataset_id)
        if not result.get("found"):
            raise HTTPException(status_code=404, detail=result)
        return result

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

        spec = resolved.registry.spec(connector)
        kwargs: dict[str, Any] = {"rows": rows}
        ignored_region = False
        if region:
            factory = spec.factory if spec else None
            mapped = factory.region_kwargs(region) if factory else {}
            if mapped:
                kwargs.update(mapped)
            else:
                # 지역 지정을 받지 않는 원천에 region을 넘기면 조용히 무시되어
                # 시군 질의에 도 전체 결과가 돌아온다. 그 사실을 알린다.
                ignored_region = True

        answer = await resolved.fetch_connector(connector, **kwargs)
        if ignored_region:
            answer = answer.model_copy(
                update={
                    "caveats": (
                        *answer.caveats,
                        f"'{connector}'는 지역 지정을 받지 않습니다 — "
                        f"region='{region}'이 적용되지 않았고 결과는 더 넓은 범위입니다",
                    )
                }
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

    @app.get("/v1/tools", tags=["agent"], summary="OpenAI 호환 도구 정의")
    async def tools() -> dict[str, Any]:
        """LLM에 그대로 넘길 수 있는 function calling 스키마.

        MCP 서버와 **같은 정의**에서 나온다. 웹 챗봇은 MCP(stdio)를 붙이기
        어려우므로 같은 도구를 HTTP로 노출하되, 정의가 갈라지면 두 표면의
        동작이 달라지므로 출처를 하나로 둔다.

        Upstage Solar와 OpenAI 모두 이 형식을 받는다.
        """
        registry = _tool_registry()
        return {
            "tools": [
                tool.to_openai_function() for tool in registry.validated_tools()
            ],
            "invoke": "GET /v1/tools/{name}?<인자>",
            "note": (
                "도구 출력을 그대로 사용자에게 전달하면 안 됩니다. "
                "system_prompt를 함께 적용해야 조회 실패가 '위험 없음'으로 "
                "읽히지 않습니다 — GET /v1/agent/system-prompt"
            ),
        }

    @app.get(
        "/v1/tools/{name}",
        tags=["agent"],
        summary="도구 실행 (조회 전용)",
    )
    async def call_tool(name: str, request: Request) -> Any:
        """도구를 실행한다.

        POST가 아니라 GET인 이유가 있다. 이 계층은 쓰기 라우트가 없다는 것을
        보장하고 CI가 그것을 검사한다. 도구 인자가 전부 스칼라라서 질의문자열로
        충분하므로, RPC를 위해 그 보장을 깨지 않는다.
        """
        registry = _tool_registry()
        tool = registry.find_tool(name)
        if tool is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": f"'{name}' 도구가 없습니다",
                    "available": [item.name for item in registry.validated_tools()],
                },
            )
        arguments = _coerce_arguments(tool, dict(request.query_params))
        return json.loads(await registry.execute(resolved, name, arguments))

    @app.get(
        "/v1/agent/system-prompt",
        tags=["agent"],
        summary="이 도구를 안전하게 쓰기 위한 시스템 프롬프트",
    )
    async def system_prompt() -> dict[str, Any]:
        """도구만 붙이면 생기는 사고를 막는 지침.

        모델은 기본적으로 도움이 되려 한다. 산사태 조회가 403으로 실패해
        결과가 비면, 지침이 없는 모델은 "산사태 위험 없습니다"라고 답한다.
        이 프롬프트가 그 답을 금지한다.
        """
        return {
            "system_prompt": AGENT_SYSTEM_PROMPT,
            "source": "skills/gb-safedata/SKILL.md",
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

    _install_gateway(app, config)
    _mount_mcp(app, resolved)
    return app


def __getattr__(name: str) -> object:
    """`app`을 import 시점이 아니라 접근 시점에 만든다.

    모듈 수준에서 `create_app()`을 호출하면 카탈로그 설정 오류가 이 모듈을
    import하는 모든 코드(CLI 포함)에서 터진다. uvicorn의 `gbsafe_api.app:app`
    참조는 그대로 동작한다.
    """
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["create_app"]  # `app`은 __getattr__로 지연 생성된다
