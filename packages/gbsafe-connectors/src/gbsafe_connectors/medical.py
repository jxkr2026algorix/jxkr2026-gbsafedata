"""응급의료·대기질 커넥터.

두 API 모두 별도의 함정이 있다.

**응급의료(15000563)는 XML만 반환한다.** 포맷 파라미터가 없다. 그리고 응답의
`hvidate`가 실제 갱신 시각이므로 이 값 없이 병상 수를 실시간처럼 표시하면
안 된다 — `MedicalCapacity.is_realtime`이 그것을 구분한다.

**AirKorea(15073861)는 개발계정 한도가 일 500건**으로 조사 대상 중 가장 낮고
간헐적으로 504를 반환한다. 서비스키 파라미터도 소문자 `serviceKey`, 포맷은
`returnType`이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar
from xml.etree import ElementTree

from gbsafe_core.domain import MedicalCapacity, Observation
from gbsafe_core.models import QualityFlag
from gbsafe_core.regions import SIDO_NAME_FULL, SIDO_NAME_SHORT, find_sigungu

from .base import KST, Connector, FetchOutcome, RawResponse


def _text(node: ElementTree.Element, tag: str) -> str | None:
    child = node.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _int(node: ElementTree.Element, tag: str) -> int | None:
    raw = _text(node, tag)
    if raw is None:
        return None
    try:
        parsed = int(float(raw))
    except ValueError:
        return None
    # 원천이 미확인을 -1로 표기하는 경우가 있어 음수는 미확인으로 본다
    return parsed if parsed >= 0 else None


def _hvidate(raw: str | None) -> datetime | None:
    """응급의료 `hvidate` (YYYYMMDDHHMMSS)를 KST datetime으로."""
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    for width, pattern in ((14, "%Y%m%d%H%M%S"), (12, "%Y%m%d%H%M"), (8, "%Y%m%d")):
        if len(digits) >= width:
            try:
                return datetime.strptime(digits[:width], pattern).replace(tzinfo=KST)
            except ValueError:
                continue
    return None


class EmergencyBedsConnector(Connector[MedicalCapacity]):
    """응급실 실시간 가용병상.

    부상자 이송 후보를 찾는 데 쓴다. 운영단계 승인 여부와 무관하게 개발계정으로
    조회되지만, 한도가 일 1,000건이다.
    """

    dataset_id: ClassVar[str] = "15000563"
    service_key_param: ClassVar[str] = "serviceKey"
    update_cycle_seconds: ClassVar[int] = 900

    def base_url(self) -> str:
        return (
            "https://apis.data.go.kr/B552657/ErmctInfoInqireService/"
            "getEmrrmRltmUsefulSckbdInfoInqire"
        )

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        region = kwargs.get("sigungu")
        params = {
            "pageNo": "1",
            "numOfRows": str(kwargs.get("rows", 30)),
            "STAGE1": str(kwargs.get("sido", SIDO_NAME_FULL)),
        }
        if region:
            resolved = find_sigungu(str(region))
            params["STAGE2"] = resolved.name if resolved else str(region)
        return params

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[MedicalCapacity]:
        root = response.xml()
        items = root.findall(".//item")
        if not items:
            return FetchOutcome(
                caveats=("해당 지역에 응급의료기관 실시간 정보가 없습니다",)
            )

        records = []
        for item in items:
            reported_at = _hvidate(_text(item, "hvidate"))
            flags: tuple[QualityFlag, ...] = ()
            notes: tuple[str, ...] = ()
            if reported_at is None:
                flags = (QualityFlag.PARTIAL_RESPONSE,)
                notes = ("갱신 시각(hvidate)이 없어 실시간 값으로 표시할 수 없습니다",)

            records.append(
                self.record(
                    MedicalCapacity(
                        facility_id=_text(item, "hpid") or "미확인",
                        name=_text(item, "dutyName") or "미확인",
                        address=_text(item, "dutyAddr"),
                        emergency_beds=_int(item, "hvec"),
                        operating_rooms=_int(item, "hvoc"),
                        icu_beds=_int(item, "hvicc"),
                        reported_at=reported_at,
                        phone=_text(item, "dutyTel3") or _text(item, "dutyTel1"),
                    ),
                    response,
                    observed_at=reported_at,
                    quality_flags=flags,
                    notes=notes,
                )
            )
        return FetchOutcome(
            records=tuple(records),
            caveats=(
                "운영단계 활용에는 기관 심의가 필요합니다 — 승인 전에는 참고용입니다",
            ),
        )


class AirQualityConnector(Connector[Observation]):
    """시도별 실시간 대기오염도.

    산불 연무 확산 판단의 보조 지표다. 한도가 일 500건이므로 폴링 주기를
    다른 API와 다르게 잡아야 한다.
    """

    dataset_id: ClassVar[str] = "15073861"
    service_key_param: ClassVar[str] = "serviceKey"
    update_cycle_seconds: ClassVar[int] = 3600

    def base_url(self) -> str:
        return (
            "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/"
            "getCtprvnRltmMesureDnsty"
        )

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        return {
            "pageNo": "1",
            "numOfRows": str(kwargs.get("rows", 30)),
            "returnType": "json",
            "sidoName": str(kwargs.get("sido", SIDO_NAME_SHORT)),
            "ver": "1.3",
        }

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[Observation]:
        payload = response.json()
        items = payload.get("response", {}).get("body", {}).get("items") or []
        if not isinstance(items, list) or not items:
            return FetchOutcome(caveats=("대기질 측정값이 조회되지 않았습니다",))

        station_filter = kwargs.get("station")
        records = []
        for item in items:
            if not isinstance(item, dict):
                continue
            station = str(item.get("stationName") or "").strip()
            if station_filter and station_filter not in station:
                continue
            observed_at = _parse_airkorea_time(str(item.get("dataTime") or ""))

            for field, (kind, unit) in _AIR_FIELDS.items():
                raw = item.get(field)
                value = _to_float(raw)
                if value is None:
                    continue
                records.append(
                    self.record(
                        Observation(
                            kind=kind,
                            value=value,
                            unit=unit,
                            station=station or None,
                            target_time=observed_at or response.retrieved_at,
                            is_forecast=False,
                            raw_code=field,
                        ),
                        response,
                        observed_at=observed_at,
                    )
                )
        return FetchOutcome(records=tuple(records))


#: AirKorea 응답 필드 → (정규화 이름, 단위).
_AIR_FIELDS: dict[str, tuple[str, str]] = {
    "pm10Value": ("pm10", "㎍/㎥"),
    "pm25Value": ("pm25", "㎍/㎥"),
    "coValue": ("co", "ppm"),
    "no2Value": ("no2", "ppm"),
    "o3Value": ("o3", "ppm"),
    "so2Value": ("so2", "ppm"),
}


def _to_float(raw: Any) -> float | None:
    """'-'와 빈 문자열을 결측으로 다룬다."""
    if raw in (None, "", "-"):
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def _parse_airkorea_time(raw: str) -> datetime | None:
    """AirKorea `dataTime` (YYYY-MM-DD HH:MM)을 KST datetime으로.

    24시 표기를 쓰는 경우가 있어 그때는 다음 날 00시로 본다.
    """
    text = raw.strip()
    if not text:
        return None
    if " 24:" in text:
        date_part = text.split(" ")[0]
        try:
            base = datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=KST)
        except ValueError:
            return None
        return base + timedelta(days=1)
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=KST)
        except ValueError:
            continue
    return None


__all__ = ["AirQualityConnector", "EmergencyBedsConnector"]
