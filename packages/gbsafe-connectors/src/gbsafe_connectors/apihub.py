"""기상청 API허브 AWS 방재기상관측 분자료 커넥터.

AWS 원문은 JSON 봉투가 아니라 주석 헤더와 `=`로 끝나는 CSV형 행이다. 컬럼
순서는 헤더가 정의하므로 응답마다 헤더를 읽고, KMA 결측 센티널은 실측 0과
구별해 `None`으로 보존한다.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from typing import Any, ClassVar, Final

from gbsafe_core.config import CredentialName
from gbsafe_core.domain import Observation
from gbsafe_core.models import QualityFlag

from .base import KST, Connector, FetchOutcome, RawResponse, confirmed_empty

_MISSING_VALUES: Final = frozenset({-99.9, -99.0, -9.0})
_TIME_COLUMNS: Final = frozenset({"TM", "TM2", "YYMMDDHHMI"})

_MEASUREMENTS: Final[dict[str, tuple[str, str]]] = {
    "TA": ("temperature", "℃"),
    "WD1": ("wind_direction_1m", "deg"),
    "WS1": ("wind_speed_1m", "m/s"),
    "WDS": ("wind_direction_instant", "deg"),
    "WSS": ("wind_speed_instant", "m/s"),
    "WD10": ("wind_direction_10m", "deg"),
    "WS10": ("wind_speed_10m", "m/s"),
    "RN-15M": ("rainfall_15m", "mm"),
    "RN-60M": ("rainfall_1h", "mm"),
    "RN-12H": ("rainfall_12h", "mm"),
    "RN-DAY": ("rainfall_daily", "mm"),
    "HM": ("humidity", "%"),
}


def _columns(lines: list[str]) -> tuple[str, ...] | None:
    """주석 중 실제 컬럼 헤더를 찾는다.

    설명문을 헤더로 오인하면 행 길이가 맞지 않아 모든 자료가 사라질 수 있으므로
    시각·지점·기온 필드가 함께 있는 줄만 인정한다.
    """
    for line in lines:
        if not line.lstrip().startswith("#"):
            continue
        content = line.lstrip()[1:].strip()
        tokens = tuple(
            token.strip().upper().replace("_", "-")
            for token in re.split(r"[,\s]+", content)
            if token.strip() and token.strip() != "="
        )
        if "STN" in tokens and "TA" in tokens and _TIME_COLUMNS.intersection(tokens):
            return tokens
    return None


def _observed_at(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw.strip(), "%Y%m%d%H%M").replace(tzinfo=KST)
    except ValueError:
        return None


def _measure(raw: str) -> float | None:
    """KMA 결측 센티널을 0이 아닌 None으로 보존한다."""
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return None if value in _MISSING_VALUES else value


class AwsObservationConnector(Connector[Observation]):
    """AWS 분자료 — 지점별 기온·강우·바람 관측."""

    dataset_id: ClassVar[str] = "15057084"
    credential: ClassVar[CredentialName] = CredentialName.KMA_APIHUB
    service_key_param: ClassVar[str] = "authKey"

    #: 지역 이름을 받지 않는다. AWS는 시군 경계가 아니라 지점번호로 조회하며,
    #: 아직 지점↔시군 대응표가 없다. `region_param`을 두면 호출부가 "문경시"를
    #: 넘기고 이 커넥터는 그것을 지점번호로 읽지 못해, 필터가 조용히 무시되거나
    #: 전국 관측 8832건이 시군 질의 결과로 돌아온다.
    region_param: ClassVar[str | None] = None
    update_cycle_seconds: ClassVar[int] = 60
    max_decision_age_seconds: ClassVar[int] = 600

    def base_url(self) -> str:
        return "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-aws2_min"

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        end_time = kwargs.get("end_time") or datetime.now(KST)
        if not isinstance(end_time, datetime):
            raise ValueError("end_time은 datetime이어야 합니다")
        if kwargs.get("region"):
            raise ValueError(
                "AWS 관측은 시군 이름으로 조회할 수 없습니다 — "
                "`station_id`에 AWS 지점번호를 주세요 (예: 273). "
                "이름을 조용히 무시하면 전국 관측이 이 시군 결과로 보입니다."
            )
        station_id = str(kwargs.get("station_id") or "0").strip()
        if not station_id.isdecimal():
            raise ValueError("station_id는 AWS 지점번호여야 합니다 (예: 273)")
        return {
            "tm2": end_time.strftime("%Y%m%d%H%M"),
            "stn": station_id,
            "disp": "1",
            "help": "0",
        }

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[Observation]:
        """주석 헤더를 기준으로 CSV형 관측 행을 정규화한다.

        HTML·표식 없는 본문·잘린 행은 자료 없음이 아니라 실패다. 반대로 시작·종료
        표식과 필수 헤더가 모두 있는 정상 봉투만 행이 없을 때 confirmed_empty다.
        """
        text = response.text()
        if text.lstrip().casefold().startswith(("<!doctype html", "<html")):
            raise ValueError("HTML 오류 페이지를 반환했습니다")

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        has_start = any(line.upper().startswith("#START") for line in lines)
        has_end = any(line.upper().endswith("END") for line in lines)
        columns = _columns(lines)
        if not has_start or not has_end or columns is None:
            raise ValueError("AWS 정상 응답 표식 또는 컬럼 헤더를 찾지 못했습니다")

        data_lines = [line for line in lines if not line.startswith("#")]
        if not data_lines:
            return confirmed_empty("정상 AWS 응답에 관측 행이 없습니다")

        time_column = next(column for column in columns if column in _TIME_COLUMNS)
        wanted_station = str(kwargs.get("station_id") or "0").strip()
        records = []
        malformed_rows = 0
        parsed_rows = 0
        matched_rows = 0

        for line in data_lines:
            values = next(csv.reader([line], skipinitialspace=True))
            if not values or values[-1].strip() != "=":
                malformed_rows += 1
                continue
            values = [value.strip() for value in values[:-1]]
            if len(values) != len(columns):
                malformed_rows += 1
                continue

            row = dict(zip(columns, values, strict=True))
            observed_at = _observed_at(row[time_column])
            station_id = row["STN"].strip()
            if observed_at is None or not station_id:
                malformed_rows += 1
                continue
            parsed_rows += 1
            if wanted_station != "0" and station_id != wanted_station:
                continue
            matched_rows += 1

            for column, (kind, unit) in _MEASUREMENTS.items():
                if column not in row:
                    continue
                raw_value = row[column]
                value = _measure(raw_value)
                flags = (QualityFlag.PARTIAL_RESPONSE,) if value is None else ()
                notes = (
                    (f"KMA 결측·비수치 표기({raw_value})를 None으로 보존했습니다",)
                    if value is None
                    else ()
                )
                records.append(
                    self.record(
                        Observation(
                            kind=kind,
                            value=value,
                            unit=unit,
                            station=f"AWS {station_id}",
                            target_time=observed_at,
                            is_forecast=False,
                            raw_code=column,
                        ),
                        response,
                        observed_at=observed_at,
                        quality_flags=flags,
                        notes=notes,
                    )
                )

        if records:
            caveats = (
                (f"잘리거나 형식이 다른 관측 행 {malformed_rows}건을 제외했습니다",)
                if malformed_rows
                else ()
            )
            # 항목은 다 왔는데 값이 전부 결측이면 관측에 성공한 것이 아니다.
            # 지점이 죽어 있어도 레코드 12건이 돌아와 `records`로 보고됐다.
            if records and all(record.payload.value is None for record in records):
                raise ValueError(
                    f"AWS 지점 {wanted_station}의 관측값이 전부 결측입니다 "
                    f"({len(records)}개 항목) — 관측소가 응답했지만 측정값이 "
                    "없습니다. 값이 없는 것이지 기상이 평온한 것이 아닙니다."
                )
            return FetchOutcome(records=tuple(records), caveats=caveats)
        if malformed_rows:
            raise ValueError(f"해석 가능한 관측 행이 없습니다 (손상 행 {malformed_rows}건)")
        if parsed_rows and not matched_rows:
            return confirmed_empty(f"정상 응답에 AWS 지점 {wanted_station} 관측이 없습니다")
        raise ValueError("관측 행에서 지원하는 기상 필드를 찾지 못했습니다")


__all__ = ["AwsObservationConnector"]
