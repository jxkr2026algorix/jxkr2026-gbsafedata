"""기상청 지진·지진해일·태풍 커넥터 테스트."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import pytest
from gbsafe_connectors.base import Connector, FetchOutcome, clear_cache
from gbsafe_connectors.seismic import EarthquakeConnector, TsunamiConnector
from gbsafe_connectors.typhoon import TyphoonConnector
from gbsafe_core.config import Settings
from gbsafe_core.models import SourceOutcome, UpstreamStatus

from tests.conftest import make_response

CONNECTORS: tuple[type[Connector[Any]], ...] = (
    EarthquakeConnector,
    TsunamiConnector,
    TyphoonConnector,
)


def _envelope(items: Any, *, code: str = "00") -> dict[str, Any]:
    return {
        "response": {
            "header": {"resultCode": code, "resultMsg": "NORMAL_SERVICE"},
            "body": {"items": items},
        }
    }


async def _fetch_body(
    connector_type: type[Connector[Any]], settings: Settings, body: Any
) -> FetchOutcome[Any]:
    """실제 소켓 없이 HTTP 경계를 거쳐 조회한다."""
    await clear_cache()

    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})
        return httpx.Response(200, json=body)

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await connector_type(settings=settings, client=client).fetch()
    finally:
        await clear_cache()


class TestKmaWindowContract:
    @pytest.mark.parametrize("connector_type", CONNECTORS)
    def test_uses_capital_service_key(self, connector_type: type[Connector[Any]]) -> None:
        assert connector_type.service_key_param == "ServiceKey"

    @pytest.mark.parametrize("connector_type", CONNECTORS)
    def test_clamps_requested_window_to_three_days(
        self, connector_type: type[Connector[Any]], settings: Settings
    ) -> None:
        params = connector_type(settings=settings).build_params(days=30)
        start = datetime.strptime(params["fromTmFc"], "%Y%m%d")
        end = datetime.strptime(params["toTmFc"], "%Y%m%d")
        assert (end - start).days == 3

    @pytest.mark.parametrize("connector_type", CONNECTORS)
    def test_does_not_claim_an_unusable_region_parameter(
        self, connector_type: type[Connector[Any]]
    ) -> None:
        assert connector_type.region_param is None


class TestOutcomeGuards:
    @pytest.mark.parametrize("connector_type", CONNECTORS)
    async def test_missing_credential_is_failed_not_authorized(
        self, connector_type: type[Connector[Any]], keyless_settings: Settings
    ) -> None:
        outcome = await connector_type(settings=keyless_settings).fetch()
        receipt = outcome.receipt(
            connector=connector_type.__name__, dataset_id=connector_type.dataset_id
        )
        assert receipt.outcome is SourceOutcome.FAILED
        assert receipt.upstream_status is UpstreamStatus.NOT_AUTHORIZED

    @pytest.mark.parametrize("connector_type", CONNECTORS)
    async def test_result_code_03_is_confirmed_empty(
        self, connector_type: type[Connector[Any]], settings: Settings
    ) -> None:
        body = {"response": {"header": {"resultCode": "03", "resultMsg": "NO_DATA"}}}
        outcome = await _fetch_body(connector_type, settings, body)
        receipt = outcome.receipt(
            connector=connector_type.__name__, dataset_id=connector_type.dataset_id
        )
        assert receipt.outcome is SourceOutcome.CONFIRMED_EMPTY

    @pytest.mark.parametrize("connector_type", CONNECTORS)
    @pytest.mark.parametrize("code", ["99", "42"])
    async def test_rejected_or_unknown_result_code_is_failed(
        self, connector_type: type[Connector[Any]], code: str, settings: Settings
    ) -> None:
        outcome = await _fetch_body(connector_type, settings, _envelope([], code=code))
        assert outcome.outcome is SourceOutcome.FAILED
        assert not outcome.confirmed_absence

    @pytest.mark.parametrize("connector_type", CONNECTORS)
    async def test_items_null_is_failed(
        self, connector_type: type[Connector[Any]], settings: Settings
    ) -> None:
        outcome = await _fetch_body(connector_type, settings, _envelope(None))
        assert outcome.outcome is SourceOutcome.FAILED
        assert "null" in outcome.degradations[0].detail

    @pytest.mark.parametrize("connector_type", CONNECTORS)
    async def test_html_error_page_is_failed(
        self, connector_type: type[Connector[Any]], settings: Settings
    ) -> None:
        outcome = await _fetch_body(
            connector_type, settings, "<html><body>upstream overloaded</body></html>"
        )
        assert outcome.outcome is SourceOutcome.FAILED
        assert not outcome.confirmed_absence


class TestEarthquakeConnector:
    def test_parses_and_deduplicates_messages(self, settings: Settings) -> None:
        item = {
            "tmFc": "202608220355",
            "tmSeq": "7",
            "mt": "2.7",
            "loc": "경북 포항시 북구 북쪽 10km 지역",
            "lat": "36.13",
            "lon": "129.36",
            "inT": "III",
        }
        outcome = EarthquakeConnector(settings=settings).parse(
            make_response(_envelope({"item": [item, dict(item)]}))
        )
        assert len(outcome.records) == 1
        assert outcome.records[0].payload.value == 2.7
        assert outcome.records[0].payload.location is not None

    def test_missing_magnitude_and_position_stay_unknown(self, settings: Settings) -> None:
        item = {"tmFc": "202608220355", "tmSeq": "8", "loc": "대한민국"}
        outcome = EarthquakeConnector(settings=settings).parse(
            make_response(_envelope({"item": item}))
        )
        payload = outcome.records[0].payload
        assert payload.value is None
        assert payload.location is None


class TestTsunamiConnector:
    def test_parses_bulletin(self, settings: Settings) -> None:
        item = {
            "tmFc": "202401010100",
            "tmSeq": "2",
            "title": "동해안 지진해일주의보 발표",
            "loc": "경상북도 동해안",
            "lat": "36.05",
            "lon": "129.40",
        }
        outcome = TsunamiConnector(settings=settings).parse(
            make_response(_envelope({"item": [item]}))
        )
        assert outcome.outcome is SourceOutcome.RECORDS
        assert outcome.records[0].payload.area_name == "경상북도 동해안"

    def test_missing_position_is_not_fabricated(self, settings: Settings) -> None:
        item = {"tmFc": "202401010100", "tmSeq": "3", "title": "지진해일 정보"}
        outcome = TsunamiConnector(settings=settings).parse(
            make_response(_envelope({"item": item}))
        )
        assert outcome.records[0].payload.location is None


class TestTyphoonConnector:
    def test_preserves_asymmetric_wind_field(self, settings: Settings) -> None:
        item = {
            "typSeq": "202610",
            "typTm": "202608221200",
            "typLat": "35.8",
            "typLon": "130.1",
            "typ15": "320",
            "typ15ed": "E",
            "typ15er": "450",
            "typ25": "120",
            "typ25ed": "NE",
            "typ25er": "180",
        }
        outcome = TyphoonConnector(settings=settings).parse(
            make_response(_envelope({"item": [item]}))
        )
        record = outcome.records[0]
        assert record.payload.latitude == 35.8
        assert record.payload.longitude == 130.1
        assert any("typ15ed=E" in note and "typ15er=450" in note for note in record.notes)
        assert any("비대칭" in caveat for caveat in outcome.caveats)

    def test_missing_position_is_not_fabricated(self, settings: Settings) -> None:
        item = {"typSeq": "202610", "typTm": "202608221200"}
        outcome = TyphoonConnector(settings=settings).parse(
            make_response(_envelope({"item": item}))
        )
        assert outcome.records[0].payload.latitude is None
        assert outcome.records[0].payload.longitude is None
