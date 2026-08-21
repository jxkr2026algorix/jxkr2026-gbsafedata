"""표준 API와 MCP 서버 테스트.

두 표면이 같은 서비스를 쓰므로 답이 일치해야 한다. 특히 '조회 실패'와
'해당 없음'을 구별해 전달하는지 검증한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

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
        """공공데이터 계층은 부작용이 없어야 한다."""
        spec = client.get("/openapi.json").json()
        for path, operations in spec["paths"].items():
            for method in operations:
                assert method.lower() in ("get", "head", "options"), f"{method} {path}"

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
        assert len(TOOLS) == 10

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
