"""MCP 도구 정의와 실행.

AI가 자연어 질문으로 경북 재난 공공데이터를 조회하고 **출처를 인용**할 수 있게
한다. 도구는 전부 읽기 전용이며, 등록 시점에 `assert_read_only`로 이름을
검사해 부작용을 암시하는 도구가 들어오는 것을 막는다.

응답 설계에서 중요한 점: 결과를 그냥 JSON으로 던지지 않고 **AI가 인용을 빼먹기
어렵게** 만든다. 각 응답에 `citations`와 `how_to_cite`가 들어가고, 조회 실패나
오래된 자료는 `warnings`로 올라온다. 이 필드들을 무시하려면 의도적으로 무시해야
한다.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import mcp.types as types
from gbsafe_api.envelope import envelope
from gbsafe_api.service import SafeDataService
from gbsafe_core.models import Answer
from gbsafe_core.safety import assert_read_only

#: 모든 응답에 붙는 인용 지침. AI가 답변에 출처를 남기게 만든다.
CITE_INSTRUCTION = (
    "이 결과를 사용해 답변할 때는 citations의 text를 그대로 인용하고, "
    "각 값의 기준 시각을 함께 밝히세요. warnings가 있으면 답변에 반영하세요."
)


@dataclass(frozen=True, slots=True)
class ToolDef:
    """MCP 도구 하나."""

    name: str
    title: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[SafeDataService, dict[str, Any]], Awaitable[dict[str, Any]]]

    def to_mcp(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            title=self.title,
            description=self.description,
            inputSchema=self.schema,
            annotations=types.ToolAnnotations(
                title=self.title,
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )


    def to_openai_function(self) -> dict[str, Any]:
        """OpenAI·Upstage Solar가 받는 function calling 스키마.

        웹 챗봇은 MCP(stdio)를 붙이기 어려워 HTTP로 같은 도구를 노출한다.
        정의를 양쪽에 따로 적으면 한쪽만 고쳐지므로 여기서 함께 만든다.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_REGION_PROP = {
    "type": "string",
    "description": "경북 시군 (예: 문경시, 안동시). 시군구 코드(47280)도 가능",
}
_HAZARD_PROP = {
    "type": "string",
    "description": "재난 유형",
    "enum": ["heavy_rain", "landslide", "wildfire", "flood", "earthquake", "heatwave"],
}


def _answer_payload(answer: Answer[Any], query: dict[str, Any]) -> dict[str, Any]:
    """조회 결과를 AI가 인용하기 쉬운 형태로 변환한다.

    실패와 오래된 자료를 `warnings`로 끌어올려, 결과가 비어 있을 때 그것이
    '해당 없음'인지 '조회 실패'인지 AI가 구별할 수 있게 한다.
    """
    body = envelope(answer, query)
    warnings: list[str] = []

    for degradation in body.degradations:
        prefix = "조회 실패" if degradation.blocks_interpretation else "부분 장애"
        warnings.append(f"[{prefix}] {degradation.dataset_id}: {degradation.detail}")

    stale = [
        record
        for record in body.records
        if not record.freshness.usable_for_decision
    ]
    if stale:
        warnings.append(
            f"{len(stale)}건이 오래된 자료입니다 — 판단 근거로 제시할 때 시점을 함께 밝히세요"
        )
    if "synthetic" in body.modes:
        warnings.append("훈련용 합성 데이터가 포함되어 있습니다 — 실제 상황이 아닙니다")
    if not body.complete:
        warnings.append(
            "일부 원천을 조회하지 못했습니다. 결과가 비어 있어도 '위험 없음'을 의미하지 않습니다"
        )

    failed = [item.connector for item in body.receipts if item.outcome == "failed"]
    if failed:
        warnings.append(
            f"조회하지 못한 원천: {', '.join(failed)} — 이 원천이 다루는 위험은 "
            "확인되지 않았습니다"
        )
    if body.record_count == 0 and not body.absence_confirmed:
        warnings.append(
            "결과가 비어 있지만 원천이 '해당 없음'을 확인해 주지 않았습니다. "
            "위험이 없다고 답하지 마세요."
        )

    return {
        "query": body.query,
        "record_count": body.record_count,
        "complete": body.complete,
        "absence_confirmed": body.absence_confirmed,
        "records": [record.model_dump(mode="json") for record in body.records],
        "citations": [citation.model_dump(mode="json") for citation in body.citations],
        "sources_checked": [receipt.model_dump(mode="json") for receipt in body.receipts],
        "degradations": [item.model_dump(mode="json") for item in body.degradations],
        "caveats": body.caveats,
        "warnings": warnings,
        "how_to_cite": CITE_INSTRUCTION,
    }


async def _search_datasets(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    return service.search_datasets(
        str(args.get("query", "")),
        hazard=args.get("hazard"),
        dev_ready_only=bool(args.get("dev_ready_only", False)),
        usable_only=bool(args.get("usable_only", True)),
        must_allow=args.get("must_allow"),
        limit=int(args.get("limit", 10)),
    )


async def _describe_dataset(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    return service.describe_dataset(str(args["dataset_id"]))


async def _verify_dataset(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    result = service.verify_dataset(
        str(args["dataset_id"]), str(args.get("operation", "read"))
    )
    return result.to_dict()


async def _cite_dataset(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    return service.cite_dataset(str(args["dataset_id"]))


async def _resolve_region(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    return service.resolve_region(str(args["region"]))


async def _hazard_context(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    region = str(args["region"])
    hazard = str(args.get("hazard", "heavy_rain"))
    answer = await service.hazard_context(region, hazard=hazard)
    return _answer_payload(answer, {"region": region, "hazard": hazard})


async def _fetch_source(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    name = str(args["source"])
    kwargs: dict[str, Any] = {}
    if region := args.get("region"):
        if name in ("weather_now", "weather_forecast"):
            kwargs["location"] = str(region)
        elif name == "emergency_beds":
            kwargs["sigungu"] = str(region)
        else:
            kwargs["region"] = str(region)
    if rows := args.get("rows"):
        kwargs["rows"] = int(rows)
    answer = await service.fetch_connector(name, **kwargs)
    return _answer_payload(answer, {"source": name, **kwargs})


async def _data_health(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    return service.data_health()


async def _quality_report(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    return service.quality_report()


async def _population_guidance(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    return service.population_guidance(str(args["purpose"]))


async def _list_sources(service: SafeDataService, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "sources": [
            {
                "name": spec.name,
                "dataset_id": spec.dataset_id,
                "summary": spec.summary,
                "hazards": [hazard.value for hazard in spec.hazards],
                "requires_local_file": spec.requires_local_file,
            }
            for spec in service.registry.all_specs()
        ],
        "note": "사용 가능 여부는 gbsafe_data_health로 확인하세요",
    }


TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="gbsafe_search_datasets",
        title="공공데이터 검색",
        description=(
            "경북 재난대피 관련 공공데이터셋을 검색합니다. 각 결과에 취득 방법, "
            "라이선스, 개발계정 착수 가능 여부, 확인된 결함이 함께 나옵니다.\n\n"
            "must_allow='derive'를 주면 변경금지(KOGL 3·4) 데이터가 제외됩니다 — "
            "재투영·클리핑·파생지표 생성이 필요한 작업에서 사용하세요."
        ),
        schema=_object(
            {
                "query": {"type": "string", "description": "검색어 (예: 산사태 대피소 인구)"},
                "hazard": _HAZARD_PROP,
                "dev_ready_only": {
                    "type": "boolean",
                    "description": "개발계정으로 지금 착수 가능한 것만 (심의 대기 제외)",
                },
                "usable_only": {
                    "type": "boolean",
                    "description": "빈 등록물·기계판독 불가 제외 (기본 true)",
                },
                "must_allow": {
                    "type": "string",
                    "enum": ["read", "derive", "redistribute", "commercial"],
                    "description": "이 연산이 라이선스상 허용되는 데이터만",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            }
        ),
        handler=_search_datasets,
    ),
    ToolDef(
        name="gbsafe_describe_dataset",
        title="데이터셋 상세 정보",
        description=(
            "데이터셋 하나의 제공기관, 갱신주기, 라이선스 조건, 취득 경로, "
            "심의 유형, 확인된 품질 결함을 반환합니다. 인용에 필요한 정보가 모두 있습니다."
        ),
        schema=_object(
            {"dataset_id": {"type": "string", "description": "data.go.kr 데이터셋 ID"}},
            ["dataset_id"],
        ),
        handler=_describe_dataset,
    ),
    ToolDef(
        name="gbsafe_verify_dataset",
        title="사용 가능 여부 검증",
        description=(
            "이 데이터셋을 이 용도로 써도 되는지 판정합니다. 라이선스뿐 아니라 "
            "심의 상태와 데이터 품질을 함께 봅니다 — 라이선스가 허용해도 개발단계 "
            "심의 대기 중이면 지금 호출할 수 없기 때문입니다.\n\n"
            "operation='derive'는 재투영, 클리핑, 래스터화, 조인, 파생 라벨 생성을 뜻합니다."
        ),
        schema=_object(
            {
                "dataset_id": {"type": "string"},
                "operation": {
                    "type": "string",
                    "enum": ["read", "derive", "redistribute", "commercial"],
                    "description": "확인할 연산 (기본 read)",
                },
            },
            ["dataset_id"],
        ),
        handler=_verify_dataset,
    ),
    ToolDef(
        name="gbsafe_cite_dataset",
        title="출처 표기 문구 생성",
        description=(
            "데이터셋의 출처 표기 문구를 만듭니다. 보고서나 발표자료에 인용을 "
            "붙일 때 쓰세요.\n\n"
            "실제 관측값을 인용할 때는 조회 응답의 citations를 쓰는 편이 정확합니다 "
            "— 관측 시각이 포함되기 때문입니다. 이 도구는 데이터셋 자체를 언급할 때 "
            "필요한 표기를 줍니다."
        ),
        schema=_object(
            {"dataset_id": {"type": "string", "description": "data.go.kr 데이터셋 ID"}},
            ["dataset_id"],
        ),
        handler=_cite_dataset,
    ),
    ToolDef(
        name="gbsafe_resolve_region",
        title="지역 코드·좌표 변환",
        description=(
            "경북 시군명을 시군구 코드, 대표 좌표, 기상청 격자(nx/ny), "
            "ASOS 지점번호로 변환합니다. 기관마다 지역 식별자가 달라 조회의 전제 조건입니다.\n\n"
            "2023년 대구로 편입된 군위군처럼 행정구역이 변경된 경우도 알려줍니다."
        ),
        schema=_object({"region": _REGION_PROP}, ["region"]),
        handler=_resolve_region,
    ),
    ToolDef(
        name="gbsafe_hazard_context",
        title="지역 현재 위험 상황",
        description=(
            "특정 지역의 현재 위험 상황을 재난 유형에 맞는 여러 원천에서 모아 옵니다. "
            "기상특보, 실황 강우, 산사태 예보, 산불위험지수 등이 재난 유형에 따라 선택됩니다.\n\n"
            "**complete가 false이면 일부 원천 조회에 실패한 것이고, 결과가 비어 있어도 "
            "'위험 없음'을 의미하지 않습니다.** 사유는 warnings에 있습니다."
        ),
        schema=_object({"region": _REGION_PROP, "hazard": _HAZARD_PROP}, ["region"]),
        handler=_hazard_context,
    ),
    ToolDef(
        name="gbsafe_list_sources",
        title="데이터 원천 목록",
        description=(
            "조회 가능한 데이터 원천(커넥터) 목록과 각각이 다루는 재난 유형을 반환합니다. "
            "gbsafe_fetch_source에 넘길 이름을 여기서 확인하세요. "
            "지금 실제로 사용 가능한지는 gbsafe_data_health로 확인합니다."
        ),
        schema=_object({}),
        handler=_list_sources,
    ),
    ToolDef(
        name="gbsafe_fetch_source",
        title="원천 직접 조회",
        description=(
            "특정 데이터 원천을 직접 조회합니다. 사용 가능한 이름은 "
            "gbsafe_list_sources로 확인하세요. 결과에 출처와 신선도가 함께 옵니다."
        ),
        schema=_object(
            {
                "source": {
                    "type": "string",
                    "description": "커넥터 이름 (예: weather_now, wildfire_risk)",
                },
                "region": _REGION_PROP,
                "rows": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            ["source"],
        ),
        handler=_fetch_source,
    ),
    ToolDef(
        name="gbsafe_data_health",
        title="원천 상태 진단",
        description=(
            "각 데이터 원천이 지금 쓸 수 있는지, 못 쓰면 왜인지 보고합니다. "
            "인증키 부재와 심의 대기를 구별해 알려주므로, 특정 데이터가 없는 이유를 "
            "추측하지 않고 확인할 수 있습니다."
        ),
        schema=_object({}),
        handler=_data_health,
    ),
    ToolDef(
        name="gbsafe_quality_report",
        title="데이터 품질 결함",
        description=(
            "실제 다운로드·호출로 확인된 포털 메타데이터 오류 목록입니다. "
            "행 수 과소 표기, 빈 등록물, 확장자 불일치 등이 포함됩니다. "
            "데이터를 신뢰하기 전에 확인하세요."
        ),
        schema=_object({}),
        handler=_quality_report,
    ),
    ToolDef(
        name="gbsafe_population_guidance",
        title="인구 데이터 사용 지침",
        description=(
            "인구 데이터를 특정 목적에 쓸 수 있는지 확인합니다. 집계 통계로 "
            "개인의 장애·질병·이동능력을 추정하려는 시도는 거부됩니다 — "
            "공개 통계는 지역 단위 취약성 지표로만 사용할 수 있습니다."
        ),
        schema=_object(
            {
                "purpose": {
                    "type": "string",
                    "description": "인구 데이터를 어떤 목적으로 쓰려는지 서술",
                }
            },
            ["purpose"],
        ),
        handler=_population_guidance,
    ),
)


def validated_tools() -> tuple[ToolDef, ...]:
    """도구 목록을 반환하기 전에 읽기 전용인지 검사한다.

    부작용을 암시하는 이름(call, dispatch, approve 등)이 들어오면 서버가
    기동하지 않는다. 공공데이터 인프라가 외부에 영향을 주는 행위를 노출하는
    실수를 구조적으로 막는다.
    """
    for tool in TOOLS:
        assert_read_only(tool.name.removeprefix("gbsafe_"))
    return TOOLS


_BY_NAME = {tool.name: tool for tool in TOOLS}


def find_tool(name: str) -> ToolDef | None:
    return _BY_NAME.get(name)


async def execute(service: SafeDataService, name: str, arguments: dict[str, Any]) -> str:
    """도구를 실행해 JSON 문자열을 반환한다.

    예외를 밖으로 던지지 않는다. AI가 오류를 읽고 다음 행동을 결정할 수 있도록
    구조화된 오류 응답으로 바꾼다.
    """
    tool = find_tool(name)
    if tool is None:
        return json.dumps(
            {
                "error": f"'{name}' 도구가 없습니다",
                "available": sorted(_BY_NAME),
            },
            ensure_ascii=False,
        )

    try:
        result = await tool.handler(service, arguments or {})
    except KeyError as error:
        result = {
            "error": f"필수 인자가 없습니다: {error}",
            "required": tool.schema.get("required", []),
        }
    except (ValueError, TypeError) as error:
        result = {"error": f"인자가 올바르지 않습니다: {error}"}
    except Exception as error:
        result = {
            "error": f"도구 실행 중 오류: {type(error).__name__}: {error}",
            "note": "이 오류는 데이터 부재가 아니라 시스템 문제입니다",
        }

    return json.dumps(result, ensure_ascii=False, indent=1, default=str)


__all__ = [
    "CITE_INSTRUCTION",
    "TOOLS",
    "ToolDef",
    "execute",
    "find_tool",
    "validated_tools",
]
