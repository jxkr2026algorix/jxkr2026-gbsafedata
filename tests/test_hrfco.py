"""홍수통제소 커넥터 테스트.

이 원천은 기관 고시 임계수위를 함께 주기 때문에 다른 커넥터에 없는 실패 방식이
하나 더 있다. **임계값을 모르는 관측소를 '안전'으로 읽는 것**이다. 경북 242개
관측소 중 64개는 임계수위가 고시돼 있지 않다.
"""

from __future__ import annotations

import json

import pytest
from conftest import make_response
from gbsafe_connectors.hrfco import (
    STATIONS,
    FloodForecastConnector,
    RiverLevelConnector,
    Station,
)
from gbsafe_core.config import Settings
from gbsafe_core.models import QualityFlag, SourceOutcome, UpstreamStatus


def _station_id(predicate) -> str:
    for station_id, station in STATIONS.items():
        if predicate(station):
            return station_id
    pytest.skip("참조표에 해당 조건의 관측소가 없습니다")


@pytest.fixture
def river(settings: Settings) -> RiverLevelConnector:
    return RiverLevelConnector(settings=settings)


@pytest.fixture
def flood(settings: Settings) -> FloodForecastConnector:
    return FloodForecastConnector(settings=settings)


def _body(rows: list[dict], **extra) -> dict:
    return {"links": [], "content": rows, **extra}


class TestStationThresholds:
    def test_reference_table_is_loaded(self) -> None:
        assert STATIONS, "경북 수위관측소 참조표가 비었습니다"

    def test_station_without_thresholds_is_not_judgeable(self) -> None:
        station = Station({"station_id": "x", "name": "임계값없음", "address": "경상북도"})
        assert not station.has_thresholds

    def test_severity_climbs_with_the_official_thresholds(self) -> None:
        station = Station(
            {
                "station_id": "x", "name": "테스트", "address": "경상북도",
                "attention_m": 1.0, "advisory_m": 2.0,
                "warning_m": 3.0, "serious_m": 4.0,
            }
        )
        from gbsafe_core.domain import Severity

        assert station.severity_for(0.5) is Severity.INFO
        assert station.severity_for(2.5) is Severity.ADVISORY
        assert station.severity_for(3.5) is Severity.WARNING
        assert station.severity_for(9.9) is Severity.EMERGENCY

    def test_exceeded_threshold_reports_the_highest_crossed(self) -> None:
        station = Station(
            {
                "station_id": "x", "name": "테스트", "address": "경상북도",
                "attention_m": 1.0, "advisory_m": 2.0,
                "warning_m": 3.0, "serious_m": 4.0,
            }
        )
        assert station.exceeded_threshold(0.5) is None
        assert station.exceeded_threshold(3.2) == ("경보", 3.0)
        assert station.exceeded_threshold(4.0) == ("심각", 4.0)


class TestRiverLevelParsing:
    def test_reads_levels_and_keeps_the_station_name(
        self, river: RiverLevelConnector
    ) -> None:
        station_id = _station_id(lambda s: s.has_thresholds)
        outcome = river.parse(
            make_response(_body([{"wlobscd": station_id, "ymdhm": "202608221510", "wl": "2.09"}]))
        )
        assert len(outcome.records) == 1
        payload = outcome.records[0].payload
        assert payload.value == 2.09
        assert payload.unit == "m"
        assert payload.station

    def test_missing_level_is_none_not_zero(self, river: RiverLevelConnector) -> None:
        """하천 수위 0은 실제로 있을 수 있는 값이라, 결측을 0으로 만들면
        '물이 없다'는 관측처럼 읽힌다."""
        station_id = _station_id(lambda s: s.has_thresholds)
        outcome = river.parse(
            make_response(_body([{"wlobscd": station_id, "ymdhm": "202608221510", "wl": " "}]))
        )
        record = outcome.records[0]
        assert record.payload.value is None
        assert QualityFlag.NO_DATA_RETURNED in record.quality_flags
        assert any("결측" in note for note in record.notes)

    @pytest.mark.parametrize("raw", ["", " ", "결측", "-", None])
    def test_unreadable_level_never_becomes_a_number(
        self, river: RiverLevelConnector, raw: object
    ) -> None:
        station_id = _station_id(lambda s: s.has_thresholds)
        outcome = river.parse(
            make_response(_body([{"wlobscd": station_id, "ymdhm": "202608221510", "wl": raw}]))
        )
        assert outcome.records[0].payload.value is None

    def test_station_without_thresholds_carries_a_warning(
        self, river: RiverLevelConnector
    ) -> None:
        """임계값을 모르는 관측소는 '안전'이 아니라 '판단 불가'다."""
        station_id = _station_id(lambda s: not s.has_thresholds)
        outcome = river.parse(
            make_response(_body([{"wlobscd": station_id, "ymdhm": "202608221510", "wl": "0.12"}]))
        )
        record = outcome.records[0]
        assert any("판단할 수 없" in note for note in record.notes), record.notes
        assert any("안전" in caveat for caveat in outcome.caveats), outcome.caveats

    def test_crossing_a_threshold_is_reported(self, river: RiverLevelConnector) -> None:
        station_id = _station_id(lambda s: isinstance(s.warning_m, float))
        station = STATIONS[station_id]
        over = float(station.warning_m) + 0.5
        outcome = river.parse(
            make_response(
                _body([{"wlobscd": station_id, "ymdhm": "202608221510", "wl": str(over)}])
            )
        )
        notes = " ".join(outcome.records[0].notes)
        assert "넘었습니다" in notes, notes
        assert any("임계수위 초과" in caveat for caveat in outcome.caveats)

    def test_level_below_every_threshold_is_not_flagged(
        self, river: RiverLevelConnector
    ) -> None:
        station_id = _station_id(lambda s: isinstance(s.attention_m, float))
        station = STATIONS[station_id]
        under = float(station.attention_m) - 1.0
        outcome = river.parse(
            make_response(
                _body([{"wlobscd": station_id, "ymdhm": "202608221510", "wl": str(under)}])
            )
        )
        assert not any("넘었습니다" in note for note in outcome.records[0].notes)


class TestRiverLevelFailPaths:
    def test_null_content_is_a_failure_not_an_absence(
        self, river: RiverLevelConnector
    ) -> None:
        """부하가 걸린 서버가 `content: null`을 준다. 관측값 없음과 구별되지 않는다."""
        with pytest.raises(ValueError, match="content"):
            river.parse(make_response({"links": [], "content": None}))

    def test_non_json_body_is_a_failure(self, river: RiverLevelConnector) -> None:
        with pytest.raises(ValueError, match="JSON"):
            river.parse(make_response(b"<html>error</html>", content_type="text/html"))

    def test_unknown_error_code_is_a_failure(self, river: RiverLevelConnector) -> None:
        with pytest.raises(ValueError, match="오류 코드"):
            river.parse(make_response({"code": "500", "message": "서버 오류"}))

    def test_content_that_is_not_a_list_is_a_failure(
        self, river: RiverLevelConnector
    ) -> None:
        with pytest.raises(ValueError, match="배열"):
            river.parse(make_response({"links": [], "content": {"wl": "1.0"}}))

    def test_no_recognisable_station_is_a_failure(self, river: RiverLevelConnector) -> None:
        """참조표에 없는 관측소만 오면 '하천이 안전하다'로 읽히면 안 된다."""
        with pytest.raises(ValueError, match="해석하지 못했"):
            river.parse(_response_rows([{"wlobscd": "0000000", "wl": "1.0"}]))

    def test_region_whose_stations_all_vanished_is_a_failure(
        self, river: RiverLevelConnector
    ) -> None:
        """문경에 관측소 12곳이 있는데 응답에 하나도 없으면 부분 응답이다.

        이것을 확인된 부재로 읽으면 하천이 안전한 것으로 보인다.
        """
        with pytest.raises(ValueError, match="부분 응답"):
            river.parse(
                make_response(_body([{"wlobscd": "0000000", "wl": "1.0"}])), region="문경시"
            )

    def test_region_without_any_station_says_so_explicitly(
        self, river: RiverLevelConnector
    ) -> None:
        """관측소가 애초에 없는 지역은 '수위 정보 없음'이지 '안전'이 아니다."""
        outcome = river.parse(
            make_response(_body([{"wlobscd": "0000000", "wl": "1.0"}])), region="울릉군"
        )
        assert outcome.confirmed_absence
        assert any("안전한 것이 아" in c for c in outcome.caveats), outcome.caveats

    async def test_missing_credential_is_reported_as_not_authorized(
        self, keyless_settings: Settings
    ) -> None:
        outcome = await RiverLevelConnector(settings=keyless_settings).fetch()
        assert not outcome.records
        assert outcome.degradations
        assert outcome.degradations[0].status is UpstreamStatus.NOT_AUTHORIZED
        receipt = outcome.receipt(connector="river_level", dataset_id="hrfco-waterlevel")
        assert receipt.outcome is SourceOutcome.FAILED


def _response_rows(rows: list[dict]):
    return make_response(_body(rows))


class TestFloodForecast:
    def test_no_data_code_is_a_confirmed_absence(
        self, flood: FloodForecastConnector
    ) -> None:
        """코드 990을 확인했을 때만 '발령 없음'으로 읽는다."""
        outcome = flood.parse(
            make_response(
                {"code": "990", "message": "검색된 자료가 없습니다.", "links": []}
            )
        )
        assert outcome.confirmed_absence
        assert not outcome.records

    def test_missing_code_and_missing_content_is_a_failure(
        self, flood: FloodForecastConnector
    ) -> None:
        """코드도 없고 목록도 없으면 발령이 없다고 단정할 근거가 없다."""
        with pytest.raises(ValueError, match="찾지 못했"):
            flood.parse(make_response({"links": []}))

    def test_null_content_without_a_code_is_a_failure(
        self, flood: FloodForecastConnector
    ) -> None:
        with pytest.raises(ValueError, match="찾지 못했"):
            flood.parse(make_response({"links": [], "content": None}))

    def test_empty_list_without_the_code_is_a_failure(
        self, flood: FloodForecastConnector
    ) -> None:
        """빈 배열만으로는 '발령 없음'인지 필터가 지운 것인지 알 수 없다."""
        with pytest.raises(ValueError, match="찾지 못했"):
            flood.parse(make_response({"links": [], "content": []}))

    def test_an_active_alert_becomes_a_record(self, flood: FloodForecastConnector) -> None:
        outcome = flood.parse(
            make_response(
                {
                    "links": [],
                    "content": [
                        {
                            "wlobscd": "2001615",
                            "obsnm": "봉화군(대현교)",
                            "fcstty": "홍수경보",
                            "fcstdt": "202608221500",
                        }
                    ],
                }
            )
        )
        assert len(outcome.records) == 1
        alert = outcome.records[0].payload
        assert "봉화군" in alert.headline
        assert alert.is_active

    def test_non_json_body_is_a_failure(self, flood: FloodForecastConnector) -> None:
        with pytest.raises(ValueError, match="JSON"):
            flood.parse(make_response(b"\x00\x01binary", content_type="application/json"))


class TestLiveShapeIsStillWhatWeParse:
    """실제 응답 형태를 고정해 둔다.

    이 형태는 2026-08-22에 실호출로 확인했다. 원천이 바꾸면 여기서 깨져야 한다.
    """

    def test_documented_waterlevel_row_parses(self, river: RiverLevelConnector) -> None:
        station_id = _station_id(lambda s: s.has_thresholds)
        raw = json.dumps(
            {
                "links": [],
                "content": [
                    {"wlobscd": station_id, "ymdhm": "202608221510", "wl": "1.65",
                     "fw": " ", "links": []}
                ],
            }
        )
        outcome = river.parse(make_response(raw))
        assert outcome.records[0].payload.value == 1.65


class TestOperationalConstraintsAreDisclosed:
    """원천이 명시한 제약이 결과에 실려야 한다.

    근거: ../jxkr2026-datasets/docs/external-portals.md
    """

    def test_raw_uncorrected_nature_is_disclosed(self, river: RiverLevelConnector) -> None:
        """보정 전 원시자료를 확정자료처럼 제시하면 안 된다."""
        station_id = _station_id(lambda s: s.has_thresholds)
        outcome = river.parse(
            make_response(_body([{"wlobscd": station_id, "ymdhm": "202608221510", "wl": "1.0"}]))
        )
        assert any("보정 전" in caveat for caveat in outcome.caveats), outcome.caveats

    def test_collection_lag_is_disclosed(self, river: RiverLevelConnector) -> None:
        """경북은 낙동강 권역이라 수집 지연이 11분 이상이다."""
        station_id = _station_id(lambda s: s.has_thresholds)
        outcome = river.parse(
            make_response(_body([{"wlobscd": station_id, "ymdhm": "202608221510", "wl": "1.0"}]))
        )
        assert any("지연" in caveat for caveat in outcome.caveats), outcome.caveats


class TestReferenceTableIsSane:
    """참조표에 들어온 값 자체를 검사한다.

    파서가 아무리 옳아도 임계값이나 좌표가 틀리면 답이 틀린다. 실제로 둘 다
    있었다 — 임계수위 자리에 0이 들어와 평상시 0.1m가 경보 초과로 보고됐고,
    문경 경천댐 좌표가 경도 126.12(약 180km 서쪽)였다.
    """

    def test_no_threshold_is_zero(self) -> None:
        """제원의 `0`은 "수위 0m에서 경보"가 아니라 미고시 표시다."""
        offenders = [
            (station.name, level)
            for station in STATIONS.values()
            for level in (
                station.attention_m,
                station.advisory_m,
                station.warning_m,
                station.serious_m,
            )
            if level == 0
        ]
        assert not offenders, f"임계수위가 0인 관측소: {offenders[:5]}"

    def test_every_coordinate_is_inside_gyeongbuk(self) -> None:
        from gbsafe_core.regions import GYEONGBUK_BBOX

        outside = [
            (station.name, station.location.lat, station.location.lon)
            for station in STATIONS.values()
            if station.location is not None
            and not GYEONGBUK_BBOX.contains(station.location)
        ]
        assert not outside, f"경북 밖 좌표: {outside}"

    def test_thresholds_are_ordered(self) -> None:
        """관심 < 주의보 < 경보 < 심각이 아니면 단계 판정이 뒤집힌다."""
        broken = []
        for station in STATIONS.values():
            levels = [
                value
                for value in (
                    station.attention_m,
                    station.advisory_m,
                    station.warning_m,
                    station.serious_m,
                )
                if isinstance(value, int | float)
            ]
            if levels != sorted(levels):
                broken.append((station.name, levels))
        assert not broken, f"임계수위 순서가 어긋난 관측소: {broken[:5]}"

    def test_a_station_without_thresholds_never_reports_an_exceedance(self) -> None:
        """임계값이 없으면 어떤 수위도 초과로 판정되면 안 된다."""
        for station in STATIONS.values():
            if station.has_thresholds:
                continue
            assert station.exceeded_threshold(999.0) is None, station.name
