"""표준 API와 MCP 서버 테스트.

두 표면이 같은 서비스를 쓰므로 답이 일치해야 한다. 특히 '조회 실패'와
'해당 없음'을 구별해 전달하는지 검증한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from gbsafe_api.app import create_app
from gbsafe_api.envelope import envelope
from gbsafe_api.service import SafeDataService
from gbsafe_connectors.registry import Registry
from gbsafe_core.config import Settings
from gbsafe_core.licensing import Operation
from gbsafe_core.models import Answer, DataMode, Degradation, UpstreamStatus
from gbsafe_mcp.tools import TOOLS, execute, find_tool, validated_tools


@pytest.fixture
def service(settings: Settings) -> SafeDataService:
    return SafeDataService(Registry(settings=settings))


@pytest.fixture
def client(service: SafeDataService) -> TestClient:
    return TestClient(create_app(service))


class TestEnvelope:
    def test_incomplete_when_blocking_degradation(self) -> None:
        answer: Answer[Any] = Answer(
            query="test",
            degradations=(
                Degradation(
                    dataset_id="15074800",
                    status=UpstreamStatus.NOT_AUTHORIZED,
                    detail="심의 대기",
                    occurred_at=datetime.now(UTC),
                ),
            ),
        )
        body = envelope(answer, {"region": "문경시"})
        assert not body.complete
        assert body.record_count == 0
        assert body.degradations[0].blocks_interpretation

    def test_record_carries_license_permissions(self, record_factory: Any) -> None:
        from gbsafe_core.models import LicenseCode

        record = record_factory({"value": 1}, license_code=LicenseCode.KOGL_4)
        body = envelope(Answer(query="t", records=(record,)), {})
        source = body.records[0].source
        assert source.may_redistribute
        assert not source.may_modify
        assert source.attribution is not None

    def test_synthetic_mode_visible(self, record_factory: Any) -> None:
        record = record_factory({"value": 1}, mode=DataMode.SYNTHETIC)
        body = envelope(Answer(query="t", records=(record,)), {})
        assert "synthetic" in body.modes
        assert body.records[0].source.mode == "synthetic"

    def test_fingerprint_present(self, record_factory: Any) -> None:
        record = record_factory({"value": 1})
        body = envelope(Answer(query="t", records=(record,)), {})
        assert body.records[0].fingerprint


class TestApiRoutes:
    def test_root_declares_read_only(self, client: TestClient) -> None:
        payload = client.get("/").json()
        assert payload["read_only"] is True

    def test_no_write_methods_exist(self, client: TestClient) -> None:
        """데이터 경로는 부작용이 없어야 한다.

        `/mcp`는 예외다. MCP는 JSON-RPC라 프로토콜 자체가 POST를 쓴다. 그
        POST가 무엇을 할 수 있는지는 HTTP 메서드가 아니라 등록된 도구가
        정한다 — `validated_tools()`가 기동 시점에 전부 조회 전용인지
        검사하고, 아래 테스트가 그것을 다시 확인한다.
        """
        spec = client.get("/openapi.json").json()
        for path, operations in spec["paths"].items():
            if path.startswith("/mcp"):
                continue
            for method in operations:
                assert method.lower() in ("get", "head", "options"), f"{method} {path}"

    def test_the_only_post_route_is_the_mcp_transport(self, client: TestClient) -> None:
        """POST가 늘어나면 그것이 무엇인지 여기서 드러나야 한다."""
        spec = client.get("/openapi.json").json()
        posts = {
            f"{method.upper()} {path}"
            for path, operations in spec["paths"].items()
            for method in operations
            if method.lower() not in ("get", "head", "options")
        }
        assert posts <= {"POST /mcp"}, f"예상하지 못한 쓰기 경로: {sorted(posts)}"

    def test_every_tool_reachable_over_mcp_is_read_only(self) -> None:
        """전송을 열어도 도구 경계는 그대로여야 한다."""
        from gbsafe_core.safety import assert_read_only

        for tool in validated_tools():
            assert_read_only(tool.name)

    def test_health(self, client: TestClient) -> None:
        payload = client.get("/v1/health").json()
        assert payload["summary"]["connectors"] >= 10
        assert "credentials" in payload

    def test_health_gives_reason_for_every_blocked_source(self, client: TestClient) -> None:
        payload = client.get("/v1/health").json()
        for item in payload["connectors"]:
            if not item["available"]:
                assert item["reason"]

    def test_search(self, client: TestClient) -> None:
        payload = client.get("/v1/datasets", params={"q": "산사태", "limit": 5}).json()
        assert payload["count"] > 0
        assert "callable_now" in payload

    def test_search_derive_filter_excludes_kogl4(self, client: TestClient) -> None:
        """변경금지 데이터는 가공 목적 검색에서 빠져야 한다."""
        payload = client.get(
            "/v1/datasets", params={"q": "홍수", "must_allow": "derive", "limit": 50}
        ).json()
        assert all(item["license"] not in ("KOGL-3", "KOGL-4") for item in payload["datasets"])

    def test_search_empty_query_returns_results(self, client: TestClient) -> None:
        payload = client.get("/v1/datasets", params={"limit": 5}).json()
        assert payload["count"] > 0

    def test_dataset_detail(self, client: TestClient) -> None:
        payload = client.get("/v1/datasets/15084084").json()
        assert payload["found"]
        assert payload["how_to_obtain"]
        assert "license_terms" in payload

    def test_unknown_dataset_suggests(self, client: TestClient) -> None:
        response = client.get("/v1/datasets/99999999")
        assert response.status_code == 404
        assert "suggestions" in response.json()["detail"]

    def test_verify_read_allowed(self, client: TestClient) -> None:
        payload = client.get("/v1/datasets/15084084/verify").json()
        assert payload["allowed"]

    def test_verify_pending_review_blocked(self, client: TestClient) -> None:
        """라이선스가 허용해도 심의 대기 중이면 쓸 수 없다."""
        payload = client.get("/v1/datasets/15074800/verify").json()
        if not payload["allowed"]:
            assert any("심의" in reason for reason in payload["reasons"])

    def test_regions_list(self, client: TestClient) -> None:
        payload = client.get("/v1/regions").json()
        assert payload["count"] == 22
        assert payload["caveat"]

    def test_resolve_region(self, client: TestClient) -> None:
        payload = client.get("/v1/regions/resolve", params={"q": "문경시"}).json()
        assert payload["code"] == "47280"
        assert payload["kma_grid"] == {"nx": 81, "ny": 106}

    def test_resolve_unknown_region_404(self, client: TestClient) -> None:
        response = client.get("/v1/regions/resolve", params={"q": "서울시"})
        assert response.status_code == 404
        assert "available" in response.json()["detail"]

    def test_quality_report(self, client: TestClient) -> None:
        payload = client.get("/v1/quality").json()
        assert payload["count"] > 0

    def test_licenses(self, client: TestClient) -> None:
        payload = client.get("/v1/licenses").json()
        kogl4 = next(item for item in payload["licenses"] if item["code"] == "KOGL-4")
        assert kogl4["allows"]["read"]
        assert not kogl4["allows"]["derive"]

    def test_hazard_types(self, client: TestClient) -> None:
        payload = client.get("/v1/hazard-types").json()
        assert any(item["value"] == "landslide" for item in payload["hazards"])

    def test_unknown_source_404_lists_available(self, client: TestClient) -> None:
        response = client.get("/v1/sources/nonexistent")
        assert response.status_code == 404
        assert response.json()["detail"]["available"]

    def test_hazard_context_without_key_is_incomplete(self, client: TestClient) -> None:
        """키가 없으면 실패 사유가 응답에 남아야 한다."""
        payload = client.get(
            "/v1/hazards/context", params={"region": "문경시", "hazard": "landslide"}
        ).json()
        assert payload["record_count"] == 0
        assert not payload["complete"]
        assert payload["degradations"]

    def test_ignored_region_is_disclosed(self, client: TestClient) -> None:
        """지역 지정을 받지 않는 원천에 region을 넘기면 결과가 더 넓다.

        조용히 무시하면 시군 질의에 도 전체 결과가 돌아오는데 호출자는 알 수 없다.
        """
        payload = client.get(
            "/v1/sources/wildfire_risk", params={"region": "문경시"}
        ).json()
        assert any("적용되지 않았" in caveat for caveat in payload["caveats"])

    def test_applied_region_is_not_flagged(self, client: TestClient) -> None:
        payload = client.get(
            "/v1/sources/weather_now", params={"region": "문경시"}
        ).json()
        assert not any("적용되지 않았" in caveat for caveat in payload["caveats"])

    def test_unknown_hazard_type_is_refused(self, client: TestClient) -> None:
        """알 수 없는 재난 유형을 heavy_rain으로 바꾸면 다른 질문에 답한다."""
        payload = client.get(
            "/v1/hazards/context", params={"region": "문경시", "hazard": "bogus"}
        ).json()
        assert not payload["complete"]
        assert any("해석할 수 없습니다" in item["detail"] for item in payload["degradations"])

    def test_receipts_distinguish_failure_from_absence(self, client: TestClient) -> None:
        payload = client.get(
            "/v1/hazards/context", params={"region": "문경시", "hazard": "landslide"}
        ).json()
        assert payload["receipts"]
        assert "absence_confirmed" in payload
        for receipt in payload["receipts"]:
            assert receipt["outcome"] in ("records", "confirmed_empty", "failed")

    def test_hazard_context_unknown_region(self, client: TestClient) -> None:
        payload = client.get(
            "/v1/hazards/context", params={"region": "서울시"}
        ).json()
        assert not payload["complete"]
        assert any("해석할 수 없습니다" in item["detail"] for item in payload["degradations"])

    def test_missing_required_region_is_422_not_500(self, client: TestClient) -> None:
        """스택 트레이스를 사용자에게 보여주면 안 된다."""
        response = client.get("/v1/sources/weather_now", params={"rows": 10})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "region" in detail["message"]
        assert detail["example"]

    def test_no_route_returns_500_on_bad_input(self, client: TestClient) -> None:
        """어떤 입력도 500을 만들면 안 된다."""
        probes = [
            ("/v1/sources/weather_now", {}),
            ("/v1/sources/weather_forecast", {"rows": 1}),
            ("/v1/sources/emergency_beds", {}),
            ("/v1/sources/shelters", {}),
            ("/v1/hazards/context", {"region": "", "hazard": "landslide"}),
            ("/v1/hazards/context", {"region": "../../etc/passwd"}),
            ("/v1/datasets", {"q": "\x00null"}),
            ("/v1/datasets", {"q": "a" * 5000}),
            ("/v1/regions/resolve", {"q": " "}),
        ]
        for path, params in probes:
            response = client.get(path, params=params)
            assert response.status_code != 500, f"{path} {params} -> 500"

    def test_transferred_region_by_name(self, client: TestClient) -> None:
        """군위군은 2023년 대구로 편입됐다. 이름으로 물어도 그 사실을 알려야 한다."""
        for query in ("군위군", "군위", "경상북도 군위군", "47720"):
            response = client.get("/v1/regions/resolve", params={"q": query})
            assert response.status_code == 404
            assert "대구" in response.json()["detail"]["message"], query

    @pytest.mark.parametrize("limit", [0, 101, -1])
    def test_search_rejects_bad_limit(self, client: TestClient, limit: int) -> None:
        assert client.get("/v1/datasets", params={"limit": limit}).status_code == 422

    def test_verify_rejects_bad_operation(self, client: TestClient) -> None:
        response = client.get(
            "/v1/datasets/15084084/verify", params={"operation": "delete"}
        )
        assert response.status_code == 422


class TestService:
    def test_verify_unknown_dataset(self, service: SafeDataService) -> None:
        result = service.verify_dataset("00000000")
        assert not result.allowed

    def test_verify_kogl4_derive_blocked(self, service: SafeDataService) -> None:
        candidates = service.registry.catalog.search("홍수", limit=50)
        kogl4 = [entry for entry in candidates if entry.license.value == "KOGL-4"]
        if kogl4:
            result = service.verify_dataset(kogl4[0].dataset_id, "derive")
            assert not result.allowed
            assert any("변경금지" in reason for reason in result.reasons)

    def test_population_guidance_blocks_individual(self, service: SafeDataService) -> None:
        result = service.population_guidance("주민 개인의 장애 여부를 추정")
        assert not result["allowed"]

    def test_population_guidance_allows_area(self, service: SafeDataService) -> None:
        result = service.population_guidance("마을별 고령인구 비율 산출")
        assert result["allowed"]

    def test_search_flags_pending_review(self, service: SafeDataService) -> None:
        result = service.search_datasets("산사태", limit=20)
        assert result["callable_now"] <= result["count"]

    def test_catalog_source_reported(self, service: SafeDataService) -> None:
        result = service.search_datasets("", limit=1)
        assert result["catalog_source"]

    async def test_fetch_unknown_connector(self, service: SafeDataService) -> None:
        answer = await service.fetch_connector("nope")
        assert not answer.is_complete


class TestMcpTools:
    def test_all_tools_read_only(self) -> None:
        """부작용을 암시하는 도구는 등록될 수 없다."""
        assert validated_tools() == TOOLS

    def test_tool_count(self) -> None:
        assert len(TOOLS) == 12

    def test_schemas_are_strict(self) -> None:
        for tool in TOOLS:
            assert tool.schema["additionalProperties"] is False

    def test_annotations_declare_read_only(self) -> None:
        for tool in TOOLS:
            mcp_tool = tool.to_mcp()
            assert mcp_tool.annotations is not None
            assert mcp_tool.annotations.read_only_hint
            assert not mcp_tool.annotations.destructive_hint

    def test_descriptions_present(self) -> None:
        for tool in TOOLS:
            assert len(tool.description) > 40

    async def test_unknown_tool_lists_alternatives(self, service: SafeDataService) -> None:
        payload = json.loads(await execute(service, "gbsafe_missing", {}))
        assert "error" in payload
        assert payload["available"]

    async def test_missing_required_argument(self, service: SafeDataService) -> None:
        payload = json.loads(await execute(service, "gbsafe_describe_dataset", {}))
        assert "error" in payload
        assert payload["required"] == ["dataset_id"]

    async def test_resolve_region(self, service: SafeDataService) -> None:
        payload = json.loads(
            await execute(service, "gbsafe_resolve_region", {"region": "문경시"})
        )
        assert payload["kma_grid"]["nx"] == 81

    async def test_search_returns_datasets(self, service: SafeDataService) -> None:
        payload = json.loads(
            await execute(service, "gbsafe_search_datasets", {"query": "대피", "limit": 3})
        )
        assert payload["count"] >= 0

    async def test_hazard_context_warns_when_incomplete(
        self, service: SafeDataService
    ) -> None:
        """AI가 조회 실패를 '위험 없음'으로 읽지 않게 경고가 있어야 한다."""
        payload = json.loads(
            await execute(
                service, "gbsafe_hazard_context", {"region": "문경시", "hazard": "landslide"}
            )
        )
        assert not payload["complete"]
        assert any("위험 없음" in warning for warning in payload["warnings"])
        assert payload["how_to_cite"]

    async def test_population_guidance_refuses(self, service: SafeDataService) -> None:
        payload = json.loads(
            await execute(
                service,
                "gbsafe_population_guidance",
                {"purpose": "개별 주민의 보행곤란 여부 추정"},
            )
        )
        assert not payload["allowed"]

    async def test_list_sources(self, service: SafeDataService) -> None:
        payload = json.loads(await execute(service, "gbsafe_list_sources", {}))
        assert len(payload["sources"]) >= 10

    async def test_data_health(self, service: SafeDataService) -> None:
        payload = json.loads(await execute(service, "gbsafe_data_health", {}))
        assert "connectors" in payload

    async def test_quality_report(self, service: SafeDataService) -> None:
        payload = json.loads(await execute(service, "gbsafe_quality_report", {}))
        assert payload["count"] > 0

    async def test_verify_dataset(self, service: SafeDataService) -> None:
        payload = json.loads(
            await execute(
                service,
                "gbsafe_verify_dataset",
                {"dataset_id": "15084084", "operation": "read"},
            )
        )
        assert payload["allowed"]

    async def test_fetch_unknown_source_reports_failure(
        self, service: SafeDataService
    ) -> None:
        payload = json.loads(
            await execute(service, "gbsafe_fetch_source", {"source": "bogus"})
        )
        assert not payload["complete"]
        assert payload["warnings"]

    async def test_bad_argument_type_handled(self, service: SafeDataService) -> None:
        """잘못된 타입이 예외로 터지지 않고 오류 응답이 되어야 한다."""
        payload = json.loads(
            await execute(service, "gbsafe_search_datasets", {"limit": "many"})
        )
        assert "error" in payload

    def test_find_tool(self) -> None:
        assert find_tool("gbsafe_data_health") is not None
        assert find_tool("nope") is None


class TestSurfaceConsistency:
    """API와 MCP가 같은 답을 주어야 한다."""

    async def test_region_resolution_matches(
        self, service: SafeDataService, client: TestClient
    ) -> None:
        api = client.get("/v1/regions/resolve", params={"q": "문경시"}).json()
        mcp = json.loads(
            await execute(service, "gbsafe_resolve_region", {"region": "문경시"})
        )
        assert api["code"] == mcp["code"]
        assert api["kma_grid"] == mcp["kma_grid"]

    async def test_verify_matches(
        self, service: SafeDataService, client: TestClient
    ) -> None:
        api = client.get(
            "/v1/datasets/15084084/verify", params={"operation": "derive"}
        ).json()
        mcp = json.loads(
            await execute(
                service,
                "gbsafe_verify_dataset",
                {"dataset_id": "15084084", "operation": "derive"},
            )
        )
        assert api["allowed"] == mcp["allowed"]

    async def test_absence_verdict_matches(
        self, service: SafeDataService, client: TestClient
    ) -> None:
        """세 표면이 빈 결과의 의미를 다르게 말하면 어느 쪽을 믿어야 할지 알 수 없다."""
        api = client.get(
            "/v1/hazards/context", params={"region": "문경시", "hazard": "landslide"}
        ).json()
        mcp = json.loads(
            await execute(
                service,
                "gbsafe_hazard_context",
                {"region": "문경시", "hazard": "landslide"},
            )
        )
        assert api["complete"] == mcp["complete"]
        assert api["absence_confirmed"] == mcp["absence_confirmed"]
        assert api["record_count"] == mcp["record_count"]
        api_failed = sorted(
            item["connector"] for item in api["receipts"] if item["outcome"] == "failed"
        )
        mcp_failed = sorted(
            item["connector"]
            for item in mcp["sources_checked"]
            if item["outcome"] == "failed"
        )
        assert api_failed == mcp_failed

    def test_license_table_matches_core(self, client: TestClient) -> None:
        payload = client.get("/v1/licenses").json()
        for item in payload["licenses"]:
            from gbsafe_core.licensing import permits
            from gbsafe_core.models import LicenseCode

            code = LicenseCode(item["code"])
            for operation in Operation:
                assert item["allows"][operation.value] == permits(code, operation)


class TestEnforcedGuards:
    """문서가 '강제된다'고 말하는 것은 실제 호출 경로에 있어야 한다."""

    def test_envelope_blocks_mixed_modes(self, record_factory: Any) -> None:
        """훈련 합성데이터가 실데이터와 함께 나가면 실제 상황으로 읽힌다."""
        from gbsafe_core.safety import SafetyViolation

        real = record_factory({"v": 1}, mode=DataMode.REAL)
        synthetic = record_factory({"v": 2}, mode=DataMode.SYNTHETIC)
        with pytest.raises(SafetyViolation, match="mode_isolation"):
            envelope(Answer(query="t", records=(real, synthetic)), {})

    def test_envelope_allows_single_mode(self, record_factory: Any) -> None:
        real = record_factory({"v": 1}, mode=DataMode.REAL)
        assert envelope(Answer(query="t", records=(real,)), {}).record_count == 1

    def test_shelter_caveats_flag_hazard_mismatch(self, service: SafeDataService) -> None:
        """지진 대피소를 호우 대피소로 쓰는 것을 막는다."""
        csv = (
            "시설명,위도,경도,대피소구분\n"
            "지진옥외대피장소,36.59,128.19,지진 옥외\n"
        ).encode()
        answer = service.normalize_csv("shelters", csv, hazard="heavy_rain")
        joined = " ".join(answer.caveats)
        assert "확인되지 않았습니다" in joined

    def test_shelter_caveats_report_unknown_state(self, service: SafeDataService) -> None:
        csv = "시설명,위도,경도\n산북면회관,36.68,128.25\n".encode()
        answer = service.normalize_csv("shelters", csv, hazard="heavy_rain")
        joined = " ".join(answer.caveats)
        assert "운영 여부" in joined or "자동 배정 대상이 아닙니다" in joined


class TestCitationCommand:
    """데이터셋을 언급하는 것만으로도 출처표시 의무가 생긴다."""

    def test_cite_returns_attribution(self, service: SafeDataService) -> None:
        result = service.cite_dataset("15084084")
        assert result["found"]
        assert "기상청" in result["text"]
        assert result["attribution"]
        assert result["attribution_required"]

    def test_cite_reports_modification_ban(self, service: SafeDataService) -> None:
        """KOGL-3은 변경금지이므로 인용 문구에 드러나야 한다."""
        result = service.cite_dataset("15073861")
        assert "변경금지" in result["license_summary"]

    def test_cite_unknown_dataset(self, service: SafeDataService) -> None:
        result = service.cite_dataset("99999999")
        assert not result["found"]

    def test_cite_endpoint(self, client: TestClient) -> None:
        payload = client.get("/v1/datasets/15084084/citation").json()
        assert payload["text"]
        assert payload["caveat"]

    def test_cite_endpoint_404(self, client: TestClient) -> None:
        assert client.get("/v1/datasets/99999999/citation").status_code == 404

    async def test_cite_tool(self, service: SafeDataService) -> None:
        payload = json.loads(
            await execute(service, "gbsafe_cite_dataset", {"dataset_id": "15084084"})
        )
        assert payload["found"]
        assert payload["text"]

    def test_tool_count_includes_cite(self) -> None:
        assert len(TOOLS) == 12


class TestSearchDisclosesPendingReview:
    """라이선스가 허용해도 심의 대기 중이면 지금 호출할 수 없다."""

    def test_pending_review_datasets_are_counted(self, service: SafeDataService) -> None:
        result = service.search_datasets("산사태", limit=20)
        assert result["callable_now"] <= result["count"]
        blocked = result["count"] - result["callable_now"]
        if blocked:
            assert result["notes"], "심의 대기가 있는데 알리지 않습니다"
            joined = " ".join(result["notes"])
            assert "심의" in joined

    def test_notes_name_the_blocked_datasets(self, service: SafeDataService) -> None:
        """어느 데이터셋이 막혔는지 알려주지 않으면 사용자가 찾아야 한다."""
        result = service.search_datasets("산사태", limit=20)
        blocked = [
            entry
            for entry in service.registry.catalog.search("산사태", limit=20)
            if not entry.dev_ready
        ]
        if blocked:
            joined = " ".join(result["notes"])
            assert blocked[0].dataset_id in joined

    def test_ready_filter_excludes_them(self, service: SafeDataService) -> None:
        result = service.search_datasets("산사태", dev_ready_only=True, limit=20)
        assert result["callable_now"] == result["count"]

    def test_api_exposes_callable_now(self, client: TestClient) -> None:
        payload = client.get(
            "/v1/datasets", params={"q": "산사태", "limit": 20}
        ).json()
        assert "callable_now" in payload
        assert payload["callable_now"] <= payload["count"]


class TestReadmeToolNamesAreReal:
    """README에 적힌 도구 이름을 그대로 불렀을 때 존재해야 한다.

    등록되는 이름에는 `gbsafe_` 접두어가 붙는다. README가 접두어 없이 싣고
    있어서, 문서를 보고 그대로 호출하면 "그런 도구 없음"이 돌아왔다. 실제로
    이 저장소를 점검하다가 그 함정에 빠졌다.
    """

    @pytest.mark.parametrize("readme", ["README.md", "README.ko.md"])
    def test_every_tool_name_in_the_readme_is_registered(self, readme: str) -> None:
        import re
        from pathlib import Path

        registered = {tool.name for tool in validated_tools()}
        text = Path(__file__).resolve().parents[1].joinpath(readme).read_text(
            encoding="utf-8"
        )
        # 도구 목록 줄만 본다 — 산문에 나오는 백틱 표현까지 강제하지 않는다.
        listed = {
            name
            for line in text.splitlines()
            if line.count("` · `") >= 3
            for name in re.findall(r"`([a-z_]+)`", line)
        }
        assert listed, f"{readme}에서 도구 목록 줄을 찾지 못했습니다"
        unknown = sorted(listed - registered)
        assert not unknown, (
            f"{readme}가 등록되지 않은 도구 이름을 싣고 있습니다: {unknown}. "
            f"등록된 이름: {sorted(registered)}"
        )

    def test_the_readme_lists_every_registered_tool(self) -> None:
        import re
        from pathlib import Path

        registered = {tool.name for tool in validated_tools()}
        text = Path(__file__).resolve().parents[1].joinpath("README.md").read_text(
            encoding="utf-8"
        )
        listed = {
            name
            for line in text.splitlines()
            if line.count("` · `") >= 3
            for name in re.findall(r"`([a-z_]+)`", line)
        }
        missing = sorted(registered - listed)
        assert not missing, f"README.md에 빠진 도구: {missing}"


class TestFileDataSourcesDiagnoseCorrectly:
    """파일데이터 원천은 '파일이 필요하다'고 말해야 한다.

    `shelters`와 `landslide_zones`는 호출할 엔드포인트가 없다. 예전에는 fetch가
    포털 기본 주소로 요청을 보내 `not_authorized`를 받았고, 파일이 필요한 상황이
    인증 문제로 보고됐다. `doctor`는 올바르게 안내하는데 `fetch`만 어긋나서,
    사용자는 인증키를 다시 발급받으러 가고 문제는 그대로 남는다.
    """

    @pytest.mark.parametrize("name", ["shelters", "landslide_zones"])
    async def test_fetch_names_the_file_requirement(
        self, service: SafeDataService, name: str
    ) -> None:
        answer = await service.fetch_connector(name)
        assert not answer.is_complete
        assert answer.degradations, "장애가 보고되지 않았습니다"
        detail = " ".join(item.detail for item in answer.degradations)
        assert "normalize-csv" in detail, detail
        assert "인증키 문제가 아닙니다" in detail, detail
        assert all(
            item.status is not UpstreamStatus.NOT_AUTHORIZED
            for item in answer.degradations
        ), "파일이 필요한 상황을 인증 실패로 보고합니다"

    @pytest.mark.parametrize("name", ["shelters", "landslide_zones"])
    async def test_fetch_makes_no_network_call(
        self, service: SafeDataService, name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """엔드포인트가 없는 원천에 요청을 보내면 안 된다."""
        import httpx

        async def explode(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("파일데이터 원천이 네트워크를 호출했습니다")

        monkeypatch.setattr(httpx.AsyncClient, "request", explode)
        answer = await service.fetch_connector(name)
        assert not answer.is_complete


class TestGatewayIsOffByDefaultAndRealWhenOn:
    """인증과 CORS는 기본이 꺼져 있고, 켜면 실제로 막아야 한다.

    기본을 열어두는 것은 로컬·CI가 설정 없이 돌기 위해서다. 다만 이 API는
    정부 인증키로 원천을 부르므로, 켰는데 실제로 막히지 않으면 우리 호출
    한도를 남이 소진한다.
    """

    def _client(self, **overrides: object) -> TestClient:
        from gbsafe_api.app import create_app
        from gbsafe_core.config import Settings
        from pydantic_settings import SettingsConfigDict

        class _NoDotenv(Settings):
            model_config = SettingsConfigDict(
                env_prefix="GBSAFE_", env_file=None, extra="ignore", frozen=True
            )

        return TestClient(create_app(settings=_NoDotenv(**overrides)))  # type: ignore[arg-type]

    def test_open_when_no_keys_configured(self) -> None:
        assert self._client().get("/v1/regions").status_code == 200

    def test_rejects_a_request_without_a_key(self) -> None:
        assert self._client(api_keys="k1").get("/v1/regions").status_code == 401

    def test_rejects_a_wrong_key(self) -> None:
        client = self._client(api_keys="k1")
        assert client.get("/v1/regions", headers={"x-api-key": "nope"}).status_code == 401

    @pytest.mark.parametrize(
        "headers",
        [{"x-api-key": "k1"}, {"Authorization": "Bearer k2"}],
    )
    def test_accepts_either_header_style(self, headers: dict[str, str]) -> None:
        client = self._client(api_keys="k1,k2")
        assert client.get("/v1/regions", headers=headers).status_code == 200

    def test_health_stays_open_so_probes_still_work(self) -> None:
        """헬스체크가 막히면 로드밸런서가 정상 인스턴스를 죽인다."""
        client = self._client(api_keys="k1")
        assert client.get("/v1/health").status_code == 200
        assert client.get("/openapi.json").status_code == 200

    def test_mcp_transport_is_gated_too(self) -> None:
        """도구 경로만 열려 있으면 인증이 의미가 없다."""
        client = self._client(api_keys="k1")
        response = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code == 401

    def test_no_cors_headers_without_configuration(self) -> None:
        """기본이 `*`이면 배포하는 순간 아무 사이트나 우리 키를 쓴다."""
        response = self._client().get(
            "/v1/regions", headers={"Origin": "https://evil.example"}
        )
        assert "access-control-allow-origin" not in {
            key.lower() for key in response.headers
        }

    def test_declared_origin_is_allowed(self) -> None:
        client = self._client(cors_allow_origins="https://dashboard.example")
        response = client.get(
            "/v1/regions", headers={"Origin": "https://dashboard.example"}
        )
        assert response.headers.get("access-control-allow-origin") == (
            "https://dashboard.example"
        )

    def test_undeclared_origin_is_not_allowed(self) -> None:
        client = self._client(cors_allow_origins="https://dashboard.example")
        response = client.get("/v1/regions", headers={"Origin": "https://evil.example"})
        assert response.headers.get("access-control-allow-origin") != (
            "https://evil.example"
        )


class TestBothSurfacesNameTheSameThing:
    """REST는 `receipts`, 도구는 `sources_checked`로 부른다.

    이름이 다른 것은 의도했지만, 한쪽만 아는 개발자가 다른 표면에서 헤매면
    영수증을 아예 안 보게 된다. 영수증을 안 보면 실패가 부재로 읽힌다.
    """

    def test_rest_envelope_carries_both_names(self, client: TestClient) -> None:
        payload = client.get(
            "/v1/hazards/context", params={"region": "문경시", "hazard": "landslide"}
        ).json()
        assert "receipts" in payload
        assert "sources_checked" in payload
        assert payload["receipts"] == payload["sources_checked"]

    async def test_tool_surface_uses_sources_checked(
        self, service: SafeDataService
    ) -> None:
        payload = json.loads(
            await execute(
                service, "gbsafe_hazard_context", {"region": "문경시", "hazard": "landslide"}
            )
        )
        assert "sources_checked" in payload


class TestPartialHazardsCannotLookReady:
    """대응 범위의 한계가 답변 자체에 실려야 한다.

    별도 엔드포인트에만 있으면 화면이 그것을 부르지 않고, 지진 답변이
    호우 답변과 똑같이 완전해 보인다.
    """

    async def test_partial_hazard_answer_leads_with_its_limit(
        self, service: SafeDataService
    ) -> None:
        answer = await service.hazard_context("문경시", hazard="earthquake")
        assert answer.caveats, "한계 설명이 없습니다"
        assert "대피소" in answer.caveats[0], answer.caveats[0]

    async def test_blocked_hazard_says_empty_is_not_absence(
        self, service: SafeDataService
    ) -> None:
        answer = await service.hazard_context("문경시", hazard="nuclear")
        assert answer.caveats
        assert "발생하지 않았다" in answer.caveats[0], answer.caveats[0]

    async def test_ready_hazard_does_not_get_a_false_limit(
        self, service: SafeDataService
    ) -> None:
        answer = await service.hazard_context("문경시", hazard="heavy_rain")
        assert not any("부분적으로만" in caveat for caveat in answer.caveats)

    def test_capability_endpoint_reports_every_hazard(self, client: TestClient) -> None:
        payload = client.get("/v1/hazards/capabilities").json()
        assert len(payload["hazards"]) == 13
        assert set(payload["summary"]) == {"ready", "partial", "blocked"}

    def test_capability_endpoint_separates_detect_from_route(
        self, client: TestClient
    ) -> None:
        """지진은 탐지되지만 갈 곳을 모른다. 두 질문이 구별돼야 한다."""
        payload = client.get("/v1/hazards/capabilities").json()
        quake = next(h for h in payload["hazards"] if h["hazard"] == "earthquake")
        assert quake["can_detect"] is True
        assert quake["can_say_where_to_go"] is False
        assert quake["caveat"]

    async def test_mcp_surface_matches_the_endpoint(
        self, service: SafeDataService, client: TestClient
    ) -> None:
        api = client.get("/v1/hazards/capabilities").json()
        mcp = json.loads(await execute(service, "gbsafe_hazard_capabilities", {}))
        assert api["summary"] == mcp["summary"]
        assert len(api["hazards"]) == len(mcp["hazards"])


class TestUndetectableHazardsAreNeverComplete:
    """탐지 수단이 없는 재난은 '확인 완료'가 될 수 없다.

    조회할 원천이 없으면 실패한 영수증도 없어서 `complete`가 true가 되고, 빈
    결과가 확인된 부재로 읽힌다. 원전이 실제로 "완전 · 해당 없음"으로 나왔고,
    그것은 확인할 수단이 없다는 사실의 정반대다.
    """

    async def test_hazard_without_detection_is_incomplete(
        self, service: SafeDataService
    ) -> None:
        answer = await service.hazard_context("문경시", hazard="nuclear")
        assert not answer.is_complete
        assert not answer.absence_is_confirmed

    async def test_it_says_why_rather_than_failing_silently(
        self, service: SafeDataService
    ) -> None:
        answer = await service.hazard_context("문경시", hazard="nuclear")
        detail = " ".join(item.detail for item in answer.degradations)
        assert "탐지 원천이 없습니다" in detail, detail

    async def test_records_do_not_make_it_complete(
        self, service: SafeDataService
    ) -> None:
        """화학사고는 대피장소는 있지만 발생 여부를 모른다.

        대피장소가 돌아왔다고 사고가 없다고 말할 수 있는 것은 아니다.
        """
        answer = await service.hazard_context("문경시", hazard="chemical_accident")
        assert answer.records, "대피장소가 비어 있으면 이 테스트가 무의미하다"
        assert not answer.is_complete
        assert not answer.absence_is_confirmed

    async def test_detectable_hazard_is_not_flagged_undetectable(
        self, service: SafeDataService
    ) -> None:
        """탐지가 되는 재난에까지 이 사유가 붙으면 경고가 의미를 잃는다.

        픽스처는 더미 인증키라 상류 조회 자체는 실패한다. 여기서 보는 것은
        `complete`가 아니라 '탐지 수단 없음'이 잘못 붙지 않는지다.
        """
        answer = await service.hazard_context("문경시", hazard="heavy_rain")
        assert not any(
            "탐지 원천이 없습니다" in item.detail for item in answer.degradations
        )


class TestPartialSourceSelectionIsDisclosed:
    """원천을 골라 조회하면 나머지를 확인하지 않았다는 사실이 남아야 한다.

    `include`로 무관한 커넥터 하나만 넣고 호우를 물으면, 실제로는 지진만 보고
    "호우 확인 완료"가 나왔다. 조회하지 않은 것은 확인한 것이 아니다.
    """

    async def test_unrelated_include_cannot_claim_completeness(
        self, service: SafeDataService
    ) -> None:
        answer = await service.hazard_context(
            "문경시", hazard="heavy_rain", include=("earthquake",)
        )
        assert not answer.is_complete
        assert not answer.absence_is_confirmed

    async def test_it_names_what_was_not_checked(
        self, service: SafeDataService
    ) -> None:
        answer = await service.hazard_context(
            "문경시", hazard="heavy_rain", include=("earthquake",)
        )
        detail = " ".join(item.detail for item in answer.degradations)
        assert "조회하지" in detail, detail
        assert "weather_warning" in detail, detail

    async def test_full_playbook_is_not_flagged(self, service: SafeDataService) -> None:
        """정상 질의에까지 이 사유가 붙으면 경고가 의미를 잃는다."""
        answer = await service.hazard_context("문경시", hazard="heavy_rain")
        assert not any("조회하지" in item.detail for item in answer.degradations)

    async def test_explicit_full_selection_is_not_flagged(
        self, service: SafeDataService
    ) -> None:
        from gbsafe_api.service import HAZARD_PLAYBOOK
        from gbsafe_core.regions import HazardDomain

        answer = await service.hazard_context(
            "문경시",
            hazard="heavy_rain",
            include=HAZARD_PLAYBOOK[HazardDomain.HEAVY_RAIN],
        )
        assert not any("조회하지" in item.detail for item in answer.degradations)


class TestNoSourcesMeansNothingWasChecked:
    """조회한 원천이 없으면 확인한 것이 없다.

    영수증이 비면 "실패한 영수증이 없다"가 되어 complete가 참이 된다. 가뭄은
    탐지 원천이 선언돼 있지 않은데 출처 0건으로 '확인 완료'가 나왔다.
    """

    @pytest.mark.parametrize("hazard", ["drought", "nuclear"])
    async def test_a_hazard_with_no_sources_is_not_complete(
        self, service: SafeDataService, hazard: str
    ) -> None:
        answer = await service.hazard_context("문경시", hazard=hazard)
        assert not answer.receipts
        assert not answer.is_complete
        assert not answer.absence_is_confirmed

    async def test_it_says_no_source_was_queried(
        self, service: SafeDataService
    ) -> None:
        answer = await service.hazard_context("문경시", hazard="drought")
        detail = " ".join(item.detail for item in answer.degradations)
        assert "조회한 원천이 없습니다" in detail, detail


class TestColdWaveIsNotHeatwave:
    """한파를 폭염으로 분류하면 정반대 시설로 보낸다.

    무더위쉼터는 냉방, 한파쉼터는 난방이다.
    """

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("한파주의보", "cold_wave"),
            ("폭염경보", "heatwave"),
            ("지진해일주의보", "tsunami"),
            ("대설경보", "heavy_snow"),
            ("태풍경보", "typhoon"),
        ],
    )
    def test_warning_titles_map_to_their_own_hazard(
        self, title: str, expected: str
    ) -> None:
        from gbsafe_connectors.kma import _hazard_from_title

        assert _hazard_from_title(title).value == expected

    def test_shelter_kind_keywords_separate_hot_from_cold(self) -> None:
        from gbsafe_connectors.filedata import _declared_hazards
        from gbsafe_core.regions import HazardDomain

        assert HazardDomain.COLD_WAVE in _declared_hazards("한파쉼터", "", "")
        assert HazardDomain.HEATWAVE in _declared_hazards("무더위쉼터", "", "")
        assert HazardDomain.HEATWAVE not in _declared_hazards("한파쉼터", "", "")


class TestToolSchemaCoversEveryHazard:
    """도구 스키마가 지원한다고 말하는 것과 실제로 받는 것이 같아야 한다.

    열거값을 손으로 적어 두었더니 재난이 13종으로 늘었을 때 6종에 멈춰 있었고,
    스키마가 태풍·지진해일·한파를 거부했다.
    """

    def test_every_hazard_domain_is_offered(self) -> None:
        from gbsafe_core.regions import HazardDomain

        tool = find_tool("gbsafe_hazard_context")
        assert tool is not None
        offered = set(tool.schema["properties"]["hazard"]["enum"])
        expected = {
            item.value for item in HazardDomain if item is not HazardDomain.OTHER
        }
        assert offered == expected, f"스키마에 빠진 재난: {expected - offered}"

    async def test_every_offered_hazard_is_actually_accepted(
        self, service: SafeDataService
    ) -> None:
        tool = find_tool("gbsafe_hazard_context")
        assert tool is not None
        for hazard in tool.schema["properties"]["hazard"]["enum"]:
            payload = json.loads(
                await execute(
                    service, "gbsafe_hazard_context", {"region": "문경시", "hazard": hazard}
                )
            )
            assert "error" not in payload, f"{hazard}: {payload.get('error')}"


class TestOpenApiSchemaIsUsable:
    """연동하는 쪽이 스키마만 보고 붙일 수 있어야 한다.

    라우트가 `dict[str, Any]`를 돌려주면 OpenAPI에 `{}`만 남고, 상대 팀은
    실제 응답을 눈으로 보고 추측해야 한다. 그러면 우리가 고쳐도 그쪽은
    모른다.
    """

    #: 응답 형태가 본질적으로 고정되지 않는 경로.
    #:
    #: `/mcp`는 JSON-RPC를 통과시키고, `/v1/tools/{name}`은 도구마다 형태가
    #: 다르다. 둘 다 `response_description`으로 읽는 법을 밝힌다.
    POLYMORPHIC: ClassVar[set[str]] = {"/mcp", "/v1/tools/{name}"}

    def _spec(self, client: TestClient) -> dict[str, Any]:
        return client.get("/openapi.json").json()

    def test_every_route_declares_a_response_model(self, client: TestClient) -> None:
        spec = self._spec(client)
        untyped = []
        for path, operations in spec["paths"].items():
            if path in self.POLYMORPHIC:
                continue
            for method, operation in operations.items():
                schema = (
                    operation.get("responses", {})
                    .get("200", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                )
                if not (schema.get("$ref") or schema.get("items") or schema.get("properties")):
                    untyped.append(f"{method.upper()} {path}")
        assert not untyped, f"응답 모델이 없는 경로: {untyped}"

    def test_polymorphic_routes_explain_themselves(self, client: TestClient) -> None:
        spec = self._spec(client)
        for path in self.POLYMORPHIC:
            operations = spec["paths"].get(path, {})
            assert operations, f"{path}가 스키마에 없습니다"
            for operation in operations.values():
                described = any(
                    response.get("description")
                    for response in operation.get("responses", {}).values()
                )
                assert described, f"{path}: 응답 설명이 없습니다"

    def test_error_responses_are_documented(self, client: TestClient) -> None:
        """404·422를 문서화하지 않으면 상대는 200만 처리하는 코드를 짠다."""
        spec = self._spec(client)
        for path in (
            "/v1/datasets/{dataset_id}",
            "/v1/datasets/{dataset_id}/verify",
            "/v1/tools/{name}",
        ):
            codes = set(spec["paths"][path]["get"]["responses"])
            assert "404" in codes, f"{path}: 404 미문서화 ({sorted(codes)})"

    def test_every_tag_has_a_description(self, client: TestClient) -> None:
        spec = self._spec(client)
        described = {tag["name"] for tag in spec.get("tags", []) if tag.get("description")}
        used = {
            tag
            for operations in spec["paths"].values()
            for operation in operations.values()
            for tag in operation.get("tags", [])
        }
        assert used <= described, f"설명 없는 태그: {sorted(used - described)}"

    def test_safety_fields_are_documented_in_the_schema(self, client: TestClient) -> None:
        """`complete`와 `absence_confirmed`는 설명 없이는 오독된다."""
        spec = self._spec(client)
        schemas = spec["components"]["schemas"]
        envelope = next(
            body
            for name, body in schemas.items()
            if "absence_confirmed" in body.get("properties", {})
        )
        for field in ("complete", "absence_confirmed"):
            description = envelope["properties"][field].get("description", "")
            assert description, f"{field}에 설명이 없습니다"


class TestClaudeInstallRedirect:
    """`/add-claude`는 사람이 옮겨 적을 수 있는 주소여야 하고, 실제 링크는
    claude.ai가 이해하는 형태로 나가야 한다."""

    def test_redirects_to_claude_with_name_and_url_prefilled(
        self, client: TestClient
    ) -> None:
        from urllib.parse import parse_qs, urlparse

        from gbsafe_api.app import CONNECTOR_NAME, PUBLIC_MCP_URL

        response = client.get("/add-claude", follow_redirects=False)

        assert response.status_code == 302
        target = urlparse(response.headers["location"])
        assert target.hostname == "claude.ai"

        query = parse_qs(target.query)
        assert query["modal"] == ["add-custom-connector"]
        # 디코딩된 값이 원본과 같아야 한다. 인코딩이 틀리면 Claude가 이름을
        # 깨진 문자열로 받거나 URL을 통째로 잘라 먹는다.
        assert query["connectorName"] == [CONNECTOR_NAME]
        assert query["connectorUrl"] == [PUBLIC_MCP_URL]

    def test_target_url_is_the_deployed_instance_not_localhost(self) -> None:
        from gbsafe_api.app import PUBLIC_MCP_URL

        # 이 링크를 받는 사람은 우리 개발 머신에 접속할 수 없다.
        assert PUBLIC_MCP_URL.startswith("https://")
        assert "localhost" not in PUBLIC_MCP_URL
        assert "127.0.0.1" not in PUBLIC_MCP_URL
        assert PUBLIC_MCP_URL.endswith("/mcp/")
