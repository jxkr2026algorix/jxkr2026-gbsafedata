"""산림청 커넥터 테스트.

이 파일이 생긴 이유가 있다. 뮤테이션 감사에서 산사태 예보단계를 전부 '낮음'으로
낮추고, 예보를 통째로 버리고, 도로변 취약구간을 버려도 501개 테스트가 전부
통과했다. 커넥터가 import되고 클래스 속성이 읽히는 것만으로 커버리지가 잡혔을
뿐, `parse()`가 만든 값을 아무도 검사하지 않았다.

여기서는 파서가 만든 **값 자체**를 검사한다.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from gbsafe_connectors.forest import (
    LandslidePredictionConnector,
    PastLandslideConnector,
    RoadsideLandslideConnector,
    WildfireRiskConnector,
    _fire_severity,
)
from gbsafe_core.config import Settings
from gbsafe_core.domain import Severity
from gbsafe_core.models import QualityFlag, SourceOutcome
from gbsafe_core.regions import HazardDomain

from tests.conftest import make_response


def _envelope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": rows}}}}


class TestLandslidePrediction:
    """산사태 예보는 메인 시나리오의 핵심이다. 값이 틀리면 대피 판단이 틀린다."""

    ADVISORY: ClassVar[dict[str, Any]] = {
        "lndslFrcstNm": "주의보",
        "sgg": "문경시",
        "anlsDt": "20260822010000",
    }
    WARNING: ClassVar[dict[str, Any]] = {
        "lndslFrcstNm": "경보",
        "sgg": "안동시",
        "anlsDt": "20260822020000",
    }

    def test_severity_is_parsed_not_flattened(self, settings: Settings) -> None:
        """예보단계를 전부 '낮음'으로 낮추면 위험이 은폐된다."""
        outcome = LandslidePredictionConnector(settings=settings).parse(
            make_response(_envelope([self.ADVISORY, self.WARNING]))
        )
        severities = {
            record.payload.area_name: record.payload.severity for record in outcome.records
        }
        assert severities["문경시"] is Severity.ADVISORY
        assert severities["안동시"] is Severity.WARNING

    def test_records_are_not_dropped(self, settings: Settings) -> None:
        """예보를 버리면 결과가 비어 '위험 없음'으로 읽힌다."""
        outcome = LandslidePredictionConnector(settings=settings).parse(
            make_response(_envelope([self.ADVISORY, self.WARNING]))
        )
        assert len(outcome.records) == 2
        assert outcome.outcome is SourceOutcome.RECORDS

    def test_hazard_and_area_are_carried(self, settings: Settings) -> None:
        outcome = LandslidePredictionConnector(settings=settings).parse(
            make_response(_envelope([self.ADVISORY]))
        )
        payload = outcome.records[0].payload
        assert payload.hazard is HazardDomain.LANDSLIDE
        assert payload.area_name == "문경시"
        assert payload.raw_level == "주의보"

    def test_warning_is_actionable_advisory_is_not(self, settings: Settings) -> None:
        """경보는 검토 착수 신호이고 주의보는 아니다."""
        outcome = LandslidePredictionConnector(settings=settings).parse(
            make_response(_envelope([self.ADVISORY, self.WARNING]))
        )
        actionable = {
            record.payload.area_name: record.payload.is_actionable
            for record in outcome.records
        }
        assert actionable["안동시"] is True
        assert actionable["문경시"] is False

    def test_analysis_time_becomes_observed_at(self, settings: Settings) -> None:
        """분석 시각이 신선도 판정의 기준이다."""
        outcome = LandslidePredictionConnector(settings=settings).parse(
            make_response(_envelope([self.ADVISORY]))
        )
        record = outcome.records[0]
        assert record.provenance.observed_at is not None
        assert record.provenance.observed_at.year == 2026

    def test_caveat_warns_against_household_reading(self, settings: Settings) -> None:
        outcome = LandslidePredictionConnector(settings=settings).parse(
            make_response(_envelope([self.ADVISORY]))
        )
        assert any("시군구 단위" in caveat for caveat in outcome.caveats)

    def test_documented_empty_confirms_absence(self, settings: Settings) -> None:
        outcome = LandslidePredictionConnector(settings=settings).parse(
            make_response({"response": {"header": {"resultCode": "00"}, "body": {"items": ""}}})
        )
        assert outcome.outcome is SourceOutcome.CONFIRMED_EMPTY


class TestRoadsideLandslide:
    """대피경로가 끊길 수 있는 구간. 버리면 경로 판단이 틀린다."""

    ROW: ClassVar[dict[str, Any]] = {
        "id": "R1",
        "ecninPssssSctinNm": "지방도 923호선 12k 지점",
        "poflcAddr": "경상북도 문경시 산북면",
        "vnaraTpeNm": "절토사면",
        "lndslMnagnNm": "경상북도",
        "lat": "36.68",
        "lon": "128.25",
    }

    def test_records_are_not_dropped(self, settings: Settings) -> None:
        outcome = RoadsideLandslideConnector(settings=settings).parse(
            make_response(_envelope([self.ROW]))
        )
        assert len(outcome.records) == 1
        assert outcome.outcome is SourceOutcome.RECORDS

    def test_zone_fields_are_carried(self, settings: Settings) -> None:
        outcome = RoadsideLandslideConnector(settings=settings).parse(
            make_response(_envelope([self.ROW]))
        )
        payload = outcome.records[0].payload
        assert payload.hazard is HazardDomain.LANDSLIDE
        assert payload.name == "지방도 923호선 12k 지점"
        assert payload.address == "경상북도 문경시 산북면"
        assert payload.grade == "절토사면"
        assert payload.managing_agency == "경상북도"

    def test_coordinates_make_it_locatable(self, settings: Settings) -> None:
        outcome = RoadsideLandslideConnector(settings=settings).parse(
            make_response(_envelope([self.ROW]))
        )
        payload = outcome.records[0].payload
        assert payload.is_locatable
        assert payload.location is not None
        assert payload.location.lat == pytest.approx(36.68)

    def test_missing_coordinates_flagged(self, settings: Settings) -> None:
        """좌표가 없으면 공간연산에서 제외되어야 하고 그 사실이 남아야 한다."""
        row = {key: value for key, value in self.ROW.items() if key not in ("lat", "lon")}
        outcome = RoadsideLandslideConnector(settings=settings).parse(
            make_response(_envelope([row]))
        )
        record = outcome.records[0]
        assert not record.payload.is_locatable
        assert QualityFlag.MISSING_COORDINATES in record.quality_flags

    def test_out_of_range_coordinates_rejected(self, settings: Settings) -> None:
        """EPSG:5186 값이 위경도 칸에 오면 위치를 모른다고 해야 한다."""
        row = {**self.ROW, "lat": "445123.5", "lon": "1050987.2"}
        outcome = RoadsideLandslideConnector(settings=settings).parse(
            make_response(_envelope([row]))
        )
        assert outcome.records[0].payload.location is None

    def test_address_filter_applies(self, settings: Settings) -> None:
        outcome = RoadsideLandslideConnector(settings=settings).parse(
            make_response(_envelope([self.ROW])), address="안동시"
        )
        assert not outcome.records


class TestPastLandslide:
    """과거 이력. 현재 위험으로 제시되면 안 된다."""

    ROW: ClassVar[dict[str, Any]] = {
        "sn": "1",
        "lndslDsstrYr": "2023",
        "ctprvNm": "경상북도",
        "sgngNm": "문경시",
        "epmnNm": "산북면",
    }

    def test_records_parsed(self, settings: Settings) -> None:
        outcome = PastLandslideConnector(settings=settings).parse(
            make_response(_envelope([self.ROW]))
        )
        assert len(outcome.records) == 1
        payload = outcome.records[0].payload
        assert payload.hazard is HazardDomain.LANDSLIDE
        assert "2023" in payload.name
        assert payload.designated_on == "2023"

    def test_address_is_composed(self, settings: Settings) -> None:
        outcome = PastLandslideConnector(settings=settings).parse(
            make_response(_envelope([self.ROW]))
        )
        assert outcome.records[0].payload.address == "경상북도 문경시 산북면"

    def test_caveat_marks_it_historical(self, settings: Settings) -> None:
        """과거 기록을 현재 위험으로 읽으면 안 된다."""
        outcome = PastLandslideConnector(settings=settings).parse(
            make_response(_envelope([self.ROW]))
        )
        assert any("현재 위험 상태를 나타내지 않습니다" in c for c in outcome.caveats)


class TestWildfireRisk:
    """산불위험지수. 낮춰 보고하면 예방 우선순위가 틀린다."""

    def test_severity_reflects_the_index(self, settings: Settings) -> None:
        rows = [
            {"sido": "경상북도", "meanavg": "45.0", "maxi": "90", "analdate": "20260822"},
        ]
        outcome = WildfireRiskConnector(settings=settings).parse(
            make_response(_envelope(rows))
        )
        assert outcome.records[0].payload.severity is Severity.EMERGENCY

    def test_low_index_stays_low(self, settings: Settings) -> None:
        rows = [{"sido": "경상북도", "meanavg": "10.0", "maxi": "20", "analdate": "20260822"}]
        outcome = WildfireRiskConnector(settings=settings).parse(
            make_response(_envelope(rows))
        )
        assert outcome.records[0].payload.severity is Severity.INFO

    def test_grade_counts_become_notes(self, settings: Settings) -> None:
        rows = [
            {
                "sido": "경상북도",
                "meanavg": "45.0",
                "maxi": "70",
                "d3": "2",
                "d4": "1",
                "analdate": "20260822",
            }
        ]
        outcome = WildfireRiskConnector(settings=settings).parse(
            make_response(_envelope(rows))
        )
        notes = " ".join(outcome.records[0].notes)
        assert "높음" in notes
        assert "매우높음" in notes

    def test_hazard_is_wildfire(self, settings: Settings) -> None:
        rows = [{"sido": "경상북도", "meanavg": "10", "analdate": "20260822"}]
        outcome = WildfireRiskConnector(settings=settings).parse(
            make_response(_envelope(rows))
        )
        assert outcome.records[0].payload.hazard is HazardDomain.WILDFIRE

    @pytest.mark.parametrize(
        ("index", "severity"),
        [
            (0.0, Severity.INFO),
            (50.0, Severity.INFO),
            (51.0, Severity.ADVISORY),
            (65.0, Severity.ADVISORY),
            (66.0, Severity.WARNING),
            (85.0, Severity.WARNING),
            (86.0, Severity.EMERGENCY),
            (100.0, Severity.EMERGENCY),
            (None, Severity.UNKNOWN),
        ],
    )
    def test_severity_bands_at_boundaries(
        self, index: float | None, severity: Severity
    ) -> None:
        """등급 경계는 산림청 구간을 따라야 한다."""
        assert _fire_severity(index) is severity
