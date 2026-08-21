"""기상특보 발표관서 코드.

기상특보 목록 API는 지역명이 아니라 **발표관서 번호(`stnId`)**를 준다. 번호를
그대로 노출하면 "108"이 서울이라는 사실을 알 수 없고, 필터 없이 조회하면
전국 특보가 경북 특보처럼 섞여 나온다.

경북은 발표관서가 둘로 나뉜다. 대구지방기상청(143)이 도 전역을 관할하고,
안동기상대(136)가 북부 내륙을 담당한다. 문경은 북부에 속한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WarningStation:
    """특보 발표관서 하나."""

    station_id: str
    name: str
    covers: str
    serves_gyeongbuk: bool = False


#: 특보 발표관서. 경북 관할 여부를 표시해 필터에 쓴다.
WARNING_STATIONS: dict[str, WarningStation] = {
    station.station_id: station
    for station in (
        WarningStation("108", "기상청 본청", "전국"),
        WarningStation("109", "수도권기상청", "서울·인천·경기"),
        WarningStation("105", "강원지방기상청", "강원"),
        WarningStation("131", "청주기상지청", "충북"),
        WarningStation("133", "대전지방기상청", "대전·세종·충남"),
        WarningStation("143", "대구지방기상청", "대구·경북", serves_gyeongbuk=True),
        WarningStation("136", "안동기상대", "경북 북부내륙", serves_gyeongbuk=True),
        WarningStation("138", "포항기상대", "경북 동해안", serves_gyeongbuk=True),
        WarningStation("146", "전주기상지청", "전북"),
        WarningStation("156", "광주지방기상청", "광주·전남"),
        WarningStation("159", "부산지방기상청", "부산·울산·경남"),
        WarningStation("184", "제주지방기상청", "제주"),
    )
}

#: 경북 특보를 조회할 때 기본으로 보는 관서.
GYEONGBUK_STATIONS: tuple[str, ...] = tuple(
    station.station_id for station in WARNING_STATIONS.values() if station.serves_gyeongbuk
)


def describe_station(station_id: str | None) -> str:
    """관서 번호를 사람이 읽을 수 있는 관할 지역으로."""
    if not station_id:
        return "미확인"
    station = WARNING_STATIONS.get(str(station_id).strip())
    if station is None:
        return f"발표관서 {station_id}"
    return f"{station.name} ({station.covers})"


def serves_gyeongbuk(station_id: str | None) -> bool:
    if not station_id:
        return False
    station = WARNING_STATIONS.get(str(station_id).strip())
    return bool(station and station.serves_gyeongbuk)


__all__ = [
    "GYEONGBUK_STATIONS",
    "WARNING_STATIONS",
    "WarningStation",
    "describe_station",
    "serves_gyeongbuk",
]
