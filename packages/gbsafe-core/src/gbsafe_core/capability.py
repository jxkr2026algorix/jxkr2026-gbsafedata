"""재난별 가용성 — 무엇을 확인할 수 있고 무엇을 확인할 수 없는가.

대피 판단은 세 가지가 다 있어야 성립한다.

    탐지(detection)   지금 났는가
    위험도(risk)      어디가 위험한가
    대피소(shelter)   어디로 가는가

경북 13종 중 세 축이 다 있는 것은 다섯이다. 지진은 **발생을 알려주지만 어느
대피소로 보낼지 모른다.** 원전은 탐지 수단 자체가 없다. 이 차이를 숨기면
"지진 대응 가능"처럼 보이고, 실제 상황에서 갈 곳 없는 안내가 나간다.

그래서 이 저장소의 중심 불변식이 재난 단위로 한 번 더 적용된다 — **확인하지
못한 것을 확인한 것처럼 보이게 하지 않는다.** 조회 실패를 '위험 없음'으로
읽지 않는 것과 같은 규칙이다.

출처는 ../jxkr2026-datasets가 실제 호출로 검증해 생성한 `capabilities.json`이며
scripts/sync_capabilities.py로 동봉본을 갱신한다.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from .models import Frozen
from .regions import HazardDomain

_CAPABILITY_FILE = Path(__file__).parent / "data" / "capabilities.json"


class Readiness(StrEnum):
    """재난 하나에 대해 지금 어디까지 답할 수 있는지."""

    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"

    @property
    def can_answer_where_to_go(self) -> bool:
        return self is Readiness.READY

    @property
    def can_detect(self) -> bool:
        return self in (Readiness.READY, Readiness.PARTIAL)


#: 조사 저장소의 한국어 재난명 → 우리 열거형.
#:
#: 이름을 그대로 쓰지 않는 이유는 API·MCP 인자가 영문 식별자이기 때문이고,
#: 매핑을 여기 한 곳에 두는 이유는 새 재난이 추가됐을 때 조용히 빠지는 것을
#: `TestEveryHazardIsMapped`가 잡게 하기 위해서다.
_KOREAN_NAMES: dict[str, HazardDomain] = {
    "호우": HazardDomain.HEAVY_RAIN,
    "홍수": HazardDomain.FLOOD,
    "산사태": HazardDomain.LANDSLIDE,
    "산불": HazardDomain.WILDFIRE,
    "태풍": HazardDomain.TYPHOON,
    "지진": HazardDomain.EARTHQUAKE,
    "지진해일": HazardDomain.TSUNAMI,
    "폭염": HazardDomain.HEATWAVE,
    "한파": HazardDomain.COLD_WAVE,
    "대설": HazardDomain.HEAVY_SNOW,
    "가뭄": HazardDomain.DROUGHT,
    "화학사고": HazardDomain.CHEMICAL_ACCIDENT,
    "원전": HazardDomain.NUCLEAR,
}

_AXIS_LABELS = {
    "detection": "탐지",
    "risk": "위험도",
    "shelter": "대피소",
}


class Axis(Frozen):
    """한 축에 대해 쓸 수 있는 원천이 몇 개인지."""

    name: str
    label: str
    usable: int
    total: int
    sources: tuple[str, ...] = ()

    @property
    def is_covered(self) -> bool:
        return self.usable > 0


class HazardCapability(Frozen):
    """재난 하나의 가용성."""

    hazard: HazardDomain
    korean_name: str
    readiness: Readiness
    detection: Axis
    risk: Axis
    shelter: Axis

    @property
    def axes(self) -> tuple[Axis, ...]:
        return (self.detection, self.risk, self.shelter)

    @property
    def missing_axes(self) -> tuple[str, ...]:
        return tuple(axis.label for axis in self.axes if not axis.is_covered)

    def caveat(self) -> str | None:
        """이 재난을 답할 때 반드시 함께 나가야 하는 한계.

        `ready`가 아니면 None을 돌려주지 않는다. 한계를 붙이지 않고 답하면
        읽는 쪽은 완전한 답으로 받아들인다.
        """
        if self.readiness is Readiness.READY:
            return None
        if self.readiness is Readiness.BLOCKED:
            return (
                f"{self.korean_name}은 현재 탐지 수단이 없습니다 — 발생 여부를 "
                "이 시스템으로 확인할 수 없습니다. 결과가 비어 있어도 "
                "'발생하지 않았다'는 뜻이 아닙니다."
            )
        if self.readiness is Readiness.UNKNOWN:
            return (
                f"{self.korean_name}의 데이터 가용성이 확인되지 않았습니다 — "
                "이 답을 완전한 것으로 보면 안 됩니다."
            )
        missing = ", ".join(self.missing_axes)
        return (
            f"{self.korean_name}은 부분적으로만 확인할 수 있습니다 — "
            f"{missing} 축의 자료가 없습니다. 대피 판단에 필요한 정보가 "
            "빠져 있으므로 이 답만으로 대피를 결정하면 안 됩니다."
        )


def _axis(name: str, raw: Any) -> Axis:
    payload = raw if isinstance(raw, dict) else {}
    sources = payload.get("sources")
    return Axis(
        name=name,
        label=_AXIS_LABELS.get(name, name),
        usable=int(payload.get("usable") or 0),
        total=int(payload.get("total") or 0),
        sources=tuple(str(item) for item in sources) if isinstance(sources, list) else (),
    )


def _load() -> dict[HazardDomain, HazardCapability]:
    if not _CAPABILITY_FILE.is_file():
        return {}
    payload = json.loads(_CAPABILITY_FILE.read_text(encoding="utf-8"))
    hazards = payload.get("hazards")
    if not isinstance(hazards, dict):
        return {}

    found: dict[HazardDomain, HazardCapability] = {}
    for korean, entry in hazards.items():
        domain = _KOREAN_NAMES.get(str(korean).strip())
        if domain is None or not isinstance(entry, dict):
            continue
        try:
            readiness = Readiness(str(entry.get("status")))
        except ValueError:
            readiness = Readiness.UNKNOWN
        found[domain] = HazardCapability(
            hazard=domain,
            korean_name=str(korean),
            readiness=readiness,
            detection=_axis("detection", entry.get("detection")),
            risk=_axis("risk", entry.get("risk")),
            shelter=_axis("shelter", entry.get("shelter")),
        )
    return found


#: 재난별 가용성. scripts/sync_capabilities.py로 갱신한다.
CAPABILITIES: dict[HazardDomain, HazardCapability] = _load()


def capability_for(hazard: HazardDomain) -> HazardCapability:
    """이 재난의 가용성. 매트릭스에 없으면 UNKNOWN으로 답한다.

    없는 것을 `ready`로 기본값 처리하지 않는다. 모르는 재난을 대응 가능한
    것으로 표시하는 것이 이 모듈이 막으려는 실패다.
    """
    known = CAPABILITIES.get(hazard)
    if known is not None:
        return known
    empty = {"usable": 0, "total": 0, "sources": []}
    return HazardCapability(
        hazard=hazard,
        korean_name=hazard.value,
        readiness=Readiness.UNKNOWN,
        detection=_axis("detection", empty),
        risk=_axis("risk", empty),
        shelter=_axis("shelter", empty),
    )


def readiness_summary() -> dict[str, tuple[str, ...]]:
    """상태별 재난 목록. 화면과 도구 응답에 그대로 쓴다."""
    summary: dict[str, list[str]] = {item.value: [] for item in Readiness}
    for capability in CAPABILITIES.values():
        summary[capability.readiness.value].append(capability.korean_name)
    return {key: tuple(value) for key, value in summary.items() if value}


__all__ = [
    "CAPABILITIES",
    "Axis",
    "HazardCapability",
    "Readiness",
    "capability_for",
    "readiness_summary",
]
