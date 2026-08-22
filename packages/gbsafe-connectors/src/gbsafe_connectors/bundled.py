"""동봉 CSV로 답하는 원천.

포털 파일데이터는 세션 의존 다운로드라 자동 취득이 안 된다. 그래서 조사
저장소가 받아 UTF-8로 변환해 둔 것을 `data/`에 함께 담고 여기서 읽는다.

화학사고 대피장소가 대표적이다. **화학물질관리법 제23조의4에 따른 법정 지정
대피장소**이고 인증키 없이 받을 수 있는데, 정작 화학사고는 실시간 탐지 축이
없어 `blocked`으로 분류된다. 탐지가 없다고 대피소까지 감추면, 실제로 갖고 있는
정보를 못 쓰게 된다 — 그래서 대피소는 제공하되 **탐지가 없다는 사실을 함께**
내보낸다.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from gbsafe_core.config import CredentialName, Settings
from gbsafe_core.domain import Shelter, ShelterKind
from gbsafe_core.models import GeoPoint, QualityFlag, UpstreamStatus
from gbsafe_core.regions import (
    GYEONGBUK_BBOX,
    SIDO_NAME_FULL,
    SIDO_NAME_SHORT,
    HazardDomain,
    find_sigungu,
)

from .base import (
    Connector,
    FetchOutcome,
    RawResponse,
    confirmed_empty,
    missing_or_impossible,
)

#: 동봉 파일 이름. 조사 저장소의 UTF-8 변환본과 같다.
CHEMICAL_SHELTER_FILE = "15128910_전국_화학사고대피장소(좌표포함).csv"


def _float(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(raw: Any) -> int | None:
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    # -99.9를 999로 만들지 않는다. 부호를 떼면 결측이 큰 수용인원이 된다.
    if missing_or_impossible(parsed):
        return None
    return int(parsed)


class ChemicalShelterConnector(Connector[Shelter]):
    """전국 화학사고 대피장소 중 경북분.

    법정 지정 대피장소라 위경도·수용인원·관리기관이 모두 들어 있다. 다만
    화학사고는 실시간 탐지 원천이 없으므로, 이 대피소만 보고 "화학사고 대응이
    된다"고 읽으면 안 된다.
    """

    dataset_id: ClassVar[str] = "15128910"
    credential: ClassVar[CredentialName | None] = None
    region_param: ClassVar[str | None] = "region"

    @property
    def dataset_name(self) -> str:
        entry = self.entry
        return entry.name if entry else "전국 화학사고대피장소"

    @property
    def provider(self) -> str:
        entry = self.entry
        return entry.provider if entry else "환경부 화학물질안전원"

    def source_path(self) -> Path:
        settings: Settings = self._settings
        return settings.data_dir / "gyeongbuk" / CHEMICAL_SHELTER_FILE

    def base_url(self) -> str:
        return f"file://{self.source_path()}"

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        return {}

    async def fetch(self, **kwargs: Any) -> FetchOutcome[Shelter]:
        """동봉 파일을 읽는다. 네트워크를 쓰지 않는다."""
        path = self.source_path()
        if not path.is_file():
            return self._degrade(
                UpstreamStatus.UNAVAILABLE,
                f"동봉 파일이 없습니다: {path}. 이 원천은 네트워크로 받을 수 "
                "없으므로 파일이 없으면 대피소를 확인할 수 없습니다 — "
                "대피소가 없다는 뜻이 아닙니다.",
            )
        try:
            body = path.read_bytes()
        except OSError as error:
            return self._degrade(
                UpstreamStatus.UNAVAILABLE, f"동봉 파일을 읽지 못했습니다: {error}"
            )

        response = RawResponse(
            body=body,
            content_type="text/csv",
            endpoint=self.base_url(),
            status=UpstreamStatus.OK,
            retrieved_at=datetime.now(UTC),
        )
        try:
            return self.parse(response, **kwargs)
        except ValueError as error:
            return self._degrade(UpstreamStatus.DEGRADED, str(error))

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[Shelter]:
        try:
            text = response.body.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError(f"CSV 인코딩을 읽지 못했습니다: {error}") from error

        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            # 파일은 있는데 행이 없다. 읽지 못한 것과 구별되지 않으므로
            # '해당 없음'으로 읽지 않는다.
            raise ValueError("CSV에 데이터 행이 없습니다 — 파일이 비었거나 형식이 다릅니다")

        region = kwargs.get("region")
        sigungu = find_sigungu(str(region)) if region else None

        records = []
        missing_coords = 0
        for index, row in enumerate(rows):
            address = str(row.get("도로명주소") or "").strip()
            # 주소 **첫머리**가 경북이어야 한다. 부분일치로 보면
            # "서울특별시 경북대로 1"이 경북 대피소로 들어온다.
            if not address.startswith((SIDO_NAME_FULL, SIDO_NAME_SHORT)):
                continue
            if sigungu is not None and sigungu.name.rstrip("시군") not in address:
                continue

            name = str(row.get("대피장소명") or "").strip()
            if not name:
                continue

            lat, lon = _float(row.get("위도")), _float(row.get("경도"))
            point = None
            flags: list[QualityFlag] = []
            if lat is not None and lon is not None:
                try:
                    candidate = GeoPoint(lat=lat, lon=lon)
                except ValueError:
                    flags.append(QualityFlag.COORDINATE_OUT_OF_RANGE)
                else:
                    # 한반도 범위만 통과시키면 서울 좌표가 경북 대피소로 남는다.
                    # 좌표가 주소와 어긋나면 대피 안내가 다른 도로 보낸다.
                    if GYEONGBUK_BBOX.contains(candidate):
                        point = candidate
                    else:
                        flags.append(QualityFlag.COORDINATE_OUT_OF_RANGE)
            if point is None:
                missing_coords += 1
                flags.append(QualityFlag.MISSING_COORDINATES)

            detail = str(row.get("세부위치명") or "").strip()
            records.append(
                self.record(
                    Shelter(
                        shelter_id=f"{self.dataset_id}-{index}",
                        name=f"{name} {detail}".strip(),
                        kind=ShelterKind.INDOOR,
                        address=address or None,
                        location=point,
                        capacity=_int(row.get("수용인원")),
                        current_occupancy=None,
                        designated=True,
                        managing_agency=str(row.get("관리기관명") or "").strip() or None,
                        contact=str(row.get("관리기관전화번호") or "").strip() or None,
                        supported_hazards=(HazardDomain.CHEMICAL_ACCIDENT,),
                    ),
                    response,
                    quality_flags=tuple(flags),
                )
            )

        if not records:
            if sigungu is not None:
                return confirmed_empty(
                    f"{sigungu.name}에는 지정된 화학사고 대피장소가 없습니다 — "
                    "전국 목록을 읽었고 이 시군 항목이 없는 것입니다."
                )
            raise ValueError("경북 화학사고 대피장소를 하나도 읽지 못했습니다")

        caveats = [
            "화학물질관리법 제23조의4에 따른 법정 지정 대피장소입니다",
            "화학사고는 실시간 탐지 원천이 없습니다 — 대피장소를 알 수 있다고 "
            "사고 발생 여부를 알 수 있는 것은 아닙니다",
            "실제 사고 시 풍향에 따라 대피 방향이 달라집니다 — 가까운 곳이 "
            "안전한 곳이 아닐 수 있습니다",
        ]
        if missing_coords:
            caveats.append(f"좌표가 없는 대피장소 {missing_coords}곳은 거리 계산에 쓸 수 없습니다")

        return FetchOutcome(records=tuple(records), caveats=tuple(caveats))
