#!/usr/bin/env python3
"""실제 공공 API가 여전히 응답하고 파서가 해석하는지 확인한다.

고정된 응답으로 하는 테스트는 **원천이 응답 형태를 바꾸면 계속 통과한다.**
그때 깨지는 것은 프로덕션이다. 이 스크립트는 실제 호출로 그 간극을 메운다.

인증키가 없으면 건너뛴다(실패가 아니다). 포크된 PR에서는 시크릿이 없으므로
정상적인 상황이다.

호출 한도를 지키기 위해 원천별로 1회만 호출한다. AirKorea는 개발계정 한도가
일 500건이라 특히 아껴야 한다.

사용법:
    uv run python scripts/smoke_live_apis.py
"""

from __future__ import annotations

import asyncio
import sys

from gbsafe_connectors import get_registry
from gbsafe_core.config import CredentialName, get_settings
from gbsafe_core.models import SourceOutcome, UpstreamStatus

#: 원천별 조회 인자. 각 커넥터를 한 번씩만 부른다.
PROBES: tuple[tuple[str, dict[str, object]], ...] = (
    ("weather_now", {"location": "문경시"}),
    ("weather_forecast", {"location": "문경시"}),
    ("weather_warning", {"days": 2}),
    ("wildfire_risk", {}),
    ("emergency_beds", {"sigungu": "문경시"}),
    ("air_quality", {"rows": 10}),
)

#: 심의 대기 중이라 403이 정상인 원천. 승인되면 목록에서 빼야 한다.
PENDING_REVIEW: frozenset[str] = frozenset(
    {"landslide_forecast", "landslide_roadside", "landslide_history"}
)


async def main() -> int:
    if not get_settings().has(CredentialName.DATA_GO_KR):
        print("data.go.kr 인증키가 없어 건너뜁니다 (실패가 아닙니다).")
        return 0

    registry = get_registry()
    failures: list[str] = []
    checked = 0

    for name, kwargs in PROBES:
        outcome = await registry.create(name).fetch(**kwargs)
        checked += 1
        status = (
            outcome.degradations[0].status.value if outcome.degradations else "ok"
        )
        detail = outcome.degradations[0].detail if outcome.degradations else ""
        print(
            f"  {name:18} {outcome.outcome.value:16} records={len(outcome.records):4} "
            f"{status}"
        )
        if detail:
            print(f"      {detail[:120]}")

        # 레코드가 있거나, 원천이 '해당 없음'을 명시했으면 정상이다.
        if outcome.outcome is SourceOutcome.FAILED:
            failures.append(f"{name}: {status} — {detail[:160]}")

    print()
    for name in sorted(PENDING_REVIEW):
        outcome = await registry.create(name).fetch()
        checked += 1
        if not outcome.degradations:
            print(f"  {name}: 심의가 승인된 것 같습니다 — PENDING_REVIEW에서 제외하세요")
            continue
        degradation = outcome.degradations[0]
        print(f"  {name:18} {degradation.status.value} (심의 대기, 정상)")
        # 심의 대기는 NOT_AUTHORIZED여야 한다. 다른 오류면 원천이 변한 것이다.
        if degradation.status is not UpstreamStatus.NOT_AUTHORIZED:
            failures.append(
                f"{name}: 심의 대기 원천이 {degradation.status.value}를 반환 "
                f"— {degradation.detail[:120]}"
            )

    print(f"\n원천 {checked}건 확인")
    if failures:
        print("\n실패:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        print(
            "\n원천이 응답 형태를 바꿨거나 인증키가 만료됐을 수 있습니다. "
            "고정 응답 테스트는 이 변화를 잡지 못합니다.",
            file=sys.stderr,
        )
        return 1
    print("모든 원천이 응답하고 파서가 해석했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
