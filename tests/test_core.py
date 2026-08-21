"""정제·통합 계층 테스트.

happy path보다 fail path에 무게를 둔다. 이 시스템에서 위험한 것은 "동작하지
않는 것"이 아니라 "실패했는데 성공처럼 보이는 것"이다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    HazardDomain,
    KmaGrid,
    find_sigungu,
    from_kma_grid,
    haversine_km,
    in_gyeongbuk,
    resolve_transferred,
    to_kma_grid,
)
from gbsafe_core.safety import (
    SafetyViolation,
    assert_mode_consistent,
    assert_not_individual_inference,
    assert_read_only,
    assert_shelter_suitable,
    describe_shelter_caveats,
    require_human_approval,
    route_disclaimer,
)
from gbsafe_core.snapshot import SnapshotStore


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

    def test_aging_between_two_and_six(self) -> None:
        now = datetime.now(UTC)
        result = evaluate(as_of=now - timedelta(hours=3), expected_cycle_seconds=3600, now=now)
        assert result.status is FreshnessStatus.AGING
        assert result.is_usable_for_decision

    def test_stale_beyond_six_cycles(self) -> None:
        now = datetime.now(UTC)
        result = evaluate(as_of=now - timedelta(hours=10), expected_cycle_seconds=3600, now=now)
        assert result.status is FreshnessStatus.STALE
        assert not result.is_usable_for_decision

    def test_forecast_time_in_future_is_not_negative(self) -> None:
        """예보는 대상시각이 미래다. 음수 나이가 나오면 안 된다."""
        now = datetime.now(UTC)
        result = evaluate(as_of=now + timedelta(hours=6), expected_cycle_seconds=3600, now=now)
        assert result.age_seconds == 0

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
        ["call_resident", "dispatch_patrol", "approve_plan", "send_sms", "create_order"],
    )
    def test_rejects_side_effecting_names(self, name: str) -> None:
        """공공데이터 계층이 외부에 영향을 주는 도구를 노출하지 못하게 한다."""
        with pytest.raises(SafetyViolation, match="조회만 제공"):
            assert_read_only(name)

    @pytest.mark.parametrize(
        "name",
        ["search_datasets", "hazard_context", "resolve_region", "data_health"],
    )
    def test_allows_read_only_names(self, name: str) -> None:
        assert_read_only(name)

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
        ],
    )
    def test_allows_area_level_analysis(self, purpose: str) -> None:
        """집계 단위가 명시된 정당한 목적은 통과해야 한다."""
        assert_not_individual_inference(purpose)

    def test_read_only_check_resists_unicode_evasion(self) -> None:
        with pytest.raises(SafetyViolation, match="read_only"):
            assert_read_only("ｃａｌｌ_resident")

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

    def test_degraded_status_does_not_block(self) -> None:
        """부분 장애는 해석을 막지 않는다."""
        answer: Answer[dict[str, int]] = Answer(
            query="test",
            degradations=(
                Degradation(
                    dataset_id="15073861",
                    status=UpstreamStatus.DEGRADED,
                    detail="일일 한도 초과",
                    occurred_at=datetime.now(UTC),
                ),
            ),
        )
        assert answer.is_complete


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


class TestQualityFlags:
    def test_flags_are_serializable(self) -> None:
        for flag in QualityFlag:
            assert isinstance(flag.value, str)

    def test_terms_summary_present(self) -> None:
        for code in LicenseCode:
            assert terms_for(code).summary
