"""데이터셋 카탈로그 — 검색·검증·인용 도구의 기반.

카탈로그는 `../jxkr2026-datasets`를 **원천으로 삼아 읽는다.** 그 저장소가 계속
갱신되므로 사본을 박아두면 즉시 낡는다. 대신 다음 순서로 찾는다.

1. `GBSAFE_CATALOG_DIR` 환경변수
2. 이 저장소와 나란히 있는 `../jxkr2026-datasets/catalog`
3. 패키지에 동봉된 폴백 스냅샷 (`data/catalog-fallback.json`)

3번은 데이터셋 저장소 없이 설치한 사용자를 위한 최소 동작 보장이며, 이때
`CatalogSource.origin`이 `fallback`이 되어 응답에 그 사실이 드러난다.

`verified-overrides.json`은 실제 다운로드·호출로 확인해 포털 표기를 덮어쓴
값이다. 포털 메타데이터가 틀린 사례가 반복 확인됐으므로 override가 항상 이긴다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from .freshness import parse_update_cycle
from .licensing import Operation, parse_license, permits
from .models import LicenseCode, QualityFlag
from .regions import HazardDomain

CATALOG_ENV_VAR = "GBSAFE_CATALOG_DIR"
SIBLING_CATALOG = Path("../jxkr2026-datasets/catalog")
FALLBACK_FILE = Path(__file__).parent / "data" / "catalog-fallback.json"


class AccessRoute(StrEnum):
    """데이터를 실제로 어떻게 얻는지.

    포털에 REST로 등록되어 있어도 포털에서 호출되지 않는 경우가 많다
    (91건 중 33건이 외부 포털 경유). 그 구별이 이 필드다.
    """

    DATA_GO_KR_REST = "data_go_kr_rest"
    EXTERNAL_PORTAL = "external_portal"
    PORTAL_DOWNLOAD = "portal_download"
    AGENCY_DOWNLOAD = "agency_download"
    UNKNOWN = "unknown"


class ReviewType(StrEnum):
    AUTO = "auto"
    REVIEW = "review"
    UNKNOWN = "unknown"


class CatalogOrigin(StrEnum):
    ENV = "env"
    SIBLING_REPO = "sibling_repo"
    FALLBACK = "fallback"


_ROUTE_TEXT: tuple[tuple[str, AccessRoute], ...] = (
    ("data.go.kr 직접 호출", AccessRoute.DATA_GO_KR_REST),
    ("외부 포털 경유", AccessRoute.EXTERNAL_PORTAL),
    ("포털 직접 다운로드", AccessRoute.PORTAL_DOWNLOAD),
    ("기관 사이트 다운로드", AccessRoute.AGENCY_DOWNLOAD),
)

_REVIEW_TEXT: tuple[tuple[str, ReviewType], ...] = (
    ("자동승인", ReviewType.AUTO),
    ("심의승인", ReviewType.REVIEW),
    ("심의", ReviewType.REVIEW),
)

#: 키워드 → 재난 유형. 데이터셋 이름·키워드에서 적합 재난을 추정한다.
#: 추정이므로 `DatasetEntry.hazard_domains`는 참고값이며 단독 근거가 아니다.
_HAZARD_KEYWORDS: tuple[tuple[str, HazardDomain], ...] = (
    ("산사태", HazardDomain.LANDSLIDE),
    ("급경사", HazardDomain.LANDSLIDE),
    ("산불", HazardDomain.WILDFIRE),
    ("산림", HazardDomain.WILDFIRE),
    ("호우", HazardDomain.HEAVY_RAIN),
    ("강우", HazardDomain.HEAVY_RAIN),
    ("강수", HazardDomain.HEAVY_RAIN),
    ("기상", HazardDomain.HEAVY_RAIN),
    ("레이더", HazardDomain.HEAVY_RAIN),
    ("침수", HazardDomain.FLOOD),
    ("홍수", HazardDomain.FLOOD),
    ("수위", HazardDomain.FLOOD),
    ("하천", HazardDomain.FLOOD),
    ("저수지", HazardDomain.FLOOD),
    ("배수", HazardDomain.FLOOD),
    ("지진", HazardDomain.EARTHQUAKE),
    ("폭염", HazardDomain.HEATWAVE),
    ("무더위", HazardDomain.HEATWAVE),
    ("한파", HazardDomain.HEATWAVE),
)

#: 검증으로 확인된 결함을 데이터셋에 붙인다.
#: 근거: ../jxkr2026-datasets/docs/data-quality-defects.md
_KNOWN_DEFECTS: dict[str, tuple[QualityFlag, ...]] = {
    "15034538": (QualityFlag.ROW_COUNT_MISMATCH,),
    "15152508": (QualityFlag.ROW_COUNT_MISMATCH, QualityFlag.FORMAT_MISMATCH),
    "15153591": (QualityFlag.EMPTY_DATASET,),
    "15072620": (QualityFlag.EMPTY_DATASET,),
    "15072622": (QualityFlag.EMPTY_DATASET,),
    "15013199": (QualityFlag.UPDATE_CLAIM_MISMATCH,),
    "15140450": (QualityFlag.NOT_MACHINE_READABLE,),
    "15089564": (QualityFlag.PROVIDER_MISMATCH,),
}


class DatasetEntry(BaseModel):
    """카탈로그 한 건. 포털 메타데이터 + 검증 결과."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str
    name: str
    portal_title: str = ""
    category: str = ""
    provider: str = ""
    department: str = ""
    access_route: AccessRoute = AccessRoute.UNKNOWN
    external_portal: str = ""
    api_type: str = ""
    formats: tuple[str, ...] = ()
    license: LicenseCode = LicenseCode.UNKNOWN
    license_raw: str = ""
    review_dev: ReviewType = ReviewType.UNKNOWN
    review_prod: ReviewType = ReviewType.UNKNOWN
    dev_traffic: int | None = None
    update_cycle_raw: str = ""
    update_cycle_seconds: int | None = None
    rows: int | None = None
    modified: str = ""
    url: str = ""
    endpoint: str = ""
    keywords: tuple[str, ...] = ()
    apply_status: str = ""
    note: str = ""
    has_coordinates: bool | None = None
    quality_flags: tuple[QualityFlag, ...] = ()
    hazard_domains: tuple[HazardDomain, ...] = ()
    verified: bool = Field(
        default=False, description="실제 다운로드·호출로 검증되어 override가 적용된 항목"
    )

    @property
    def dev_ready(self) -> bool:
        """개발계정으로 지금 바로 착수할 수 있는지.

        운영단계 심의는 여기 반영하지 않는다. 착수 가능성만 본다.
        """
        return self.review_dev is not ReviewType.REVIEW

    @property
    def usable_now(self) -> bool:
        """빈 등록물·기계판독 불가를 제외한 실사용 가능 여부."""
        blocking = {QualityFlag.EMPTY_DATASET, QualityFlag.NOT_MACHINE_READABLE}
        return not (blocking & set(self.quality_flags))

    def permits(self, operation: Operation) -> bool:
        return permits(self.license, operation)

    def searchable_text(self) -> str:
        return " ".join(
            [
                self.dataset_id,
                self.name,
                self.portal_title,
                self.category,
                self.provider,
                self.department,
                self.external_portal,
                self.note,
                " ".join(self.keywords),
            ]
        ).lower()


@dataclass(frozen=True, slots=True)
class CatalogSource:
    """카탈로그를 어디서 읽었는지. 응답에 실어 보낸다."""

    origin: CatalogOrigin
    path: Path
    loaded_at: datetime
    entry_count: int
    override_count: int

    def describe(self) -> str:
        """사람이 읽을 수 있는 출처 설명.

        절대 경로를 담지 않는다. 이 문자열은 원격 AI 클라이언트와 API 응답으로
        나가므로 서버의 파일시스템 구조를 노출하면 안 된다.
        """
        if self.origin is CatalogOrigin.FALLBACK:
            return (
                f"동봉된 폴백 카탈로그 {self.entry_count}건 — "
                "최신 검증 결과를 쓰려면 jxkr2026-datasets 저장소를 나란히 두거나 "
                f"{CATALOG_ENV_VAR}를 지정하세요"
            )
        source = {
            CatalogOrigin.ENV: f"{CATALOG_ENV_VAR}로 지정된 카탈로그",
            CatalogOrigin.SIBLING_REPO: "나란한 jxkr2026-datasets 카탈로그",
        }.get(self.origin, "카탈로그")
        return f"{source} {self.entry_count}건 (검증 override {self.override_count}건)"

    def describe_local(self) -> str:
        """운영자용 설명. 경로를 포함하므로 로컬 진단에만 쓴다."""
        return f"{self.path} — {self.describe()}"


def _split_multi(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    parts = raw.replace("/", ",").replace("+", ",").split(",")
    return tuple(part.strip() for part in parts if part.strip())


def _parse_int(raw: Any) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _match_enum[T](raw: str | None, table: tuple[tuple[str, T], ...], default: T) -> T:
    if not raw:
        return default
    text = raw.strip()
    for needle, value in table:
        if needle in text:
            return value
    return default


def _infer_hazards(text: str) -> tuple[HazardDomain, ...]:
    found: list[HazardDomain] = []
    for needle, domain in _HAZARD_KEYWORDS:
        if needle in text and domain not in found:
            found.append(domain)
    return tuple(found)


def _build_entry(row: dict[str, Any], *, verified: bool) -> DatasetEntry:
    dataset_id = str(row.get("pk") or row.get("dataset_id") or "").strip()
    name = str(row.get("catalog_name") or row.get("name") or row.get("portal_title") or "").strip()
    cycle_raw = str(row.get("update_cycle") or "")
    haystack = f"{name} {row.get('portal_title', '')} {row.get('keywords', '')}"

    flags = list(_KNOWN_DEFECTS.get(dataset_id, ()))
    has_coords_raw = str(row.get("has_coords") or "").strip().upper()
    has_coordinates = {"Y": True, "N": False}.get(has_coords_raw)
    if has_coordinates is False and QualityFlag.MISSING_COORDINATES not in flags:
        flags.append(QualityFlag.MISSING_COORDINATES)

    return DatasetEntry(
        dataset_id=dataset_id,
        name=name or dataset_id,
        portal_title=str(row.get("portal_title") or ""),
        category=str(row.get("category") or ""),
        provider=str(row.get("provider") or ""),
        department=str(row.get("dept") or ""),
        access_route=_match_enum(
            str(row.get("access_route") or ""), _ROUTE_TEXT, AccessRoute.UNKNOWN
        ),
        external_portal=str(row.get("external_portal") or ""),
        api_type=str(row.get("api_type") or ""),
        formats=_split_multi(str(row.get("format") or "")),
        license=parse_license(str(row.get("license") or row.get("license_raw") or "")),
        license_raw=str(row.get("license_raw") or row.get("license") or ""),
        review_dev=_match_enum(str(row.get("review_dev") or ""), _REVIEW_TEXT, ReviewType.UNKNOWN),
        review_prod=_match_enum(
            str(row.get("review_prod") or ""), _REVIEW_TEXT, ReviewType.UNKNOWN
        ),
        dev_traffic=_parse_int(row.get("dev_traffic")),
        update_cycle_raw=cycle_raw,
        update_cycle_seconds=parse_update_cycle(cycle_raw),
        rows=_parse_int(row.get("rows")),
        modified=str(row.get("modified") or ""),
        url=str(row.get("url") or ""),
        endpoint=str(row.get("endpoint") or ""),
        keywords=_split_multi(str(row.get("keywords") or "")),
        apply_status=str(row.get("apply_status") or ""),
        note=str(row.get("note") or ""),
        has_coordinates=has_coordinates,
        quality_flags=tuple(flags),
        hazard_domains=_infer_hazards(haystack),
        verified=verified,
    )


def _candidate_dirs() -> list[tuple[CatalogOrigin, Path]]:
    """환경변수가 없을 때 탐색할 경로. 환경변수는 `load()`가 직접 다룬다."""
    candidates: list[tuple[CatalogOrigin, Path]] = []
    # 이 파일 기준으로도, 현재 작업 디렉터리 기준으로도 찾는다.
    package_root = Path(__file__).resolve().parents[4]
    sibling = package_root.parent / "jxkr2026-datasets" / "catalog"
    candidates.append((CatalogOrigin.SIBLING_REPO, sibling))
    candidates.append((CatalogOrigin.SIBLING_REPO, Path.cwd() / SIBLING_CATALOG))
    return candidates


class CatalogUnavailable(RuntimeError):
    """지정된 카탈로그를 읽을 수 없을 때.

    명시적 설정이 조용히 무시되면 사용자가 잘못된 데이터를 근거로 판단한다.
    """


class Catalog:
    """검색·검증·인용 대상이 되는 데이터셋 목록."""

    def __init__(self, entries: dict[str, DatasetEntry], source: CatalogSource) -> None:
        self._entries = entries
        self.source = source

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries.values())

    @classmethod
    def load(cls, directory: Path | None = None) -> Self:
        """카탈로그를 읽는다.

        명시적으로 지정된 경로(인자 또는 `GBSAFE_CATALOG_DIR`)를 읽지 못하면
        **예외를 던진다.** 조용히 다른 카탈로그로 넘어가면 사용자는 자기가 지정한
        데이터를 보고 있다고 믿으면서 다른 데이터를 보게 된다.

        지정이 없을 때만 나란한 저장소 → 동봉 폴백 순으로 찾는다.
        """
        explicit = directory
        origin = CatalogOrigin.ENV
        if explicit is None:
            env_value = os.environ.get(CATALOG_ENV_VAR)
            if env_value:
                explicit = Path(env_value).expanduser()

        if explicit is not None:
            entries, overrides = _read_directory(explicit)
            if not entries:
                raise CatalogUnavailable(
                    f"지정된 카탈로그 경로를 읽을 수 없습니다: {explicit}\n"
                    f"'{explicit / 'datago-datasets.json'}'이 존재하고 올바른 JSON 배열인지 "
                    f"확인하세요. 자동 탐색을 쓰려면 {CATALOG_ENV_VAR}를 비우세요."
                )
            return cls(
                entries,
                CatalogSource(origin, explicit, datetime.now(UTC), len(entries), overrides),
            )

        for candidate_origin, path in _candidate_dirs():
            entries, overrides = _read_directory(path)
            if entries:
                return cls(
                    entries,
                    CatalogSource(
                        candidate_origin, path, datetime.now(UTC), len(entries), overrides
                    ),
                )

        entries = _read_fallback()
        if not entries:
            raise CatalogUnavailable(
                "카탈로그를 찾을 수 없습니다. jxkr2026-datasets 저장소를 나란히 두거나 "
                f"{CATALOG_ENV_VAR}로 경로를 지정하세요."
            )
        return cls(
            entries,
            CatalogSource(
                CatalogOrigin.FALLBACK, FALLBACK_FILE, datetime.now(UTC), len(entries), 0
            ),
        )

    def get(self, dataset_id: str) -> DatasetEntry | None:
        return self._entries.get(dataset_id.strip())

    def search(
        self,
        query: str = "",
        *,
        hazard: HazardDomain | None = None,
        route: AccessRoute | None = None,
        dev_ready_only: bool = False,
        usable_only: bool = False,
        permits_operation: Operation | None = None,
        limit: int = 20,
    ) -> tuple[DatasetEntry, ...]:
        """자연어 토큰과 조건으로 데이터셋을 찾는다.

        점수는 단순 토큰 일치 수다. 정교한 검색보다 **왜 이 결과가 나왔는지
        설명 가능한 것**이 중요하다.
        """
        tokens = [token for token in query.lower().split() if token]
        scored: list[tuple[int, DatasetEntry]] = []

        for entry in self._entries.values():
            if hazard is not None and hazard not in entry.hazard_domains:
                continue
            if route is not None and entry.access_route is not route:
                continue
            if dev_ready_only and not entry.dev_ready:
                continue
            if usable_only and not entry.usable_now:
                continue
            if permits_operation is not None and not entry.permits(permits_operation):
                continue

            if not tokens:
                scored.append((0, entry))
                continue

            haystack = entry.searchable_text()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                # 이름에 직접 걸린 항목을 우선한다
                name_hits = sum(1 for token in tokens if token in entry.name.lower())
                scored.append((score * 10 + name_hits, entry))

        scored.sort(key=lambda pair: (-pair[0], pair[1].dataset_id))
        return tuple(entry for _, entry in scored[:limit])

    def by_hazard(self, hazard: HazardDomain) -> tuple[DatasetEntry, ...]:
        return tuple(entry for entry in self._entries.values() if hazard in entry.hazard_domains)

    def defects(self) -> tuple[DatasetEntry, ...]:
        return tuple(entry for entry in self._entries.values() if entry.quality_flags)

    def summary(self) -> dict[str, Any]:
        """카탈로그 전체 통계. doctor·health 응답에 쓴다."""
        routes: dict[str, int] = {}
        licenses: dict[str, int] = {}
        for entry in self._entries.values():
            routes[entry.access_route.value] = routes.get(entry.access_route.value, 0) + 1
            licenses[entry.license.value] = licenses.get(entry.license.value, 0) + 1
        return {
            "total": len(self._entries),
            "origin": self.source.origin.value,
            "verified": sum(1 for entry in self._entries.values() if entry.verified),
            "dev_ready": sum(1 for entry in self._entries.values() if entry.dev_ready),
            "with_defects": len(self.defects()),
            "derivable": sum(
                1 for entry in self._entries.values() if entry.permits(Operation.DERIVE)
            ),
            "by_route": routes,
            "by_license": licenses,
        }


def _read_directory(directory: Path) -> tuple[dict[str, DatasetEntry], int]:
    """카탈로그 디렉터리를 읽는다. 없거나 깨졌으면 빈 결과."""
    if not directory.is_dir():
        return {}, 0

    rows: list[dict[str, Any]] = []
    main = directory / "datago-datasets.json"
    if main.is_file():
        try:
            payload = json.loads(main.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}, 0
        if isinstance(payload, list):
            rows = [row for row in payload if isinstance(row, dict)]
        elif isinstance(payload, dict):
            nested = payload.get("datasets")
            if isinstance(nested, list):
                rows = [row for row in nested if isinstance(row, dict)]

    if not rows:
        return {}, 0

    overrides: dict[str, dict[str, Any]] = {}
    override_file = directory / "verified-overrides.json"
    if override_file.is_file():
        try:
            payload = json.loads(override_file.read_text(encoding="utf-8"))
            raw = payload.get("overrides") if isinstance(payload, dict) else None
            if isinstance(raw, dict):
                overrides = {
                    key: value for key, value in raw.items() if isinstance(value, dict)
                }
        except (json.JSONDecodeError, OSError):
            overrides = {}

    routing: dict[str, str] = {}
    routing_file = directory / "link-routing.json"
    if routing_file.is_file():
        try:
            payload = json.loads(routing_file.read_text(encoding="utf-8"))
            raw = payload.get("routing") if isinstance(payload, dict) else None
            if isinstance(raw, dict):
                routing = {key: str(value) for key, value in raw.items()}
        except (json.JSONDecodeError, OSError):
            routing = {}

    entries: dict[str, DatasetEntry] = {}
    applied = 0
    for row in rows:
        dataset_id = str(row.get("pk") or "").strip()
        if not dataset_id:
            continue
        merged = dict(row)
        override = overrides.get(dataset_id)
        if override:
            merged.update(override)
            applied += 1
        if dataset_id in routing and not merged.get("external_portal"):
            merged["external_portal"] = routing[dataset_id]
        entries[dataset_id] = _build_entry(merged, verified=bool(override))

    # 보완 발굴 목록도 카탈로그에 합친다 (경북 시군 파일데이터)
    supplementary = directory / "gyeongbuk-supplementary.csv"
    if supplementary.is_file():
        for row in _read_csv(supplementary):
            dataset_id = str(row.get("pk") or "").strip()
            if dataset_id and dataset_id not in entries:
                row.setdefault("category", "경북 보완 발굴")
                row.setdefault("access_route", "포털 직접 다운로드")
                entries[dataset_id] = _build_entry(row, verified=False)

    return entries, applied


def _read_csv(path: Path) -> list[dict[str, Any]]:
    import csv

    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


def _read_fallback() -> dict[str, DatasetEntry]:
    if not FALLBACK_FILE.is_file():
        return {}
    try:
        payload = json.loads(FALLBACK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    rows = payload.get("datasets") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {}
    entries: dict[str, DatasetEntry] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        entry = _build_entry(row, verified=bool(row.get("verified")))
        if entry.dataset_id:
            entries[entry.dataset_id] = entry
    return entries


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return Catalog.load()


def reset_catalog_cache() -> None:
    get_catalog.cache_clear()
