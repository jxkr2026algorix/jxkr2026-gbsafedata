"""안전 경계.

기획에서 "절대 하지 않을 것"으로 정한 항목들을 코드가 강제한다. 문서에만 있는
금지는 언젠가 어겨지므로, 위반 가능한 지점마다 함수를 두고 호출을 요구한다.

강제하는 경계:

1. 공공데이터 인프라는 **외부에 영향을 주는 행위를 하지 않는다** — 전화 발신,
   대피명령, 주민 상태 변경. `assert_read_only()`가 막는다.
2. **집계 인구통계로 개인을 추정하지 않는다.** `assert_not_individual_inference()`.
3. **AI가 대피명령을 승인하지 않는다.** `require_human_approval()`.
4. **출처·시점 없는 값을 판단 근거로 제시하지 않는다.** `assert_citable()`.
5. **실데이터와 훈련데이터를 섞지 않는다.** `assert_mode_consistent()`.
6. **재난유형이 맞지 않는 대피소를 배정하지 않는다.** `assert_shelter_suitable()`.

이 모듈은 예외를 던진다. 경고 로그로 흘려보내지 않는다.
"""

from __future__ import annotations

from .domain import Shelter
from .models import DataMode, Freshness, Record
from .regions import HazardDomain


class SafetyViolation(RuntimeError):
    """안전 경계를 넘으려 했을 때. 실행을 중단시킨다."""

    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        super().__init__(f"[{rule}] {detail}")


#: MCP·API가 절대 노출하지 않는 행위. 이름에 이 단어가 들어간 도구는 등록을 거부한다.
FORBIDDEN_EFFECTS: frozenset[str] = frozenset(
    {
        "call",
        "dial",
        "sms",
        "notify",
        "broadcast",
        "dispatch",
        "order",
        "command",
        "evacuate",
        "approve",
        "assign",
        "update",
        "delete",
        "create",
        "write",
        "send",
    }
)

#: 개인 단위 추정으로 이어지는 속성. 집계 데이터에서 이것을 도출하면 안 된다.
INDIVIDUAL_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "disability",
        "장애",
        "질병",
        "disease",
        "mobility",
        "이동능력",
        "보행곤란",
        "와상",
        "휠체어",
        "산소",
        "medical_condition",
        "환자",
    }
)


def assert_read_only(tool_name: str) -> None:
    """MCP 도구·API 엔드포인트가 부작용 없는 조회인지 확인한다.

    공공데이터 인프라(접근 A)는 조회만 한다. 전화 발신이나 상태 변경은
    운영 플랫폼(접근 B)의 책임이며, 그 경계가 흐려지면 인프라가 주민에게
    직접 명령을 내리는 사고가 가능해진다.
    """
    tokens = {token for token in tool_name.lower().replace("-", "_").split("_") if token}
    offending = tokens & FORBIDDEN_EFFECTS
    if offending:
        raise SafetyViolation(
            "read_only",
            f"'{tool_name}'은 외부에 영향을 주는 동작으로 읽힙니다"
            f"({', '.join(sorted(offending))}). "
            "공공데이터 인프라는 조회만 제공하며 전화·명령·상태변경은 운영 플랫폼의 책임입니다.",
        )


def assert_not_individual_inference(purpose: str) -> None:
    """집계 통계로 개인 속성을 추정하려는 시도를 막는다.

    공개 인구통계는 지역의 잠재적 취약성만 보여준다. 특정 주민이 이동에
    어려움이 있다고 판단하는 데 쓸 수 없다.
    """
    text = purpose.lower()
    hits = sorted(attr for attr in INDIVIDUAL_ATTRIBUTES if attr in text)
    if hits:
        raise SafetyViolation(
            "no_individual_inference",
            f"집계 인구통계로 개인 속성({', '.join(hits)})을 추정할 수 없습니다. "
            "지역 단위 취약성 지표로만 사용하고, 개인별 지원 필요 여부는 "
            "기관의 주민 명부에서 확인해야 합니다.",
        )


def require_human_approval(action: str) -> None:
    """AI가 자동으로 결정할 수 없는 행위임을 알린다.

    대피명령·안내문 발신·계획 확정은 담당 공무원의 검토와 승인을 거친다.
    """
    raise SafetyViolation(
        "human_approval_required",
        f"'{action}'은 담당 공무원의 검토·승인이 필요한 행위입니다. "
        "이 시스템은 근거와 후보를 제시하며 결정은 사람이 합니다.",
    )


def assert_citable(record: Record[object]) -> None:
    """출처와 시점이 판단 근거로 쓸 수 있는 상태인지 확인한다."""
    provenance = record.provenance
    if not provenance.dataset_id or not provenance.provider:
        raise SafetyViolation(
            "citation_required",
            "출처(데이터셋·기관)가 없는 값은 판단 근거로 제시할 수 없습니다.",
        )
    if not record.freshness.is_usable_for_decision:
        raise SafetyViolation(
            "staleness",
            f"「{provenance.dataset_name}」의 신선도가 {record.freshness.status.value}입니다: "
            f"{record.freshness.reason}. 최신 자료를 확인하거나 오래된 자료임을 명시해야 합니다.",
        )


def assert_mode_consistent(records: tuple[Record[object], ...]) -> None:
    """실데이터와 훈련 합성데이터가 한 결과에 섞이지 않게 한다.

    훈련 중 합성 강우량이 실제 대피소 정보와 섞여 실제 상황처럼 보이는 것이
    이 시스템에서 가장 위험한 실패다.
    """
    modes = {record.provenance.mode for record in records}
    if DataMode.SYNTHETIC in modes and DataMode.REAL in modes:
        raise SafetyViolation(
            "mode_isolation",
            "실제 운영 데이터와 훈련용 합성 데이터가 같은 결과에 섞였습니다. "
            "훈련은 별도 세션에서 실행하고 화면에 훈련 표시를 유지해야 합니다.",
        )


def assert_shelter_suitable(shelter: Shelter, hazard: HazardDomain) -> None:
    """대피소를 해당 재난에 배정할 수 있는지 확인한다.

    지진 옥외대피장소를 호우 대피소로 자동 전용하는 것을 막는다.
    """
    if not shelter.serves(hazard):
        supported = ", ".join(item.value for item in shelter.supported_hazards) or "미확인"
        raise SafetyViolation(
            "shelter_hazard_mismatch",
            f"「{shelter.name}」은 {hazard.value} 대피시설로 확인되지 않았습니다 "
            f"(확인된 재난: {supported}). 재난유형별 적합성을 기관이 확인해야 합니다.",
        )


def describe_shelter_caveats(shelter: Shelter) -> tuple[str, ...]:
    """대피소 정보를 제시할 때 함께 표시할 주의사항.

    데이터에 존재하는 것과 실제 재난 당시 운영 가능한 것은 다르다.
    """
    caveats: list[str] = []
    if not shelter.designated:
        caveats.append("공식 지정시설이 아닌 후보시설입니다")
    if shelter.operating is None:
        caveats.append("현재 운영 여부가 확인되지 않았습니다")
    if not shelter.occupancy_is_trustworthy:
        caveats.append("현재 수용인원은 확인된 값이 아니므로 실시간으로 표시하면 안 됩니다")
    if shelter.location is None:
        caveats.append("좌표가 없어 거리·경로 계산에 사용할 수 없습니다")
    if shelter.wheelchair_accessible is None:
        caveats.append("장애인 접근 가능 여부가 확인되지 않았습니다")
    if not shelter.supported_hazards:
        caveats.append("적용 가능한 재난유형이 확인되지 않아 자동 배정 대상이 아닙니다")
    return tuple(caveats)


def route_disclaimer(verified: bool) -> str:
    """경로를 제시할 때 붙이는 문구.

    현장검증 전에는 '공식 안전경로'가 아니라 '대피 후보 경로'다.
    """
    if verified:
        return "현장 확인을 거친 경로입니다."
    return (
        "현장 검증 전 대피 후보 경로입니다. 공식 안전경로가 아니며 "
        "농로·마을길·보행로가 도로망 데이터에 누락될 수 있습니다."
    )


def freshness_disclaimer(freshness: Freshness) -> str | None:
    """신선도에 따라 함께 표시할 경고. 문제 없으면 None."""
    if freshness.is_usable_for_decision:
        return None
    return f"이 값은 {freshness.reason} — 판단 근거로 쓰기 전 원천을 다시 확인하세요."
