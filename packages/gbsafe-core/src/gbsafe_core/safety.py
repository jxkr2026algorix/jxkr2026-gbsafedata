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

import re
import unicodedata

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

#: 보호 대상 속성. 개인 단위로 추정하면 안 되는 것들.
_SENSITIVE_ATTRIBUTES: tuple[str, ...] = (
    "장애", "질병", "질환", "건강상태", "건강 상태", "환자", "요양", "치매",
    "disability", "disabled", "disease", "illness", "medical condition",
    "medical_condition", "health status", "patient", "frail", "dementia",
    "이동능력", "이동 능력", "이동이 불편", "이동 불편", "보행곤란", "보행 곤란",
    "보행이 곤란", "혼자 못 걷", "걷지 못", "거동불편", "거동 불편", "와상", "휠체어",
    "wheelchair", "mobility", "unable to walk", "cannot walk", "bedridden",
    "immobile", "evacuate unaided", "unaided", "without assistance",
    "도움 없이", "혼자 대피",
    "산소", "oxygen", "ventilator", "인공호흡", "투석", "dialysis",
)

#: 개인·가구 단위를 가리키는 표현. 보호 속성과 함께 나오면 개인 추정이다.
_INDIVIDUAL_GRAIN: tuple[str, ...] = (
    "각자", "개인", "개별", "각 주민", "주민별", "특정 주민", "누가", "누구",
    "명단", "명부", "목록", "가구별", "세대별", "가구당", "세대당", "집집",
    "가구 단위", "세대 단위", "가구마다", "세대마다",
    "per person", "per-person", "per resident", "per household", "per address",
    "per house", "each resident", "each person", "each household", "individual",
    "individuals", "by name", "who needs", "who is", "who cannot", "who can",
    "list of residents", "residents unable", "residents by", "identify",
)

#: 지역 단위 집계를 가리키는 표현. 이것만 있으면 정당한 용도다.
_AGGREGATE_GRAIN: tuple[str, ...] = (
    "비율", "지수", "규모", "총인구", "인구수", "집계", "통계", "분포",
    "마을별", "지역별", "시군별", "읍면동", "단위 취약성",
    "ratio", "proportion", "index", "aggregate", "total population",
    "distribution", "by village", "by region", "by district",
)


def _normalize(text: str) -> str:
    """유니코드 트릭으로 키워드 검사를 우회하는 것을 막는다.

    NFKC로 전각·호환 문자를 접고, 방향 제어(Cf)와 제로폭 문자를 제거한다.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    stripped = "".join(
        ch
        for ch in folded
        if unicodedata.category(ch) != "Cf" and ch not in "\u200b\u200c\u200d"
    )
    return re.sub(r"\s+", " ", stripped)


def _split_identifier(name: str) -> frozenset[str]:
    """식별자를 단어로 쪼갠다.

    snake_case만 나누면 `callAmbulance`가 한 토큰이 되어 금지어 검사를 통과한다.
    camelCase 경계에서도 잘라야 한다.
    """
    # NFKC를 먼저 적용해야 전각 문자가 ASCII가 된다. 순서를 바꾸면
    # `ｃａｌｌAmbulance`의 camelCase 경계를 찾지 못한다.
    folded = unicodedata.normalize("NFKC", name)
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", folded)
    normalized = _normalize(spaced)
    return frozenset(token for token in re.split(r"[^a-z0-9]+", normalized) if token)


def assert_read_only(tool_name: str) -> None:
    """MCP 도구·API 엔드포인트가 부작용 없는 조회인지 확인한다.

    공공데이터 인프라(접근 A)는 조회만 한다. 전화 발신이나 상태 변경은
    운영 플랫폼(접근 B)의 책임이며, 그 경계가 흐려지면 인프라가 주민에게
    직접 명령을 내리는 사고가 가능해진다.
    """
    tokens = _split_identifier(tool_name)
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

    키워드 하나로 판단하지 않는다. 위험한 것은 어휘가 아니라 **질문의 단위**다.
    "고령인구 비율"은 정당하고 "누가 혼자 못 걷는지"는 아니며, 둘 다 같은
    데이터에서 나온다. 그래서 보호 속성과 개인·가구 단위 표현이 함께 나타나는지
    본다.

    단위가 모호하면 막는다. 통과시켜 개인 추정에 쓰이는 것보다 되묻는 편이
    안전하다.
    """
    text = _normalize(purpose)
    attributes = sorted({item for item in _SENSITIVE_ATTRIBUTES if _normalize(item) in text})
    if not attributes:
        return

    grains = sorted({item for item in _INDIVIDUAL_GRAIN if _normalize(item) in text})
    if grains:
        raise SafetyViolation(
            "no_individual_inference",
            f"집계 인구통계로 개인 단위 속성({', '.join(attributes)})을 추정할 수 "
            f"없습니다. 요청이 개인·가구 단위({', '.join(grains)})를 가리킵니다. "
            "지역 단위 취약성 지표로만 사용하고, 개인별 지원 필요 여부는 기관의 "
            "주민 명부에서 확인해야 합니다.",
        )

    if not any(item for item in _AGGREGATE_GRAIN if _normalize(item) in text):
        raise SafetyViolation(
            "no_individual_inference",
            f"보호 대상 속성({', '.join(attributes)})을 다루는 요청이 지역 집계 "
            "단위임을 명시하지 않았습니다. 공개 통계로는 개인의 장애·질병·이동능력을 "
            "판단할 수 없습니다. '마을별 비율'처럼 집계 단위를 명시하거나, 개인별 "
            "지원 필요 여부는 기관의 주민 명부에서 확인하세요.",
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
