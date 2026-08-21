"""경북 행정구역과 좌표 변환.

두 가지 실제 문제를 다룬다.

1. **기관마다 지역 식별자가 다르다.** 어떤 API는 시도명 문자열("경상북도" 또는
   "경북")을, 어떤 API는 5자리 시군구 코드를, 기상청은 격자 좌표(nx/ny)를,
   ASOS는 지점번호를 요구한다. 같은 문경시를 네 가지로 불러야 한다.

2. **기상청 격자 변환식이 공개 페이지에 없다.** nx/ny는 위경도가 아니며 변환식은
   첨부 ZIP에만 있다(../jxkr2026-datasets/docs/api-operations.md). 그래서
   Lambert Conformal Conic 변환을 여기에 직접 구현한다.

시군 대표 좌표는 청사 기준 **근사값**이다. 지도 표시나 격자 산출에는 충분하지만
경계 판정이나 거리 계산의 근거로 쓰면 안 된다.
"""

from __future__ import annotations

import math
from enum import StrEnum

from .models import BBox, Frozen, GeoPoint

#: 경북 전역을 감싸는 bbox. OSM 클리핑에 검증된 값과 동일하다.
GYEONGBUK_BBOX = BBox(min_lon=127.8, min_lat=35.57, max_lon=131.87, max_lat=37.55)

SIDO_CODE = "47"
SIDO_NAME_FULL = "경상북도"
SIDO_NAME_SHORT = "경북"


class HazardDomain(StrEnum):
    """재난 유형. 데이터셋 적합성 판단에 쓴다."""

    HEAVY_RAIN = "heavy_rain"
    LANDSLIDE = "landslide"
    FLOOD = "flood"
    WILDFIRE = "wildfire"
    EARTHQUAKE = "earthquake"
    HEATWAVE = "heatwave"
    OTHER = "other"


class Sigungu(Frozen):
    """경북 시군 하나."""

    code: str
    name: str
    center: GeoPoint

    @property
    def full_name(self) -> str:
        return f"{SIDO_NAME_FULL} {self.name}"


def _point(lat: float, lon: float) -> GeoPoint:
    return GeoPoint(lat=lat, lon=lon)


#: 경북 시군 22개. 코드는 행정표준코드 시군구 5자리.
#: 좌표는 시군 청사 기준 근사값이며 경계 판정에 쓰면 안 된다.
SIGUNGU: dict[str, Sigungu] = {
    item.code: item
    for item in (
        Sigungu(code="47110", name="포항시", center=_point(36.0190, 129.3435)),
        Sigungu(code="47130", name="경주시", center=_point(35.8562, 129.2247)),
        Sigungu(code="47150", name="김천시", center=_point(36.1398, 128.1136)),
        Sigungu(code="47170", name="안동시", center=_point(36.5684, 128.7294)),
        Sigungu(code="47190", name="구미시", center=_point(36.1195, 128.3446)),
        Sigungu(code="47210", name="영주시", center=_point(36.8056, 128.6240)),
        Sigungu(code="47230", name="영천시", center=_point(35.9733, 128.9387)),
        Sigungu(code="47250", name="상주시", center=_point(36.4109, 128.1591)),
        Sigungu(code="47280", name="문경시", center=_point(36.5866, 128.1867)),
        Sigungu(code="47290", name="경산시", center=_point(35.8250, 128.7415)),
        Sigungu(code="47730", name="의성군", center=_point(36.3527, 128.6971)),
        Sigungu(code="47750", name="청송군", center=_point(36.4362, 129.0571)),
        Sigungu(code="47760", name="영양군", center=_point(36.6667, 129.1125)),
        Sigungu(code="47770", name="영덕군", center=_point(36.4150, 129.3656)),
        Sigungu(code="47820", name="청도군", center=_point(35.6473, 128.7361)),
        Sigungu(code="47830", name="고령군", center=_point(35.7261, 128.2628)),
        Sigungu(code="47840", name="성주군", center=_point(35.9192, 128.2831)),
        Sigungu(code="47850", name="칠곡군", center=_point(35.9954, 128.4017)),
        Sigungu(code="47900", name="예천군", center=_point(36.6575, 128.4527)),
        Sigungu(code="47920", name="봉화군", center=_point(36.8932, 128.7325)),
        Sigungu(code="47930", name="울진군", center=_point(36.9930, 129.4004)),
        Sigungu(code="47940", name="울릉군", center=_point(37.4845, 130.9057)),
    )
}

#: 2023년 대구광역시로 이관된 지역. 옛 자료에는 경북으로 남아 있어 혼동을 만든다.
TRANSFERRED_OUT: dict[str, str] = {
    "47720": "군위군 — 2023-07-01 대구광역시로 편입. 경북 자료에 남아 있으면 시점을 확인해야 한다",
}

#: 시군별 대표 ASOS 지점번호. 문경(137)은 스모크 테스트로 검증됐다.
ASOS_STATIONS: dict[str, int] = {
    "47280": 137,  # 문경
    "47170": 136,  # 안동
    "47110": 138,  # 포항
    "47250": 137,  # 상주 — 인접 문경 지점 대용
    "47210": 272,  # 영주
    "47190": 279,  # 구미
    "47130": 283,  # 경주(구미·포항 사이)
    "47930": 130,  # 울진
    "47940": 115,  # 울릉도
    "47920": 271,  # 봉화
    "47760": 277,  # 영양
    "47750": 276,  # 청송
    "47230": 281,  # 영천
    "47290": 143,  # 경산 — 대구 지점 대용
    "47820": 143,  # 청도 — 대구 지점 대용
    "47830": 279,  # 고령 — 구미 지점 대용
    "47840": 279,  # 성주 — 구미 지점 대용
    "47850": 279,  # 칠곡 — 구미 지점 대용
    "47900": 137,  # 예천 — 문경 지점 대용
    "47730": 278,  # 의성
    "47770": 138,  # 영덕 — 포항 지점 대용
    "47150": 279,  # 김천 — 구미 지점 대용
}


def find_sigungu(query: str) -> Sigungu | None:
    """코드 또는 이름으로 시군을 찾는다.

    "문경", "문경시", "경상북도 문경시", "47280" 모두 받는다.
    기관별로 표기가 다르므로 관대하게 맞춘다.
    """
    text = query.strip()
    if not text:
        return None
    if text in SIGUNGU:
        return SIGUNGU[text]

    normalized = text.replace(SIDO_NAME_FULL, "").replace(SIDO_NAME_SHORT, "").strip()
    if not normalized:
        return None

    for item in SIGUNGU.values():
        if normalized in (item.name, item.name.rstrip("시군")):
            return item
    # 부분 일치는 마지막 수단 — "문경시 산북면"처럼 하위 행정구역이 붙은 경우
    for item in SIGUNGU.values():
        stem = item.name.rstrip("시군")
        if stem and stem in normalized:
            return item
    return None


def resolve_transferred(code: str) -> str | None:
    return TRANSFERRED_OUT.get(code.strip())


def asos_station_for(code: str) -> int | None:
    return ASOS_STATIONS.get(code.strip())


# ── 기상청 격자(DFS) 변환 ────────────────────────────────────────────
# Lambert Conformal Conic. 상수는 기상청 격자 정의를 따른다.

_RE = 6371.00877  # 지구 반경 (km)
_GRID = 5.0  # 격자 간격 (km)
_SLAT1 = 30.0  # 표준 위도 1
_SLAT2 = 60.0  # 표준 위도 2
_OLON = 126.0  # 기준점 경도
_OLAT = 38.0  # 기준점 위도
_XO = 43  # 기준점 X 격자
_YO = 136  # 기준점 Y 격자

_DEGRAD = math.pi / 180.0


class KmaGrid(Frozen):
    """기상청 단기예보 격자 좌표."""

    nx: int
    ny: int


def _lcc_constants() -> tuple[float, float, float, float]:
    re = _RE / _GRID
    slat1 = _SLAT1 * _DEGRAD
    slat2 = _SLAT2 * _DEGRAD
    olat = _OLAT * _DEGRAD

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = sf**sn * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / ro**sn
    return re, sn, sf, ro


def to_kma_grid(point: GeoPoint) -> KmaGrid:
    """위경도를 기상청 격자 nx/ny로 변환한다.

    기상청 단기예보 API는 위경도를 받지 않으므로 이 변환이 필수다.
    """
    _, sn, sf, ro = _lcc_constants()

    ra = math.tan(math.pi * 0.25 + point.lat * _DEGRAD * 0.5)
    ra = (_RE / _GRID) * sf / ra**sn
    theta = point.lon * _DEGRAD - _OLON * _DEGRAD
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    x = ra * math.sin(theta) + _XO
    y = ro - ra * math.cos(theta) + _YO
    # 반올림 상수 검증: 서울(60,127) 부산(98,76) 제주(53,38). +1.5는 전 지점을 1씩 민다.
    return KmaGrid(nx=math.floor(x + 0.5), ny=math.floor(y + 0.5))


def from_kma_grid(grid: KmaGrid) -> GeoPoint:
    """격자 좌표를 위경도로 되돌린다. 격자 중심의 근사 위치다."""
    _, sn, sf, ro = _lcc_constants()

    xn = grid.nx - _XO
    yn = ro - grid.ny + _YO
    ra = math.sqrt(xn * xn + yn * yn)
    if sn < 0.0:
        ra = -ra
    alat = ((_RE / _GRID) * sf / ra) ** (1.0 / sn)
    alat = 2.0 * math.atan(alat) - math.pi * 0.5

    if abs(xn) <= 0.0:
        theta = 0.0
    elif abs(yn) <= 0.0:
        theta = math.pi * 0.5
        if xn < 0.0:
            theta = -theta
    else:
        theta = math.atan2(xn, yn)
    alon = theta / sn + _OLON * _DEGRAD

    return GeoPoint(lat=alat / _DEGRAD, lon=alon / _DEGRAD)


def grid_for(query: str) -> KmaGrid | None:
    """시군 이름·코드로 격자 좌표를 얻는다."""
    sigungu = find_sigungu(query)
    return to_kma_grid(sigungu.center) if sigungu else None


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """두 지점의 대권 거리(km).

    도로 거리가 아니라 직선 거리다. 대피 이동거리로 제시하면 안 되며
    후보 정렬이나 반경 필터에만 쓴다.
    """
    lat1, lon1 = a.lat * _DEGRAD, a.lon * _DEGRAD
    lat2, lon2 = b.lat * _DEGRAD, b.lon * _DEGRAD
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _RE * math.asin(math.sqrt(h))


def in_gyeongbuk(point: GeoPoint) -> bool:
    """경북 bbox 안인지. 경계 판정이 아니라 개략 필터다."""
    return GYEONGBUK_BBOX.contains(point)
