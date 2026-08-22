"""정제·통합 계층 테스트.

happy path보다 fail path에 무게를 둔다. 이 시스템에서 위험한 것은 "동작하지
않는 것"이 아니라 "실패했는데 성공처럼 보이는 것"이다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from gbsafe_core.domain import (
    AlertAction,
    HazardAlert,
    Severity,
    Shelter,
    ShelterKind,
    parse_alert_action,
    parse_severity,
)
from gbsafe_core.freshness import evaluate, parse_update_cycle, unknown
from gbsafe_core.licensing import (
    LicenseViolation,
    Operation,
    parse_license,
    permits,
    redistribution_contamination,
    require,
    terms_for,
)
from gbsafe_core.models import (
    Answer,
    BBox,
    DataMode,
    Degradation,
    FreshnessStatus,
    GeoPoint,
    LicenseCode,
    QualityFlag,
    UpstreamStatus,
)
from gbsafe_core.regions import (
    ASOS_STATION_INFO,
    ASOS_STATIONS,
    SIGUNGU,
    HazardDomain,
    KmaGrid,
    asos_station_detail,
    find_sigungu,
    from_kma_grid,
    haversine_km,
    in_gyeongbuk,
    resolve_transferred,
    to_kma_grid,
)
from gbsafe_core.safety import (
    SafetyViolation,
    assert_citable,
    assert_mode_consistent,
    assert_not_individual_inference,
    assert_read_only,
    assert_shelter_suitable,
    describe_shelter_caveats,
    require_human_approval,
    route_disclaimer,
)
from gbsafe_core.snapshot import SnapshotStore
from pydantic import ValidationError


class TestGeoPoint:
    def test_accepts_korean_coordinates(self) -> None:
        point = GeoPoint(lat=36.5866, lon=128.1867)
        assert point.as_geojson()["coordinates"] == [128.1867, 36.5866]

    @pytest.mark.parametrize(
        ("lat", "lon"),
        [
            (0.0, 0.0),
            (36.5866, 1128.1867),
            (445000.0, 1050000.0),  # EPSG:5186 값을 위경도 칸에 넣은 실수
            (128.1867, 36.5866),  # 위경도를 뒤집은 실수
        ],
    )
    def test_rejects_out_of_range(self, lat: float, lon: float) -> None:
        with pytest.raises(ValueError, match=r"less than|greater than"):
            GeoPoint(lat=lat, lon=lon)


class TestBBox:
    def test_rejects_inverted(self) -> None:
        with pytest.raises(ValueError, match="min 값은 max"):
            BBox(min_lon=130.0, min_lat=36.0, max_lon=128.0, max_lat=37.0)

    def test_contains(self) -> None:
        box = BBox(min_lon=127.8, min_lat=35.57, max_lon=131.87, max_lat=37.55)
        assert box.contains(GeoPoint(lat=36.5866, lon=128.1867))
        assert not box.contains(GeoPoint(lat=37.5665, lon=126.978))

    @pytest.mark.parametrize(
        ("lat", "lon", "why"),
        [
            (36.5, 126.9, "서쪽 경계 밖 — 충남"),
            (36.5, 131.95, "동쪽 경계 밖 — 독도 동편"),
            (34.9, 128.5, "남쪽 경계 밖 — 경남"),
            (38.2, 128.5, "북쪽 경계 밖 — 강원"),
        ],
    )
    def test_each_edge_is_checked_independently(
        self, lat: float, lon: float, why: str
    ) -> None:
        """네 변을 각각 벗어나는 점을 따로 본다.

        위도만 어긋난 점으로 검사하면 경도 하한·상한 중 하나가 사라져도
        결과가 같아 통과한다. 경계 판정이 조용히 반쪽이 되는 경로다.
        """
        box = BBox(min_lon=127.8, min_lat=35.57, max_lon=131.87, max_lat=37.55)
        assert not box.contains(GeoPoint(lat=lat, lon=lon)), why

    def test_boundary_points_are_inside(self) -> None:
        """경계값은 포함이다. `<=`가 `<`로 바뀌면 여기서 걸린다."""
        box = BBox(min_lon=127.8, min_lat=35.57, max_lon=131.87, max_lat=37.55)
        assert box.contains(GeoPoint(lat=35.57, lon=127.8))
        assert box.contains(GeoPoint(lat=37.55, lon=131.87))


class TestKmaGrid:
    @pytest.mark.parametrize(
        ("lat", "lon", "nx", "ny"),
        [
            (37.5665, 126.9780, 60, 127),
            (35.1796, 129.0756, 98, 76),
            (33.4996, 126.5312, 53, 38),
        ],
    )
    def test_matches_published_cells(
        self, lat: float, lon: float, nx: int, ny: int
    ) -> None:
        """공개된 기준 격자와 일치해야 한다.

        잘못된 격자를 넣어도 API는 200과 그럴듯한 값을 주므로, 이 검증이
        없으면 다른 도시의 날씨를 읽으면서 성공한 것처럼 보인다.
        """
        grid = to_kma_grid(GeoPoint(lat=lat, lon=lon))
        assert (grid.nx, grid.ny) == (nx, ny)

    def test_roundtrip_stays_within_cell(self) -> None:
        origin = GeoPoint(lat=36.5866, lon=128.1867)
        recovered = from_kma_grid(to_kma_grid(origin))
        assert haversine_km(origin, recovered) < 5.0

    def test_mungyeong_is_not_gumi(self) -> None:
        """이전에 쓰던 (90,95)는 구미 근처로 71km 벗어난다."""
        mungyeong = GeoPoint(lat=36.5866, lon=128.1867)
        wrong = from_kma_grid(KmaGrid(nx=90, ny=95))
        assert haversine_km(mungyeong, wrong) > 50.0


class TestRegions:
    @pytest.mark.parametrize(
        "query", ["문경시", "문경", "47280", "경상북도 문경시", "문경시 산북면"]
    )
    def test_resolves_aliases(self, query: str) -> None:
        found = find_sigungu(query)
        assert found is not None
        assert found.code == "47280"

    @pytest.mark.parametrize("query", ["서울시", "부산", "", "   ", "존재하지않는시"])
    def test_rejects_non_gyeongbuk(self, query: str) -> None:
        assert find_sigungu(query) is None

    def test_flags_transferred_region(self) -> None:
        """군위군은 2023년 대구로 편입됐다. 경북 자료에 남아 있으면 시점 문제다."""
        note = resolve_transferred("47720")
        assert note is not None
        assert "대구" in note

    def test_in_gyeongbuk(self) -> None:
        assert in_gyeongbuk(GeoPoint(lat=36.5866, lon=128.1867))
        assert not in_gyeongbuk(GeoPoint(lat=37.5665, lon=126.978))


class TestLicensing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("제한없음", LicenseCode.UNRESTRICTED),
            ("이용허락범위 제한 없음", LicenseCode.UNRESTRICTED),
            ("공공누리 제1유형", LicenseCode.KOGL_1),
            ("공공누리 제4유형(출처표시-상업적이용금지-변경금지)", LicenseCode.KOGL_4),
            ("KOGL-3", LicenseCode.KOGL_3),
            ("ODbL", LicenseCode.ODBL),
            (None, LicenseCode.UNKNOWN),
            ("", LicenseCode.UNKNOWN),
            ("알 수 없는 표기", LicenseCode.UNKNOWN),
        ],
    )
    def test_parse(self, raw: str | None, expected: LicenseCode) -> None:
        assert parse_license(raw) is expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # 실제 카탈로그에 있는 표기. 공백 때문에 14건이 UNKNOWN으로 새어나갔다.
            ("공공저작물 : 출처표시 (제 1유형)", LicenseCode.KOGL_1),
            ("공공저작물 : 출처표시, 변경금지 (제 3유형)", LicenseCode.KOGL_3),
            (
                "공공저작물 : 출처표시, 상업적 이용금지, 변경금지 (제 4유형)",
                LicenseCode.KOGL_4,
            ),
            ("이용허락범위 제한 없음", LicenseCode.UNRESTRICTED),
            # 표기 변형
            ("제 2 유 형", LicenseCode.KOGL_2),
            ("KOGL - 4", LicenseCode.KOGL_4),
            ("kogl_1", LicenseCode.KOGL_1),
            ("Open Database License", LicenseCode.ODBL),
            # 포털·문서마다 다르게 쓰는 표기
            ("1유형", LicenseCode.KOGL_1),
            ("유형 1", LicenseCode.KOGL_1),
            ("공공누리 1유형", LicenseCode.KOGL_1),
            ("Type 1", LicenseCode.KOGL_1),
            ("KOGL Type 4", LicenseCode.KOGL_4),
        ],
    )
    def test_parse_real_portal_strings(self, raw: str, expected: LicenseCode) -> None:
        """표기 변형으로 UNKNOWN이 되면 허용된 연산이 이유 없이 막힌다."""
        assert parse_license(raw) is expected

    def test_catalog_licence_strings_all_resolve(self) -> None:
        """실제 카탈로그의 라이선스 표기가 전부 판별되어야 한다.

        데이터셋 저장소가 갱신되면서 새 표기가 들어올 수 있으므로 여기서 잡는다.
        라이선스가 아예 명시되지 않은 경우만 UNKNOWN이 허용된다.
        """
        from gbsafe_core.catalog import get_catalog

        unresolved = [
            entry.license_raw
            for entry in get_catalog()
            if entry.license_raw
            and entry.license is LicenseCode.UNKNOWN
            and "표기 없음" not in entry.license_raw
            and entry.license_raw.upper() != "UNKNOWN"
        ]
        assert not unresolved, f"판별하지 못한 라이선스 표기: {unresolved}"

    def test_kogl4_forbids_derivation(self) -> None:
        """홍수위험지도 계열이 전부 4유형이고, 재투영조차 위반이다."""
        assert permits(LicenseCode.KOGL_4, Operation.READ)
        assert not permits(LicenseCode.KOGL_4, Operation.DERIVE)
        with pytest.raises(LicenseViolation, match="변경금지"):
            require(LicenseCode.KOGL_4, Operation.DERIVE, "홍수위험지도")

    def test_kogl3_forbids_derivation_but_allows_commercial(self) -> None:
        assert not permits(LicenseCode.KOGL_3, Operation.DERIVE)
        assert permits(LicenseCode.KOGL_3, Operation.COMMERCIAL)

    def test_unknown_allows_read_only(self) -> None:
        """미확인은 관대하게 추정하지 않고 조회만 허용한다."""
        assert permits(LicenseCode.UNKNOWN, Operation.READ)
        for operation in (Operation.DERIVE, Operation.REDISTRIBUTE, Operation.COMMERCIAL):
            assert not permits(LicenseCode.UNKNOWN, operation)

    def test_share_alike_contamination_detected(self) -> None:
        """OSM과 정부 데이터를 병합해 배포하면 share-alike가 전염된다."""
        warning = redistribution_contamination(
            frozenset({LicenseCode.ODBL, LicenseCode.KOGL_1})
        )
        assert warning is not None
        assert "전염" in warning

    def test_blocked_redistribution_detected(self) -> None:
        warning = redistribution_contamination(frozenset({LicenseCode.UNKNOWN}))
        assert warning is not None
        assert "재배포가 허용되지 않는" in warning

    def test_compatible_set_has_no_warning(self) -> None:
        assert (
            redistribution_contamination(
                frozenset({LicenseCode.KOGL_1, LicenseCode.UNRESTRICTED})
            )
            is None
        )

    def test_empty_set(self) -> None:
        assert redistribution_contamination(frozenset()) is None


class TestFreshness:
    def test_fresh_within_two_cycles(self) -> None:
        now = datetime.now(UTC)
        result = evaluate(as_of=now - timedelta(minutes=30), expected_cycle_seconds=3600, now=now)
        assert result.status is FreshnessStatus.FRESH
        assert result.is_usable_for_decision

    def test_aging_is_not_decision_usable(self) -> None:
        """갱신주기의 3배는 시간당 자료로 3시간 전 값이다.

        대피 판단에서 3시간 전 강우를 현재로 제시하면 안 되므로, AGING은
        시점을 함께 밝혀야 하는 상태로 다룬다.
        """
        now = datetime.now(UTC)
        result = evaluate(as_of=now - timedelta(hours=3), expected_cycle_seconds=3600, now=now)
        assert result.status is FreshnessStatus.AGING
        assert not result.is_usable_for_decision
        assert result.needs_timestamp_disclosure

    def test_max_decision_age_overrides_fresh(self) -> None:
        """등급이 FRESH여도 판단 허용 나이를 넘으면 재확인이 필요하다."""
        now = datetime.now(UTC)
        result = evaluate(
            as_of=now - timedelta(minutes=50),
            expected_cycle_seconds=3600,
            max_decision_age_seconds=1800,
            now=now,
        )
        assert result.status is FreshnessStatus.FRESH
        assert not result.is_usable_for_decision

    def test_stale_beyond_six_cycles(self) -> None:
        now = datetime.now(UTC)
        result = evaluate(as_of=now - timedelta(hours=10), expected_cycle_seconds=3600, now=now)
        assert result.status is FreshnessStatus.STALE
        assert not result.is_usable_for_decision

    def test_small_future_skew_is_tolerated(self) -> None:
        """서버 간 시계 오차 범위의 미래 시각은 정상으로 본다."""
        now = datetime.now(UTC)
        result = evaluate(as_of=now + timedelta(minutes=5), expected_cycle_seconds=3600, now=now)
        assert result.age_seconds == 0
        assert result.status is FreshnessStatus.FRESH

    def test_far_future_timestamp_is_unknown_not_fresh(self) -> None:
        """미래 시각을 0으로 깎으면 2099년 관측이 영원히 fresh로 남는다."""
        result = evaluate(
            as_of=datetime(2099, 1, 1, tzinfo=UTC), expected_cycle_seconds=3600
        )
        assert result.status is FreshnessStatus.UNKNOWN
        assert not result.is_usable_for_decision
        assert "미래" in result.reason

    def test_unknown_is_not_fresh(self) -> None:
        """'모른다'를 '최신이다'로 바꾸지 않는다."""
        result = unknown()
        assert result.status is FreshnessStatus.UNKNOWN
        assert not result.is_usable_for_decision

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValueError, match="시간대"):
            evaluate(as_of=datetime(2026, 8, 22, 2, 0), expected_cycle_seconds=3600)

    @pytest.mark.parametrize(
        ("raw", "seconds"),
        [
            ("1분", 60), ("5분", 300), ("실시간", 300), ("1시간", 3600),
            ("일일", 86400), ("매월", 2_592_000), ("분기", 7_776_000),
            (None, None), ("", None), ("-", None), ("알 수 없음", None),
        ],
    )
    def test_parse_update_cycle(self, raw: str | None, seconds: int | None) -> None:
        assert parse_update_cycle(raw) == seconds


class TestAlertAction:
    @pytest.mark.parametrize(
        ("title", "action"),
        [
            ("호우주의보 발표", AlertAction.ISSUED),
            ("호우주의보 해제", AlertAction.CANCELLED),
            ("호우경보 변경", AlertAction.EXTENDED),
            ("", AlertAction.UNKNOWN),
        ],
    )
    def test_parse(self, title: str, action: AlertAction) -> None:
        assert parse_alert_action(title) is action

    def test_cancelled_alert_is_not_active(self) -> None:
        """해제 통보문을 발효 중으로 읽으면 종료된 위험을 현재 위험으로 표시한다."""
        alert = HazardAlert(
            hazard=HazardDomain.HEAVY_RAIN,
            severity=Severity.WARNING,
            headline="호우경보 해제",
            area_name="대구지방기상청",
            action=AlertAction.CANCELLED,
        )
        assert not alert.is_active
        assert not alert.is_actionable

    def test_issued_warning_is_actionable(self) -> None:
        alert = HazardAlert(
            hazard=HazardDomain.HEAVY_RAIN,
            severity=Severity.WARNING,
            headline="호우경보 발표",
            area_name="대구지방기상청",
            action=AlertAction.ISSUED,
        )
        assert alert.is_actionable

    @pytest.mark.parametrize(
        ("raw", "severity"),
        [
            ("주의보", Severity.ADVISORY),
            ("경보", Severity.WARNING),
            ("예비특보", Severity.INFO),
            ("매우높음", Severity.EMERGENCY),
            (None, Severity.UNKNOWN),
        ],
    )
    def test_parse_severity(self, raw: str | None, severity: Severity) -> None:
        assert parse_severity(raw) is severity


class TestShelter:
    def test_unknown_hazard_serves_nothing(self) -> None:
        """재난유형이 확인되지 않은 대피소는 자동 배정 대상이 아니다."""
        shelter = Shelter(shelter_id="s1", name="마을회관")
        assert not shelter.serves(HazardDomain.HEAVY_RAIN)
        assert not shelter.serves(HazardDomain.EARTHQUAKE)

    def test_earthquake_shelter_rejects_heavy_rain(self) -> None:
        """지진 옥외대피장소를 호우 대피소로 전용하면 위험하다."""
        shelter = Shelter(
            shelter_id="s2",
            name="지진 옥외대피장소",
            kind=ShelterKind.OUTDOOR,
            supported_hazards=(HazardDomain.EARTHQUAKE,),
        )
        assert shelter.serves(HazardDomain.EARTHQUAKE)
        with pytest.raises(SafetyViolation, match="대피시설로 확인되지 않았습니다"):
            assert_shelter_suitable(shelter, HazardDomain.HEAVY_RAIN)

    def test_remaining_capacity_none_when_unverified(self) -> None:
        """현재 인원이 확인되지 않으면 남은 수용량은 0이 아니라 미확인이다."""
        shelter = Shelter(shelter_id="s3", name="체육관", capacity=300)
        assert shelter.remaining_capacity is None
        assert not shelter.occupancy_is_trustworthy

    def test_remaining_capacity_computed(self) -> None:
        shelter = Shelter(
            shelter_id="s4",
            name="체육관",
            capacity=300,
            current_occupancy=120,
            last_verified_at=datetime.now(UTC),
        )
        assert shelter.remaining_capacity == 180
        assert shelter.occupancy_is_trustworthy

    def test_caveats_cover_unknowns(self) -> None:
        caveats = describe_shelter_caveats(Shelter(shelter_id="s5", name="후보시설"))
        joined = " ".join(caveats)
        assert "공식 지정시설이 아닌" in joined
        assert "운영 여부" in joined
        assert "좌표가 없어" in joined


class TestSafety:
    @pytest.mark.parametrize(
        "name",
        [
            # 직접 표현
            "call_resident", "dispatch_patrol", "approve_plan", "send_sms", "create_order",
            "dial_now", "broadcast_message", "order_evacuation", "delete_record",
            # 동의어 — 금지어 목록으로는 끝없이 새어나갔다
            "ring_resident", "phone_resident", "page_patrol", "alert_all",
            "telephone", "trigger_call", "invoke_action", "execute_plan",
            "mutate_state", "patch_record", "remove_entry", "insert_row",
            # 굴절형
            "calls", "calling", "dispatched", "notifying", "approvals",
            # leetspeak
            "c4ll_resident", "s3nd_alert", "d1al",
            # 접사
            "mycall", "thecall", "call2", "xcall", "do_call",
            # camelCase / 전각
            "callAmbulance", "SendAlert", "ｃａｌｌAmbulance", "evacuateVillage",
            # 키릴 혼동문자 — 시각적으로 라틴 문자와 같다
            "cаll_resident", "sеnd_alert", "nоtify_all",
            # 조회 동사와 변경 동사가 섞인 경우
            "updateStatus", "write_status", "getAndDeleteRecord", "list_and_notify",
            "search_then_call", "fetch_and_update",
        ],
    )
    def test_rejects_side_effecting_names(self, name: str) -> None:
        """공공데이터 계층이 외부에 영향을 주는 도구를 노출하지 못하게 한다.

        허용목록(조회 동사 필수) + 거부목록(변경 동사 금지)을 함께 쓴다.
        금지어만으로는 동의어·굴절형·혼동문자에 계속 뚫린다.
        """
        with pytest.raises(SafetyViolation, match="read_only"):
            assert_read_only(name)

    @pytest.mark.parametrize(
        "name",
        [
            "search_datasets", "hazard_context", "resolve_region", "data_health",
            "describe_dataset", "verify_dataset", "list_sources", "fetch_source",
            "quality_report", "population_guidance",
            "searchDatasets", "describeDataset", "getShelters", "findRoutes",
            "checkFreshness", "listRegions", "queryCatalog", "readSnapshot",
            "reportQuality", "compareScenarios", "citeSources", "statusCheck",
            "get_dataset_detail", "inspect_quality", "summarize_hazards",
        ],
    )
    def test_allows_read_only_names(self, name: str) -> None:
        """조회 도구가 오탐으로 막히면 서버가 기동하지 않는다."""
        assert_read_only(name)

    def test_every_real_mcp_tool_passes(self) -> None:
        """실제 등록된 도구가 전부 통과해야 한다."""
        from gbsafe_mcp.tools import validated_tools

        assert len(validated_tools()) == 12

    @pytest.mark.parametrize(
        "purpose",
        [
            # 직접 표현
            "주민 각자의 장애 여부를 추정",
            "보행곤란 주민 목록 생성",
            "estimate individual mobility",
            "특정 주민의 질병 확인",
            # 어휘를 바꾼 우회 시도 — 키워드 검사만으로는 전부 통과했다
            "who needs a wheelchair in each household",
            "list residents unable to walk on their own",
            "가구별로 혼자 못 걷는 사람 수를 알려줘",
            "이동이 불편한 주민 명단",
            "누가 도움 없이 대피할 수 없는지 파악",
            "identify frail elderly individuals per address",
            "개별 세대의 요양 필요 여부",
            "bedridden residents by house",
            "each resident's ability to evacuate unaided",
            "세대마다 휠체어 필요 여부",
            "who cannot evacuate without assistance",
            # 단위를 밝히지 않은 모호한 요청
            "산소호흡기 사용자 위치",
            "치매 환자 현황",
            # 전각 문자로 키워드 검사를 피하려는 시도
            "ｗｈｅｅｌｃｈａｉｒ per household",
            # 보호 속성을 직접 말하지 않는 완곡·간접 표현
            "prioritise the ones who would struggle to leave on their own",
            "이동이 어려운 사람이 누구인지",
            "residents who cannot leave by themselves",
            "스스로 대피하기 어려운 대상자 목록",
            "flag the people who need assistance evacuating",
            "먼저 연락해야 할 이동이 어려운 주민",
            "identify residents who need assistance",
            "find the vulnerable individuals",
            "who cannot evacuate alone",
            "list of residents requiring help",
            "residents needing physical assistance for evacuation",
            "those who need help walking",
            "persons with ambulation difficulties",
            "residents with health conditions",
            "취약계층 명단",
        ],
    )
    def test_blocks_individual_inference(self, purpose: str) -> None:
        """위험한 것은 어휘가 아니라 질문의 단위다.

        "고령인구 비율"과 "누가 혼자 못 걷는지"는 같은 데이터에서 나오지만
        후자만 개인 식별로 이어진다.
        """
        with pytest.raises(SafetyViolation, match="no_individual_inference"):
            assert_not_individual_inference(purpose)

    @pytest.mark.parametrize(
        "purpose",
        [
            "마을별 고령인구 비율로 취약성 순위를 매긴다",
            "예상 대피 인원 규모 추정",
            "지역 단위 취약성 지수 계산",
            "시군별 인구 분포 확인",
            "마을별 장애인 등록 비율 통계",
            "elderly ratio by village",
            "total population for evacuation planning",
            "읍면동 단위 취약성 지수",
            "aggregate distribution of elderly by region",
        ],
    )
    def test_allows_area_level_analysis(self, purpose: str) -> None:
        """집계 단위가 명시된 정당한 목적은 통과해야 한다."""
        assert_not_individual_inference(purpose)

    @pytest.mark.parametrize(
        "name",
        [
            "ｃａｌｌ_resident",
            # camelCase는 snake_case만 나누면 한 토큰이 되어 검사를 통과했다
            "callAmbulance",
            "SendAlert",
            "dispatchPatrol",
            "approvePlan",
            "evacuateVillage",
            "updateStatus",
            "ｃａｌｌAmbulance",
            "ＳｅｎｄAlert",
        ],
    )
    def test_read_only_check_resists_evasion(self, name: str) -> None:
        with pytest.raises(SafetyViolation, match="read_only"):
            assert_read_only(name)

    @pytest.mark.parametrize(
        "name",
        [
            "searchDatasets",
            "hazardContext",
            "resolveRegion",
            "dataHealth",
            "listSources",
            "qualityReport",
            "populationGuidance",
            "describeDataset",
            "verifyDataset",
            "fetchSource",
        ],
    )
    def test_read_only_check_allows_query_names(self, name: str) -> None:
        """조회 도구가 오탐으로 막히면 서버가 기동하지 않는다."""
        assert_read_only(name)

    def test_human_approval_always_raises(self) -> None:
        with pytest.raises(SafetyViolation, match="검토·승인"):
            require_human_approval("대피명령 발령")

    def test_mode_isolation(self, record_factory) -> None:
        """실데이터와 훈련데이터가 한 결과에 섞이면 안 된다."""
        real = record_factory({"v": 1}, mode=DataMode.REAL)
        synthetic = record_factory({"v": 2}, mode=DataMode.SYNTHETIC)
        assert_mode_consistent((real,))
        assert_mode_consistent((synthetic,))
        with pytest.raises(SafetyViolation, match="훈련용 합성 데이터가 같은 결과"):
            assert_mode_consistent((real, synthetic))

    def test_route_disclaimer_distinguishes_verification(self) -> None:
        assert "후보 경로" in route_disclaimer(verified=False)
        assert "현장 확인" in route_disclaimer(verified=True)


class TestAnswer:
    def test_empty_with_degradation_is_incomplete(self) -> None:
        """조회 실패를 '해당 없음'과 구별할 수 있어야 한다."""
        answer: Answer[dict[str, int]] = Answer(
            query="test",
            degradations=(
                Degradation(
                    dataset_id="15074800",
                    status=UpstreamStatus.NOT_AUTHORIZED,
                    detail="심의 대기",
                    occurred_at=datetime.now(UTC),
                ),
            ),
        )
        assert not answer.is_complete
        assert not answer.records

    def test_empty_without_degradation_is_complete(self) -> None:
        answer: Answer[dict[str, int]] = Answer(query="test")
        assert answer.is_complete

    def test_citations_deduplicated(self, record_factory) -> None:
        stamp = datetime.now(UTC)
        records = tuple(
            record_factory({"v": index}, observed_at=stamp) for index in range(3)
        )
        answer = Answer(query="test", records=records)
        assert len(answer.citations) == 1

    @pytest.mark.parametrize(
        "status",
        [UpstreamStatus.DEGRADED, UpstreamStatus.UNAVAILABLE, UpstreamStatus.NOT_AUTHORIZED],
    )
    def test_every_failure_blocks_interpretation(self, status: UpstreamStatus) -> None:
        """어떤 실패도 '해당 없음'으로 읽히면 안 된다.

        한도 초과나 파싱 실패를 '부분 장애'로 통과시키면 응답을 만들지 못한
        원천이 있는데도 complete=true가 된다.
        """
        answer: Answer[dict[str, int]] = Answer(
            query="test",
            degradations=(
                Degradation(
                    dataset_id="15073861",
                    status=status,
                    detail="실패",
                    occurred_at=datetime.now(UTC),
                ),
            ),
        )
        assert not answer.is_complete
        assert answer.degradations[0].blocks_interpretation


class TestRecord:
    def test_fingerprint_is_stable(self, record_factory) -> None:
        stamp = datetime.now(UTC)
        first = record_factory({"value": 1}, observed_at=stamp)
        second = record_factory({"value": 1}, observed_at=stamp)
        assert first.fingerprint() == second.fingerprint()

    def test_fingerprint_changes_with_payload(self, record_factory) -> None:
        stamp = datetime.now(UTC)
        first = record_factory({"value": 1}, observed_at=stamp)
        second = record_factory({"value": 2}, observed_at=stamp)
        assert first.fingerprint() != second.fingerprint()

    def test_citation_text_includes_provenance(self, record_factory) -> None:
        record = record_factory({"value": 1})
        text = record.citation.to_text()
        assert "테스트기관" in text
        assert "KOGL-1" in text

    def test_synthetic_mode_marked_in_citation(self, record_factory) -> None:
        """훈련 데이터가 실제처럼 보이면 안 된다."""
        record = record_factory({"value": 1}, mode=DataMode.SYNTHETIC)
        assert "SYNTHETIC" in record.citation.to_text()

    def test_naive_datetime_rejected_in_provenance(self) -> None:
        from gbsafe_core.models import Provenance

        with pytest.raises(ValueError, match="시간대"):
            Provenance(
                dataset_id="x",
                dataset_name="x",
                provider="x",
                retrieved_at=datetime(2026, 8, 22, 2, 0),
            )


class TestSnapshotStore:
    def test_roundtrip(self, store: SnapshotStore) -> None:
        ref = store.put(dataset_id="15084084", body=b'{"a":1}')
        assert store.get("15084084", ref.snapshot_id) == b'{"a":1}'

    def test_same_content_is_idempotent(self, store: SnapshotStore) -> None:
        """폴링이나 Webhook 재전송이 파일을 늘리면 안 된다."""
        first = store.put(dataset_id="15084084", body=b'{"a":1}')
        second = store.put(dataset_id="15084084", body=b'{"a":1}')
        assert first.snapshot_id == second.snapshot_id
        assert len(store.history("15084084")) == 1

    def test_different_content_accumulates(self, store: SnapshotStore) -> None:
        store.put(dataset_id="15084084", body=b'{"a":1}')
        store.put(dataset_id="15084084", body=b'{"a":2}')
        assert len(store.history("15084084")) == 2

    def test_latest_returns_newest(self, store: SnapshotStore) -> None:
        store.put(dataset_id="d", body=b"first")
        store.put(dataset_id="d", body=b"second")
        latest = store.latest("d")
        assert latest is not None
        assert store.get("d", latest.snapshot_id) in (b"first", b"second")

    def test_secrets_are_redacted(self, store: SnapshotStore) -> None:
        """스냅샷 메타데이터에 인증키가 남으면 안 된다."""
        ref = store.put(
            dataset_id="15084084",
            body=b"{}",
            request_params={"serviceKey": "SECRET123", "nx": "81"},
        )
        meta = ref.path.with_suffix(ref.path.suffix + ".meta.json").read_text()
        assert "SECRET123" not in meta
        assert "<redacted>" in meta
        assert '"nx": "81"' in meta

    def test_missing_snapshot_returns_none(self, store: SnapshotStore) -> None:
        assert store.get("nonexistent", "deadbeef") is None
        assert store.latest("nonexistent") is None
        assert store.history("nonexistent") == ()

    def test_dataset_id_sanitized(self, store: SnapshotStore) -> None:
        """경로 조작을 시도하는 dataset_id가 디렉터리를 벗어나면 안 된다."""
        ref = store.put(dataset_id="../../etc/passwd", body=b"x")
        assert store.root in ref.path.parents


class TestCatalogConfiguration:
    """명시적 설정이 조용히 무시되면 사용자는 다른 데이터를 보게 된다."""

    def test_explicit_missing_dir_raises(self, tmp_path: Path) -> None:
        from gbsafe_core.catalog import Catalog, CatalogUnavailable

        with pytest.raises(CatalogUnavailable, match="읽을 수 없습니다"):
            Catalog.load(tmp_path / "does-not-exist")

    def test_explicit_empty_dir_raises(self, tmp_path: Path) -> None:
        from gbsafe_core.catalog import Catalog, CatalogUnavailable

        with pytest.raises(CatalogUnavailable):
            Catalog.load(tmp_path)

    def test_explicit_corrupt_json_raises(self, tmp_path: Path) -> None:
        from gbsafe_core.catalog import Catalog, CatalogUnavailable

        (tmp_path / "datago-datasets.json").write_text("{ not json", encoding="utf-8")
        with pytest.raises(CatalogUnavailable):
            Catalog.load(tmp_path)

    def test_env_var_misconfiguration_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gbsafe_core.catalog import CATALOG_ENV_VAR, Catalog, CatalogUnavailable

        monkeypatch.setenv(CATALOG_ENV_VAR, str(tmp_path / "nope"))
        with pytest.raises(CatalogUnavailable):
            Catalog.load()

    def test_describe_hides_absolute_path(self) -> None:
        """이 문자열은 원격 AI 클라이언트로 나간다. 서버 경로를 노출하면 안 된다."""
        from gbsafe_core.catalog import get_catalog

        source = get_catalog().source
        assert "/" not in source.describe()
        assert str(source.path) not in source.describe()
        # 운영자용 설명에는 경로가 있어야 한다
        assert str(source.path) in source.describe_local()

    def test_summary_hides_absolute_path(self) -> None:
        from gbsafe_core.catalog import get_catalog

        summary = get_catalog().summary()
        assert "path" not in summary

    def test_auto_discovery_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from gbsafe_core.catalog import CATALOG_ENV_VAR, Catalog

        monkeypatch.delenv(CATALOG_ENV_VAR, raising=False)
        catalog = Catalog.load()
        assert len(catalog) > 0


class TestQualityFlags:
    def test_flags_are_serializable(self) -> None:
        for flag in QualityFlag:
            assert isinstance(flag.value, str)

    def test_terms_summary_present(self) -> None:
        for code in LicenseCode:
            assert terms_for(code).summary


class TestSourceReceipts:
    """빈 결과의 의미를 추정하지 않고 증명한다."""

    def test_records_outcome_requires_records(self) -> None:
        from gbsafe_core.models import SourceOutcome, SourceReceipt

        with pytest.raises(ValueError, match="레코드가 하나 이상"):
            SourceReceipt(
                connector="weather_now",
                dataset_id="15084084",
                outcome=SourceOutcome.RECORDS,
                record_count=0,
                checked_at=datetime.now(UTC),
                upstream_status=UpstreamStatus.OK,
            )

    def test_empty_outcome_forbids_records(self) -> None:
        from gbsafe_core.models import SourceOutcome, SourceReceipt

        with pytest.raises(ValueError, match="레코드를 가질 수 없습니다"):
            SourceReceipt(
                connector="weather_now",
                dataset_id="15084084",
                outcome=SourceOutcome.CONFIRMED_EMPTY,
                record_count=3,
                checked_at=datetime.now(UTC),
                upstream_status=UpstreamStatus.OK,
            )

    def test_failed_receipt_makes_answer_incomplete(self) -> None:
        """실패한 원천이 있으면 degradation이 없어도 불완전하다."""
        from gbsafe_core.models import SourceOutcome, SourceReceipt

        answer: Answer[dict[str, int]] = Answer(
            query="t",
            receipts=(
                SourceReceipt(
                    connector="landslide_forecast",
                    dataset_id="15074800",
                    outcome=SourceOutcome.FAILED,
                    record_count=0,
                    checked_at=datetime.now(UTC),
                    upstream_status=UpstreamStatus.NOT_AUTHORIZED,
                ),
            ),
        )
        assert not answer.is_complete
        assert not answer.absence_is_confirmed
        assert answer.failed_sources() == ("landslide_forecast",)

    def test_confirmed_empty_allows_absence(self) -> None:
        """원천이 '해당 없음'을 명시하면 빈 결과를 그렇게 읽어도 된다."""
        from gbsafe_core.models import SourceOutcome, SourceReceipt

        answer: Answer[dict[str, int]] = Answer(
            query="t",
            receipts=(
                SourceReceipt(
                    connector="weather_warning",
                    dataset_id="15000415",
                    outcome=SourceOutcome.CONFIRMED_EMPTY,
                    record_count=0,
                    checked_at=datetime.now(UTC),
                    upstream_status=UpstreamStatus.OK,
                ),
            ),
        )
        assert answer.is_complete
        assert answer.absence_is_confirmed

    def test_no_receipts_means_absence_unconfirmed(self) -> None:
        """아무 원천도 조회하지 않았으면 확인된 것이 없다."""
        answer: Answer[dict[str, int]] = Answer(query="t")
        assert not answer.absence_is_confirmed


class TestSnapshotDurability:
    """부분 기록된 스냅샷이 '마지막 정상자료'로 제시되면 안 된다."""

    def test_corrupted_blob_is_rejected(self, store: SnapshotStore) -> None:
        ref = store.put(dataset_id="15084084", body=b'{"a":1}')
        ref.path.write_bytes(b'{"a":1} TRUNCATED')
        assert store.get("15084084", ref.snapshot_id) is None

    def test_intact_blob_is_returned(self, store: SnapshotStore) -> None:
        ref = store.put(dataset_id="15084084", body=b'{"a":1}')
        assert store.get("15084084", ref.snapshot_id) == b'{"a":1}'

    def test_no_temporary_files_left(self, store: SnapshotStore) -> None:
        store.put(dataset_id="15084084", body=b"x" * 5000)
        leftovers = [
            path.name
            for path in store.root.rglob("*")
            if path.name.startswith(".") and path.name.endswith(".tmp")
        ]
        assert not leftovers


class TestIndividualGrainBranch:
    """보호 속성 + 개인 단위 표현이 함께 오는 경로를 직접 검사한다.

    `grains` 분기를 무력화해도 통과하는 구멍이 있었다. 단위가 명시되지 않은
    요청은 다른 분기가 막아주기 때문에, 개인 단위가 **명시된** 요청으로만
    이 분기를 검증할 수 있다.
    """

    @pytest.mark.parametrize(
        ("purpose", "grain"),
        [
            ("가구별 장애인 비율 통계", "가구별"),
            ("세대별 이동 불편 인원 집계", "세대별"),
            ("각 주민의 보행곤란 여부 분포", "각 주민"),
            ("per household disability ratio statistics", "per household"),
            ("each resident's mobility index aggregate", "each resident"),
            ("주민별 요양 필요 비율", "주민별"),
        ],
    )
    def test_individual_grain_blocked_even_with_aggregate_words(
        self, purpose: str, grain: str
    ) -> None:
        """'비율'·'통계'가 있어도 개인·가구 단위가 명시되면 막는다.

        가구별 집계는 가구 식별로 이어지므로 지역 집계와 다르다.
        """
        with pytest.raises(SafetyViolation, match="no_individual_inference") as caught:
            assert_not_individual_inference(purpose)
        assert grain in str(caught.value), f"사유에 단위({grain})가 없습니다"

    @pytest.mark.parametrize(
        "purpose",
        [
            "마을별 장애인 등록 비율 통계",
            "읍면동 단위 고령인구 비율",
            "시군별 이동 불편 인구 집계",
            "aggregate disability ratio by region",
        ],
    )
    def test_area_grain_still_allowed(self, purpose: str) -> None:
        """지역 단위 집계는 정당한 용도다."""
        assert_not_individual_inference(purpose)


class TestCoordinateRangeIsEnforced:
    """좌표계 혼동은 지도에 엉뚱한 위치를 찍는다."""

    @pytest.mark.parametrize(
        ("lat", "lon", "note"),
        [
            (445123.5, 1050987.2, "EPSG:5186 값"),
            (200000.0, 500000.0, "EPSG:5179 값"),
            (0.0, 0.0, "null island"),
            (128.1867, 36.5866, "위경도 뒤바뀜"),
            (91.0, 128.0, "위도 범위 초과"),
            (36.5, 200.0, "경도 범위 초과"),
            (-36.5, 128.0, "남반구"),
        ],
    )
    def test_out_of_range_rejected(self, lat: float, lon: float, note: str) -> None:
        with pytest.raises(ValueError, match=r"less than|greater than"):
            GeoPoint(lat=lat, lon=lon)

    @pytest.mark.parametrize(
        ("lat", "lon"),
        [
            (36.5866, 128.1867),  # 문경
            (33.1, 126.2),        # 제주 남단
            (38.5, 128.3),        # 강원 북부
        ],
    )
    def test_korean_coordinates_accepted(self, lat: float, lon: float) -> None:
        assert GeoPoint(lat=lat, lon=lon).lat == lat


class TestTransferredRegionLookup:
    """군위군은 2023년 대구로 편입됐다. 경북으로 답하면 시점이 틀린다."""

    @pytest.mark.parametrize("query", ["47720", "군위군", "군위", "경상북도 군위군", "경북 군위군"])
    def test_transfer_is_reported(self, query: str) -> None:
        note = resolve_transferred(query)
        assert note is not None, f"{query}: 편입 사실을 알려주지 않습니다"
        assert "대구" in note
        assert "2023" in note

    @pytest.mark.parametrize("query", ["문경시", "안동시", "47280", ""])
    def test_current_regions_are_not_flagged(self, query: str) -> None:
        assert resolve_transferred(query) is None

    def test_transferred_region_is_not_resolvable(self) -> None:
        """편입된 지역을 경북 시군으로 해석하면 안 된다."""
        assert find_sigungu("군위군") is None


class TestAsosStationMapping:
    """시군에 배정한 관측지점이 실제로 가장 가까운 지점인지 기계로 확인한다.

    지점번호가 틀려도 API는 200과 그럴듯한 강우량을 준다. 그래서 손으로 적은
    번호는 틀린 채로 오래 남는다. 실제로 일곱 곳이 최근접 지점이 아니었고
    영덕군은 13.7km 거리에 자기 지점을 두고 42.6km 떨어진 포항을 읽었다.
    """

    def test_every_sigungu_has_a_station(self) -> None:
        missing = [item.name for code, item in SIGUNGU.items() if code not in ASOS_STATIONS]
        assert not missing, f"관측지점이 배정되지 않은 시군: {missing}"

    def test_every_station_exists_in_the_reference(self) -> None:
        """표에 없는 번호를 쓰면 조회 자체가 조용히 실패한다."""
        unknown = {
            code: station
            for code, station in ASOS_STATIONS.items()
            if station not in ASOS_STATION_INFO
        }
        assert not unknown, f"지점표에 없는 번호: {unknown}"

    def test_each_mapping_is_the_nearest_station(self) -> None:
        """더 가까운 지점을 두고 먼 지점을 읽고 있으면 실패한다."""
        wrong: list[str] = []
        for code, station_id in ASOS_STATIONS.items():
            sigungu = SIGUNGU[code]
            nearest, nearest_km = min(
                (
                    (number, haversine_km(sigungu.center, station.location))
                    for number, station in ASOS_STATION_INFO.items()
                ),
                key=lambda pair: pair[1],
            )
            if nearest != station_id:
                current = ASOS_STATION_INFO[station_id]
                wrong.append(
                    f"{sigungu.name}: {station_id}({current.name}) "
                    f"{haversine_km(sigungu.center, current.location):.1f}km 대신 "
                    f"{nearest}({ASOS_STATION_INFO[nearest].name}) {nearest_km:.1f}km"
                )
        assert not wrong, "최근접 관측지점이 아닙니다:\n" + "\n".join(wrong)

    def test_distant_station_carries_a_caveat(self) -> None:
        """먼 지점을 대신 읽을 때는 그 사실이 드러나야 한다."""
        match = asos_station_detail("47830")  # 고령군 — 합천 19.8km
        assert match is not None
        assert not match.is_local
        assert match.caveat is not None
        assert "합천" in match.caveat
        assert "20km" in match.caveat or "19km" in match.caveat

    def test_local_station_has_no_caveat(self) -> None:
        match = asos_station_detail("47250")  # 상주시 — 상주 0.3km
        assert match is not None
        assert match.is_local
        assert match.caveat is None

    def test_mungyeong_uses_its_own_station(self) -> None:
        """문경은 1971년부터 자기 관측지점(273)이 있다.

        이전에는 20km 떨어진 상주(137)를 읽었다. 주 시나리오 지역이라
        여기서 틀리면 전체 판단이 다른 지역의 비를 근거로 삼는다.
        """
        match = asos_station_detail("47280")
        assert match is not None
        assert match.station.station_id == 273
        assert match.distance_km < 10.0

    def test_reference_holds_only_operating_fixed_stations(self) -> None:
        """이동관측차량과 레이더는 시간자료 조회 대상이 아니다.

        대구_차량(193)은 경산시에서 대구(143)와 같은 9.9km라, 표에 남아 있으면
        정렬 순서만 바뀌어도 경산시가 관측차량을 가리킬 수 있었다.
        """
        for station in ASOS_STATION_INFO.values():
            assert "_차량" not in station.name, station
            assert "(레)" not in station.name, station


class TestCitationGate:
    """`assert_citable`은 출처 없는 값과 오래된 값을 판단 근거에서 막는다."""

    def test_complete_and_fresh_record_passes(self, record_factory) -> None:
        assert_citable(record_factory({"rain_mm": 12.0}))

    @pytest.mark.parametrize("blank", ["dataset_id", "provider"])
    def test_missing_origin_is_rejected(self, blank: str, record_factory) -> None:
        """데이터셋 id와 기관은 **각각** 필수다.

        한쪽만 검사하면 나머지 한쪽이 비어도 인용문이 만들어진다.
        """
        record = record_factory({"rain_mm": 12.0})
        provenance = record.provenance.model_copy(update={blank: ""})
        stripped = record.model_copy(update={"provenance": provenance})
        with pytest.raises(SafetyViolation, match="출처"):
            assert_citable(stripped)

    def test_stale_record_is_rejected(self, record_factory) -> None:
        """갱신주기를 한참 넘긴 값을 현재 상황처럼 인용하면 안 된다."""
        old = datetime.now(UTC) - timedelta(days=30)
        record = record_factory({"rain_mm": 12.0}, observed_at=old, cycle=3600)
        assert not record.freshness.is_usable_for_decision
        with pytest.raises(SafetyViolation, match="신선도"):
            assert_citable(record)


class TestShelterCaveatsAreIndividuallyReported:
    """주의사항은 각각 독립적으로 붙어야 한다.

    한 항목이 사라져도 다른 항목이 남아 있으면 목록은 여전히 비어 있지 않다.
    그래서 '비어 있지 않다'만 보는 검사로는 누락을 잡을 수 없다.
    """

    def _complete_shelter(self, **overrides: object) -> Shelter:
        base: dict[str, object] = {
            "shelter_id": "s-complete",
            "name": "완비 대피소",
            "designated": True,
            "operating": True,
            "capacity": 300,
            "current_occupancy": 100,
            "last_verified_at": datetime.now(UTC),
            "location": GeoPoint(lat=36.5866, lon=128.1867),
            "wheelchair_accessible": True,
            "supported_hazards": (HazardDomain.HEAVY_RAIN,),
        }
        base.update(overrides)
        return Shelter(**base)  # type: ignore[arg-type]

    def test_complete_shelter_has_no_caveats(self) -> None:
        assert describe_shelter_caveats(self._complete_shelter()) == ()

    def test_untrustworthy_occupancy_is_called_out(self) -> None:
        """확인되지 않은 수용인원을 실시간 값처럼 쓰면 만원인 곳으로 보낸다."""
        shelter = self._complete_shelter(last_verified_at=None)
        caveats = describe_shelter_caveats(shelter)
        assert any("수용인원" in item for item in caveats), caveats

    def test_unknown_hazard_type_is_called_out(self) -> None:
        shelter = self._complete_shelter(supported_hazards=())
        caveats = describe_shelter_caveats(shelter)
        assert any("재난유형" in item for item in caveats), caveats

    def test_missing_wheelchair_access_is_called_out(self) -> None:
        shelter = self._complete_shelter(wheelchair_accessible=None)
        caveats = describe_shelter_caveats(shelter)
        assert any("장애인" in item for item in caveats), caveats


class TestReadOnlyGuardResistsInvisibleCharacters:
    """보이지 않는 문자를 끼워 변경 동사를 숨기는 우회를 막는다."""

    @pytest.mark.parametrize(
        ("filler", "label"),
        [
            ("\u00ad", "soft hyphen"),
            ("\u200b", "zero width space"),
            ("\u200c", "zero width non-joiner"),
            ("\u200d", "zero width joiner"),
            ("\u2060", "word joiner"),
            ("\ufeff", "zero width no-break space"),
            ("\u202e", "right-to-left override"),
        ],
    )
    def test_hidden_character_does_not_smuggle_a_write_verb(
        self, filler: str, label: str
    ) -> None:
        """제로폭 문자만 지우면 나머지 서식 문자로 같은 우회가 된다.

        `get_ca<soft hyphen>ll_resident`는 서식 문자를 남겨두면 `ca`+`ll`로
        쪼개져 `call`이 사라지고, 앞의 `get` 때문에 조회 도구로 통과한다.
        """
        with pytest.raises(SafetyViolation, match="변경 동작"):
            assert_read_only(f"get_ca{filler}ll_resident")


class TestRecordIsImmutable:
    def test_provenance_cannot_be_swapped_after_creation(self, record_factory) -> None:
        """출처가 붙은 뒤 값만 바꿔치기하는 경로가 없어야 한다."""
        record = record_factory({"rain_mm": 12.0})
        with pytest.raises(ValidationError):
            record.payload = {"rain_mm": 0.0}  # type: ignore[misc]
        with pytest.raises(ValidationError):
            record.provenance = record.provenance  # type: ignore[misc]


class TestFailedSourcesNamesOnlyFailures:
    def test_successful_sources_are_not_reported_as_failed(self) -> None:
        """성공한 원천이 실패 목록에 섞이면 실패의 의미가 사라진다."""
        from gbsafe_core.models import SourceOutcome, SourceReceipt

        def receipt(connector: str, outcome: SourceOutcome, count: int) -> SourceReceipt:
            return SourceReceipt(
                connector=connector,
                dataset_id="15084084",
                outcome=outcome,
                record_count=count,
                checked_at=datetime.now(UTC),
                upstream_status=UpstreamStatus.OK,
            )

        answer: Answer[dict[str, int]] = Answer(
            query="t",
            receipts=(
                receipt("weather_now", SourceOutcome.RECORDS, 3),
                receipt("weather_warning", SourceOutcome.CONFIRMED_EMPTY, 0),
                receipt("landslide_forecast", SourceOutcome.FAILED, 0),
            ),
        )
        assert answer.failed_sources() == ("landslide_forecast",)


class TestAbsenceInvariantSurvivesMutation:
    """빈 결과를 '해당 없음'으로 읽어도 되는지 판단하는 경로 전체를 고정한다.

    이 저장소의 중심 불변식이 여기 걸려 있다. 조회 실패가 '위험 없음'으로
    읽히는 순간이 가장 위험한 실패이며, 그 판단은 아래 네 곳을 지난다:
    `SourceOutcome.is_trustworthy_absence` → `Answer.is_complete` →
    `Answer.absence_is_confirmed` → 호출자.
    """

    @staticmethod
    def _receipt(connector: str, outcome, count: int):
        from gbsafe_core.models import SourceReceipt

        return SourceReceipt(
            connector=connector,
            dataset_id="15084084",
            outcome=outcome,
            record_count=count,
            checked_at=datetime.now(UTC),
            upstream_status=UpstreamStatus.OK,
        )

    def test_only_confirmed_empty_is_a_trustworthy_absence(self) -> None:
        """`FAILED`가 신뢰할 수 있는 부재로 분류되면 장애가 '위험 없음'이 된다."""
        from gbsafe_core.models import SourceOutcome

        assert SourceOutcome.CONFIRMED_EMPTY.is_trustworthy_absence
        assert not SourceOutcome.FAILED.is_trustworthy_absence
        assert not SourceOutcome.RECORDS.is_trustworthy_absence

    def test_trustworthy_absence_is_a_value_not_a_method(self) -> None:
        """`@property`가 사라지면 메서드 객체가 항상 참이 되어 전부 통과한다."""
        from gbsafe_core.models import SourceOutcome

        assert SourceOutcome.FAILED.is_trustworthy_absence is False

    def test_freshness_usability_is_a_value_not_a_method(self, record_factory) -> None:
        """같은 이유로 신선도 판정도 bool이어야 한다."""
        old = datetime.now(UTC) - timedelta(days=30)
        record = record_factory({"v": 1}, observed_at=old, cycle=3600)
        assert record.freshness.is_usable_for_decision is False
        assert record.freshness.needs_timestamp_disclosure is True

    def test_a_failed_source_makes_the_answer_incomplete(self) -> None:
        from gbsafe_core.models import SourceOutcome

        answer: Answer[dict[str, int]] = Answer(
            query="t",
            receipts=(
                self._receipt("weather_now", SourceOutcome.RECORDS, 3),
                self._receipt("landslide_forecast", SourceOutcome.FAILED, 0),
            ),
        )
        assert not answer.is_complete
        assert not answer.absence_is_confirmed

    def test_all_successful_sources_leave_the_answer_complete(self) -> None:
        """성공한 원천이 불완전으로 분류되면 멀쩡한 답이 계속 보류된다."""
        from gbsafe_core.models import SourceOutcome

        answer: Answer[dict[str, int]] = Answer(
            query="t",
            receipts=(
                self._receipt("weather_now", SourceOutcome.RECORDS, 3),
                self._receipt("weather_warning", SourceOutcome.CONFIRMED_EMPTY, 0),
            ),
        )
        assert answer.is_complete
        assert answer.absence_is_confirmed

    def test_absence_needs_both_completeness_and_trustworthy_receipts(self) -> None:
        """두 조건은 **함께** 성립해야 한다. 하나만으로 부재를 인정하면 안 된다."""
        from gbsafe_core.models import Degradation, SourceOutcome

        blocked: Answer[dict[str, int]] = Answer(
            query="t",
            receipts=(self._receipt("weather_warning", SourceOutcome.CONFIRMED_EMPTY, 0),),
            degradations=(
                Degradation(
                    dataset_id="15074800",
                    status=UpstreamStatus.NOT_AUTHORIZED,
                    detail="심의 대기",
                    occurred_at=datetime.now(UTC),
                ),
            ),
        )
        assert not blocked.is_complete
        assert not blocked.absence_is_confirmed


class TestFreshnessDecisionBoundary:
    def test_age_exactly_at_the_limit_is_still_usable(self) -> None:
        """경계값은 사용 가능이다. `<=`가 `<`로 바뀌면 여기서 걸린다."""
        from gbsafe_core.models import Freshness, FreshnessStatus

        now = datetime.now(UTC)
        at_limit = Freshness(
            status=FreshnessStatus.FRESH,
            age_seconds=3600,
            expected_cycle_seconds=3600,
            as_of=now,
            evaluated_at=now,
            reason="테스트",
            max_decision_age_seconds=3600,
        )
        assert at_limit.is_usable_for_decision

    def test_one_second_past_the_limit_is_not_usable(self) -> None:
        from gbsafe_core.models import Freshness, FreshnessStatus

        now = datetime.now(UTC)
        past = Freshness(
            status=FreshnessStatus.FRESH,
            age_seconds=3601,
            expected_cycle_seconds=3600,
            as_of=now,
            evaluated_at=now,
            reason="테스트",
            max_decision_age_seconds=3600,
        )
        assert not past.is_usable_for_decision


class TestFrozenBaseIsImmutable:
    def test_frozen_models_reject_assignment(self) -> None:
        """`Frozen`을 상속한 모델 전체가 이 보장에 기대고 있다."""
        box = BBox(min_lon=127.8, min_lat=35.57, max_lon=131.87, max_lat=37.55)
        with pytest.raises(ValidationError):
            box.min_lon = 120.0  # type: ignore[misc]


class TestBBoxRejectsDegenerateBounds:
    @pytest.mark.parametrize(
        ("kwargs", "why"),
        [
            ({"min_lon": 128.0, "max_lon": 128.0}, "경도 폭이 0인 상자"),
            ({"min_lat": 36.0, "max_lat": 36.0}, "위도 높이가 0인 상자"),
        ],
    )
    def test_zero_area_box_is_rejected(self, kwargs: dict[str, float], why: str) -> None:
        """넓이가 0인 상자는 어떤 점도 포함하지 못하면서 통과한다."""
        base = {"min_lon": 127.8, "min_lat": 35.57, "max_lon": 131.87, "max_lat": 37.55}
        with pytest.raises(ValueError, match="min 값은 max"):
            BBox(**{**base, **kwargs})


class TestCitationLabelsOnlyNonRealModes:
    def test_real_data_carries_no_mode_tag(self, record_factory) -> None:
        """실데이터에 `[REAL]`이 붙으면 훈련 표시와 구별이 흐려진다."""
        text = record_factory({"v": 1}).citation.to_text()
        assert "[REAL]" not in text

    def test_synthetic_data_is_tagged(self, record_factory) -> None:
        text = record_factory({"v": 1}, mode=DataMode.SYNTHETIC).citation.to_text()
        assert "[SYNTHETIC]" in text

    def test_source_url_is_included_when_present(self, record_factory) -> None:
        text = record_factory({"v": 1}).citation.to_text()
        assert "https://example.test/dataset" in text
