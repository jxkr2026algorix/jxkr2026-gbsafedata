"""커넥터 계층 테스트.

원천 응답 형태를 고정해 검증한다. 실호출은 개발계정 한도를 소진하고 시점에 따라
결과가 달라져 재현되지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from gbsafe_connectors.base import _classify
from gbsafe_connectors.filedata import (
    LandslideRiskZoneCsvConnector,
    ShelterCsvConnector,
    decode_csv,
    local_response,
    pick,
)
from gbsafe_connectors.forest import WildfireRiskConnector, _fire_severity
from gbsafe_connectors.kma import (
    UltraShortNowcastConnector,
    WeatherWarningConnector,
    _latest_forecast_base,
    _latest_nowcast_base,
    _parse_measure,
)
from gbsafe_connectors.medical import EmergencyBedsConnector, _parse_airkorea_time
from gbsafe_connectors.registry import Registry
from gbsafe_connectors.stations import describe_station, serves_gyeongbuk
from gbsafe_core.config import Settings
from gbsafe_core.domain import AlertAction, Severity
from gbsafe_core.models import QualityFlag, UpstreamStatus
from gbsafe_core.regions import HazardDomain

from tests.conftest import (
    EMERGENCY_XML,
    GATEWAY_AUTH_ERROR,
    KMA_NOWCAST_BODY,
    KMA_WARNING_BODY,
    SHELTER_CSV,
    make_response,
)


class TestClassify:
    """포털은 오류를 HTTP 200 본문에 담아 보낸다."""

    def test_gateway_auth_error_in_200_body(self) -> None:
        status, detail = _classify(GATEWAY_AUTH_ERROR.encode(), 200)
        assert status is UpstreamStatus.NOT_AUTHORIZED
        assert "인증키 미등록" in detail

    def test_normal_json(self) -> None:
        status, _ = _classify(b'{"response":{"header":{"resultCode":"00"}}}', 200)
        assert status is UpstreamStatus.OK

    def test_non_zero_result_code(self) -> None:
        status, detail = _classify(
            b'{"response":{"header":{"resultCode":"99","resultMsg":"NO_DATA"}}}', 200
        )
        assert status is UpstreamStatus.DEGRADED
        assert "99" in detail

    def test_quota_exceeded(self) -> None:
        status, detail = _classify(b"LIMITED_NUMBER_OF_SERVICE", 200)
        assert status is UpstreamStatus.DEGRADED
        assert "허용량" in detail

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (401, UpstreamStatus.NOT_AUTHORIZED),
            (403, UpstreamStatus.NOT_AUTHORIZED),
            (429, UpstreamStatus.DEGRADED),
            (500, UpstreamStatus.UNAVAILABLE),
            (504, UpstreamStatus.UNAVAILABLE),
            (404, UpstreamStatus.UNAVAILABLE),
        ],
    )
    def test_http_status_codes(self, code: int, expected: UpstreamStatus) -> None:
        status, _ = _classify(b"", code)
        assert status is expected


class TestNowcastConnector:
    def test_parses_categories(self, settings: Settings) -> None:
        connector = UltraShortNowcastConnector(settings=settings)
        outcome = connector.parse(make_response(KMA_NOWCAST_BODY), location="문경시")
        kinds = {record.payload.kind for record in outcome.records}
        assert kinds == {"temperature", "rainfall_1h", "precipitation_type"}
        assert all(not record.payload.is_forecast for record in outcome.records)

    def test_observation_carries_provenance(self, settings: Settings) -> None:
        connector = UltraShortNowcastConnector(settings=settings)
        outcome = connector.parse(make_response(KMA_NOWCAST_BODY), location="문경시")
        record = outcome.records[0]
        assert record.provenance.dataset_id == "15084084"
        assert record.provenance.observed_at is not None
        assert record.citation.to_text()

    def test_empty_items_is_healthy_not_error(self, settings: Settings) -> None:
        """자료 없음과 조회 실패를 구별한다."""
        body = {"response": {"header": {"resultCode": "00"}, "body": {"items": ""}}}
        connector = UltraShortNowcastConnector(settings=settings)
        outcome = connector.parse(make_response(body), location="문경시")
        assert outcome.is_empty_but_healthy
        assert outcome.caveats

    def test_unknown_region_raises(self, settings: Settings) -> None:
        connector = UltraShortNowcastConnector(settings=settings)
        with pytest.raises(ValueError, match="경북 시군으로 해석할 수 없습니다"):
            connector.build_params(location="서울시")

    def test_grid_matches_verified_value(self, settings: Settings) -> None:
        connector = UltraShortNowcastConnector(settings=settings)
        params = connector.build_params(location="문경시")
        assert params["nx"] == "81"
        assert params["ny"] == "106"

    @pytest.mark.parametrize(
        ("raw", "value"),
        [
            ("23.4", 23.4),
            ("0", 0.0),
            ("강수없음", 0.0),
            ("적설없음", 0.0),
            ("1mm 미만", 0.5),
            ("5mm", 5.0),
            ("", 0.0),
            ("이상한값", None),
        ],
    )
    def test_parse_measure(self, raw: str, value: float | None) -> None:
        assert _parse_measure(raw) == value


class TestWeatherWarningConnector:
    def test_filters_to_gyeongbuk(self, settings: Settings) -> None:
        """필터하지 않으면 전국 특보가 경북 특보처럼 섞인다."""
        connector = WeatherWarningConnector(settings=settings)
        outcome = connector.parse(make_response(KMA_WARNING_BODY))
        codes = {record.payload.area_code for record in outcome.records}
        assert codes <= {"143", "136", "138"}
        assert "108" not in codes

    def test_excludes_cancellations_by_default(self, settings: Settings) -> None:
        """해제 통보문을 발효 중으로 읽으면 종료된 위험을 현재로 표시한다."""
        connector = WeatherWarningConnector(settings=settings)
        outcome = connector.parse(make_response(KMA_WARNING_BODY))
        actions = {record.payload.action for record in outcome.records}
        assert AlertAction.CANCELLED not in actions
        assert any("해제 통보문" in caveat for caveat in outcome.caveats)

    def test_can_include_cancellations(self, settings: Settings) -> None:
        connector = WeatherWarningConnector(settings=settings)
        with_all = connector.parse(make_response(KMA_WARNING_BODY), active_only=False)
        only_active = connector.parse(make_response(KMA_WARNING_BODY))
        assert len(with_all.records) > len(only_active.records)

    def test_station_names_are_human_readable(self, settings: Settings) -> None:
        connector = WeatherWarningConnector(settings=settings)
        outcome = connector.parse(make_response(KMA_WARNING_BODY))
        names = {record.payload.area_name for record in outcome.records}
        assert any("대구지방기상청" in name for name in names)

    def test_severity_parsed(self, settings: Settings) -> None:
        connector = WeatherWarningConnector(settings=settings)
        outcome = connector.parse(make_response(KMA_WARNING_BODY))
        severities = {record.payload.severity for record in outcome.records}
        assert Severity.WARNING in severities

    def test_no_gyeongbuk_alerts_reports_healthy(self, settings: Settings) -> None:
        body = {
            "response": {
                "header": {"resultCode": "00"},
                "body": {
                    "items": {
                        "item": [
                            {
                                "stnId": "108",
                                "title": "[특보] 호우주의보 발표",
                                "tmFc": 202608220335,
                            }
                        ]
                    }
                },
            }
        }
        connector = WeatherWarningConnector(settings=settings)
        outcome = connector.parse(make_response(body))
        assert outcome.is_empty_but_healthy
        assert any("발효 중인 특보가 없습니다" in caveat for caveat in outcome.caveats)


class TestStations:
    @pytest.mark.parametrize(("code", "expected"), [("143", True), ("136", True), ("108", False)])
    def test_serves_gyeongbuk(self, code: str, expected: bool) -> None:
        assert serves_gyeongbuk(code) is expected

    def test_describe_unknown(self) -> None:
        assert "999" in describe_station("999")

    def test_describe_none(self) -> None:
        assert describe_station(None) == "미확인"


class TestWildfireConnector:
    @pytest.mark.parametrize(
        ("index", "severity"),
        [
            (10.0, Severity.INFO),
            (55.0, Severity.ADVISORY),
            (70.0, Severity.WARNING),
            (90.0, Severity.EMERGENCY),
            (None, Severity.UNKNOWN),
        ],
    )
    def test_severity_bands(self, index: float | None, severity: Severity) -> None:
        assert _fire_severity(index) is severity

    def test_parses_grade_counts(self, settings: Settings) -> None:
        body = {
            "response": {
                "body": {
                    "items": {
                        "item": [
                            {
                                "sido": "경상북도",
                                "meanavg": "45.0",
                                "maxi": "70",
                                "d1": "5",
                                "d3": "2",
                                "analdate": "20260822",
                            }
                        ]
                    }
                }
            }
        }
        connector = WildfireRiskConnector(settings=settings)
        outcome = connector.parse(make_response(body))
        assert len(outcome.records) == 1
        record = outcome.records[0]
        assert record.payload.hazard is HazardDomain.WILDFIRE
        assert record.payload.severity is Severity.WARNING
        assert any("높음" in note for note in record.notes)
        assert any("특정 마을" in caveat for caveat in outcome.caveats)


class TestEmergencyConnector:
    def test_parses_xml(self, settings: Settings) -> None:
        """이 API는 XML만 반환한다."""
        connector = EmergencyBedsConnector(settings=settings)
        outcome = connector.parse(
            make_response(EMERGENCY_XML, content_type="application/xml")
        )
        assert len(outcome.records) == 1
        payload = outcome.records[0].payload
        assert payload.name == "문경제일병원"
        assert payload.emergency_beds == 16
        assert payload.is_realtime

    def test_missing_hvidate_flags_not_realtime(self, settings: Settings) -> None:
        """hvidate가 없으면 실시간으로 표시하면 안 된다."""
        xml = EMERGENCY_XML.replace("<hvidate>20260822033500</hvidate>", "")
        connector = EmergencyBedsConnector(settings=settings)
        outcome = connector.parse(make_response(xml, content_type="application/xml"))
        record = outcome.records[0]
        assert not record.payload.is_realtime
        assert QualityFlag.PARTIAL_RESPONSE in record.quality_flags
        assert any("실시간 값으로 표시할 수 없습니다" in note for note in record.notes)

    def test_negative_bed_count_is_unknown(self, settings: Settings) -> None:
        """원천이 미확인을 -1로 표기하는 경우가 있다."""
        xml = EMERGENCY_XML.replace("<hvec>16</hvec>", "<hvec>-1</hvec>")
        connector = EmergencyBedsConnector(settings=settings)
        outcome = connector.parse(make_response(xml, content_type="application/xml"))
        assert outcome.records[0].payload.emergency_beds is None


class TestAirKoreaTime:
    @pytest.mark.parametrize(
        ("raw", "hour"),
        [("2026-08-22 14:00", 14), ("2026-08-22 01:00", 1)],
    )
    def test_parses_normal(self, raw: str, hour: int) -> None:
        parsed = _parse_airkorea_time(raw)
        assert parsed is not None
        assert parsed.hour == hour

    def test_handles_24_hour_notation(self) -> None:
        """24시 표기는 다음 날 00시다."""
        parsed = _parse_airkorea_time("2026-08-22 24:00")
        assert parsed is not None
        assert parsed.day == 23
        assert parsed.hour == 0

    @pytest.mark.parametrize("raw", ["", "   ", "not-a-date"])
    def test_rejects_invalid(self, raw: str) -> None:
        assert _parse_airkorea_time(raw) is None


class TestCsvDecoding:
    def test_detects_cp949(self) -> None:
        """경북 파일데이터는 CP949다. UTF-8로 읽으면 깨진다."""
        rows, encoding, was_cp949 = decode_csv(SHELTER_CSV.encode("cp949"))
        assert was_cp949
        assert encoding in ("cp949", "euc-kr")
        assert rows[0]["시설명"] == "산북면사무소"

    def test_detects_utf8(self) -> None:
        rows, _encoding, was_cp949 = decode_csv(SHELTER_CSV.encode("utf-8"))
        assert not was_cp949
        assert rows[0]["시설명"] == "산북면사무소"

    def test_empty_body(self) -> None:
        rows, _, _ = decode_csv(b"")
        assert rows == []

    def test_pick_finds_aliases(self) -> None:
        row = {"소재지도로명주소": "경북 문경시", "시설명": "회관"}
        assert pick(row, "address") == "경북 문경시"
        assert pick(row, "name") == "회관"
        assert pick(row, "capacity") is None


class TestShelterCsvConnector:
    def test_normalizes_rows(self, settings: Settings) -> None:
        connector = ShelterCsvConnector(settings=settings)
        outcome = connector.parse(local_response(SHELTER_CSV.encode("cp949")))
        assert len(outcome.records) == 4
        names = {record.payload.name for record in outcome.records}
        assert "산북면사무소" in names

    def test_missing_coordinates_flagged(self, settings: Settings) -> None:
        connector = ShelterCsvConnector(settings=settings)
        outcome = connector.parse(local_response(SHELTER_CSV.encode("cp949")))
        no_coords = [
            record
            for record in outcome.records
            if record.payload.name == "좌표없는대피소"
        ]
        assert no_coords
        assert QualityFlag.MISSING_COORDINATES in no_coords[0].quality_flags
        assert any("좌표가 없어" in caveat for caveat in outcome.caveats)

    def test_earthquake_shelter_not_assigned_to_rain(self, settings: Settings) -> None:
        """원본에 지진으로 명시된 시설이 호우 대피소로 쓰이면 안 된다."""
        connector = ShelterCsvConnector(settings=settings)
        outcome = connector.parse(local_response(SHELTER_CSV.encode("cp949")))
        quake = next(
            record
            for record in outcome.records
            if "지진" in record.payload.name
        )
        assert quake.payload.serves(HazardDomain.EARTHQUAKE)
        assert not quake.payload.serves(HazardDomain.HEAVY_RAIN)

    def test_unnamed_hazard_shelters_serve_nothing(self, settings: Settings) -> None:
        connector = ShelterCsvConnector(settings=settings)
        outcome = connector.parse(local_response(SHELTER_CSV.encode("cp949")))
        plain = next(
            record for record in outcome.records if record.payload.name == "산북면사무소"
        )
        assert plain.payload.supported_hazards == ()
        assert any("자동 배정 대상이 아닙니다" in caveat for caveat in outcome.caveats)

    def test_occupancy_never_fabricated(self, settings: Settings) -> None:
        """확인되지 않은 현재 인원을 0으로 채우지 않는다."""
        connector = ShelterCsvConnector(settings=settings)
        outcome = connector.parse(local_response(SHELTER_CSV.encode("cp949")))
        assert all(record.payload.current_occupancy is None for record in outcome.records)
        assert all(record.payload.operating is None for record in outcome.records)

    def test_region_filter(self, settings: Settings) -> None:
        connector = ShelterCsvConnector(settings=settings)
        outcome = connector.parse(
            local_response(SHELTER_CSV.encode("cp949")), region="산북면"
        )
        assert len(outcome.records) == 1

    def test_out_of_range_coordinates_flagged(self, settings: Settings) -> None:
        """EPSG:5186 값이 위경도 칸에 들어간 경우를 잡는다."""
        csv = (
            "시설명,위도,경도\n"
            "잘못된좌표,445123.5,1050987.2\n"
        )
        connector = ShelterCsvConnector(settings=settings)
        outcome = connector.parse(local_response(csv.encode("utf-8")))
        assert QualityFlag.COORDINATE_OUT_OF_RANGE in outcome.records[0].quality_flags


class TestLandslideZoneCsv:
    def test_normalizes(self, settings: Settings) -> None:
        csv = (
            "위험지구명,소재지,위도,경도,위험등급,세대수,인구수,지정일자\n"
            "가좌리1지구,경상북도 문경시 산북면,36.6800,128.2500,B,12,25,2020-03-01\n"
        )
        connector = LandslideRiskZoneCsvConnector(settings=settings)
        outcome = connector.parse(local_response(csv.encode("utf-8")))
        payload = outcome.records[0].payload
        assert payload.hazard is HazardDomain.LANDSLIDE
        assert payload.households_at_risk == 12
        assert payload.is_locatable
        assert any("현재 발생 여부가 아닙니다" in caveat for caveat in outcome.caveats)


class TestConnectorAvailability:
    async def test_missing_key_yields_not_authorized(
        self, keyless_settings: Settings
    ) -> None:
        """키 부재는 예외가 아니라 정상적인 운영 상태다."""
        connector = UltraShortNowcastConnector(settings=keyless_settings)
        assert not connector.available
        outcome = await connector.fetch(location="문경시")
        assert not outcome.ok
        assert outcome.degradations[0].status is UpstreamStatus.NOT_AUTHORIZED
        assert "발급" in outcome.degradations[0].detail

    async def test_no_records_with_blocking_degradation(
        self, keyless_settings: Settings
    ) -> None:
        connector = UltraShortNowcastConnector(settings=keyless_settings)
        outcome = await connector.fetch(location="문경시")
        assert not outcome.records
        assert not outcome.is_empty_but_healthy


class TestRegistry:
    def test_lists_connectors(self, settings: Settings) -> None:
        registry = Registry(settings=settings)
        assert len(registry.names()) >= 10

    def test_unknown_name_lists_alternatives(self, settings: Settings) -> None:
        registry = Registry(settings=settings)
        with pytest.raises(KeyError, match="사용 가능"):
            registry.create("nonexistent")

    def test_health_reports_reason_for_each_blocked(self, settings: Settings) -> None:
        registry = Registry(settings=settings)
        for item in registry.health():
            if not item.available:
                assert item.reason, f"{item.name}에 사용 불가 사유가 없습니다"

    def test_pending_review_not_reported_available(self, settings: Settings) -> None:
        """키가 있어도 심의 대기 중이면 호출은 403이 된다."""
        registry = Registry(settings=settings)
        landslide = [
            item for item in registry.health() if item.name == "landslide_forecast"
        ]
        assert landslide
        if landslide[0].dev_review_required:
            assert not landslide[0].available
            assert "심의" in (landslide[0].reason or "")

    def test_specs_for_dataset(self, settings: Settings) -> None:
        registry = Registry(settings=settings)
        specs = registry.specs_for_dataset("15084084")
        assert {spec.name for spec in specs} == {"weather_now", "weather_forecast"}

    def test_for_hazard(self, settings: Settings) -> None:
        registry = Registry(settings=settings)
        assert registry.for_hazard(HazardDomain.LANDSLIDE)

    def test_credential_status_includes_source(self, settings: Settings) -> None:
        registry = Registry(settings=settings)
        for info in registry.credential_status().values():
            assert info["source"].startswith("http")


class TestBaseTimes:
    def test_nowcast_base_is_past(self) -> None:
        assert _latest_nowcast_base() <= datetime.now(UTC).astimezone(
            _latest_nowcast_base().tzinfo
        )

    def test_forecast_base_is_valid_hour(self) -> None:
        assert _latest_forecast_base().hour in (2, 5, 8, 11, 14, 17, 20, 23)


class TestConcurrencyAndCache:
    """동시 요청이 호출 한도를 배수로 소진하거나 실패를 성공으로 바꾸면 안 된다."""

    async def test_identical_concurrent_requests_hit_upstream_once(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """20개 동시 요청이 20번 호출하면 AirKorea 일 한도(500)의 4%가 한 번에 사라진다."""
        import asyncio

        from gbsafe_connectors.base import clear_cache

        await clear_cache()
        calls = {"n": 0}

        async def fake_send(self, url, params):
            calls["n"] += 1
            await asyncio.sleep(0.01)
            return make_response(KMA_WARNING_BODY)

        monkeypatch.setattr(WeatherWarningConnector, "_send", fake_send)
        outcomes = await asyncio.gather(
            *[
                WeatherWarningConnector(settings=settings).fetch()
                for _ in range(20)
            ]
        )
        assert calls["n"] == 1
        assert all(outcome.ok for outcome in outcomes)
        assert len({len(outcome.records) for outcome in outcomes}) == 1
        await clear_cache()

    async def test_concurrent_failures_all_report_degradation(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """403이 캐시 표시로 덮이면 권한 거부가 '조회 정상, 자료 없음'이 된다.

        산사태 조회에서 이것이 일어나면 심의 대기가 '산사태 위험 없음'으로 읽힌다.
        """
        import asyncio

        from gbsafe_connectors.base import clear_cache

        await clear_cache()

        async def denied(self, url, params):
            await asyncio.sleep(0.01)
            return make_response(
                b"", status=UpstreamStatus.NOT_AUTHORIZED, content_type="text/plain"
            )

        monkeypatch.setattr(WeatherWarningConnector, "_send", denied)
        outcomes = await asyncio.gather(
            *[WeatherWarningConnector(settings=settings).fetch() for _ in range(8)]
        )
        assert all(not outcome.ok for outcome in outcomes)
        assert all(outcome.degradations for outcome in outcomes)
        assert not any(
            "없습니다" in caveat for outcome in outcomes for caveat in outcome.caveats
        )
        await clear_cache()

    async def test_cached_response_is_labelled(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        from gbsafe_connectors.base import clear_cache

        await clear_cache()

        async def once(self, url, params):
            return make_response(KMA_WARNING_BODY)

        monkeypatch.setattr(WeatherWarningConnector, "_send", once)
        first = await WeatherWarningConnector(settings=settings).fetch()
        second = await WeatherWarningConnector(settings=settings).fetch()
        assert not any("캐시" in caveat for caveat in first.caveats)
        assert any("캐시" in caveat for caveat in second.caveats)
        assert len(second.records) == len(first.records)
        await clear_cache()
