# 아키텍처

## 계층

```
원천 API·파일          기관별 형식, 서로 다른 지역 식별자, 오류를 200에 담아 보냄
      │
      ▼
gbsafe-connectors      호출·재시도·캐시·스냅샷 보존, 실패를 Degradation으로 변환
      │
      ▼
gbsafe-core            정규화된 엔티티 + 출처 + 신선도 + 품질플래그 = Record
      │
      ├──────────────┐
      ▼              ▼
gbsafe-api      gbsafe-mcp      같은 서비스 계층을 공유해 답이 갈라지지 않는다
      │              │
      ▼              ▼
외부 행정시스템    AI 에이전트
```

`gbsafe-cli`는 서비스 계층을 직접 쓰고 두 서버를 기동한다.

## 왜 이렇게 나눴는가

**서비스 계층이 하나인 이유.** API와 MCP가 각자 로직을 가지면 같은 질문에 다른 답을 준다. `gbsafe_api.service.SafeDataService`에만 로직이 있고 두 표면은 표현만 담당한다. 테스트가 두 표면의 일치를 검증한다.

**커넥터가 예외를 던지지 않는 이유.** 재난 상황에서 한 원천이 죽었다고 전체 조회가 실패하면 쓸 수 없다. 각 커넥터는 `FetchOutcome`을 반환하고, 실패는 `Degradation`이 되어 나머지 결과와 함께 전달된다.

**출처가 값에 붙어 다니는 이유.** 나중에 붙이려면 어디선가 빠진다. `Record`는 `payload`와 `provenance`를 함께 갖는 하나의 타입이고, 커넥터가 값을 만들 때 이미 출처가 결정된다.

## 핵심 타입

```python
Record[T]
├── payload: T                 # 정규화된 값 (Observation, HazardAlert, Shelter ...)
├── provenance: Provenance     # 어디서·언제·라이선스·스냅샷·모드
├── freshness: Freshness       # 나이와 판단 가능 여부
├── quality_flags: tuple       # 검증으로 확인된 결함
└── notes: tuple

Answer[T]
├── records: tuple[Record[T]]
├── degradations: tuple[Degradation]   # 조회하지 못한 원천과 사유
└── caveats: tuple[str]                # 해석 시 주의사항
```

`Answer.is_complete`가 `False`면 `records`가 전부가 아니다. 이 구별이 이 시스템의 핵심 안전장치다.

## 시간 처리

세 가지 시각을 구별한다.

| 필드 | 의미 |
| --- | --- |
| `retrieved_at` | GB SafeData가 조회한 시각 |
| `observed_at` | 원천의 관측 시각 |
| `published_at` | 원천의 발표 시각 |

신선도는 `observed_at` > `published_at` > `retrieved_at` 순으로 판단한다. 조회 시각을 관측 시각처럼 쓰면 6시간 전 데이터가 방금 것으로 보인다.

모든 datetime은 시간대를 갖는다. naive datetime은 스키마 단계에서 거부된다 — KST와 UTC를 섞으면 9시간 오차가 조용히 생긴다.

## 신선도 판정

절대 임계값을 쓰지 않는다. 5분 주기 레이더와 분기 갱신 통계에 같은 기준을 적용하는 것은 의미가 없다.

| 상태 | 기준 |
| --- | --- |
| `fresh` | 갱신주기 2배 이내 |
| `aging` | 2~6배 |
| `stale` | 6배 초과 |
| `unknown` | 원천이 관측시각을 주지 않음 |

갱신주기는 **커넥터가 선언한 값이 카탈로그보다 우선한다.** 포털 메타데이터의 `update_cycle`이 대부분 비어 있어서, 그것만 쓰면 시간당 갱신 데이터가 영원히 `fresh`로 남는다.

`unknown`은 `fresh`가 아니다. '모른다'와 '최신이다'는 다르다.

## 캐시와 스냅샷

두 가지가 다른 목적을 갖는다.

**캐시**(프로세스 내 TTL)는 개발계정 호출 한도를 지킨다. TTL은 갱신주기에서 계산하고 60초~1시간으로 제한한다.

**스냅샷**(내용 주소 파일)은 사후 검증을 위해 원본을 보존한다. SHA-256 기반이라 같은 응답을 여러 번 받아도 한 번만 저장되고, 폴링 중복이 파일을 늘리지 않는다. 이 멱등성이 `Provenance.snapshot_id`의 안정성을 보장한다.

원천 장애 시 마지막 정상 스냅샷으로 폴백하되 `UpstreamStatus.CACHED`로 그 사실을 알린다. `GBSAFE_OFFLINE=true`면 스냅샷만 쓴다.

인증키는 스냅샷 메타데이터에서 제거된다.

## 라이선스 게이트

`licensing.require(license, operation, subject)`가 파생 연산 진입부에서 호출된다. KOGL-3·4는 `DERIVE`를 허용하지 않으므로 재투영·클리핑·조인·파생라벨 생성이 예외로 막힌다.

`UNKNOWN`은 조회만 허용한다. 관대하게 추정해 통과시키는 것보다 막고 확인하게 하는 편이 안전하다.

`redistribution_contamination()`이 여러 라이선스를 병합해 배포할 때의 share-alike 전염을 경고한다.

## 안전 경계

`gbsafe_core.safety`가 예외로 강제한다.

| 함수 | 막는 것 |
| --- | --- |
| `assert_read_only` | 부작용을 암시하는 MCP 도구 등록 |
| `assert_not_individual_inference` | 집계 통계로 개인 속성 추정 |
| `require_human_approval` | AI의 대피명령 자동 결정 |
| `assert_citable` | 출처·신선도 없는 값을 판단 근거로 사용 |
| `assert_mode_consistent` | 실데이터와 훈련데이터 혼합 |
| `assert_shelter_suitable` | 재난유형이 맞지 않는 대피소 배정 |

MCP 서버는 기동 시점에 `validated_tools()`로 모든 도구 이름을 검사한다. 도구를 잘못 추가하면 서버가 기동하지 않는다.

## 지역 식별자

기관마다 다르다.

| 원천 | 식별자 |
| --- | --- |
| 기상청 단기예보 | 격자 nx/ny (Lambert Conformal Conic 변환) |
| 기상청 특보 | 발표관서 번호 (경북 143·136·138) |
| 기상청 ASOS | 지점번호 |
| 대부분 | 시군구 코드 5자리 |

격자 변환식은 공개 페이지에 없어 `regions.py`에 직접 구현했다. 공개된 기준 격자(서울 60,127 / 부산 98,76 / 제주 53,38)로 검증한다.

**잘못된 격자를 넣어도 API는 200과 그럴듯한 값을 반환한다.** 다른 도시의 날씨를 읽으면서 성공한 것처럼 보이므로 변환 정확성이 조용한 오류의 주요 원인이다.

## 카탈로그

`jxkr2026-datasets`를 원천으로 읽는다. 사본을 박아두면 즉시 낡기 때문이다.

찾는 순서: `GBSAFE_CATALOG_DIR` → 나란한 `../jxkr2026-datasets/catalog` → 동봉 폴백.

`verified-overrides.json`이 포털 표기를 덮어쓴다. 실제 다운로드·호출로 확인한 값이고, 포털 메타데이터가 틀린 사례가 반복 확인됐기 때문에 override가 항상 이긴다.

## 확장

**커넥터 추가:** `Connector[PayloadT]`를 상속해 `base_url`, `build_params`, `parse`를 구현하고 `registry.SPECS`에 등록한다. 재시도·캐시·스냅샷·장애 변환은 기반 클래스가 처리한다.

**엔티티 추가:** `domain.py`에 `Frozen` 기반 모델을 만든다. 미확인 상태를 `None`으로 두고, 그 `None`이 '허용'이나 '0'으로 해석되지 않도록 프로퍼티를 설계한다.

**MCP 도구 추가:** `tools.py`의 `TOOLS`에 `ToolDef`를 추가한다. 이름이 부작용을 암시하면 등록이 거부된다.
