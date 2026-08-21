#!/usr/bin/env python3
"""테스트가 실제로 무엇을 잡는지 측정한다.

커버리지는 "이 줄이 실행됐다"만 말한다. 재난 데이터에서 필요한 것은 "이 줄이
틀렸을 때 테스트가 실패한다"는 보장이다. 그래서 **위험을 은폐하는 방향으로**
코드를 고의로 망가뜨리고, 테스트가 그것을 잡는지 본다.

각 뮤테이션은 실제로 일어날 수 있는 실패를 하나씩 흉내낸다. 예를 들어 산사태
예보단계를 전부 '낮음'으로 낮추는 것은, 파서가 등급 필드를 잘못 읽으면
그대로 벌어지는 일이다.

살아남은(survived) 뮤테이션은 그 실패를 아무 테스트도 잡지 못한다는 뜻이다.

사용법:
    uv run python scripts/mutation_audit.py              # 전체
    uv run python scripts/mutation_audit.py connector    # 이름으로 필터
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONNECTORS = REPO_ROOT / "packages/gbsafe-connectors/src/gbsafe_connectors"
CORE = REPO_ROOT / "packages/gbsafe-core/src/gbsafe_core"
API = REPO_ROOT / "packages/gbsafe-api/src/gbsafe_api"


@dataclass(frozen=True, slots=True)
class Mutation:
    """위험을 은폐하는 방향의 코드 변경 하나."""

    name: str
    path: Path
    old: str
    new: str
    harm: str
    """이 변경이 실제 상황에서 무엇을 잘못 만드는가."""


MUTATIONS: tuple[Mutation, ...] = (
    # ── 커넥터: 위험을 낮춰 보고하거나 버린다 ──────────────────────
    Mutation(
        "landslide-severity-flattened",
        CONNECTORS / "forest.py",
        "severity=parse_severity(level),",
        "severity=Severity.INFO,",
        "산사태 예보단계를 전부 '낮음'으로 낮춰 보고한다",
    ),
    Mutation(
        "landslide-records-dropped",
        CONNECTORS / "forest.py",
        '''        return FetchOutcome(
            records=tuple(records),
            caveats=(
                "시군구 단위 예보입니다''',
        '''        records = []
        return FetchOutcome(
            records=tuple(records),
            caveats=(
                "시군구 단위 예보입니다''',
        "산사태 예보를 통째로 버려 위험을 은폐한다",
    ),
    Mutation(
        "roadside-landslide-dropped",
        CONNECTORS / "forest.py",
        '''                    quality_flags=flags,
                )
            )
        return FetchOutcome(records=tuple(records), confirmed_absence=not records)''',
        '''                    quality_flags=flags,
                )
            )
        records = []
        return FetchOutcome(records=tuple(records), confirmed_absence=not records)''',
        "대피경로 단절 위험 구간을 버린다",
    ),
    Mutation(
        "wildfire-severity-flattened",
        CONNECTORS / "forest.py",
        "        severity = _fire_severity(peak if peak is not None else mean)",
        "        severity = Severity.INFO",
        "산불위험을 전부 '낮음'으로 낮춰 보고한다",
    ),
    Mutation(
        "forecast-presented-as-observation",
        CONNECTORS / "kma.py",
        "                        is_forecast=True,",
        "                        is_forecast=False,",
        "예보값을 현재 관측으로 제시해 대응 시점 판단을 틀리게 만든다",
    ),
    Mutation(
        "missing-measure-becomes-zero",
        CONNECTORS / "kma.py",
        """    if lowered in _MISSING_VALUES:
        return None""",
        """    if lowered in _MISSING_VALUES:
        return 0.0""",
        "결측 기온을 0℃로 만들어 실측처럼 보이게 한다",
    ),
    Mutation(
        "cancellation-not-reconciled",
        CONNECTORS / "kma.py",
        """    effective_ids = {
        id(bulletin)
        for bulletin in latest.values()
        if bulletin.action is not AlertAction.CANCELLED
    }""",
        """    effective_ids = {id(bulletin) for bulletin in ordered}""",
        "해제된 특보를 발효 중으로 남겨 종료된 위험을 현재로 표시한다",
    ),
    Mutation(
        "warning-region-filter-removed",
        CONNECTORS / "kma.py",
        "            if gyeongbuk_only and not serves_gyeongbuk(station_id):",
        "            if False:",
        "전국 특보를 경북 특보로 섞어 보고한다",
    ),
    Mutation(
        "emergency-beds-fake-realtime",
        CONNECTORS / "medical.py",
        "            reported_at = _hvidate(_text(item, \"hvidate\"))",
        "            reported_at = datetime.now(KST)",
        "갱신 시각이 없는 병상 수를 실시간으로 위장한다",
    ),
    Mutation(
        "shelter-hazard-invented",
        CONNECTORS / "filedata.py",
        "            declared = _declared_hazards(kind_raw, name, address)",
        "            declared = tuple(HazardDomain)",
        "지진 대피소를 호우 대피소로 쓸 수 있다고 주장한다",
    ),
    Mutation(
        "csv-unreadable-becomes-absence",
        CONNECTORS / "filedata.py",
        """        if rows and not records and not region and not hazard_hint:
            raise ValueError(""",
        """        if False:
            raise ValueError(""",
        "컬럼을 못 읽은 대피소 파일을 '대피소 없음'으로 보고한다",
    ),
    Mutation(
        "malformed-json-becomes-absence",
        CONNECTORS / "kma.py",
        """    if items is None:
        raise ValueError(""",
        """    if items is None:
        return []
    if False:
        raise ValueError(""",
        "구조를 알아보지 못한 응답을 '자료 없음'으로 보고한다",
    ),
    Mutation(
        "degraded-response-parsed",
        CONNECTORS / "base.py",
        """        if response.status is UpstreamStatus.DEGRADED:""",
        """        if False:""",
        "한도 초과·오류 응답을 파싱해 빈 결과를 '해당 없음'으로 만든다",
    ),
    Mutation(
        "error-body-stored-as-snapshot",
        CONNECTORS / "base.py",
        "        if response.status is UpstreamStatus.OK:\n            ref = self._store.put(",
        (
            "        if response.status in (UpstreamStatus.OK, UpstreamStatus.DEGRADED):"
            "\n            ref = self._store.put("
        ),
        "오류 응답을 스냅샷에 남겨 나중에 '마지막 정상자료'로 제시한다",
    ),
    Mutation(
        "cached-status-overwrites-failure",
        CONNECTORS / "base.py",
        (
            "    if response.status is UpstreamStatus.CACHED "
            "or response.status in _PRESERVED_STATUSES:"
        ),
        "    if response.status is UpstreamStatus.CACHED:",
        "동시 요청에서 403을 '조회 정상, 자료 없음'으로 바꾼다",
    ),
    Mutation(
        "region-param-guessed",
        CONNECTORS / "base.py",
        """        if region is None or cls.region_param is None:
            return {}
        return {cls.region_param: region}""",
        """        if region is None:
            return {}
        return {"region": region}""",
        "지역 필터가 조용히 무시되어 시군 질의에 도 전체 결과를 준다",
    ),
    # ── 부재 증명 ────────────────────────────────────────────────
    Mutation(
        "absence-faked-without-receipts",
        CORE / "models.py",
        """        if not self.receipts:
            return False""",
        """        if not self.receipts:
            return True""",
        "아무 원천도 조회하지 않고 '해당 없음'을 확인했다고 주장한다",
    ),
    Mutation(
        "failed-receipt-ignored",
        CORE / "models.py",
        """        return all(
            receipt.outcome is not SourceOutcome.FAILED for receipt in self.receipts
        )""",
        """        return True""",
        "실패한 원천이 있어도 결과가 완전하다고 보고한다",
    ),
    Mutation(
        "degraded-not-blocking",
        CORE / "models.py",
        "            UpstreamStatus.DEGRADED,\n        )",
        "        )",
        "파싱 실패와 한도 초과를 '해석 가능'으로 통과시킨다",
    ),
    Mutation(
        "receipt-validator-disabled",
        CORE / "models.py",
        """        if self.outcome is SourceOutcome.RECORDS and self.record_count == 0:
            raise ValueError("RECORDS는 레코드가 하나 이상이어야 합니다")""",
        """        if False:
            raise ValueError("RECORDS는 레코드가 하나 이상이어야 합니다")""",
        "자기모순 영수증(레코드 0건인데 RECORDS)을 허용한다",
    ),
    # ── 안전 경계 ────────────────────────────────────────────────
    Mutation(
        "individual-inference-allowed",
        CORE / "safety.py",
        "    if not attributes:\n        return",
        "    if True:\n        return",
        "집계 통계로 개인의 장애·이동능력을 추정하게 허용한다",
    ),
    Mutation(
        "individual-grain-ignored",
        CORE / "safety.py",
        "    if grains:\n        raise SafetyViolation(",
        "    if False:\n        raise SafetyViolation(",
        "'각 가구별 장애인' 같은 개인 단위 질의를 통과시킨다",
    ),
    Mutation(
        "read-only-gate-disabled",
        CORE / "safety.py",
        "    if mutations:\n        raise SafetyViolation(",
        "    if False:\n        raise SafetyViolation(",
        "전화 발신·대피명령 도구를 MCP에 등록할 수 있게 한다",
    ),
    Mutation(
        "read-verb-allowlist-disabled",
        CORE / "safety.py",
        "    if not (set(tokens) & READ_VERBS):",
        "    if False:",
        "조회 동사가 없는 임의 이름의 도구를 등록할 수 있게 한다",
    ),
    Mutation(
        "mode-mixing-allowed",
        CORE / "safety.py",
        "    if DataMode.SYNTHETIC in modes and DataMode.REAL in modes:",
        "    if False:",
        "훈련 합성데이터를 실제 데이터와 섞어 실제 상황처럼 보이게 한다",
    ),
    Mutation(
        "shelter-suitability-ignored",
        CORE / "domain.py",
        "        return hazard in self.supported_hazards",
        "        return True",
        "재난유형이 확인되지 않은 대피소를 모든 재난에 배정한다",
    ),
    Mutation(
        "cancelled-alert-active",
        CORE / "domain.py",
        """        return self.action in (
            AlertAction.ISSUED,
            AlertAction.EXTENDED,
            AlertAction.CURRENT_STATE,
        )""",
        "        return True",
        "해제된 특보를 발효 중으로 보고한다",
    ),
    Mutation(
        "current-state-treated-as-inactive",
        CORE / "domain.py",
        """        return self.action in (
            AlertAction.ISSUED,
            AlertAction.EXTENDED,
            AlertAction.CURRENT_STATE,
        )""",
        "        return self.action in (AlertAction.ISSUED, AlertAction.EXTENDED)",
        "산사태 예보단계처럼 현재 상태를 주는 원천의 경보를 조용히 묻는다",
    ),
    Mutation(
        "unverified-occupancy-trusted",
        CORE / "domain.py",
        "        return self.current_occupancy is not None and self.last_verified_at is not None",
        "        return True",
        "확인되지 않은 대피소 수용인원을 실시간으로 표시한다",
    ),
    # ── 라이선스 ─────────────────────────────────────────────────
    Mutation(
        "kogl4-allows-derivation",
        CORE / "licensing.py",
        """        allowed=frozenset({Operation.READ, Operation.REDISTRIBUTE}),
        attribution_required=True,
        share_alike=False,
        summary="공공누리 제4유형""",
        """        allowed=_ALL,
        attribution_required=True,
        share_alike=False,
        summary="공공누리 제4유형""",
        "변경금지 데이터의 재투영·클리핑을 허용해 이용조건을 위반한다",
    ),
    Mutation(
        "unknown-licence-permissive",
        CORE / "licensing.py",
        """    LicenseCode.UNKNOWN: LicenseTerms(
        LicenseCode.UNKNOWN,
        allowed=_READ_ONLY,""",
        """    LicenseCode.UNKNOWN: LicenseTerms(
        LicenseCode.UNKNOWN,
        allowed=_ALL,""",
        "라이선스를 모르는 데이터를 제한 없이 쓰게 허용한다",
    ),
    Mutation(
        "licence-gate-never-raises",
        CORE / "licensing.py",
        "    if not permits(license_code, operation):\n        raise LicenseViolation(",
        "    if False:\n        raise LicenseViolation(",
        "라이선스 게이트를 무력화한다",
    ),
    # ── 신선도 ───────────────────────────────────────────────────
    Mutation(
        "aging-usable-for-decision",
        CORE / "models.py",
        "        if self.status is not FreshnessStatus.FRESH:\n            return False",
        "        if False:\n            return False",
        "갱신주기 6배까지 오래된 값을 판단 근거로 허용한다",
    ),
    Mutation(
        "max-decision-age-ignored",
        CORE / "models.py",
        """        if self.max_decision_age_seconds is None or self.age_seconds is None:
            return True
        return self.age_seconds <= self.max_decision_age_seconds""",
        """        return True""",
        "원천별 판단 허용 나이를 무시한다",
    ),
    Mutation(
        "future-timestamp-fresh",
        CORE / "freshness.py",
        "    if -age > MAX_CLOCK_SKEW:",
        "    if False:",
        "2099년 관측을 영원히 '최신'으로 유지한다",
    ),
    Mutation(
        "unknown-freshness-usable",
        CORE / "freshness.py",
        "        status=FreshnessStatus.UNKNOWN,\n        age_seconds=None,",
        "        status=FreshnessStatus.FRESH,\n        age_seconds=None,",
        "관측 시각을 모르는 값을 최신으로 보고한다",
    ),
    # ── 좌표·지역 ────────────────────────────────────────────────
    Mutation(
        "grid-conversion-off-by-one",
        CORE / "regions.py",
        "    return KmaGrid(nx=math.floor(x + 0.5), ny=math.floor(y + 0.5))",
        "    return KmaGrid(nx=math.floor(x + 1.5), ny=math.floor(y + 1.5))",
        "다른 격자의 날씨를 성공적으로 반환한다 (조용한 오류)",
    ),
    Mutation(
        "coordinate-range-unchecked",
        CORE / "models.py",
        'Latitude = Annotated[float, Field(ge=33.0, le=39.0)]',
        'Latitude = Annotated[float, Field()]',
        "EPSG:5186 값을 위경도로 받아 지도에 엉뚱한 위치를 찍는다",
    ),
    Mutation(
        "transferred-region-hidden",
        CORE / "regions.py",
        "    return TRANSFERRED_OUT.get(normalized)",
        "    return None",
        "대구로 편입된 군위군을 여전히 경북으로 답한다",
    ),
    # ── 스냅샷 ───────────────────────────────────────────────────
    Mutation(
        "snapshot-hash-unverified",
        CORE / "snapshot.py",
        "            if self.digest(body) != snapshot_id:\n                return None",
        "            if False:\n                return None",
        "손상된 스냅샷을 '마지막 정상자료'로 제시한다",
    ),
    Mutation(
        "snapshot-secrets-not-redacted",
        CORE / "snapshot.py",
        '        key: ("<redacted>" if key.lower() in _SECRET_KEYS else value)',
        "        key: value",
        "스냅샷 메타데이터에 인증키를 남긴다",
    ),
    # ── API 계층 ─────────────────────────────────────────────────
    Mutation(
        "envelope-drops-mode-check",
        API / "envelope.py",
        "    assert_mode_consistent(answer.records)",
        "    pass",
        "훈련 데이터가 섞인 응답을 그대로 내보낸다",
    ),
    Mutation(
        "unknown-hazard-defaults",
        API / "service.py",
        """        if hazard_domain is None:
            supported = ", ".join(item.value for item in HazardDomain)""",
        """        if False:
            supported = ", ".join(item.value for item in HazardDomain)""",
        "알 수 없는 재난 유형을 호우로 바꿔 다른 질문에 답한다",
    ),
    Mutation(
        "search-hides-pending-review",
        API / "service.py",
        "        blocked = [entry for entry in results if not entry.dev_ready]",
        "        blocked = []",
        "심의 대기 데이터셋을 지금 쓸 수 있는 것처럼 보고한다",
    ),
)


def run_suite() -> tuple[bool, str]:
    """테스트를 돌린다. (통과 여부, 마지막 줄)"""
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/", "-q", "-x", "--no-header"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    tail = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return result.returncode == 0, tail[-1] if tail else ""


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else ""
    selected = [m for m in MUTATIONS if needle in m.name] if needle else list(MUTATIONS)

    print(f"뮤테이션 {len(selected)}건을 검사합니다.\n")
    survived: list[Mutation] = []
    unapplied: list[Mutation] = []

    for index, mutation in enumerate(selected, start=1):
        original = mutation.path.read_text(encoding="utf-8")
        if mutation.old not in original:
            unapplied.append(mutation)
            print(f"[{index:2}/{len(selected)}] {mutation.name:36} 적용 불가 (코드가 변경됨)")
            continue
        try:
            mutation.path.write_text(
                original.replace(mutation.old, mutation.new, 1), encoding="utf-8"
            )
            passed, summary = run_suite()
        finally:
            mutation.path.write_text(original, encoding="utf-8")

        if passed:
            survived.append(mutation)
            print(f"[{index:2}/{len(selected)}] {mutation.name:36} 생존  ← {summary}")
        else:
            print(f"[{index:2}/{len(selected)}] {mutation.name:36} 검출  ({summary})")

    print()
    killed = len(selected) - len(survived) - len(unapplied)
    print(f"검출 {killed} / 생존 {len(survived)} / 적용불가 {len(unapplied)}")

    if unapplied:
        print("\n적용할 수 없는 뮤테이션 (대상 코드가 바뀌었으니 갱신 필요):")
        for mutation in unapplied:
            print(f"  - {mutation.name}")

    if survived:
        print("\n생존한 뮤테이션 — 아무 테스트도 이 실패를 잡지 못합니다:")
        for mutation in survived:
            print(f"  - {mutation.name}")
            print(f"      {mutation.harm}")
        return 1

    print("\n모든 뮤테이션이 검출됐습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
