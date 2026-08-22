"""재난별 가용성 테스트.

이 계층이 막는 실패는 하나다 — **확인할 수 없는 재난을 확인할 수 있는 것처럼
보이게 하는 것.** 지진은 발생을 알려주지만 어느 대피소로 보낼지 모르고,
원전은 탐지 수단 자체가 없다. 그 차이가 지워지면 갈 곳 없는 안내가 나간다.
"""

from __future__ import annotations

import pytest
from gbsafe_core.capability import (
    CAPABILITIES,
    Readiness,
    capability_for,
    readiness_summary,
)
from gbsafe_core.regions import HazardDomain


class TestMatrixIsLoaded:
    def test_every_surveyed_hazard_is_present(self) -> None:
        assert len(CAPABILITIES) == 13, sorted(c.korean_name for c in CAPABILITIES.values())

    def test_every_hazard_domain_except_other_is_mapped(self) -> None:
        """새 재난이 추가됐는데 매핑이 빠지면 조용히 UNKNOWN이 된다."""
        unmapped = [
            hazard.value
            for hazard in HazardDomain
            if hazard is not HazardDomain.OTHER and hazard not in CAPABILITIES
        ]
        assert not unmapped, f"가용성 매트릭스에 없는 재난: {unmapped}"

    def test_summary_covers_every_hazard(self) -> None:
        summary = readiness_summary()
        total = sum(len(names) for names in summary.values())
        assert total == len(CAPABILITIES)


class TestReadinessIsHonest:
    def test_ready_hazards_have_all_three_axes(self) -> None:
        for capability in CAPABILITIES.values():
            if capability.readiness is not Readiness.READY:
                continue
            assert not capability.missing_axes, (
                f"{capability.korean_name}이 ready인데 {capability.missing_axes} 축이 빕니다"
            )

    def test_partial_hazards_are_missing_something(self) -> None:
        """빠진 것이 없는데 partial이면 분류가 틀린 것이다."""
        for capability in CAPABILITIES.values():
            if capability.readiness is not Readiness.PARTIAL:
                continue
            assert capability.missing_axes, (
                f"{capability.korean_name}이 partial인데 빠진 축이 없습니다"
            )

    def test_blocked_hazards_cannot_detect(self) -> None:
        for capability in CAPABILITIES.values():
            if capability.readiness is not Readiness.BLOCKED:
                continue
            assert not capability.detection.is_covered
            assert not capability.readiness.can_detect

    def test_only_ready_can_say_where_to_go(self) -> None:
        for capability in CAPABILITIES.values():
            expected = capability.readiness is Readiness.READY
            assert capability.readiness.can_answer_where_to_go is expected


class TestCaveatsAreAttached:
    def test_ready_hazard_has_no_caveat(self) -> None:
        assert capability_for(HazardDomain.HEAVY_RAIN).caveat() is None

    @pytest.mark.parametrize(
        "hazard",
        [h for h, c in CAPABILITIES.items() if c.readiness is not Readiness.READY],
    )
    def test_every_incomplete_hazard_carries_a_caveat(self, hazard: HazardDomain) -> None:
        """한계를 붙이지 않고 답하면 읽는 쪽은 완전한 답으로 받아들인다."""
        caveat = capability_for(hazard).caveat()
        assert caveat, f"{hazard.value}에 한계 설명이 없습니다"

    def test_earthquake_says_it_cannot_route(self) -> None:
        caveat = capability_for(HazardDomain.EARTHQUAKE).caveat()
        assert caveat is not None
        assert "대피소" in caveat

    def test_blocked_hazard_says_empty_is_not_absence(self) -> None:
        """탐지 수단이 없으면 빈 결과가 '발생하지 않음'이 아니다."""
        caveat = capability_for(HazardDomain.NUCLEAR).caveat()
        assert caveat is not None
        assert "탐지 수단이 없" in caveat
        assert "발생하지 않았다" in caveat

    def test_unknown_hazard_is_not_treated_as_ready(self) -> None:
        """매트릭스에 없는 재난을 대응 가능한 것으로 기본값 처리하면 안 된다."""
        capability = capability_for(HazardDomain.OTHER)
        assert capability.readiness is Readiness.UNKNOWN
        assert capability.caveat() is not None
        assert not capability.readiness.can_detect
        assert not capability.readiness.can_answer_where_to_go
