"""홍수통제소 하천수위 커넥터.

이 원천이 다른 것들과 다른 점은 **기관이 정한 임계값을 함께 준다**는 것이다.
관측소마다 관심·주의보·경보·심각 수위가 고시돼 있어, 수위 3.2m가 위험한지
아닌지를 우리가 추정하지 않고 기관 기준으로 판정할 수 있다.

그래서 이 커넥터가 지켜야 할 경계가 하나 더 생긴다. **임계값을 모르는 관측소는
'안전'이 아니라 '판단 불가'다.** 경북 242개 관측소 중 64개는 임계수위가 고시돼
있지 않다. 그 관측소의 수위를 임계값 없이 제시하면서 아무 경고도 붙이지 않으면,
읽는 쪽은 낮은 숫자를 보고 안전하다고 읽는다.

인증키가 쿼리가 아니라 **URL 경로**에 들어간다. 그래서 `service_key_param`이
None이고 `base_url()`이 키 배치를 직접 책임진다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from gbsafe_core.config import CredentialName
from gbsafe_core.domain import AlertAction, HazardAlert, Observation, Severity
from gbsafe_core.models import GeoPoint, QualityFlag
from gbsafe_core.regions import HazardDomain, find_sigungu

from .base import (
    KST,
    Connector,
    FetchOutcome,
    RawResponse,
    confirmed_empty,
    missing_or_impossible,
)

_STATION_FILE = Path(__file__).parent / "data" / "hrfco-stations.json"

#: 홍수통제소가 "검색된 자료가 없습니다"를 알리는 코드.
#:
#: 이 코드가 올 때만 '해당 없음'으로 읽는다. 코드가 없거나 모르는 코드면
#: 실패로 본다 — 홍수특보가 없는 것과 조회에 실패한 것을 섞으면 안 된다.
NO_DATA_CODE = "990"


class Station:
    """수위관측소 하나와 고시된 임계수위."""

    __slots__ = (
        "address",
        "advisory_m",
        "attention_m",
        "location",
        "name",
        "serious_m",
        "station_id",
        "warning_m",
    )

    def __init__(self, row: dict[str, Any]) -> None:
        self.station_id = str(row.get("station_id") or "")
        self.name = str(row.get("name") or self.station_id)
        self.address = str(row.get("address") or "")
        lat, lon = row.get("lat"), row.get("lon")
        self.location = (
            GeoPoint(lat=float(lat), lon=float(lon))
            if isinstance(lat, int | float) and isinstance(lon, int | float)
            else None
        )
        self.attention_m = row.get("attention_m")
        self.advisory_m = row.get("advisory_m")
        self.warning_m = row.get("warning_m")
        self.serious_m = row.get("serious_m")

    @property
    def has_thresholds(self) -> bool:
        return any(
            isinstance(value, int | float)
            for value in (self.attention_m, self.advisory_m, self.warning_m, self.serious_m)
        )

    def severity_for(self, level_m: float) -> Severity:
        """고시 임계값에 견준 경보 단계. 높은 단계부터 본다."""
        for threshold, severity in (
            (self.serious_m, Severity.EMERGENCY),
            (self.warning_m, Severity.WARNING),
            (self.advisory_m, Severity.ADVISORY),
            (self.attention_m, Severity.INFO),
        ):
            if isinstance(threshold, int | float) and level_m >= threshold:
                return severity
        return Severity.INFO

    def exceeded_threshold(self, level_m: float) -> tuple[str, float] | None:
        for label, threshold in (
            ("심각", self.serious_m),
            ("경보", self.warning_m),
            ("주의보", self.advisory_m),
            ("관심", self.attention_m),
        ):
            if isinstance(threshold, int | float) and level_m >= threshold:
                return label, float(threshold)
        return None


def _load_stations() -> dict[str, Station]:
    if not _STATION_FILE.is_file():
        return {}
    payload = json.loads(_STATION_FILE.read_text(encoding="utf-8"))
    stations = {}
    for row in payload.get("stations", ()):
        station = Station(row)
        if station.station_id:
            stations[station.station_id] = station
    return stations


#: 경북 수위관측소 참조표. scripts/sync_hrfco_stations.py로 재생성한다.
STATIONS: dict[str, Station] = _load_stations()


def _level(raw: Any) -> float | None:
    """수위 문자열을 실수로. 빈 값과 해석 불가는 None이다.

    0.0으로 떨어뜨리면 안 된다. 하천 수위 0은 실제로 있을 수 있는 값이라,
    결측을 0으로 만들면 '물이 없다'는 관측처럼 읽힌다.
    """
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    # -99는 모든 경보 임계값 아래라 조용히 '안전한 낮은 수위'로 읽힌다.
    return None if missing_or_impossible(value) else value


def _observed_at(raw: Any) -> datetime | None:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) < 12:
        return None
    try:
        return datetime.strptime(digits[:12], "%Y%m%d%H%M").replace(tzinfo=KST)
    except ValueError:
        return None


class RiverLevelConnector(Connector[Observation]):
    """경북 하천 실시간 수위 + 고시 임계수위 판정."""

    dataset_id: ClassVar[str] = "hrfco-waterlevel"
    credential: ClassVar[CredentialName | None] = CredentialName.HRFCO
    service_key_param: ClassVar[str | None] = None
    region_param: ClassVar[str | None] = "region"
    refresh_seconds: ClassVar[int | None] = 600

    @property
    def dataset_name(self) -> str:
        return "한강홍수통제소 실시간 수위"

    @property
    def provider(self) -> str:
        return "환경부 한강홍수통제소"

    def base_url(self) -> str:
        key = self._settings.credential(CredentialName.HRFCO) or ""
        return f"https://api.hrfco.go.kr/{key}/waterlevel/list/10M.json"

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        return {}

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[Observation]:
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"홍수통제소 응답을 JSON으로 읽지 못했습니다: {error}") from error

        if not isinstance(payload, dict):
            raise ValueError("홍수통제소 응답이 객체가 아닙니다")

        code = str(payload.get("code") or "").strip()
        if code == NO_DATA_CODE:
            return confirmed_empty("홍수통제소가 '검색된 자료가 없습니다'를 반환했습니다")
        if code and code != "200":
            raise ValueError(f"홍수통제소 오류 코드 {code}: {payload.get('message')}")

        content = payload.get("content")
        if content is None:
            # `content: null`을 빈 결과로 읽지 않는다. 부하가 걸린 서버가 이렇게
            # 응답하는 경우가 있어, 관측값 없음과 구분되지 않는다.
            raise ValueError("홍수통제소 응답에 content가 없습니다")
        if not isinstance(content, list):
            raise ValueError("홍수통제소 content가 배열이 아닙니다")

        region = kwargs.get("region")
        sigungu = find_sigungu(str(region)) if region else None

        records: list[Any] = []
        without_thresholds: list[str] = []
        missing_level = 0
        alerts: list[str] = []

        for row in content:
            if not isinstance(row, dict):
                continue
            station_id = str(row.get("wlobscd") or "").strip()
            station = STATIONS.get(station_id)
            if station is None:
                continue
            if sigungu is not None:
                stem = sigungu.name.rstrip("시군")
                if stem not in station.address and stem not in station.name:
                    continue

            level = _level(row.get("wl"))
            observed = _observed_at(row.get("ymdhm"))

            flags: list[QualityFlag] = []
            notes: list[str] = []
            if level is None:
                missing_level += 1
                flags.append(QualityFlag.NO_DATA_RETURNED)
                notes.append("수위가 결측입니다 — 0m가 아니라 값이 없는 것입니다")
            if not station.has_thresholds:
                without_thresholds.append(station.name)
                notes.append(
                    "이 관측소는 경보 임계수위가 고시돼 있지 않습니다 — "
                    "수위만으로 위험 여부를 판단할 수 없습니다"
                )
            elif level is not None:
                crossed = station.exceeded_threshold(level)
                if crossed is not None:
                    label, threshold = crossed
                    notes.append(f"{label} 기준 {threshold}m를 넘었습니다 (현재 {level}m)")
                    alerts.append(f"{station.name} {label}({level}m ≥ {threshold}m)")

            records.append(
                self.record(
                    Observation(
                        kind="water_level",
                        value=level,
                        unit="m",
                        station=station.name,
                        location=station.location,
                        target_time=observed or response.retrieved_at,
                        is_forecast=False,
                        raw_code=station_id,
                    ),
                    response,
                    observed_at=observed,
                    quality_flags=tuple(flags),
                    notes=tuple(notes),
                )
            )

        if not records:
            # 이 지역에 관측소가 **있는데** 관측값이 하나도 오지 않았다면 그것은
            # '수위 정보 없음'이 아니라 부분 응답이다. 홍수통제소는 전국 관측값을
            # 한 번에 주므로, 아는 관측소가 응답에서 통째로 빠진 것은 원천 쪽
            # 누락이다. 이것을 확인된 부재로 읽으면 하천이 안전한 것으로 보인다.
            if sigungu is not None:
                stem = sigungu.name.rstrip("시군")
                known = [
                    item.name
                    for item in STATIONS.values()
                    if stem in item.address or stem in item.name
                ]
                if known:
                    raise ValueError(
                        f"{sigungu.name}에 수위관측소 {len(known)}곳이 있는데 "
                        "이번 응답에는 관측값이 하나도 없습니다 — 부분 응답입니다"
                    )
                return confirmed_empty(
                    f"{sigungu.name}에는 홍수통제소 수위관측소가 없습니다 — "
                    "수위 정보가 없는 것이지 하천이 안전한 것이 아닙니다"
                )
            raise ValueError("수위 관측값을 하나도 해석하지 못했습니다")

        # 이 지역에 아는 관측소가 몇 곳인데 몇 곳이 왔는지 밝힌다. 전부
        # 사라졌을 때만 실패로 보면, 12곳 중 3곳이 빠진 응답이 조용히 통과한다.
        if sigungu is not None:
            stem = sigungu.name.rstrip("시군")
            known = {
                item.station_id
                for item in STATIONS.values()
                if stem in item.address or stem in item.name
            }
            seen = {str(record.payload.raw_code) for record in records}
            absent = known - seen
            if absent:
                names = ", ".join(
                    sorted(STATIONS[item].name for item in absent if item in STATIONS)
                )
                caveats_missing = (
                    f"{sigungu.name}의 수위관측소 {len(known)}곳 중 {len(absent)}곳"
                    f"({names})의 관측값이 이번 응답에 없습니다 — 그 지점의 수위는 "
                    "확인되지 않았습니다."
                )
            else:
                caveats_missing = None
        else:
            caveats_missing = None

        caveats = [
            "수위는 관측소 지점값입니다 — 같은 하천이라도 지점마다 다릅니다",
            "임계수위는 기관 고시값이며 실제 침수 여부는 현장 확인이 필요합니다",
            "T/M 관측소 원시자료로 **보정 전 값**입니다 — 최종 확정자료와 다를 수 있습니다",
            "경북은 낙동강 권역이라 수집 지연이 11분 이상입니다 — "
            "관측시각과 현재 시각의 차이를 그대로 읽으면 안 됩니다",
        ]
        if without_thresholds:
            caveats.append(
                f"임계수위가 고시되지 않은 관측소 {len(without_thresholds)}곳이 있습니다 "
                f"({', '.join(without_thresholds[:3])}"
                f"{' 외' if len(without_thresholds) > 3 else ''}) — "
                "이 지점은 수위가 낮아도 '안전'으로 읽으면 안 됩니다"
            )
        if missing_level:
            caveats.append(f"수위가 결측인 관측소 {missing_level}곳이 있습니다")
        # 좌표가 없는 관측소는 지도·거리 계산에서 조용히 빠진다. 그 지점의
        # 수위를 읽고도 어디인지 모르는 상태이므로 밝힌다.
        unplaced = sorted(
            {
                record.payload.station
                for record in records
                if record.payload.location is None and record.payload.station
            }
        )
        if unplaced:
            caveats.append(
                f"좌표가 확인되지 않은 관측소 {len(unplaced)}곳"
                f"({', '.join(unplaced[:3])}{' 외' if len(unplaced) > 3 else ''})은 "
                "지도 표시와 거리 계산에 쓸 수 없습니다."
            )
        if caveats_missing:
            caveats.insert(0, caveats_missing)
        if alerts:
            caveats.append("임계수위 초과: " + ", ".join(alerts[:5]))

        return FetchOutcome(records=tuple(records), caveats=tuple(caveats))


class FloodForecastConnector(Connector[HazardAlert]):
    """홍수특보 발령 현황.

    발령이 없을 때 홍수통제소는 코드 990과 "검색된 자료가 없습니다"를 준다.
    그 코드를 확인했을 때만 '발령 없음'으로 읽고, 그 밖의 응답은 실패로 둔다.
    """

    dataset_id: ClassVar[str] = "hrfco-fldfct"
    credential: ClassVar[CredentialName | None] = CredentialName.HRFCO
    service_key_param: ClassVar[str | None] = None
    refresh_seconds: ClassVar[int | None] = 600

    @property
    def dataset_name(self) -> str:
        return "한강홍수통제소 홍수특보"

    @property
    def provider(self) -> str:
        return "환경부 한강홍수통제소"

    def base_url(self) -> str:
        key = self._settings.credential(CredentialName.HRFCO) or ""
        return f"https://api.hrfco.go.kr/{key}/fldfct/list.json"

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        return {}

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[HazardAlert]:
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"홍수특보 응답을 JSON으로 읽지 못했습니다: {error}") from error
        if not isinstance(payload, dict):
            raise ValueError("홍수특보 응답이 객체가 아닙니다")

        code = str(payload.get("code") or "").strip()
        content = payload.get("content")

        if code == NO_DATA_CODE:
            return confirmed_empty(
                "홍수통제소가 발령된 홍수특보가 없다고 응답했습니다 (코드 990)"
            )
        if isinstance(content, list) and content:
            records = []
            for row in content:
                if not isinstance(row, dict):
                    continue
                issued = _observed_at(row.get("fcstdt") or row.get("ymdhm"))
                title = str(row.get("fcstty") or row.get("fldfctty") or "홍수특보")
                where = str(row.get("obsnm") or row.get("wlobscd") or "")
                records.append(
                    self.record(
                        HazardAlert(
                            hazard=HazardDomain.FLOOD,
                            severity=Severity.WARNING,
                            headline=f"{where} {title}".strip(),
                            area_name=where or "미확인",
                            action=AlertAction.ISSUED,
                            area_code=str(row.get("wlobscd") or "") or None,
                            issued_at=issued,
                            raw_level=title,
                        ),
                        response,
                        observed_at=issued,
                    )
                )
            if records:
                return FetchOutcome(
                    records=tuple(records),
                    caveats=("홍수특보는 하천 구간 단위입니다 — 특정 마을 상태가 아닙니다",),
                )

        # 코드도 없고 내용도 못 읽었다. 발령이 없다고 단정할 근거가 없다.
        raise ValueError(
            "홍수특보 응답에서 발령 목록도 '자료 없음' 코드도 찾지 못했습니다"
        )
