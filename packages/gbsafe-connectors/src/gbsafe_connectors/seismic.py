"""기상청 지진·지진해일 통보문 커넥터.

지진은 전국에서 발생한 기록을 그대로 돌려준다. 경북 문자열만 남기면 인접 지역과
해역의 지진이 경북에 미치는 영향을 숨길 수 있기 때문이다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, ClassVar

from gbsafe_core.domain import (
    HazardAlert,
    Observation,
    parse_alert_action,
    parse_severity,
)
from gbsafe_core.models import GeoPoint, QualityFlag, UpstreamStatus
from gbsafe_core.regions import HazardDomain

from .base import KST, Connector, FetchOutcome, RawResponse, confirmed_empty
from .kma import _items


def _has_identity(item: Any, *fields: str) -> bool:
    """이 행이 실제 사건을 가리키는지.

    통보문 API가 빈 객체를 섞어 보내는 경우가 있는데, 그것을 레코드로 만들면
    "규모 미확인, 위치 미확인, 시각 미확인인 지진이 발생했다"가 된다. 없는
    사건을 발생으로 보고하는 것은 있는 사건을 놓치는 것만큼 나쁘다.
    """
    if not isinstance(item, dict):
        return False
    return any(str(item.get(field) or "").strip() for field in fields)


def _number(raw: Any) -> float | None:
    """결측 수치를 0으로 만들지 않는다. 0은 실제 규모·반경으로 오인될 수 있다."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _korea_point(lat_raw: Any, lon_raw: Any) -> GeoPoint | None:
    """누락·범위 밖 좌표를 (0, 0) 같은 가짜 진앙으로 만들지 않는다."""
    lat = _number(lat_raw)
    lon = _number(lon_raw)
    if lat is None or lon is None or not (33 <= lat <= 39 and 124 <= lon <= 132):
        return None
    return GeoPoint(lat=lat, lon=lon)


def _stamp(raw: Any) -> datetime | None:
    """기상청 숫자 시각을 KST로 읽고, 깨진 시각은 현재시각으로 꾸미지 않는다."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    formats = ((14, "%Y%m%d%H%M%S"), (12, "%Y%m%d%H%M"))
    for length, pattern in formats:
        if len(digits) < length:
            continue
        try:
            return datetime.strptime(digits[:length], pattern).replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def _result_code(payload: Any) -> str:
    """정상 봉투의 결과코드만 읽어 HTML·엉뚱한 JSON을 무재해로 오인하지 않는다."""
    if not isinstance(payload, dict):
        raise ValueError("기상청 응답이 객체가 아닙니다")
    response = payload.get("response")
    if not isinstance(response, dict):
        raise ValueError("기상청 응답에 response 노드가 없습니다")
    header = response.get("header")
    if not isinstance(header, dict):
        raise ValueError("기상청 응답에 header 노드가 없습니다")
    code = str(header.get("resultCode", "")).strip()
    if not code:
        raise ValueError("기상청 응답에 resultCode가 없습니다")
    return code.zfill(2) if code.isdigit() else code


def _response_items(payload: Any) -> list[dict[str, Any]] | None:
    """03만 명시적 부재로, null·모르는 코드·깨진 항목은 실패로 남긴다."""
    code = _result_code(payload)
    if code == "03":
        return None
    if code != "00":
        raise ValueError(f"기상청 오류 resultCode {code}")

    items = _items(payload)
    if items:
        return items

    marker = payload["response"]["body"]["items"]
    if marker in ("", []):
        return []
    if isinstance(marker, dict) and marker.get("item") == []:
        return []
    raise ValueError("기상청 응답의 item 항목을 하나도 해석하지 못했습니다")


class _DatedKmaConnector[PayloadT](Connector[PayloadT]):
    """오늘 기준 최대 3일 통보문을 조회하는 기상청 공통 경계."""

    service_key_param: ClassVar[str] = "ServiceKey"
    num_rows: ClassVar[int]
    update_cycle_seconds: ClassVar[int] = 600

    @property
    def provider(self) -> str:
        return "기상청"

    def build_params(self, **kwargs: Any) -> dict[str, str]:
        """과다 기간이 resultCode 99가 되는 실제 API 제한을 호출 전에 막는다."""
        today = datetime.now(KST).date()
        days = max(0, min(int(kwargs.get("days", 3)), 3))
        return {
            "pageNo": "1",
            "numOfRows": str(self.num_rows),
            "dataType": "JSON",
            "fromTmFc": (today - timedelta(days=days)).strftime("%Y%m%d"),
            "toTmFc": today.strftime("%Y%m%d"),
        }

    async def _send(self, url: str, params: dict[str, str]) -> RawResponse:
        """문서화된 03만 파서에 넘겨 일반 오류와 '발효 없음'을 구별한다.

        공통 전송 계층은 모든 0 아닌 resultCode를 장애로 막는다. 이 API의 03은
        정상적인 NO_DATA이므로, 정상 봉투에서 확인된 경우에만 예외적으로 복원한다.
        """
        response = await super()._send(url, params)
        if response.status is not UpstreamStatus.DEGRADED:
            return response
        try:
            code = _result_code(response.json())
        except (json.JSONDecodeError, TypeError, ValueError):
            return response
        if code != "03":
            return response
        return RawResponse(
            body=response.body,
            content_type=response.content_type,
            endpoint=response.endpoint,
            status=UpstreamStatus.OK,
            retrieved_at=response.retrieved_at,
            snapshot_id=response.snapshot_id,
        )


class EarthquakeConnector(_DatedKmaConnector[Observation]):
    """국내 규모 2.0 이상 지진 통보문. 지역 필터 없이 전국 기록을 제공한다."""

    dataset_id: ClassVar[str] = "15000420"
    num_rows: ClassVar[int] = 50

    @property
    def dataset_name(self) -> str:
        return "기상청 지진정보 조회서비스"

    def base_url(self) -> str:
        return "https://apis.data.go.kr/1360000/EqkInfoService/getEqkMsg"

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[Observation]:
        payload = response.json()
        items = _response_items(payload)
        if items is None:
            return confirmed_empty(
                "기상청이 조회 기간에 지진 통보문이 없다고 응답했습니다 (코드 03)"
            )
        if not items:
            return confirmed_empty("정상 성공 봉투의 지진 통보문 목록이 비어 있습니다")

        records = []
        seen: set[tuple[str, str]] = set()
        skipped = 0
        for item in items:
            # 신원이 하나도 없는 행은 지진이 아니다. 빈 객체를 통과시키면
            # 규모·진앙·시각이 전부 None인 '지진 발생' 레코드가 만들어진다.
            if not _has_identity(item, "tmFc", "tmSeq", "mt", "loc", "lat", "lon"):
                skipped += 1
                continue
            published = _stamp(item.get("tmFc"))
            sequence = str(item.get("tmSeq") or "").strip()
            key = (str(item.get("tmFc") or ""), sequence)
            if key in seen:
                continue
            seen.add(key)

            magnitude = _number(item.get("mt"))
            point = _korea_point(item.get("lat"), item.get("lon"))
            flags: list[QualityFlag] = []
            notes: list[str] = []
            if magnitude is None:
                flags.append(QualityFlag.PARTIAL_RESPONSE)
                notes.append("규모가 결측입니다 — 0.0이 아니라 확인되지 않은 값입니다")
            if point is None:
                flags.append(QualityFlag.MISSING_COORDINATES)
                notes.append(
                    "진앙 좌표가 없거나 한반도 범위를 벗어나 지도 위치를 확정할 수 없습니다"
                )
            if intensity := str(item.get("inT") or "").strip():
                notes.append(f"최대진도: {intensity}")

            occurred = _stamp(item.get("tmEqk")) or published or response.retrieved_at
            records.append(
                self.record(
                    Observation(
                        kind="earthquake_magnitude",
                        value=magnitude,
                        unit="M",
                        station=str(item.get("loc") or "").strip() or None,
                        location=point,
                        target_time=occurred,
                        raw_code=sequence or None,
                    ),
                    response,
                    observed_at=occurred,
                    published_at=published,
                    quality_flags=tuple(flags),
                    notes=tuple(notes),
                )
            )
        return FetchOutcome(
            records=tuple(records),
            caveats=(
                "전국 지진을 반환합니다 — 행정구역 문자열로 걸러 인접 진앙을 숨기지 않습니다",
            ),
        )


class TsunamiConnector(_DatedKmaConnector[HazardAlert]):
    """지진해일 통보문. 코드 03을 현재 발효 자료가 없다는 확인으로 보존한다."""

    dataset_id: ClassVar[str] = "15000420"
    num_rows: ClassVar[int] = 20

    @property
    def dataset_name(self) -> str:
        return "기상청 지진해일 통보문"

    def base_url(self) -> str:
        return "https://apis.data.go.kr/1360000/EqkInfoService/getTsunamiMsg"

    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[HazardAlert]:
        payload = response.json()
        items = _response_items(payload)
        if items is None:
            return confirmed_empty("기상청이 지진해일 통보문이 없다고 응답했습니다 (코드 03)")
        if not items:
            return confirmed_empty("정상 성공 봉투의 지진해일 통보문 목록이 비어 있습니다")

        records = []
        skipped = 0
        for item in items:
            if not _has_identity(item, "tmFc", "title", "rem", "cnt", "loc"):
                skipped += 1
                continue
            title = str(
                item.get("title") or item.get("rem") or item.get("cnt") or "지진해일 통보문"
            ).strip()
            area = str(item.get("loc") or "").strip() or "영향 지역 미확인"
            issued = _stamp(item.get("tmFc"))
            point = _korea_point(item.get("lat"), item.get("lon"))
            flags = () if point is not None else (QualityFlag.MISSING_COORDINATES,)
            notes = tuple(
                f"{label}: {value}"
                for label, value in (
                    ("참고", str(item.get("rem") or "").strip()),
                    ("수정", str(item.get("cor") or "").strip()),
                )
                if value and value != title
            )
            records.append(
                self.record(
                    HazardAlert(
                        hazard=HazardDomain.TSUNAMI,
                        severity=parse_severity(title),
                        headline=title,
                        area_name=area,
                        action=parse_alert_action(title),
                        area_code=str(item.get("tmSeq") or "").strip() or None,
                        location=point,
                        issued_at=issued,
                        raw_level=str(item.get("fcTp") or "").strip() or None,
                    ),
                    response,
                    published_at=issued,
                    quality_flags=flags,
                    notes=notes,
                )
            )
        return FetchOutcome(
            records=tuple(records),
            caveats=("지진해일 탐지 통보문이며 대피장소·대피경로 자료를 포함하지 않습니다",),
        )


__all__ = ["EarthquakeConnector", "TsunamiConnector"]
