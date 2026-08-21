"""라이선스 게이트.

이 프로젝트는 라이선스를 문서에 적어두는 수준을 넘어 **코드가 강제**한다.
근거는 실제로 확인된 제약이다(../jxkr2026-datasets/README.md):

- 홍수 계열 21종은 공공누리 제4유형 — 출처표시 + 상업적 이용금지 + **변경금지**
- AirKorea는 제3유형 — **변경금지**
- OSM은 ODbL — share-alike이므로 재배포 시 전염된다

변경금지 데이터에 재투영·클리핑·래스터화·파생 라벨 생성을 하면 이용조건 위반이다.
그런데 그것이 대피 분석이 데이터로 하려는 일의 대부분이다. 그래서 파생 연산을
호출하는 지점에서 `require()`를 통과해야만 진행되도록 만들었다.
"""

from __future__ import annotations

import re
from enum import StrEnum

from .models import LicenseCode


class Operation(StrEnum):
    """라이선스가 구분해서 다루는 행위."""

    READ = "read"
    """조회·화면 표시. 사실상 항상 허용된다."""

    DERIVE = "derive"
    """변경·가공. 재투영, 클리핑, 래스터화, 조인, 파생 지표 생성."""

    REDISTRIBUTE = "redistribute"
    """원본 또는 가공본을 저장소·산출물에 담아 배포."""

    COMMERCIAL = "commercial"
    """상업적 이용."""


class LicenseTerms:
    """한 라이선스가 어떤 행위를 허용하는지."""

    __slots__ = ("_allowed", "attribution_required", "code", "share_alike", "summary")

    def __init__(
        self,
        code: LicenseCode,
        *,
        allowed: frozenset[Operation],
        attribution_required: bool,
        share_alike: bool,
        summary: str,
    ) -> None:
        self.code = code
        self._allowed = allowed
        self.attribution_required = attribution_required
        self.share_alike = share_alike
        self.summary = summary

    def permits(self, operation: Operation) -> bool:
        return operation in self._allowed

    def forbidden(self) -> frozenset[Operation]:
        return frozenset(Operation) - self._allowed


_ALL = frozenset(Operation)
_READ_ONLY = frozenset({Operation.READ})

TERMS: dict[LicenseCode, LicenseTerms] = {
    LicenseCode.UNRESTRICTED: LicenseTerms(
        LicenseCode.UNRESTRICTED,
        allowed=_ALL,
        attribution_required=False,
        share_alike=False,
        summary="이용허락범위 제한 없음",
    ),
    LicenseCode.KOGL_1: LicenseTerms(
        LicenseCode.KOGL_1,
        allowed=_ALL,
        attribution_required=True,
        share_alike=False,
        summary="공공누리 제1유형 — 출처표시",
    ),
    LicenseCode.KOGL_2: LicenseTerms(
        LicenseCode.KOGL_2,
        allowed=frozenset({Operation.READ, Operation.DERIVE, Operation.REDISTRIBUTE}),
        attribution_required=True,
        share_alike=False,
        summary="공공누리 제2유형 — 출처표시 + 상업적 이용금지",
    ),
    LicenseCode.KOGL_3: LicenseTerms(
        LicenseCode.KOGL_3,
        allowed=frozenset({Operation.READ, Operation.REDISTRIBUTE, Operation.COMMERCIAL}),
        attribution_required=True,
        share_alike=False,
        summary="공공누리 제3유형 — 출처표시 + 변경금지",
    ),
    LicenseCode.KOGL_4: LicenseTerms(
        LicenseCode.KOGL_4,
        allowed=frozenset({Operation.READ, Operation.REDISTRIBUTE}),
        attribution_required=True,
        share_alike=False,
        summary="공공누리 제4유형 — 출처표시 + 상업적 이용금지 + 변경금지",
    ),
    LicenseCode.ODBL: LicenseTerms(
        LicenseCode.ODBL,
        allowed=_ALL,
        attribution_required=True,
        share_alike=True,
        summary="ODbL — 출처표시 + 동일조건 변경허락(share-alike 전염)",
    ),
    LicenseCode.PROPRIETARY: LicenseTerms(
        LicenseCode.PROPRIETARY,
        allowed=_READ_ONLY,
        attribution_required=True,
        share_alike=False,
        summary="개별 계약 필요",
    ),
    LicenseCode.UNKNOWN: LicenseTerms(
        LicenseCode.UNKNOWN,
        allowed=_READ_ONLY,
        attribution_required=True,
        share_alike=False,
        summary="라이선스 미확인 — 조회만 허용",
    ),
}

#: 공공누리 유형 번호 → LicenseCode.
_KOGL_TYPES: dict[str, LicenseCode] = {
    "1": LicenseCode.KOGL_1,
    "2": LicenseCode.KOGL_2,
    "3": LicenseCode.KOGL_3,
    "4": LicenseCode.KOGL_4,
}

#: 유형 번호를 찾는다. 포털 표기가 "제1유형", "제 1유형", "KOGL-1",
#: "공공저작물 : 출처표시 (제 1유형)"처럼 흔들리므로 공백·구두점을 허용한다.
#: 실제 카탈로그에서 "제 1유형"(공백 포함) 14건이 미인식으로 새어나갔다.
_KOGL_PATTERN = re.compile(r"(?:제\s*([1-4])\s*유\s*형|kogl\s*[-–—_]?\s*([1-4]))")

#: 유형 번호가 없을 때 문구로 판별하는 표기.
_TEXT_PATTERNS: tuple[tuple[str, LicenseCode], ...] = (
    ("제한없음", LicenseCode.UNRESTRICTED),
    ("제한 없음", LicenseCode.UNRESTRICTED),
    ("odbl", LicenseCode.ODBL),
    ("open database license", LicenseCode.ODBL),
)


class LicenseViolation(RuntimeError):
    """이용조건이 금지한 연산을 시도했을 때.

    막는 것이 목적이므로 조용히 무시하지 않고 예외로 올린다.
    """

    def __init__(self, license_code: LicenseCode, operation: Operation, subject: str) -> None:
        terms = TERMS[license_code]
        self.license_code = license_code
        self.operation = operation
        self.subject = subject
        super().__init__(
            f"{subject}: {terms.summary} 조건에서 '{operation.value}' 연산은 허용되지 않습니다. "
            f"화면 표시(read)만 가능하거나 원천기관의 별도 허락이 필요합니다."
        )


def parse_license(raw: str | None) -> LicenseCode:
    """포털의 라이선스 표기 문자열을 코드로 정규화한다.

    판별할 수 없으면 UNKNOWN이며, UNKNOWN은 조회만 허용된다. 관대하게 추정해서
    위반을 통과시키는 것보다 막고 확인하게 하는 편이 안전하다.

    다만 **표기 변형 때문에 UNKNOWN이 되는 것은 다른 문제다.** 실제로 허용된
    연산이 막혀 사용자가 이유를 알 수 없게 된다. 그래서 유형 번호는 공백·구두점
    변형을 흡수해 찾는다.
    """
    if not raw:
        return LicenseCode.UNKNOWN
    text = raw.strip().lower()

    match = _KOGL_PATTERN.search(text)
    if match is not None:
        number = match.group(1) or match.group(2)
        resolved = _KOGL_TYPES.get(number)
        if resolved is not None:
            return resolved

    for needle, code in _TEXT_PATTERNS:
        if needle in text:
            return code
    return LicenseCode.UNKNOWN


def terms_for(license_code: LicenseCode) -> LicenseTerms:
    return TERMS.get(license_code, TERMS[LicenseCode.UNKNOWN])


def permits(license_code: LicenseCode, operation: Operation) -> bool:
    return terms_for(license_code).permits(operation)


def require(license_code: LicenseCode, operation: Operation, subject: str) -> None:
    """연산을 허용하지 않으면 LicenseViolation을 던진다.

    파생 계산(재투영·클리핑·조인 등) 진입부에서 호출한다.
    """
    if not permits(license_code, operation):
        raise LicenseViolation(license_code, operation, subject)


def redistribution_contamination(codes: frozenset[LicenseCode]) -> str | None:
    """여러 라이선스를 한 산출물로 병합해 배포할 때의 문제를 설명한다.

    share-alike(ODbL)와 그렇지 않은 정부 데이터를 병합해 배포하면
    출처표시만 요구하는 데이터에까지 share-alike가 얹힌다. None이면 문제없음.
    """
    if not codes:
        return None
    blocked = sorted(
        code.value for code in codes if not permits(code, Operation.REDISTRIBUTE)
    )
    if blocked:
        return f"재배포가 허용되지 않는 라이선스가 포함되어 있습니다: {', '.join(blocked)}"

    contagious = sorted(code.value for code in codes if terms_for(code).share_alike)
    others = sorted(
        code.value
        for code in codes
        if not terms_for(code).share_alike and code is not LicenseCode.UNRESTRICTED
    )
    if contagious and others:
        return (
            f"share-alike 라이선스({', '.join(contagious)})와 "
            f"그렇지 않은 라이선스({', '.join(others)})를 같은 산출물로 배포하면 "
            "후자에도 동일조건 변경허락이 전염됩니다. 산출물을 분리하세요."
        )
    return None


def attribution_notice(license_code: LicenseCode, provider: str, dataset_name: str) -> str | None:
    """출처표시 의무가 있으면 표시 문구를 만든다."""
    if not terms_for(license_code).attribution_required:
        return None
    return f"출처: {provider} 「{dataset_name}」 ({terms_for(license_code).summary})"
