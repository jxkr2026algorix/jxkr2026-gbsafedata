"""경북 재난 공공데이터 원천 커넥터.

각 커넥터는 하나의 데이터셋을 조회해 `gbsafe_core`의 정규화된 엔티티로
돌려준다. 실패는 예외가 아니라 `Degradation`으로 표현되므로, 조회 실패가
'위험 없음'으로 읽히는 일이 없다.
"""

from __future__ import annotations

from .base import (
    Connector,
    ConnectorError,
    FetchOutcome,
    RawResponse,
    clear_cache,
)
from .filedata import (
    LandslideRiskZoneCsvConnector,
    ShelterCsvConnector,
    decode_csv,
    local_response,
)
from .forest import (
    LandslidePredictionConnector,
    PastLandslideConnector,
    RoadsideLandslideConnector,
    WildfireRiskConnector,
)
from .kma import (
    ShortTermForecastConnector,
    UltraShortNowcastConnector,
    WeatherWarningConnector,
)
from .medical import AirQualityConnector, EmergencyBedsConnector
from .registry import SPECS, ConnectorHealth, ConnectorSpec, Registry, get_registry

__all__ = [
    "SPECS",
    "AirQualityConnector",
    "Connector",
    "ConnectorError",
    "ConnectorHealth",
    "ConnectorSpec",
    "EmergencyBedsConnector",
    "FetchOutcome",
    "LandslidePredictionConnector",
    "LandslideRiskZoneCsvConnector",
    "PastLandslideConnector",
    "RawResponse",
    "Registry",
    "RoadsideLandslideConnector",
    "ShelterCsvConnector",
    "ShortTermForecastConnector",
    "UltraShortNowcastConnector",
    "WeatherWarningConnector",
    "WildfireRiskConnector",
    "clear_cache",
    "decode_csv",
    "get_registry",
    "local_response",
]

__version__ = "0.1.0"
