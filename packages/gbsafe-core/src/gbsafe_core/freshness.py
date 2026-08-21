"""신선도 판정.

재난 데이터에서 '오래된 값'과 '값이 없음'은 전혀 다른 의미인데, 화면에서는
둘 다 그냥 숫자로 보인다. 그래서 나이를 계산해 등급을 붙이고, 판단 근거로
쓸 수 있는지(`is_usable_for_decision`)를 명시한다.

등급 기준은 데이터셋의 예상 갱신주기 대비 상대적으로 정한다. 5분 주기 레이더와
분기 갱신 통계에 같은 절대 임계값을 쓰는 것은 의미가 없다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .models import Freshness, FreshnessStatus

#: 예상 갱신주기의 몇 배까지를 각 등급으로 볼지.
AGING_CYCLE_MULTIPLE = 2.0
STALE_CYCLE_MULTIPLE = 6.0

#: 갱신주기가 알려지지 않은 데이터에 적용하는 절대 임계값.
UNKNOWN_CYCLE_AGING = timedelta(hours=6)
UNKNOWN_CYCLE_STALE = timedelta(days=2)


def evaluate(
    *,
    as_of: datetime,
    expected_cycle_seconds: int | None,
    now: datetime | None = None,
) -> Freshness:
    """관측 시각과 갱신주기로 신선도를 판정한다.

    `as_of`가 미래인 경우도 다룬다. 예보 데이터는 대상시각이 미래이므로
    정상이며, 이때는 나이를 0으로 본다.
    """
    evaluated_at = now or datetime.now(UTC)
    if as_of.tzinfo is None:
        raise ValueError("as_of는 시간대를 포함해야 합니다")

    age = evaluated_at - as_of
    age_seconds = max(0, int(age.total_seconds()))

    if expected_cycle_seconds and expected_cycle_seconds > 0:
        aging_at = expected_cycle_seconds * AGING_CYCLE_MULTIPLE
        stale_at = expected_cycle_seconds * STALE_CYCLE_MULTIPLE
        if age_seconds <= aging_at:
            status = FreshnessStatus.FRESH
            reason = f"갱신주기 {expected_cycle_seconds}초 대비 {age_seconds}초 경과 — 정상"
        elif age_seconds <= stale_at:
            status = FreshnessStatus.AGING
            reason = (
                f"갱신주기 {expected_cycle_seconds}초의 "
                f"{age_seconds / expected_cycle_seconds:.1f}배 경과 — 갱신 지연 가능"
            )
        else:
            status = FreshnessStatus.STALE
            reason = (
                f"갱신주기 {expected_cycle_seconds}초의 "
                f"{age_seconds / expected_cycle_seconds:.1f}배 경과 — 판단 근거로 쓰기 부적합"
            )
    else:
        if age_seconds <= UNKNOWN_CYCLE_AGING.total_seconds():
            status = FreshnessStatus.FRESH
            reason = f"갱신주기 미확인, {age_seconds}초 경과 — 최근 자료"
        elif age_seconds <= UNKNOWN_CYCLE_STALE.total_seconds():
            status = FreshnessStatus.AGING
            reason = f"갱신주기 미확인, {age_seconds / 3600:.1f}시간 경과"
        else:
            status = FreshnessStatus.STALE
            reason = f"갱신주기 미확인, {age_seconds / 86400:.1f}일 경과 — 최신성 확인 필요"

    return Freshness(
        status=status,
        age_seconds=age_seconds,
        expected_cycle_seconds=expected_cycle_seconds,
        as_of=as_of,
        evaluated_at=evaluated_at,
        reason=reason,
    )


def unknown(*, now: datetime | None = None) -> Freshness:
    """관측 시각 자체를 알 수 없는 경우.

    '모른다'를 '최신이다'로 바꿔 표시하지 않기 위해 별도 상태로 남긴다.
    """
    evaluated_at = now or datetime.now(UTC)
    return Freshness(
        status=FreshnessStatus.UNKNOWN,
        age_seconds=None,
        expected_cycle_seconds=None,
        as_of=evaluated_at,
        evaluated_at=evaluated_at,
        reason="원천이 관측·발표 시각을 제공하지 않아 신선도를 판정할 수 없습니다",
    )


#: 갱신주기 표기 문자열 → 초. 포털 표기가 한국어 자연어라 매핑이 필요하다.
_CYCLE_TEXT: tuple[tuple[str, int], ...] = (
    ("1분", 60),
    ("5분", 300),
    ("10분", 600),
    ("15분", 900),
    ("30분", 1800),
    ("실시간", 300),
    ("수시", 3600),
    ("1시간", 3600),
    ("시간", 3600),
    ("일일", 86400),
    ("매일", 86400),
    ("일", 86400),
    ("주간", 604800),
    ("주", 604800),
    ("월간", 2_592_000),
    ("매월", 2_592_000),
    ("월", 2_592_000),
    ("분기", 7_776_000),
    ("반기", 15_552_000),
    ("연간", 31_536_000),
    ("년", 31_536_000),
)


def parse_update_cycle(raw: str | None) -> int | None:
    """포털의 갱신주기 표기를 초로 변환한다. 판별 불가면 None."""
    if not raw:
        return None
    text = raw.strip()
    if not text or text == "-":
        return None
    for needle, seconds in _CYCLE_TEXT:
        if needle in text:
            return seconds
    return None
