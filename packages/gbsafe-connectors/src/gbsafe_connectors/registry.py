"""커넥터 레지스트리 — 어떤 데이터 원천이 지금 쓸 수 있는지.

API·MCP·CLI가 공통으로 쓰는 진입점이다. 두 가지를 제공한다.

1. **이름으로 커넥터 조회.** 도구가 커넥터 클래스를 직접 알 필요가 없다.
2. **상태 보고.** 각 원천이 사용 가능한지, 불가능하면 왜인지. 키가 없거나 심의
   대기 중인 상태를 '오류'가 아니라 정상적인 운영 상태로 다룬다.

`health()`가 반환하는 목록은 화면과 `/v1/health`에 그대로 나가며, 사용자는
"산사태 정보가 안 보이는 이유가 무엇인지"를 추측하지 않고 확인할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gbsafe_core.catalog import Catalog, get_catalog
from gbsafe_core.config import CREDENTIAL_SOURCES, CredentialName, Settings, get_settings
from gbsafe_core.regions import HazardDomain
from gbsafe_core.snapshot import SnapshotStore

from .apihub import AwsObservationConnector
from .base import Connector
from .filedata import LandslideRiskZoneCsvConnector, ShelterCsvConnector
from .forest import (
    LandslidePredictionConnector,
    PastLandslideConnector,
    RoadsideLandslideConnector,
    WildfireRiskConnector,
)
from .hrfco import FloodForecastConnector, RiverLevelConnector
from .kma import (
    ShortTermForecastConnector,
    UltraShortNowcastConnector,
    WeatherWarningConnector,
)
from .medical import AirQualityConnector, EmergencyBedsConnector


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    """레지스트리에 등록된 커넥터 하나의 설명."""

    name: str
    factory: type[Connector[Any]]
    summary: str
    hazards: tuple[HazardDomain, ...]
    requires_local_file: bool = False

    @property
    def dataset_id(self) -> str:
        return self.factory.dataset_id


#: 등록된 커넥터. 이름은 MCP 도구·API 파라미터에서 그대로 쓰인다.
SPECS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec(
        name="aws_observation",
        factory=AwsObservationConnector,
        summary="AWS 방재기상관측 — 지점별 1분 기온·강우·바람 (기상청 API허브)",
        hazards=(HazardDomain.HEAVY_RAIN, HazardDomain.WILDFIRE),
    ),
    ConnectorSpec(
        name="weather_now",
        factory=UltraShortNowcastConnector,
        summary="초단기실황 — 현재 강우·기온·습도·풍속 (기상청)",
        hazards=(HazardDomain.HEAVY_RAIN, HazardDomain.WILDFIRE),
    ),
    ConnectorSpec(
        name="weather_forecast",
        factory=ShortTermForecastConnector,
        summary="단기예보 — 향후 강수·기온 추이 (기상청)",
        hazards=(HazardDomain.HEAVY_RAIN, HazardDomain.WILDFIRE),
    ),
    ConnectorSpec(
        name="weather_warning",
        factory=WeatherWarningConnector,
        summary="기상특보 — 호우·대설·강풍 주의보·경보 (기상청)",
        hazards=(HazardDomain.HEAVY_RAIN, HazardDomain.HEATWAVE),
    ),
    ConnectorSpec(
        name="wildfire_risk",
        factory=WildfireRiskConnector,
        summary="산불위험예보 — 시도·시군구 위험지수 (산림청)",
        hazards=(HazardDomain.WILDFIRE,),
    ),
    ConnectorSpec(
        name="landslide_forecast",
        factory=LandslidePredictionConnector,
        summary="산사태 예측정보 — 시군구별 예보단계 (산림청, 개발단계 심의 필요)",
        hazards=(HazardDomain.LANDSLIDE,),
    ),
    ConnectorSpec(
        name="landslide_roadside",
        factory=RoadsideLandslideConnector,
        summary="도로변 산사태 취약구간 — 대피경로 단절 위험 (산림청)",
        hazards=(HazardDomain.LANDSLIDE,),
    ),
    ConnectorSpec(
        name="landslide_history",
        factory=PastLandslideConnector,
        summary="과거 산사태 발생 이력 (산림청)",
        hazards=(HazardDomain.LANDSLIDE,),
    ),
    ConnectorSpec(
        name="emergency_beds",
        factory=EmergencyBedsConnector,
        summary="응급실 실시간 가용병상 (국립중앙의료원)",
        hazards=(HazardDomain.OTHER,),
    ),
    ConnectorSpec(
        name="air_quality",
        factory=AirQualityConnector,
        summary="시도별 실시간 대기오염도 — 산불 연무 보조지표 (한국환경공단)",
        hazards=(HazardDomain.WILDFIRE,),
    ),
    ConnectorSpec(
        name="river_level",
        factory=RiverLevelConnector,
        summary="경북 하천 실시간 수위 + 기관 고시 임계수위 (한강홍수통제소)",
        hazards=(HazardDomain.FLOOD, HazardDomain.HEAVY_RAIN),
    ),
    ConnectorSpec(
        name="flood_forecast",
        factory=FloodForecastConnector,
        summary="홍수특보 발령 현황 (한강홍수통제소)",
        hazards=(HazardDomain.FLOOD, HazardDomain.HEAVY_RAIN),
    ),
    ConnectorSpec(
        name="shelters",
        factory=ShelterCsvConnector,
        summary="대피시설 CSV 정규화 (행정안전부 표준데이터)",
        hazards=(HazardDomain.HEAVY_RAIN, HazardDomain.LANDSLIDE, HazardDomain.EARTHQUAKE),
        requires_local_file=True,
    ),
    ConnectorSpec(
        name="landslide_zones",
        factory=LandslideRiskZoneCsvConnector,
        summary="산사태취약지역 CSV 정규화 (경북 시군)",
        hazards=(HazardDomain.LANDSLIDE,),
        requires_local_file=True,
    ),
)

_BY_NAME: dict[str, ConnectorSpec] = {spec.name: spec for spec in SPECS}


@dataclass(frozen=True, slots=True)
class ConnectorHealth:
    """한 커넥터의 현재 상태."""

    name: str
    dataset_id: str
    dataset_name: str
    provider: str
    summary: str
    available: bool
    reason: str | None
    requires_local_file: bool
    license: str
    dev_review_required: bool
    snapshot_count: int
    last_snapshot_at: str | None
    hazards: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dataset_id": self.dataset_id,
            "dataset_name": self.dataset_name,
            "provider": self.provider,
            "summary": self.summary,
            "available": self.available,
            "reason": self.reason,
            "requires_local_file": self.requires_local_file,
            "license": self.license,
            "dev_review_required": self.dev_review_required,
            "snapshot_count": self.snapshot_count,
            "last_snapshot_at": self.last_snapshot_at,
            "hazards": list(self.hazards),
        }


class Registry:
    """커넥터 생성과 상태 조회를 담당한다."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        catalog: Catalog | None = None,
        store: SnapshotStore | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._catalog = catalog or get_catalog()
        self._store = store or SnapshotStore.from_settings(self._settings)

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @property
    def store(self) -> SnapshotStore:
        return self._store

    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in SPECS)

    def spec(self, name: str) -> ConnectorSpec | None:
        return _BY_NAME.get(name.strip())

    def create(self, name: str) -> Connector[Any]:
        """이름으로 커넥터를 만든다. 없는 이름이면 사용 가능한 목록을 알려준다."""
        spec = self.spec(name)
        if spec is None:
            available = ", ".join(self.names())
            raise KeyError(f"'{name}' 커넥터가 없습니다. 사용 가능: {available}")
        return spec.factory(
            settings=self._settings, catalog=self._catalog, store=self._store
        )

    def all_specs(self) -> tuple[ConnectorSpec, ...]:
        return SPECS

    def specs_for_dataset(self, dataset_id: str) -> tuple[ConnectorSpec, ...]:
        """한 데이터셋을 다루는 커넥터들. 같은 데이터셋에 여러 오퍼레이션이 있다."""
        target = dataset_id.strip()
        return tuple(spec for spec in SPECS if spec.dataset_id == target)

    def for_hazard(self, hazard: HazardDomain) -> tuple[ConnectorSpec, ...]:
        return tuple(spec for spec in SPECS if hazard in spec.hazards)

    def health(self) -> tuple[ConnectorHealth, ...]:
        """전체 커넥터 상태. 사용 불가 사유를 사람이 읽을 수 있게 담는다."""
        report = []
        for spec in SPECS:
            connector = self.create(spec.name)
            entry = connector.entry
            history = self._store.history(spec.dataset_id)

            # 키가 있어도 개발단계 심의 대기 중이면 실제 호출은 403이 된다.
            # 키 보유만 보고 '사용 가능'으로 보고하면 진단이 거짓이 된다.
            pending_review = bool(entry and not entry.dev_ready)
            reason = connector.unavailable_reason()
            if reason is None and pending_review:
                reason = (
                    "개발단계 심의승인 대상 — 활용신청이 승인되기 전까지 호출이 거부됩니다"
                )
            if reason is None and spec.requires_local_file:
                reason = "포털에서 CSV를 내려받아 전달해야 합니다 (자동 다운로드가 세션에 의존)"

            report.append(
                ConnectorHealth(
                    name=spec.name,
                    dataset_id=spec.dataset_id,
                    dataset_name=connector.dataset_name,
                    provider=connector.provider,
                    summary=spec.summary,
                    available=(
                        connector.available
                        and not spec.requires_local_file
                        and not pending_review
                    ),
                    reason=reason,
                    requires_local_file=spec.requires_local_file,
                    license=entry.license.value if entry else "unknown",
                    dev_review_required=bool(entry and not entry.dev_ready),
                    snapshot_count=len(history),
                    last_snapshot_at=history[-1].stored_at.isoformat() if history else None,
                    hazards=tuple(hazard.value for hazard in spec.hazards),
                )
            )
        return tuple(report)

    def credential_status(self) -> dict[str, dict[str, Any]]:
        """인증 정보별 보유 여부와 발급 경로."""
        return {
            name.value: {
                "present": self._settings.has(name),
                "source": CREDENTIAL_SOURCES[name],
            }
            for name in CredentialName
        }

    def summary(self) -> dict[str, Any]:
        health = self.health()
        return {
            "connectors": len(health),
            "available": sum(1 for item in health if item.available),
            "blocked_by_credentials": sum(
                1
                for item in health
                if not item.available
                and not item.requires_local_file
                and not item.dev_review_required
            ),
            "pending_review": sum(1 for item in health if item.dev_review_required),
            "requires_local_file": sum(1 for item in health if item.requires_local_file),
            "catalog": self._catalog.summary(),
            "offline_mode": self._settings.offline,
        }


def get_registry() -> Registry:
    """기본 설정으로 레지스트리를 만든다."""
    return Registry()


__all__ = [
    "SPECS",
    "ConnectorHealth",
    "ConnectorSpec",
    "Registry",
    "get_registry",
]
