"""공통 픽스처.

테스트는 네트워크를 쓰지 않는다. 실호출은 개발계정 한도를 소진하고 결과가
시점에 따라 달라져 재현되지 않으므로, 실제 응답 형태를 고정한 스텁으로 검증한다.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from gbsafe_connectors.base import RawResponse
from gbsafe_core.config import Settings
from gbsafe_core.models import (
    DataMode,
    LicenseCode,
    Provenance,
    Record,
    UpstreamStatus,
)
from gbsafe_core.snapshot import SnapshotStore
from pydantic_settings import SettingsConfigDict


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트가 개발자의 .env를 읽지 않게 한다.

    이 픽스처가 없으면 '키 없음' 상황을 검증하려는 테스트가 실제 키를 집어
    실호출을 하고, 통과 여부가 개발자 환경에 따라 달라진다.
    """
    for name in list(os.environ):
        if name.startswith("GBSAFE_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
async def isolate_response_cache():
    """커넥터 응답 캐시를 테스트마다 비운다.

    캐시는 모듈 전역이라 한 테스트가 넣어둔 응답을 다음 테스트가 그대로
    집는다. 실제로 새 테스트 파일이 늘었을 때 관계없는 스냅샷 테스트가
    깨졌고, 단독 실행에서는 통과해 원인을 찾기 어려웠다. 각 테스트가
    `clear_cache()`를 기억해서 부르는 방식은 잊는 순간 같은 일이 다시 난다.
    """
    from gbsafe_connectors.base import clear_cache

    await clear_cache()
    yield
    await clear_cache()


class _NoDotenvSettings(Settings):
    """`.env` 파일을 읽지 않는 설정. 테스트 전용."""

    model_config = SettingsConfigDict(
        env_prefix="GBSAFE_",
        env_file=None,
        extra="ignore",
        frozen=True,
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """스냅샷을 임시 디렉터리에 쓰는 설정. 더미 키를 갖는다."""
    return _NoDotenvSettings(
        data_go_kr_service_key="test-key",
        store_dir=tmp_path / "store",
    )


@pytest.fixture
def keyless_settings(tmp_path: Path) -> Settings:
    """인증키가 전혀 없는 상태. 정상적인 운영 상태로 다뤄져야 한다."""
    return _NoDotenvSettings(store_dir=tmp_path / "store")


@pytest.fixture
def store(settings: Settings) -> SnapshotStore:
    return SnapshotStore.from_settings(settings)


def make_response(
    body: Any,
    *,
    status: UpstreamStatus = UpstreamStatus.OK,
    content_type: str = "application/json",
    endpoint: str = "https://example.test/api",
    retrieved_at: datetime | None = None,
) -> RawResponse:
    """커넥터 parse()에 넣을 응답을 만든다."""
    if isinstance(body, bytes):
        raw = body
    elif isinstance(body, str):
        raw = body.encode("utf-8")
    else:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return RawResponse(
        body=raw,
        content_type=content_type,
        endpoint=endpoint,
        status=status,
        retrieved_at=retrieved_at or datetime.now(UTC),
    )


def make_provenance(
    *,
    dataset_id: str = "15084084",
    license_code: LicenseCode = LicenseCode.KOGL_1,
    mode: DataMode = DataMode.REAL,
    observed_at: datetime | None = None,
    cycle: int | None = 3600,
) -> Provenance:
    return Provenance(
        dataset_id=dataset_id,
        dataset_name="테스트 데이터셋",
        provider="테스트기관",
        source_url="https://example.test/dataset",
        endpoint="https://example.test/api",
        license=license_code,
        mode=mode,
        upstream_status=UpstreamStatus.OK,
        retrieved_at=datetime.now(UTC),
        observed_at=observed_at or datetime.now(UTC),
        expected_cycle_seconds=cycle,
    )


def make_record(payload: Any, **kwargs: Any) -> Record[Any]:
    from gbsafe_core.freshness import evaluate

    provenance = make_provenance(**kwargs)
    return Record(
        payload=payload,
        provenance=provenance,
        freshness=evaluate(
            as_of=provenance.effective_time,
            expected_cycle_seconds=provenance.expected_cycle_seconds,
        ),
    )


@pytest.fixture
def response_factory():
    """커넥터 parse()에 넣을 응답을 만드는 팩토리."""
    return make_response


@pytest.fixture
def record_factory():
    """출처·신선도가 붙은 Record를 만드는 팩토리."""
    return make_record


#: 기상청 초단기실황 실제 응답 형태.
KMA_NOWCAST_BODY: dict[str, Any] = {
    "response": {
        "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
        "body": {
            "items": {
                "item": [
                    {
                        "baseDate": "20260822",
                        "baseTime": "0200",
                        "category": "T1H",
                        "obsrValue": "23.4",
                        "nx": 81,
                        "ny": 106,
                    },
                    {
                        "baseDate": "20260822",
                        "baseTime": "0200",
                        "category": "RN1",
                        "obsrValue": "0",
                        "nx": 81,
                        "ny": 106,
                    },
                    {
                        "baseDate": "20260822",
                        "baseTime": "0200",
                        "category": "PTY",
                        "obsrValue": "0",
                        "nx": 81,
                        "ny": 106,
                    },
                ]
            },
            "totalCount": 3,
        },
    }
}

#: 기상특보 응답. 경북(143)과 타 지역(108), 해제 통보문이 섞여 있다.
KMA_WARNING_BODY: dict[str, Any] = {
    "response": {
        "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
        "body": {
            "items": {
                "item": [
                    {
                        "stnId": "143",
                        "title": "[특보] 제08-62호 : 2026.08.21.13:10 / 호우경보 발표 (*)",
                        "tmFc": 202608211310,
                        "tmSeq": 62,
                    },
                    {
                        "stnId": "143",
                        "title": "[특보] 제08-71호 : 2026.08.21.20:30 / 호우주의보 해제 (*)",
                        "tmFc": 202608212030,
                        "tmSeq": 71,
                    },
                    {
                        "stnId": "108",
                        "title": "[특보] 제08-259호 : 2026.08.22.03:35 / 호우주의보 발표 (*)",
                        "tmFc": 202608220335,
                        "tmSeq": 259,
                    },
                    {
                        "stnId": "136",
                        "title": "[특보] 제08-12호 : 2026.08.21.18:00 / 강풍주의보 발표 (*)",
                        "tmFc": 202608211800,
                        "tmSeq": 12,
                    },
                ]
            }
        },
    }
}

#: 게이트웨이 인증 오류. HTTP 200으로 온다.
GATEWAY_AUTH_ERROR = (
    "<OpenAPI_ServiceResponse><cmmMsgHeader>"
    "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
    "<returnReasonCode>30</returnReasonCode>"
    "</cmmMsgHeader></OpenAPI_ServiceResponse>"
)

#: 응급의료 XML 응답.
EMERGENCY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>00</resultCode></header><body><items>
<item>
  <hpid>A2700006</hpid>
  <dutyName>문경제일병원</dutyName>
  <dutyAddr>경상북도 문경시</dutyAddr>
  <hvec>16</hvec><hvoc>3</hvoc><hvicc>17</hvicc>
  <hvidate>20260822033500</hvidate>
  <dutyTel3>054-000-0000</dutyTel3>
</item>
</items></body></response>"""

#: 대피시설 CSV (CP949로 인코딩해서 쓴다).
SHELTER_CSV = (
    "시설명,소재지도로명주소,위도,경도,최대수용인원,관리기관명,지정일자,대피소구분\n"
    "산북면사무소,경상북도 문경시 산북면,36.6800,128.2500,150,문경시,2024-01-15,실내\n"
    "동로초등학교,경상북도 문경시 동로면,36.7100,128.3200,300,문경시,2024-01-15,실내체육관\n"
    "좌표없는대피소,경상북도 문경시 마성면,,,80,문경시,,\n"
    "지진옥외대피장소,경상북도 문경시 점촌동,36.5900,128.1900,500,문경시,2023-05-01,지진 옥외\n"
)
