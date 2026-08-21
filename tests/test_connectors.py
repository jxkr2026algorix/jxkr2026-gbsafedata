"""커넥터 계층 테스트.

원천 응답 형태를 고정해 검증한다. 실호출은 개발계정 한도를 소진하고 시점에 따라
결과가 달라져 재현되지 않는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import ClassVar

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
from gbsafe_connectors.medical import (
    AirQualityConnector,
    EmergencyBedsConnector,
    _parse_airkorea_time,
)
from gbsafe_connectors.registry import Registry
from gbsafe_connectors.stations import describe_station, serves_gyeongbuk
from gbsafe_core.config import Settings
from gbsafe_core.domain import AlertAction, Severity
from gbsafe_core.models import QualityFlag, SourceOutcome, UpstreamStatus
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
            ("1mm 미만", 0.5),
            ("5mm", 5.0),
            ("이상한값", None),
            # 결측은 0이 아니라 None이다. 0으로 바꾸면 기온 결측이 0℃가 된다.
            ("", None),
            ("-", None),
            ("null", None),
        ],
    )
    def test_parse_measure(self, raw: str, value: float | None) -> None:
        assert _parse_measure(raw) == value

    @pytest.mark.parametrize(
        ("raw", "category", "value"),
        [
            ("강수없음", "RN1", 0.0),
            ("적설없음", "SNO", 0.0),
            # 기온·습도에 '없음' 표기가 오면 0이 아니라 미확인이다
            ("강수없음", "T1H", None),
            ("적설없음", "REH", None),
        ],
    )
    def test_zero_only_for_precipitation(
        self, raw: str, category: str, value: float | None
    ) -> None:
        assert _parse_measure(raw, category) == value

    def test_missing_temperature_is_not_zero(self, settings: Settings) -> None:
        """결측 기온이 0℃로 보고되면 실측처럼 읽힌다."""
        body = {
            "response": {
                "header": {"resultCode": "00"},
                "body": {
                    "items": {
                        "item": [
                            {
                                "baseDate": "20260822",
                                "baseTime": "0200",
                                "category": "T1H",
                                "obsrValue": "",
                            }
                        ]
                    }
                },
            }
        }
        connector = UltraShortNowcastConnector(settings=settings)
        outcome = connector.parse(make_response(body), location="문경시")
        assert outcome.records[0].payload.value is None
        assert QualityFlag.PARTIAL_RESPONSE in outcome.records[0].quality_flags


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
        assert any("해제" in caveat for caveat in outcome.caveats)

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


class TestFallbackCatalog:
    """동봉 폴백은 클린 클론의 유일한 카탈로그다."""

    def test_every_connector_dataset_is_named(self) -> None:
        """커넥터가 참조하는 데이터셋이 폴백에 없으면 이름 없이 ID만 표시된다.

        15123407(문경시 산사태취약지역)은 보완 발굴 CSV에만 있어서 실제로
        누락됐다.
        """
        import json
        from pathlib import Path

        fallback = (
            Path(__file__).resolve().parents[1]
            / "packages/gbsafe-core/src/gbsafe_core/data/catalog-fallback.json"
        )
        payload = json.loads(fallback.read_text(encoding="utf-8"))
        names = {
            str(item["pk"]): item.get("catalog_name") for item in payload["datasets"]
        }
        registry = Registry()
        for spec in registry.all_specs():
            assert spec.dataset_id in names, f"{spec.name}: {spec.dataset_id} 누락"
            label = names[spec.dataset_id]
            assert label and label != spec.dataset_id, f"{spec.name}: 이름 없음"


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


class TestOutcomeClassification:
    """파싱 실패를 '해당 없음'으로 보고하면 위험이 은폐된다."""

    def test_malformed_envelope_is_failure_not_absence(self, settings: Settings) -> None:
        connector = UltraShortNowcastConnector(settings=settings)
        with pytest.raises(ValueError, match="정상 봉투"):
            connector.parse(make_response({"unexpected": "shape"}), location="문경시")

    def test_error_result_code_is_failure(self, settings: Settings) -> None:
        body = {"response": {"header": {"resultCode": "99", "resultMsg": "NO_DATA"}}}
        connector = UltraShortNowcastConnector(settings=settings)
        with pytest.raises(ValueError, match="정상 봉투"):
            connector.parse(make_response(body), location="문경시")

    def test_documented_no_data_is_confirmed_empty(self, settings: Settings) -> None:
        """정상 응답에 항목이 없는 것은 확인된 부재다."""
        body = {"response": {"header": {"resultCode": "00"}, "body": {"items": ""}}}
        connector = UltraShortNowcastConnector(settings=settings)
        outcome = connector.parse(make_response(body), location="문경시")
        assert outcome.is_empty_but_healthy
        assert outcome.outcome.is_trustworthy_absence

    def test_records_outcome(self, settings: Settings) -> None:
        connector = UltraShortNowcastConnector(settings=settings)
        outcome = connector.parse(make_response(KMA_NOWCAST_BODY), location="문경시")
        assert outcome.outcome.value == "records"
        assert not outcome.is_empty_but_healthy

    def test_receipt_records_what_happened(self, settings: Settings) -> None:
        connector = UltraShortNowcastConnector(settings=settings)
        outcome = connector.parse(make_response(KMA_NOWCAST_BODY), location="문경시")
        receipt = outcome.receipt(connector="weather_now", dataset_id="15084084")
        assert receipt.record_count == len(outcome.records)
        assert receipt.outcome.value == "records"

    def test_empty_csv_is_failure_not_absence(self, settings: Settings) -> None:
        """헤더만 있는 CSV와 인코딩 오류를 구별할 수 없으므로 실패로 본다."""
        connector = ShelterCsvConnector(settings=settings)
        with pytest.raises(ValueError, match="데이터 행이 없습니다"):
            connector.parse(local_response(b""))


class TestRegionParameterMapping:
    """지역 인자 이름이 기관마다 달라서, 통일해 넘기면 필터가 조용히 무시된다."""

    def test_each_connector_declares_its_own_name(self, settings: Settings) -> None:
        registry = Registry(settings=settings)
        declared = {
            spec.name: spec.factory.region_param for spec in registry.all_specs()
        }
        # 기상청은 격자 계산용 location, 응급의료는 sigungu, 도로변은 주소 문자열
        assert declared["weather_now"] == "location"
        assert declared["emergency_beds"] == "sigungu"
        assert declared["landslide_roadside"] == "address"
        assert declared["shelters"] == "region"

    def test_region_kwargs_maps_to_declared_name(self, settings: Settings) -> None:
        connector = UltraShortNowcastConnector(settings=settings)
        assert type(connector).region_kwargs("문경시") == {"location": "문경시"}

    def test_connector_without_region_gets_nothing(self, settings: Settings) -> None:
        """무시될 인자를 넘기면 호출자가 필터가 적용됐다고 오해한다."""
        connector = WildfireRiskConnector(settings=settings)
        assert type(connector).region_kwargs("문경시") == {}

    def test_none_region_yields_nothing(self, settings: Settings) -> None:
        connector = UltraShortNowcastConnector(settings=settings)
        assert type(connector).region_kwargs(None) == {}


class TestMalformedInputNeverConfirmsAbsence:
    """구조를 알아보지 못한 응답이 '해당 없음'으로 보고되면 위험이 은폐된다.

    이 검사를 처음 돌렸을 때 35건이 CONFIRMED_EMPTY로 새어나갔다. 파서가 알려진
    구조를 확인하지 않고 빈 목록을 돌려주면, 그 빈 목록이 '위험 없음'이 된다.
    """

    MALFORMED: tuple[tuple[str, object], ...] = (
        ("empty object", {}),
        ("null", None),
        ("array", []),
        ("empty response node", {"response": {}}),
        ("success with string body", {"response": {"header": {"resultCode": "00"}, "body": "x"}}),
        (
            "success with numeric items",
            {"response": {"header": {"resultCode": "00"}, "body": {"items": 42}}},
        ),
        (
            "success with bool items",
            {"response": {"header": {"resultCode": "00"}, "body": {"items": True}}},
        ),
        ("success without items key", {"response": {"header": {"resultCode": "00"}, "body": {}}}),
        ("error result code", {"response": {"header": {"resultCode": "99"}}}),
        ("html error page", "<html><body>500</body></html>"),
        ("truncated json", '{"response": {"header"'),
        ("plausible wrong fields", {"result": {"data": [{"x": 1}]}}),
        ("whitespace", "   "),
        ("empty body", b""),
    )

    def _connectors(self, settings: Settings) -> tuple[tuple[str, object, dict[str, object]], ...]:
        return (
            ("weather_now", UltraShortNowcastConnector(settings=settings), {"location": "문경시"}),
            ("weather_warning", WeatherWarningConnector(settings=settings), {}),
            ("wildfire_risk", WildfireRiskConnector(settings=settings), {}),
            ("emergency_beds", EmergencyBedsConnector(settings=settings), {}),
            ("air_quality", AirQualityConnector(settings=settings), {}),
            ("shelters", ShelterCsvConnector(settings=settings), {}),
        )

    def test_no_parser_claims_absence_on_malformed_input(self, settings: Settings) -> None:
        leaks: list[str] = []
        for label, body in self.MALFORMED:
            for name, connector, kwargs in self._connectors(settings):
                try:
                    outcome = connector.parse(make_response(body), **kwargs)
                except Exception:  # 예외는 FAILED로 처리되므로 정상
                    continue
                if outcome.outcome is SourceOutcome.CONFIRMED_EMPTY:
                    leaks.append(f"{name} <- {label}")
        assert not leaks, f"구조 미확인 응답을 '해당 없음'으로 보고: {leaks}"

    def test_documented_no_data_still_confirms(self, settings: Settings) -> None:
        """원천이 명시한 '자료 없음'은 여전히 확인된 부재여야 한다."""
        body = {"response": {"header": {"resultCode": "00"}, "body": {"items": ""}}}
        outcome = UltraShortNowcastConnector(settings=settings).parse(
            make_response(body), location="문경시"
        )
        assert outcome.outcome is SourceOutcome.CONFIRMED_EMPTY

    def test_valid_data_still_parses(self, settings: Settings) -> None:
        outcome = UltraShortNowcastConnector(settings=settings).parse(
            make_response(KMA_NOWCAST_BODY), location="문경시"
        )
        assert outcome.outcome is SourceOutcome.RECORDS


class TestOfflineSnapshotFailures:
    """스냅샷 부재·손상이 '해당 없음'으로 보이면 안 된다."""

    async def test_missing_snapshot_is_failure(self, tmp_path) -> None:
        from gbsafe_core.config import Settings
        from pydantic_settings import SettingsConfigDict

        class Offline(Settings):
            model_config = SettingsConfigDict(
                env_prefix="GBSAFE_", env_file=None, extra="ignore", frozen=True
            )

        settings = Offline(
            data_go_kr_service_key="k",
            store_dir=tmp_path / "store",
            offline=True,
        )
        from gbsafe_connectors.base import clear_cache

        await clear_cache()
        outcome = await WeatherWarningConnector(settings=settings).fetch()
        assert outcome.outcome is SourceOutcome.FAILED
        assert not outcome.ok
        assert "스냅샷이 없습니다" in outcome.degradations[0].detail
        await clear_cache()

    async def test_corrupted_snapshot_is_failure(self, tmp_path) -> None:
        """손상된 스냅샷이 '마지막 정상자료'로 제시되면 잘못된 판단 근거가 된다."""
        from gbsafe_core.config import Settings
        from gbsafe_core.snapshot import SnapshotStore
        from pydantic_settings import SettingsConfigDict

        class Offline(Settings):
            model_config = SettingsConfigDict(
                env_prefix="GBSAFE_", env_file=None, extra="ignore", frozen=True
            )

        settings = Offline(
            data_go_kr_service_key="k",
            store_dir=tmp_path / "store",
            offline=True,
        )
        store = SnapshotStore.from_settings(settings)
        ref = store.put(
            dataset_id="15000415",
            body=json.dumps(KMA_WARNING_BODY, ensure_ascii=False).encode(),
        )
        ref.path.write_bytes(b'{"truncated')

        from gbsafe_connectors.base import clear_cache

        await clear_cache()
        outcome = await WeatherWarningConnector(settings=settings, store=store).fetch()
        assert outcome.outcome is SourceOutcome.FAILED
        assert "읽을 수 없습니다" in outcome.degradations[0].detail
        await clear_cache()

    async def test_concurrent_offline_misses_all_report_failure(self, tmp_path) -> None:
        """대기자 없는 실패에서 asyncio 경고가 stderr로 새지 않아야 한다."""
        import asyncio

        from gbsafe_core.config import Settings
        from pydantic_settings import SettingsConfigDict

        class Offline(Settings):
            model_config = SettingsConfigDict(
                env_prefix="GBSAFE_", env_file=None, extra="ignore", frozen=True
            )

        settings = Offline(
            data_go_kr_service_key="k",
            store_dir=tmp_path / "store",
            offline=True,
        )
        from gbsafe_connectors.base import clear_cache

        await clear_cache()
        outcomes = await asyncio.gather(
            *[WeatherWarningConnector(settings=settings).fetch() for _ in range(6)]
        )
        assert all(item.outcome is SourceOutcome.FAILED for item in outcomes)
        assert all(item.degradations for item in outcomes)
        await clear_cache()


class TestProvenanceIsNotFabricated:
    """출처가 조작되면 인용이 거짓말이 된다."""

    def test_provenance_matches_the_source(self, settings: Settings) -> None:
        connector = UltraShortNowcastConnector(settings=settings)
        outcome = connector.parse(make_response(KMA_NOWCAST_BODY), location="문경시")
        provenance = outcome.records[0].provenance
        assert provenance.dataset_id == UltraShortNowcastConnector.dataset_id
        assert provenance.provider == "기상청"
        # 관측 시각은 원천이 준 baseDate/baseTime이어야 하고 수집 시각과 달라야 한다
        assert provenance.observed_at is not None
        assert provenance.observed_at != provenance.retrieved_at

    def test_missing_update_time_is_not_invented(self, settings: Settings) -> None:
        """hvidate가 없으면 실시간으로 표시할 수 없다."""
        xml = (
            '<?xml version="1.0"?><response><header><resultCode>00</resultCode>'
            "</header><body><items><item><hpid>A1</hpid><dutyName>병원</dutyName>"
            "<hvec>5</hvec></item></items></body></response>"
        )
        outcome = EmergencyBedsConnector(settings=settings).parse(
            make_response(xml, content_type="application/xml")
        )
        record = outcome.records[0]
        assert record.provenance.observed_at is None
        assert not record.payload.is_realtime
        assert not record.freshness.is_usable_for_decision
        assert QualityFlag.PARTIAL_RESPONSE in record.quality_flags

    def test_unknown_dataset_gets_restrictive_licence(self, settings: Settings) -> None:
        """카탈로그에 없는 데이터셋을 관대하게 처리하면 위반이 통과한다."""
        from gbsafe_core.licensing import Operation, permits
        from gbsafe_core.models import LicenseCode

        class Ghost(UltraShortNowcastConnector):
            dataset_id = "99999999"

        outcome = Ghost(settings=settings).parse(
            make_response(KMA_NOWCAST_BODY), location="문경시"
        )
        licence = outcome.records[0].provenance.license
        assert licence is LicenseCode.UNKNOWN
        assert not permits(licence, Operation.DERIVE)


class TestNullIsNotAbsence:
    """`null`은 '자료 없음' 표기가 아니다. 서버 과부하에서도 나온다."""

    def test_items_null_is_failure(self, settings: Settings) -> None:
        body = {"response": {"header": {"resultCode": "00"}, "body": {"items": None}}}
        with pytest.raises(ValueError, match="null"):
            UltraShortNowcastConnector(settings=settings).parse(
                make_response(body), location="문경시"
            )

    @pytest.mark.parametrize("marker", ["", []])
    def test_documented_markers_still_confirm(
        self, settings: Settings, marker: object
    ) -> None:
        body = {"response": {"header": {"resultCode": "00"}, "body": {"items": marker}}}
        outcome = UltraShortNowcastConnector(settings=settings).parse(
            make_response(body), location="문경시"
        )
        assert outcome.outcome is SourceOutcome.CONFIRMED_EMPTY

    def test_forest_empty_result_is_failure(self, settings: Settings) -> None:
        """`{'result': []}`로 봉투 검사를 우회할 수 있었다."""
        with pytest.raises(ValueError):
            WildfireRiskConnector(settings=settings).parse(make_response({"result": []}))

    def test_forest_populated_result_parses(self, settings: Settings) -> None:
        body = {
            "result": [
                {"sido": "경상북도", "meanavg": "10", "maxi": "20", "analdate": "20260822"}
            ]
        }
        outcome = WildfireRiskConnector(settings=settings).parse(make_response(body))
        assert outcome.outcome is SourceOutcome.RECORDS


class TestUnreadableCsvIsNotAbsence:
    """컬럼을 못 읽은 대피소 파일이 '대피소 없음'이 되면 안 된다."""

    def test_wrong_columns_is_failure(self, settings: Settings) -> None:
        with pytest.raises(ValueError, match="컬럼을 찾지 못했습니다"):
            ShelterCsvConnector(settings=settings).parse(
                local_response(b"colA,colB\nx,y\n")
            )

    def test_wrong_columns_is_failure_for_zones(self, settings: Settings) -> None:
        with pytest.raises(ValueError, match="컬럼을 찾지 못했습니다"):
            LandslideRiskZoneCsvConnector(settings=settings).parse(
                local_response(b"colA,colB\nx,y\n")
            )

    def test_region_filter_removing_all_is_confirmed(self, settings: Settings) -> None:
        """그 지역에 항목이 없는 것은 확인된 부재다."""
        csv = "시설명,소재지도로명주소,위도,경도\n회관,경상북도 문경시,36.68,128.25\n"
        outcome = ShelterCsvConnector(settings=settings).parse(
            local_response(csv.encode()), region="없는지역"
        )
        assert outcome.outcome is SourceOutcome.CONFIRMED_EMPTY
        assert any("없습니다" in caveat for caveat in outcome.caveats)


class TestWarningStateReconstruction:
    """해제 통보문을 버리기만 하면 이미 끝난 특보가 발효 중으로 남는다."""

    @staticmethod
    def _body(items: list[dict[str, object]]) -> dict[str, object]:
        return {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": items}}}}

    ISSUED: ClassVar[dict[str, object]] = {
        "stnId": "143",
        "title": "[특보] 제08-62호 : 2026.08.21.13:10 / 호우경보 발표 (*)",
        "tmFc": 202608211310,
    }
    CANCELLED: ClassVar[dict[str, object]] = {
        "stnId": "143",
        "title": "[특보] 제08-71호 : 2026.08.21.20:30 / 호우경보 해제 (*)",
        "tmFc": 202608212030,
    }

    def test_cancelled_retires_its_issuance(self, settings: Settings) -> None:
        """13:10 발표 + 20:30 해제 이력에서 발효 중인 특보는 없다."""
        outcome = WeatherWarningConnector(settings=settings).parse(
            make_response(self._body([self.ISSUED, self.CANCELLED]))
        )
        assert not outcome.records
        assert any("해제" in caveat for caveat in outcome.caveats)

    def test_issuance_without_cancellation_stays_active(self, settings: Settings) -> None:
        outcome = WeatherWarningConnector(settings=settings).parse(
            make_response(self._body([self.ISSUED]))
        )
        assert len(outcome.records) == 1
        assert outcome.records[0].payload.is_active

    def test_reissued_after_cancellation_is_active(self, settings: Settings) -> None:
        """해제된 뒤 다시 발표되면 발효 중이다."""
        reissued = {
            "stnId": "143",
            "title": "[특보] 제08-80호 : 2026.08.22.02:00 / 호우경보 발표 (*)",
            "tmFc": 202608220200,
        }
        outcome = WeatherWarningConnector(settings=settings).parse(
            make_response(self._body([self.CANCELLED, reissued]))
        )
        assert len(outcome.records) == 1
        assert "02:00" in outcome.records[0].payload.headline

    def test_different_hazard_kinds_are_independent(self, settings: Settings) -> None:
        """강풍 해제가 호우 발표를 무효로 만들면 안 된다."""
        wind_cancelled = {
            "stnId": "143",
            "title": "[특보] 제08-71호 : 2026.08.21.20:30 / 강풍주의보 해제 (*)",
            "tmFc": 202608212030,
        }
        outcome = WeatherWarningConnector(settings=settings).parse(
            make_response(self._body([self.ISSUED, wind_cancelled]))
        )
        assert len(outcome.records) == 1
        assert "호우경보" in outcome.records[0].payload.headline

    def test_different_offices_are_independent(self, settings: Settings) -> None:
        """안동기상대의 해제가 대구지방기상청의 발표를 무효로 만들면 안 된다."""
        other_office = {
            "stnId": "136",
            "title": "[특보] 제08-12호 : 2026.08.21.20:30 / 호우경보 해제 (*)",
            "tmFc": 202608212030,
        }
        outcome = WeatherWarningConnector(settings=settings).parse(
            make_response(self._body([self.ISSUED, other_office]))
        )
        assert len(outcome.records) == 1
        assert outcome.records[0].payload.area_code == "143"

    def test_full_history_available_when_requested(self, settings: Settings) -> None:
        outcome = WeatherWarningConnector(settings=settings).parse(
            make_response(self._body([self.ISSUED, self.CANCELLED])), active_only=False
        )
        assert len(outcome.records) == 2
        assert any(not item.payload.is_active for item in outcome.records)
