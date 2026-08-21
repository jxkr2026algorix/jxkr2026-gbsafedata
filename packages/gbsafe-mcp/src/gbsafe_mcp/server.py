"""GB SafeData MCP 서버.

AI가 경북 재난 공공데이터를 **출처를 인용하며** 조회하는 읽기 전용 서버다.

전화 발신·대피명령·주민 상태 변경 도구는 존재하지 않는다. 그것은 운영
플랫폼(살길 Ops)의 책임이며, 공공데이터 인프라가 주민에게 직접 명령을 내리는
경로를 만들지 않기 위해 구조적으로 분리한다. `validated_tools()`가 기동 시점에
도구 이름을 검사해 이 경계를 강제한다.
"""

from __future__ import annotations

import mcp.types as types
from gbsafe_api.service import SafeDataService
from mcp.server import ServerRequestContext
from mcp.server.lowlevel import Server

from .tools import execute, validated_tools

SERVER_NAME = "gbsafedata"
INSTRUCTIONS = """
경북(경상북도) 재난대피 공공데이터를 출처와 함께 조회하는 도구입니다.

## 사용 원칙

1. **출처 없이 답하지 마세요.** 모든 응답에 citations가 있습니다. 답변에 기관명,
   데이터셋명, 기준 시각을 밝히세요.

2. **complete가 false면 결과가 불완전합니다.** 일부 원천 조회에 실패한 것이며,
   records가 비어 있어도 '위험 없음'을 의미하지 않습니다. warnings를 읽고
   사용자에게 무엇을 확인하지 못했는지 알리세요.

3. **오래된 자료를 최신처럼 제시하지 마세요.** freshness.usable_for_decision이
   false인 값은 시점을 반드시 함께 밝히세요.

4. **추측으로 빈칸을 채우지 마세요.** 데이터가 없으면 없다고 답하고, 어디서
   확인할 수 있는지 안내하세요(gbsafe_describe_dataset의 how_to_obtain).

5. **집계 통계로 개인을 추정하지 마세요.** 인구 데이터는 지역 단위 취약성
   지표입니다. 특정 주민의 이동능력·장애·질병을 판단하는 데 쓸 수 없습니다.

6. **대피 결정을 내리지 마세요.** 이 도구는 근거와 후보를 제시합니다. 어느 마을을
   먼저 대피시킬지, 어느 대피소로 보낼지는 담당 공무원이 검토·승인합니다.

## 권장 순서

지역 확인(gbsafe_resolve_region) → 위험 상황(gbsafe_hazard_context) →
필요시 개별 원천(gbsafe_fetch_source) → 데이터 신뢰성 확인(gbsafe_quality_report)

특정 데이터가 왜 없는지 알아야 할 때는 gbsafe_data_health를 쓰세요.
인증키 부재와 심의 대기를 구별해 알려줍니다.
""".strip()


def create_server(service: SafeDataService | None = None) -> Server[object]:
    """MCP 서버를 만든다. 도구가 읽기 전용인지 검사한 뒤 등록한다."""
    resolved = service or SafeDataService()
    tools = validated_tools()

    async def on_list_tools(
        context: ServerRequestContext[object],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[tool.to_mcp() for tool in tools])

    async def on_call_tool(
        context: ServerRequestContext[object],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        payload = await execute(resolved, params.name, dict(params.arguments or {}))
        is_error = '"error"' in payload[:200]
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=payload)],
            is_error=is_error,
        )

    return Server(
        SERVER_NAME,
        version="0.1.0",
        title="GB SafeData",
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def run_stdio() -> None:
    """stdio로 서버를 실행한다. MCP 클라이언트가 이 방식으로 연결한다."""
    from mcp.server.stdio import stdio_server

    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


__all__ = ["INSTRUCTIONS", "SERVER_NAME", "create_server", "run_stdio"]
