"""파일데이터 커넥터 — 대피시설·산사태취약지역 CSV.

포털 파일데이터는 실시간 API가 아니지만 대피소와 취약지역의 기반이다. 세 가지를
처리한다.

1. **인코딩.** 경북 파일데이터는 CP949다. UTF-8로 읽으면 깨진다.
2. **컬럼명 불일치.** 같은 의미의 컬럼이 기관마다 다른 이름을 쓴다.
3. **좌표 부재.** 상당수 행에 좌표가 없다. 이때 `RiskZone.is_locatable`이
   False가 되어 공간연산에서 제외되고, 그 사실이 품질 플래그로 남는다.

대피소는 특히 조심해서 다룬다. **원본에 재난유형이 명시되지 않으면
`supported_hazards`를 비워 둔다.** 비어 있으면 `Shelter.serves()`가 모든
재난에 False를 반환하므로 자동 배정 대상이 되지 않는다. 지진 대피소를 호우
대피소로 전용하는 사고를 막는 지점이다.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from gbsafe_core.domain import RiskZone, Shelter, ShelterKind
from gbsafe_core.models import GeoPoint, QualityFlag, UpstreamStatus
from gbsafe_core.regions import HazardDomain

from .base import Connector, FetchOutcome, RawResponse

#: 컬럼 의미 → 실제로 관측된 컬럼명 후보. 앞쪽이 우선한다.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("시설명", "대피소명", "명칭", "시설_명", "구역명", "위험지구명", "지구명"),
    "address": ("소재지도로명주소", "소재지지번주소", "주소", "소재지", "위치", "상세주소"),
    "latitude": ("위도", "latitude", "lat", "Y좌표", "y"),
    "longitude": ("경도", "longitude", "lon", "lng", "X좌표", "x"),
    "capacity": ("최대수용인원", "수용인원", "수용가능인원", "정원"),
    "area": ("시설면적", "면적"),
    "agency": ("관리기관명", "관리기관", "담당부서", "관리주체", "시군명"),
    "phone": ("관리기관전화번호", "전화번호", "연락처"),
    "grade": ("위험등급", "등급", "危險등급", "취약등급"),
    "designated_on": ("지정일자", "지정일", "고시일자", "데이터기준일자"),
    "kind": ("대피소구분", "시설구분", "구분", "유형"),
    "households": ("세대수", "가구수"),
    "people": ("인구수", "거주인원", "대상인원"),
}

#: 시설 구분 표기 → 실내/실외.
_OUTDOOR_HINTS = ("옥외", "실외", "운동장", "공터", "광장", "주차장")
_INDOOR_HINTS = ("실내", "체육관", "회관", "학교", "센터", "청사", "교실")

#: 재난유형이 명시된 경우에만 매핑한다. 추정하지 않는다.
_HAZARD_HINTS: tuple[tuple[str, HazardDomain], ...] = (
    ("지진", HazardDomain.EARTHQUAKE),
    ("침수", HazardDomain.FLOOD),
    ("홍수", HazardDomain.FLOOD),
    ("산사태", HazardDomain.LANDSLIDE),
    ("산불", HazardDomain.WILDFIRE),
    ("호우", HazardDomain.HEAVY_RAIN),
    ("풍수해", HazardDomain.HEAVY_RAIN),
    ("한파", HazardDomain.HEATWAVE),
    ("무더위", HazardDomain.HEATWAVE),
    ("폭염", HazardDomain.HEATWAVE),
)


def decode_csv(body: bytes) -> tuple[list[dict[str, str]], str, bool]:
    """CSV를 읽는다. 인코딩을 자동 판별하고 CP949였는지 알려준다.

    반환: (행 목록, 사용한 인코딩, CP949 여부)
    """
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            text = body.decode(encoding)
        except UnicodeDecodeError:
            continue
        reader = csv.DictReader(io.StringIO(text))
        rows = [
            {(key or "").strip(): (value or "").strip() for key, value in row.items()}
            for row in reader
        ]
        if rows:
            return rows, encoding, encoding in ("cp949", "euc-kr")
    return [], "unknown", False


def pick(row: dict[str, str], field: str) -> str | None:
    """별칭 목록에서 값을 찾는다. 부분 일치도 허용한다."""
    aliases = COLUMN_ALIASES.get(field, ())
    for alias in aliases:
        value = row.get(alias)
        if value:
            return value
    normalized = {key.replace(" ", ""): value for key, value in row.items()}
    for alias in aliases:
        value = normalized.get(alias.replace(" ", ""))
        if value:
            return value
    for alias in aliases:
        for key, value in row.items():
            if value and alias.replace(" ", "") in key.replace(" ", ""):
                return value
    return None


def _to_int(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _to_point(row: dict[str, str]) -> tuple[GeoPoint | None, QualityFlag | None]:
    """좌표를 뽑는다. 범위를 벗어나면 다른 좌표계이므로 플래그를 남긴다."""
    lat_raw = pick(row, "latitude")
    lon_raw = pick(row, "longitude")
    if not lat_raw or not lon_raw:
        return None, QualityFlag.MISSING_COORDINATES
    try:
        lat, lon = float(lat_raw), float(lon_raw)
    except ValueError:
        return None, QualityFlag.MISSING_COORDINATES
    try:
        return GeoPoint(lat=lat, lon=lon), None
    except ValueError:
        # EPSG:5179/5186 값이 위경도 컬럼에 들어간 경우가 실제로 있다
        return None, QualityFlag.COORDINATE_OUT_OF_RANGE


def _shelter_kind(raw: str | None, name: str) -> ShelterKind:
    text = f"{raw or ''} {name}"
    if any(hint in text for hint in _OUTDOOR_HINTS):
        return ShelterKind.OUTDOOR
    if any(hint in text for hint in _INDOOR_HINTS):
        return ShelterKind.INDOOR
    return ShelterKind.UNKNOWN


def _declared_hazards(*texts: str | None) -> tuple[HazardDomain, ...]:
    """원본에 명시된 재난유형만 반환한다. 추정하지 않는다."""
    haystack = " ".join(text for text in texts if text)
    found: list[HazardDomain] = []
    for needle, domain in _HAZARD_HINTS:
        if needle in haystack and domain not in found:
            found.append(domain)
    return tuple(found)


class ShelterCsvConnector(Connector[Shelter]):
    """대피시설 CSV를 정규화한다.

    이 커넥터는 네트워크 호출 대신 로컬 파일 또는 전달된 바이트를 읽는다.
    파일데이터 다운로드 URL이 세션에 의존하는 경우가 많아, 취득은 사용자가
    수행하고(문서화됨) 정규화는 여기서 담당한다.
    """

    dataset_id: ClassVar[str] = "3083902"
    credential: ClassVar[None] = None

    def base_url(self) -> str:
        entry = self.entry
        return entry.url if entry and entry.url else "https://www.data.go.kr"

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        return {}

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[Shelter]:
        rows, encoding, was_cp949 = decode_csv(response.body)
        if not rows:
            raise ValueError(
                "CSV에 데이터 행이 없습니다 — 파일이 비었거나 인코딩·구분자가 다릅니다"
            )

        region = kwargs.get("region")
        hazard_hint = kwargs.get("hazard")
        records = []
        skipped_no_coords = 0

        for index, row in enumerate(rows):
            name = pick(row, "name")
            if not name:
                continue
            address = pick(row, "address")
            if region and region not in f"{address or ''} {name}":
                continue

            point, coord_flag = _to_point(row)
            if point is None:
                skipped_no_coords += 1

            kind_raw = pick(row, "kind")
            declared = _declared_hazards(kind_raw, name, address)
            if hazard_hint and declared and HazardDomain(hazard_hint) not in declared:
                continue

            flags = tuple(flag for flag in (coord_flag,) if flag is not None)
            if was_cp949:
                flags = (*flags, QualityFlag.ENCODING_CP949)

            records.append(
                self.record(
                    Shelter(
                        shelter_id=f"{self.dataset_id}-{index}",
                        name=name,
                        kind=_shelter_kind(kind_raw, name),
                        address=address,
                        location=point,
                        capacity=_to_int(pick(row, "capacity")),
                        current_occupancy=None,
                        supported_hazards=declared,
                        operating=None,
                        managing_agency=pick(row, "agency"),
                        contact=pick(row, "phone"),
                        designated=bool(pick(row, "designated_on")),
                    ),
                    response,
                    quality_flags=flags,
                )
            )

        caveats = [
            "현재 운영 여부와 수용인원은 확인되지 않았습니다 — 실시간으로 표시하면 안 됩니다",
        ]
        if skipped_no_coords:
            caveats.append(
                f"{skipped_no_coords}건은 좌표가 없어 거리·경로 계산에 쓸 수 없습니다"
            )
        no_hazard = sum(1 for record in records if not record.payload.supported_hazards)
        if no_hazard:
            caveats.append(
                f"{no_hazard}건은 적용 재난유형이 원본에 없어 자동 배정 대상이 아닙니다"
            )
        if was_cp949:
            caveats.append(f"CP949 인코딩을 {encoding}로 해석했습니다")

        return FetchOutcome(
            records=tuple(records), caveats=tuple(caveats), confirmed_absence=not records
        )


class LandslideRiskZoneCsvConnector(Connector[RiskZone]):
    """산사태취약지역 CSV를 정규화한다.

    문경시 데이터(15123407)는 좌표가 포함되어 있어 공간연산이 가능하다.
    """

    dataset_id: ClassVar[str] = "15123407"
    credential: ClassVar[None] = None

    def base_url(self) -> str:
        entry = self.entry
        return entry.url if entry and entry.url else "https://www.data.go.kr"

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        return {}

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[RiskZone]:
        rows, encoding, was_cp949 = decode_csv(response.body)
        if not rows:
            raise ValueError(
                "CSV에 데이터 행이 없습니다 — 파일이 비었거나 인코딩·구분자가 다릅니다"
            )

        region = kwargs.get("region")
        records = []
        without_coords = 0

        for index, row in enumerate(rows):
            name = pick(row, "name") or pick(row, "address")
            if not name:
                continue
            address = pick(row, "address")
            if region and region not in f"{address or ''} {name}":
                continue

            point, coord_flag = _to_point(row)
            if point is None:
                without_coords += 1
            flags = tuple(flag for flag in (coord_flag,) if flag is not None)
            if was_cp949:
                flags = (*flags, QualityFlag.ENCODING_CP949)

            records.append(
                self.record(
                    RiskZone(
                        zone_id=f"{self.dataset_id}-{index}",
                        hazard=HazardDomain.LANDSLIDE,
                        name=name,
                        address=address,
                        location=point,
                        grade=pick(row, "grade"),
                        managing_agency=pick(row, "agency"),
                        designated_on=pick(row, "designated_on"),
                        households_at_risk=_to_int(pick(row, "households")),
                        people_at_risk=_to_int(pick(row, "people")),
                    ),
                    response,
                    quality_flags=flags,
                )
            )

        caveats = ["사전 지정된 취약지역입니다 — 현재 발생 여부가 아닙니다"]
        if without_coords:
            caveats.append(f"{without_coords}건은 좌표가 없습니다")
        if was_cp949:
            caveats.append(f"CP949 인코딩을 {encoding}로 해석했습니다")
        return FetchOutcome(
            records=tuple(records), caveats=tuple(caveats), confirmed_absence=not records
        )


def local_response(
    path_or_bytes: Any, *, endpoint: str = "local://file", content_type: str = "text/csv"
) -> RawResponse:
    """로컬 CSV를 커넥터에 넣기 위한 RawResponse를 만든다.

    파일데이터는 세션 없이 자동 다운로드가 어려운 경우가 많으므로,
    사용자가 받아둔 파일을 정규화 경로에 태울 수 있게 한다.
    """
    body = (
        path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    )
    return RawResponse(
        body=body,
        content_type=content_type,
        endpoint=endpoint,
        status=UpstreamStatus.OK,
        retrieved_at=datetime.now(UTC),
    )


__all__ = [
    "COLUMN_ALIASES",
    "LandslideRiskZoneCsvConnector",
    "ShelterCsvConnector",
    "decode_csv",
    "local_response",
    "pick",
]
