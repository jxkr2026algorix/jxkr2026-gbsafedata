"""GB SafeData 정제·통합 계층.

경북 재난대피 공공데이터를 AI와 외부 시스템이 **출처와 함께** 조회할 수 있게
만드는 기반이다. 이 패키지는 값을 계산하지 않고, 값이 어디서 왔고 언제 것이며
무엇을 해도 되는지를 잃지 않게 만든다.

핵심 원칙:

- 출처(`Provenance`) 없이 값이 이동하지 않는다
- 라이선스가 금지한 연산은 실행되지 않는다 (`licensing.require`)
- 신선도와 원천 장애는 응답에서 숨겨지지 않는다 (`Answer.degradations`)
- 실데이터와 훈련데이터는 시스템 차원에서 분리된다 (`DataMode`)
- 안전 경계는 예외로 강제된다 (`safety`)
"""

from __future__ import annotations

from .catalog import AccessRoute, Catalog, DatasetEntry, ReviewType, get_catalog
from .config import CredentialName, Settings, get_settings
from .domain import (
    AlertAction,
    DatasetDescriptor,
    HazardAlert,
    MedicalCapacity,
    Observation,
    PopulationProfile,
    RiskZone,
    RoadObstruction,
    Severity,
    Shelter,
    ShelterKind,
    parse_alert_action,
)
from .freshness import evaluate as evaluate_freshness
from .freshness import parse_update_cycle
from .freshness import unknown as unknown_freshness
from .licensing import LicenseViolation, Operation, attribution_notice, parse_license
from .licensing import permits as license_permits
from .licensing import require as require_license
from .models import (
    Answer,
    BBox,
    Citation,
    DataMode,
    Degradation,
    Freshness,
    FreshnessStatus,
    GeoPoint,
    LicenseCode,
    Provenance,
    QualityFlag,
    Record,
    UpstreamStatus,
)
from .regions import (
    GYEONGBUK_BBOX,
    SIGUNGU,
    HazardDomain,
    KmaGrid,
    Sigungu,
    find_sigungu,
    haversine_km,
    to_kma_grid,
)
from .safety import SafetyViolation, assert_read_only
from .snapshot import SnapshotRef, SnapshotStore

__all__ = [
    "GYEONGBUK_BBOX",
    "SIGUNGU",
    "AccessRoute",
    "AlertAction",
    "Answer",
    "BBox",
    "Catalog",
    "Citation",
    "CredentialName",
    "DataMode",
    "DatasetDescriptor",
    "DatasetEntry",
    "Degradation",
    "Freshness",
    "FreshnessStatus",
    "GeoPoint",
    "HazardAlert",
    "HazardDomain",
    "KmaGrid",
    "LicenseCode",
    "LicenseViolation",
    "MedicalCapacity",
    "Observation",
    "Operation",
    "PopulationProfile",
    "Provenance",
    "QualityFlag",
    "Record",
    "ReviewType",
    "RiskZone",
    "RoadObstruction",
    "SafetyViolation",
    "Settings",
    "Severity",
    "Shelter",
    "ShelterKind",
    "Sigungu",
    "SnapshotRef",
    "SnapshotStore",
    "UpstreamStatus",
    "assert_read_only",
    "attribution_notice",
    "evaluate_freshness",
    "find_sigungu",
    "get_catalog",
    "get_settings",
    "haversine_km",
    "license_permits",
    "parse_alert_action",
    "parse_license",
    "parse_update_cycle",
    "require_license",
    "to_kma_grid",
    "unknown_freshness",
]

__version__ = "0.1.0"
