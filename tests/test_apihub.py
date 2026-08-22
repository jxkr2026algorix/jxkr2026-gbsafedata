"""기상청 API허브 AWS 분자료 커넥터 테스트."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from gbsafe_connectors.apihub import AwsObservationConnector
from gbsafe_connectors.base import KST
from gbsafe_connectors.registry import Registry
from gbsafe_core.config import CredentialName, Settings
from gbsafe_core.models import QualityFlag, SourceOutcome, UpstreamStatus
from pydantic_settings import SettingsConfigDict

from tests.conftest import make_response


class _ApiHubSettings(Settings):
    """실제 `.env`를 읽지 않는 API허브 테스트 설정."""

    model_config = SettingsConfigDict(
        env_prefix="GBSAFE_",
        env_file=None,
        extra="ignore",
        frozen=True,
    )


@pytest.fixture
def apihub_settings(tmp_path: Path) -> Settings:
    return _ApiHubSettings(
        kma_apihub_auth_key="test-key",
        store_dir=tmp_path / "store",
    )


def _text_response(body: str):
    return make_response(body, content_type="text/plain; charset=utf-8")


class TestAwsObservationConnector:
    def test_builds_verified_request_shape(self, apihub_settings: Settings) -> None:
        connector = AwsObservationConnector(settings=apihub_settings)
        params = connector.build_params(
            end_time=datetime(2026, 8, 22, 14, 58, tzinfo=KST),
            station_id="273",
        )

        assert connector.base_url().endswith("/nph-aws2_min")
        assert params == {
            "tm2": "202608221458",
            "stn": "273",
            "disp": "1",
            "help": "0",
        }
        assert "authKey" not in params

    def test_parses_reordered_header_instead_of_fixed_positions(
        self, apihub_settings: Settings
    ) -> None:
        """컬럼 순서가 바뀌어도 헤더를 따라야 다른 값을 기온·강수로 오인하지 않는다."""
        body = """\
#START7777
# STN,TM,RN-60m,TA,WS1
273,202608221458,2.5,25.1,3.7,=
#7777END
"""
        connector = AwsObservationConnector(settings=apihub_settings)
        outcome = connector.parse(_text_response(body), station_id="273")
        values = {record.payload.kind: record.payload.value for record in outcome.records}

        assert values == {
            "rainfall_1h": 2.5,
            "temperature": 25.1,
            "wind_speed_1m": 3.7,
        }
        assert all(record.payload.station == "AWS 273" for record in outcome.records)
        assert all(record.provenance.observed_at is not None for record in outcome.records)
        assert outcome.receipt(
            connector="aws_observation",
            dataset_id=AwsObservationConnector.dataset_id,
        ).outcome is SourceOutcome.RECORDS

    def test_parses_live_api_time_column_label(self, apihub_settings: Settings) -> None:
        """실응답은 시각 컬럼을 TM이 아니라 YYMMDDHHMI로 표기한다."""
        body = """\
#START7777
# YYMMDDHHMI STN WD1 WS1 WDS WSS WD10 WS10 TA RE RN-15m RN-60m RN-12H RN-DAY HM PA PS TD
# KST ID deg m/s deg m/s deg m/s C 1 mm mm mm mm % hPa hPa C
202608221523,273,55.2,0.8,67.5,1.1,66.1,0.6,29.7,-99.9,0.0,0.0,0.0,0.0,79.3,991.1,1010.4,25.7,=
#7777END
"""
        connector = AwsObservationConnector(settings=apihub_settings)
        outcome = connector.parse(_text_response(body), station_id="273")

        temperature = next(
            record for record in outcome.records if record.payload.kind == "temperature"
        )
        assert temperature.payload.value == 29.7
        assert temperature.provenance.observed_at == datetime(
            2026, 8, 22, 15, 23, tzinfo=KST
        )

    @pytest.mark.parametrize("sentinel", ["-99.9", "-99", "-9"])
    def test_missing_temperature_and_rainfall_are_never_zero(
        self, apihub_settings: Settings, sentinel: str
    ) -> None:
        """결측 센티널을 0으로 바꾸면 0℃ 또는 무강수라는 거짓 실측이 된다.

        풍향은 실측을 넣어 둔다. 전 항목이 결측인 행은 관측 자체가 실패한
        것이라 별도로 거부되며(`test_every_field_missing_is_a_failure`),
        여기서 보려는 것은 개별 센티널이 0이 되지 않는지다.
        """
        body = f"""\
#START7777
# TM STN WD1 TA RN-15m
202608221458,273,180.0,{sentinel},{sentinel},=
#7777END
"""
        connector = AwsObservationConnector(settings=apihub_settings)
        outcome = connector.parse(_text_response(body), station_id="273")

        assert {"temperature", "rainfall_15m"} <= {
            record.payload.kind for record in outcome.records
        }
        missing = [
            record
            for record in outcome.records
            if record.payload.kind in ("temperature", "rainfall_15m")
        ]
        assert all(record.payload.value is None for record in missing)
        assert all(
            QualityFlag.PARTIAL_RESPONSE in record.quality_flags
            for record in missing
        )

    def test_comment_only_success_envelope_is_confirmed_empty(
        self, apihub_settings: Settings
    ) -> None:
        """START/END와 필수 컬럼 헤더가 정상이므로 행이 없다는 사실을 확인할 수 있다.

        임의의 주석만 있는 본문과 달리 API의 시작·종료 표식과 해석 가능한 컬럼
        헤더가 모두 있어 정상 응답 봉투임을 식별할 수 있으므로 confirmed_empty다.
        """
        body = """\
#START7777
# TM STN TA RN-15m
#7777END
"""
        connector = AwsObservationConnector(settings=apihub_settings)
        outcome = connector.parse(_text_response(body), station_id="273")

        assert outcome.outcome is SourceOutcome.CONFIRMED_EMPTY
        assert outcome.is_empty_but_healthy

    async def test_html_error_page_is_failed_not_empty(
        self,
        apihub_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """프록시 HTML 오류를 자료 없음으로 읽으면 조회 실패가 안전으로 둔갑한다."""

        async def html_response(_self, _url, _params):
            return make_response(
                "<html><body>upstream error</body></html>",
                content_type="text/html",
            )

        monkeypatch.setattr(AwsObservationConnector, "_send", html_response)
        connector = AwsObservationConnector(settings=apihub_settings)
        outcome = await connector.fetch(
            end_time=datetime(2026, 8, 22, 14, 51, tzinfo=KST),
            station_id="273",
        )

        assert outcome.outcome is SourceOutcome.FAILED
        assert not outcome.is_empty_but_healthy
        assert outcome.receipt(
            connector="aws_observation",
            dataset_id=connector.dataset_id,
        ).outcome is SourceOutcome.FAILED

    async def test_truncated_row_is_failed_not_zero_filled(
        self,
        apihub_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """짧은 행의 빠진 강수·기온 칸을 0으로 채우면 존재하지 않는 실측이 생긴다."""
        body = """\
#START7777
# TM STN TA RN-15m
202608221458,273,25.1,=
#7777END
"""

        async def truncated_response(_self, _url, _params):
            return _text_response(body)

        monkeypatch.setattr(AwsObservationConnector, "_send", truncated_response)
        connector = AwsObservationConnector(settings=apihub_settings)
        outcome = await connector.fetch(
            end_time=datetime(2026, 8, 22, 14, 52, tzinfo=KST),
            station_id="273",
        )

        assert outcome.outcome is SourceOutcome.FAILED
        assert not outcome.records
        assert not outcome.confirmed_absence


class TestAwsObservationAvailability:
    async def test_missing_credential_is_not_authorized_without_crashing(
        self, keyless_settings: Settings
    ) -> None:
        connector = AwsObservationConnector(settings=keyless_settings)
        outcome = await connector.fetch(station_id="273")

        assert connector.credential is CredentialName.KMA_APIHUB
        assert outcome.outcome is SourceOutcome.FAILED
        assert outcome.degradations[0].status is UpstreamStatus.NOT_AUTHORIZED
        assert "kma_apihub_auth_key" in outcome.degradations[0].detail

    def test_registry_exposes_connector(self, apihub_settings: Settings) -> None:
        connector = Registry(settings=apihub_settings).create("aws_observation")

        assert isinstance(connector, AwsObservationConnector)
        assert connector.available
        # 지역 이름을 지점번호로 오인하지 않도록 region_param을 두지 않는다.
        assert type(connector).region_kwargs("273") == {}


class TestRegionNameIsRejectedNotIgnored:
    """시군 이름을 조용히 무시하면 전국 관측이 그 시군 결과로 보인다."""

    def test_region_name_raises_instead_of_being_dropped(self, settings) -> None:
        connector = AwsObservationConnector(settings=settings)
        with pytest.raises(ValueError, match="시군 이름으로 조회할 수 없습니다"):
            connector.build_params(region="문경시")

    def test_connector_declares_no_region_param(self) -> None:
        """`region_kwargs`가 빈 dict를 주어야 hazard_context가 이름을 넘기지 않는다."""
        assert AwsObservationConnector.region_param is None
        assert AwsObservationConnector.region_kwargs("문경시") == {}

    def test_station_number_is_accepted(self, settings) -> None:
        connector = AwsObservationConnector(settings=settings)
        assert connector.build_params(station_id="273")["stn"] == "273"


class TestEveryFieldMissingIsAFailure:
    """지점이 응답했지만 측정값이 하나도 없으면 관측에 성공한 것이 아니다.

    실제 라이브 호출에서 전 항목 -99.9인 지점이 레코드 12건과 함께 `records`로
    보고됐다. 값이 없는 것이지 기상이 평온한 것이 아니다.
    """

    def test_all_sentinel_row_is_rejected(self, apihub_settings: Settings) -> None:
        body = """\
#START7777
# TM STN WD1 TA RN-15m
202608221458,273,-99.9,-99.9,-99.9,=
#7777END
"""
        connector = AwsObservationConnector(settings=apihub_settings)
        with pytest.raises(ValueError, match="전부 결측"):
            connector.parse(_text_response(body), station_id="273")
