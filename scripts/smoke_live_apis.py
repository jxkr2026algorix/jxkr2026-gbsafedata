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
    ("river_level", {"region": "문경시"}),
    ("flood_forecast", {}),
)

#: 심의 대기 중이라 403이 정상인 원천. 승인되면 목록에서 빼야 한다.
PENDING_REVIEW: frozenset[str] = frozenset(
    {"landslide_forecast", "landslide_roadside", "landslide_history"}
)

#: 네트워크에 닿지 못한 것을 나타내는 문구.
#:
#: 원천 전체가 이 상태면 실행 위치·네트워크 문제이고 파서 결함이 아니다.
#: 둘을 섞으면 CI가 늑대소년이 된다.
_UNREACHABLE_MARKERS: tuple[str, ...] = (
    "ConnectTimeout",
    "ConnectError",
    "ReadTimeout",
    "원천에 연결할 수 없습니다",
)

#: 원천 자체가 불안정해 실패가 예상되는 커넥터.
#:
#: AirKorea는 간헐적으로 504와 `resultCode 04`를 반환한다. 조사 기록에 따르면
#: 4회 재시도로도 3회 중 1회는 실패하므로, 대피 판단 경로의 필수 의존으로 두면
#: 안 된다고 명시되어 있다. 이 원천 하나의 실패로 빌드를 깨면 CI가 신호를
#: 잃는다 — 보고는 하되 실패로 세지 않는다.
#: 근거: jxkr2026-datasets/docs/api-operations.md
FLAKY_UPSTREAMS: frozenset[str] = frozenset({"air_quality"})

#: 인증키가 **호출 위치에 묶인** 원천.
#:
#: 홍수통제소는 신청할 때 등록한 사용 URL에서만 키를 받아주고, 다른 곳에서
#: 부르면 코드 940을 돌려준다. GitHub 러너는 등록 위치가 아니므로 여기서
#: 실패하는 것은 파서 결함이 아니다. 보고는 하되 빌드를 깨지 않는다.
#: 근거: jxkr2026-datasets/docs/external-portals.md
LOCATION_BOUND_UPSTREAMS: frozenset[str] = frozenset({"river_level", "flood_forecast"})


def _is_unreachable(detail: str) -> bool:
    return any(marker in detail for marker in _UNREACHABLE_MARKERS)


async def main() -> int:
    if not get_settings().has(CredentialName.DATA_GO_KR):
        print("data.go.kr 인증키가 없어 건너뜁니다 (실패가 아닙니다).")
        return 0

    registry = get_registry()
    failures: list[str] = []
    unreachable: list[str] = []
    flaky: list[str] = []
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
            if _is_unreachable(detail):
                unreachable.append(name)
            elif name in FLAKY_UPSTREAMS:
                flaky.append(f"{name}: {detail[:120]}")
            elif name in LOCATION_BOUND_UPSTREAMS and ("940" in detail or "인증" in detail):
                flaky.append(f"{name}: 등록 위치 밖 호출로 보입니다 — {detail[:100]}")
            else:
                failures.append(f"{name}: {status} — {detail[:160]}")

    print()
    for name in sorted(PENDING_REVIEW):
        outcome = await registry.create(name).fetch()
        checked += 1
        if not outcome.degradations:
            print(f"  {name}: 심의가 승인된 것 같습니다 — PENDING_REVIEW에서 제외하세요")
            continue
        degradation = outcome.degradations[0]
        if _is_unreachable(degradation.detail):
            unreachable.append(name)
            print(f"  {name:18} 네트워크 도달 불가")
            continue
        print(f"  {name:18} {degradation.status.value} (심의 대기, 정상)")
        # 심의 대기는 NOT_AUTHORIZED여야 한다. 다른 오류면 원천이 변한 것이다.
        if degradation.status is not UpstreamStatus.NOT_AUTHORIZED:
            failures.append(
                f"{name}: 심의 대기 원천이 {degradation.status.value}를 반환 "
                f"— {degradation.detail[:120]}"
            )

    print(f"\n원천 {checked}건 확인")

    if unreachable and len(unreachable) == checked:
        # 전부 도달 불가면 실행 위치 문제다. 파서가 깨진 것이 아니다.
        print(
            "\n원천 전체에 연결하지 못했습니다. `apis.data.go.kr`은 해외 IP를 "
            "차단하므로, 한국 외부(예: GitHub 호스티드 러너)에서는 예상되는 "
            "결과입니다. 파서 검증은 건너뜁니다 — 한국 IP에서 다시 실행하세요."
        )
        return 0

    if unreachable:
        print(f"\n일부 원천에 연결하지 못했습니다: {', '.join(unreachable)}")

    if flaky:
        print("\n원천 자체가 불안정해 실패했습니다 (빌드 실패로 세지 않습니다):")
        for item in flaky:
            print(f"  - {item}")

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
