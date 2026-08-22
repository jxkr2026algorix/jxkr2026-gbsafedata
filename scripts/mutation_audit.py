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

import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONNECTORS = REPO_ROOT / "packages/gbsafe-connectors/src/gbsafe_connectors"
CORE = REPO_ROOT / "packages/gbsafe-core/src/gbsafe_core"
API = REPO_ROOT / "packages/gbsafe-api/src/gbsafe_api"
MCP = REPO_ROOT / "packages/gbsafe-mcp/src/gbsafe_mcp"


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
    # ── 자동 뮤테이션 탐색(cosmic-ray)이 찾아낸 생존자 ──────────────
    # 손으로 고른 뮤테이션은 내가 의심한 곳만 건드린다. 아래는 모든 식을
    # 기계적으로 변형해 살아남은 것들이라, 내가 보지 않은 곳에서 나왔다.
    Mutation(
        "read-only-guard-keeps-format-chars",
        CORE / "safety.py",
        r'if unicodedata.category(ch) != "Cf" and ch not in "\u200b\u200c\u200d"',
        r'if unicodedata.category(ch) != "Cf" or ch not in "\u200b\u200c\u200d"',
        "제로폭 문자만 지워 soft hyphen으로 변경 동사를 숨긴 도구를 통과시킨다",
    ),
    Mutation(
        "citation-drops-dataset-id-check",
        CORE / "safety.py",
        "if not provenance.dataset_id or not provenance.provider:",
        "if not provenance.provider:",
        "데이터셋 id 없는 값을 판단 근거로 인용하게 한다",
    ),
    Mutation(
        "citation-drops-provider-check",
        CORE / "safety.py",
        "if not provenance.dataset_id or not provenance.provider:",
        "if not provenance.dataset_id:",
        "기관 없는 값을 판단 근거로 인용하게 한다",
    ),
    Mutation(
        "citation-inverts-staleness-gate",
        CORE / "safety.py",
        "if not record.freshness.is_usable_for_decision:",
        "if not not record.freshness.is_usable_for_decision:",
        "오래된 값을 현재 상황처럼 인용하게 한다",
    ),
    Mutation(
        "shelter-hides-untrusted-occupancy",
        CORE / "safety.py",
        "if not shelter.occupancy_is_trustworthy:",
        "if not not shelter.occupancy_is_trustworthy:",
        "확인되지 않은 수용인원을 실시간 값처럼 제시해 만원인 대피소로 보낸다",
    ),
    Mutation(
        "shelter-hides-unknown-hazard",
        CORE / "safety.py",
        "if not shelter.supported_hazards:",
        "if not not shelter.supported_hazards:",
        "적용 재난이 확인되지 않은 대피소를 경고 없이 배정 대상으로 만든다",
    ),
    Mutation(
        "shelter-hides-unknown-accessibility",
        CORE / "safety.py",
        "if shelter.wheelchair_accessible is None:",
        "if shelter.wheelchair_accessible is not None:",
        "장애인 접근 여부 미확인을 알리지 않아 이동약자를 못 가는 곳으로 보낸다",
    ),
    Mutation(
        "record-becomes-mutable",
        CORE / "models.py",
        'model_config = ConfigDict(frozen=True, extra="forbid")',
        'model_config = ConfigDict(frozen=False, extra="forbid")',
        "출처가 붙은 뒤 값만 바꿔치기할 수 있게 해 인용을 신뢰할 수 없게 만든다",
    ),
    Mutation(
        "failed-sources-includes-successes",
        CORE / "models.py",
        "if receipt.outcome is SourceOutcome.FAILED",
        "if receipt.outcome >= SourceOutcome.FAILED",
        "성공한 원천을 실패 목록에 섞어 실패의 의미를 지운다",
    ),
    Mutation(
        "bbox-drops-western-edge",
        CORE / "models.py",
        "self.min_lon <= point.lon <= self.max_lon",
        "self.min_lon is not point.lon <= self.max_lon",
        "경북 서쪽 밖의 좌표를 경북으로 판정한다",
    ),
    Mutation(
        "bbox-drops-southern-edge",
        CORE / "models.py",
        "self.min_lat <= point.lat <= self.max_lat",
        "self.min_lat is not point.lat <= self.max_lat",
        "경북 남쪽 밖의 좌표를 경북으로 판정한다",
    ),
    Mutation(
        "mungyeong-reads-sangju-rainfall",
        CORE / "regions.py",
        '"47280": 273,',
        '"47280": 137,',
        "문경 강우를 20km 떨어진 상주 관측값으로 읽는다",
    ),
    Mutation(
        "yeongdeok-reads-pohang-rainfall",
        CORE / "regions.py",
        '"47770": 277,',
        '"47770": 138,',
        "영덕 강우를 42km 떨어진 포항 관측값으로 읽는다",
    ),
    Mutation(
        "station-number-off-by-one",
        CORE / "regions.py",
        '"47190": 279,',
        '"47190": 278,',
        "구미 대신 의성 관측값을 읽는다 — 번호가 실재해 조회는 성공한다",
    ),
    Mutation(
        "distant-station-loses-its-caveat",
        CORE / "regions.py",
        "if self.is_local:\n            return None",
        "if not self.is_local:\n            return None",
        "26km 떨어진 관측값을 이 지역 실측처럼 제시한다",
    ),
    # ── 부재 판정 경로. 이 저장소의 중심 불변식이 여기 걸려 있다 ────
    Mutation(
        "failure-counts-as-trustworthy-absence",
        CORE / "models.py",
        "return self is SourceOutcome.CONFIRMED_EMPTY",
        "return self >= SourceOutcome.CONFIRMED_EMPTY",
        "조회 실패를 '해당 없음'으로 분류해 장애를 위험 없음으로 만든다",
    ),
    Mutation(
        "absence-property-becomes-method",
        CORE / "models.py",
        "    @property\n    def is_trustworthy_absence",
        "    def is_trustworthy_absence",
        "속성이 메서드가 되어 항상 참으로 읽히고 모든 실패가 부재로 통과한다",
    ),
    Mutation(
        "freshness-property-becomes-method",
        CORE / "models.py",
        "    @property\n    def is_usable_for_decision",
        "    def is_usable_for_decision",
        "속성이 메서드가 되어 항상 참으로 읽히고 오래된 값이 판단 근거가 된다",
    ),
    Mutation(
        "timestamp-disclosure-becomes-method",
        CORE / "models.py",
        "    @property\n    def needs_timestamp_disclosure",
        "    def needs_timestamp_disclosure",
        "시점 표시 필요 여부가 항상 참이 되어 경고의 의미가 사라진다",
    ),
    Mutation(
        "complete-answer-marked-incomplete",
        CORE / "models.py",
        "receipt.outcome is not SourceOutcome.FAILED for receipt in self.receipts",
        "receipt.outcome < SourceOutcome.FAILED for receipt in self.receipts",
        "성공한 조회를 실패로 세어 멀쩡한 답을 계속 보류시킨다",
    ),
    Mutation(
        "absence-needs-only-one-condition",
        CORE / "models.py",
        "return self.is_complete and all(",
        "return self.is_complete or all(",
        "장애가 있어도 영수증만 깨끗하면 부재를 확인된 것으로 만든다",
    ),
    Mutation(
        "freshness-boundary-shifts",
        CORE / "models.py",
        "return self.age_seconds <= self.max_decision_age_seconds",
        "return self.age_seconds < self.max_decision_age_seconds",
        "판단 가능 경계의 값을 사용 불가로 바꿔 신선한 자료를 버린다",
    ),
    Mutation(
        "frozen-base-becomes-mutable",
        CORE / "models.py",
        'model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)',
        'model_config = ConfigDict(frozen=False, extra="forbid", populate_by_name=True)',
        "모든 값 객체가 변경 가능해져 검증을 통과한 뒤 값을 바꿔치기할 수 있다",
    ),
    Mutation(
        "degenerate-bbox-accepted",
        CORE / "models.py",
        "if self.min_lon >= self.max_lon or self.min_lat >= self.max_lat:",
        "if self.min_lon > self.max_lon or self.min_lat > self.max_lat:",
        "넓이가 0인 영역을 허용해 어떤 좌표도 포함하지 못하는 상자를 만든다",
    ),
    Mutation(
        "real-data-tagged-as-non-real",
        CORE / "models.py",
        "if self.mode is not DataMode.REAL:",
        "if self.mode >= DataMode.REAL:",
        "실데이터에 모드 표시를 붙여 훈련 데이터와 구별을 흐린다",
    ),
    Mutation(
        "credential-leaks-in-endpoint",
        CONNECTORS / "base.py",
        "            endpoint=self._redact_endpoint(response.endpoint),",
        "            endpoint=response.endpoint,",
        "URL 경로에 든 정부 인증키를 모든 레코드의 출처로 노출한다",
    ),
    Mutation(
        "cold-wave-classified-as-heatwave",
        CONNECTORS / "kma.py",
        '        ("한파", HazardDomain.COLD_WAVE),',
        '        ("한파", HazardDomain.HEATWAVE),',
        "한파 특보를 폭염으로 분류해 난방이 필요한 상황에 냉방 시설을 안내한다",
    ),
    Mutation(
        "no-sources-reported-complete",
        API / "service.py",
        "        if not receipts and not records:",
        "        if False:",
        "조회한 원천이 하나도 없는데 확인 완료로 보고한다",
    ),
    Mutation(
        "tool-schema-freezes-hazard-list",
        MCP / "tools.py",
        '    "enum": [item.value for item in HazardDomain if item is not HazardDomain.OTHER],',
        '    "enum": ["heavy_rain", "landslide", "wildfire", "flood", "earthquake", "heatwave"],',
        "도구 스키마가 지원 재난의 절반을 거부해 없는 재난처럼 보이게 한다",
    ),
    Mutation(
        "unreadable-csv-with-filter-reads-as-absence",
        CONNECTORS / "filedata.py",
        '        if rows and not any(pick(row, "name") for row in rows):',
        "        if rows and not records and not region and not hazard_hint:",
        "필터가 있으면 읽지 못한 CSV를 '해당 시군에 대피소 없음'으로 보고한다",
    ),
    Mutation(
        "chemical-province-filter-accepts-outsiders",
        CONNECTORS / "bundled.py",
        "            if not address.startswith((SIDO_NAME_FULL, SIDO_NAME_SHORT)):",
        "            if SIDO_NAME_FULL not in address and SIDO_NAME_SHORT not in address:",
        "'서울특별시 경북대로'를 경북 대피소로 받아들인다",
    ),
    Mutation(
        "aws-all-missing-reported-as-records",
        CONNECTORS / "apihub.py",
        "            if records and all(record.payload.value is None for record in records):",
        "            if False:",
        "관측값이 전부 결측인 지점을 관측 성공으로 보고한다",
    ),
    Mutation(
        "partial-selection-claims-completeness",
        API / "service.py",
        "        skipped = tuple(name for name in playbook if name not in names)",
        "        skipped = ()",
        "원천을 골라 조회하고도 재난 전체를 확인한 것처럼 보고한다",
    ),
    Mutation(
        "receipt-hides-cached-upstream",
        CONNECTORS / "base.py",
        (
            "            self.degradations[0].status\n"
            "            if self.degradations\n"
            "            else self.upstream_status"
        ),
        (
            "            self.degradations[0].status\n"
            "            if self.degradations\n"
            "            else UpstreamStatus.OK"
        ),
        "보존자료로 답하고도 영수증에 upstream=ok로 보고한다",
    ),
    Mutation(
        "cache-ignores-credentials",
        CONNECTORS / "base.py",
        'cache_key = f"{self._cache_scope()}|{url}?{sorted(params.items())}"',
        'cache_key = f"{url}?{sorted(params.items())}"',
        "정상 키로 채운 캐시를 잘못된 키 호출자에게 성공으로 돌려준다",
    ),
    Mutation(
        "rainfall-sentinel-becomes-rain",
        CONNECTORS / "kma.py",
        (
            "    if category in _NON_NEGATIVE_CATEGORIES:\n"
            "        return None if missing_or_impossible(value) else value"
        ),
        "    if category in _NON_NEGATIVE_CATEGORIES:\n        return value",
        "결측 강수(-99)를 실측 강수량으로 보고한다",
    ),
    Mutation(
        "river-sentinel-becomes-low-water",
        CONNECTORS / "hrfco.py",
        "    return None if missing_or_impossible(value) else value",
        "    return value",
        "결측 수위(-99m)를 모든 경보 아래의 안전한 수위로 보고한다",
    ),
    Mutation(
        "missing-fire-index-reads-as-low",
        CONNECTORS / "forest.py",
        "    if index is None or missing_or_impossible(index) or index > 100:",
        "    if index is None:",
        "측정하지 못한 산불위험을 '낮음'으로 보고한다",
    ),
    Mutation(
        "undetectable-hazard-reported-complete",
        API / "service.py",
        "        if not capability.readiness.can_detect:",
        "        if False:",
        "탐지 수단이 없는 재난을 '완전 · 해당 없음'으로 보고한다",
    ),
    Mutation(
        "hazard-limit-caveat-dropped",
        API / "service.py",
        "        if limitation:\n            caveats.insert(0, limitation)",
        "        if not limitation:\n            caveats.insert(0, str(limitation))",
        "partial 재난의 한계를 지워 불완전한 답을 완전한 것처럼 보이게 한다",
    ),
    Mutation(
        "cache-drops-the-absence-verdict",
        CONNECTORS / "base.py",
        "                confirmed_absence=outcome.confirmed_absence,\n",
        "",
        "캐시를 거친 '해당 없음'을 조회 실패로 강등해 확인된 부재를 버린다",
    ),
    Mutation(
        "citation-drops-source-url",
        CORE / "models.py",
        "if self.source_url:",
        "if not self.source_url:",
        "인용문에서 원본 페이지 주소를 지워 사람이 확인할 경로를 없앤다",
    ),
)


def _drop_bytecode_caches() -> None:
    """뮤테이션 사이에 남은 .pyc를 지운다.

    CPython은 소스의 (mtime, size)로 캐시 유효성을 판단한다. 두 뮤테이션이
    바이트 수가 같고 같은 초에 쓰이면 앞 뮤테이션의 .pyc가 그대로 재사용되어,
    **적용하지 않은 코드로 테스트가 돈다**. 그러면 이 스크립트가 "검출"이라고
    보고한 것이 실제로는 검출이 아닐 수 있다. 감사 도구가 조용히 틀리는 것은
    이 저장소가 막으려는 실패 그 자체다.
    """
    for cache in REPO_ROOT.glob("packages/*/src/*/__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def run_suite() -> tuple[bool, str]:
    """테스트를 돌린다. (통과 여부, 마지막 줄)"""
    _drop_bytecode_caches()
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/", "-q", "-x", "--no-header"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    tail = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return result.returncode == 0, tail[-1] if tail else ""


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else ""
    selected = [m for m in MUTATIONS if needle in m.name] if needle else list(MUTATIONS)

    # 중단돼도 소스를 되돌린다.
    #
    # 이 스크립트는 실행 중 소스 파일을 실제로 변형한다. Ctrl-C나 SIGTERM으로
    # 죽으면 `finally`가 돌지 못해 변형이 그대로 남고, 그 상태는 `git status`를
    # 보지 않으면 눈에 띄지 않는다. 실제로 감사를 중단했다가 뒤집힌 조건문이
    # 작업트리에 남은 적이 있다. 저장소를 지키는 도구가 저장소를 깨뜨리면 안 된다.
    pending: dict[Path, str] = {}

    def restore_and_exit(signum: int, _frame: object) -> None:
        for path, original in pending.items():
            path.write_text(original, encoding="utf-8")
        print(f"\n중단됨 — 변형한 파일 {len(pending)}개를 되돌렸습니다.", file=sys.stderr)
        raise SystemExit(130)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, restore_and_exit)

    print(f"뮤테이션 {len(selected)}건을 검사합니다.\n")
    survived: list[Mutation] = []
    unapplied: list[Mutation] = []

    for index, mutation in enumerate(selected, start=1):
        original = mutation.path.read_text(encoding="utf-8")
        if mutation.old not in original:
            unapplied.append(mutation)
            print(f"[{index:2}/{len(selected)}] {mutation.name:36} 적용 불가 (코드가 변경됨)")
            continue
        pending[mutation.path] = original
        try:
            mutation.path.write_text(
                original.replace(mutation.old, mutation.new, 1), encoding="utf-8"
            )
            passed, summary = run_suite()
        finally:
            mutation.path.write_text(original, encoding="utf-8")
            pending.pop(mutation.path, None)

        if passed:
            survived.append(mutation)
            print(f"[{index:2}/{len(selected)}] {mutation.name:36} 생존  ← {summary}")
        else:
            print(f"[{index:2}/{len(selected)}] {mutation.name:36} 검출  ({summary})")

    print()
    killed = len(selected) - len(survived) - len(unapplied)
    print(f"검출 {killed} / 생존 {len(survived)} / 적용불가 {len(unapplied)}")

    if unapplied:
        print(
            "\n적용할 수 없는 뮤테이션 — 대상 코드가 바뀌어 **검사되지 않았습니다**:"
        )
        for mutation in unapplied:
            print(f"  - {mutation.name}")
            print(f"      {mutation.harm}")
        print(
            "\n적용 실패를 통과로 두면 리팩터링 한 번에 뮤테이션이 조용히 "
            "빠지고, 이 스크립트는 계속 '모두 검출'이라고 보고한다. "
            "old 패턴을 현재 코드에 맞게 고치세요."
        )

    if survived:
        print("\n생존한 뮤테이션 — 아무 테스트도 이 실패를 잡지 못합니다:")
        for mutation in survived:
            print(f"  - {mutation.name}")
            print(f"      {mutation.harm}")

    if survived or unapplied:
        return 1

    print("\n모든 뮤테이션이 검출됐습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
