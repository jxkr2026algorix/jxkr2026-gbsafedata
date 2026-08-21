# 안전 경계

이 문서는 GB SafeData가 **하지 않는 일**과 그것을 어떻게 강제하는지 설명한다.

재난 시스템에서 위험한 실패는 "동작하지 않는 것"이 아니라 **"실패했는데 성공처럼 보이는 것"**이다. 조회 실패가 '위험 없음'으로, 훈련 데이터가 실제 상황으로, 오래된 값이 현재 값으로 보이는 것이 그것이다.

## 절대 경계

### 1. 공공데이터 인프라는 외부에 영향을 주지 않는다

전화 발신, 대피명령, 주민 상태 변경은 이 계층의 책임이 아니다. 운영 플랫폼이 담당한다.

**강제 방식:**

- 표준 API에 POST·PUT·DELETE·PATCH 라우트가 없다. 테스트가 OpenAPI 스펙을 검사해 GET/HEAD/OPTIONS 외의 메서드가 생기면 실패한다.
- MCP 서버는 기동 시 `validated_tools()`로 모든 도구 이름을 검사한다. 통과하지 못하면 서버가 기동하지 않는다.

판정 방식이 **허용목록 + 변경 동사 거부**다. 금지어 목록만 쓰면 막을 수 없다는 것이
실측으로 확인됐다 — `call`을 막으면 `ring`·`phone`·`telephone`이, 그것마저 막으면
`calls`·`calling`·`c4ll`·`mycall`이 통과하고, 키릴 문자 `cаll`은 `call`과 시각적으로
같지만 다른 문자열이라 어떤 목록도 잡지 못한다.

그래서 두 조건을 함께 요구한다.

1. 이름에 `READ_VERBS`의 조회 동사가 **하나는** 있어야 한다 (`search`, `get`, `list`, `describe`, `verify`, `report`, `health` 등)
2. `MUTATION_VERBS`의 변경 동사가 **하나도** 없어야 한다 (`update`, `write`, `send`, `call`, `dispatch`, `approve` 등)

2번이 필요한 이유는 `updateStatus`처럼 조회 동사(`status`)와 변경 동사(`update`)가
함께 있는 이름이 1번만으로는 통과하기 때문이다.

이름은 비교 전에 NFKC 정규화되고 혼동 문자가 ASCII로 접힌다. 전각 문자, 키릴·그리스
혼동 문자, leetspeak(`c4ll`)이 모두 같은 형태로 모인다.

```python
assert_read_only("call_resident")
# SafetyViolation: [read_only] 'call_resident'에 변경 동작(call)이 있습니다.
# 공공데이터 인프라는 조회만 제공하며 전화·명령·상태변경은 운영 플랫폼의 책임입니다.
```

새 조회 도구를 추가할 때 `READ_VERBS`에 동사를 등록해야 하고, 그 등록이 곧
"이 도구는 부작용이 없다"는 명시적 선언이 된다.

경계가 흐려지면 공공데이터 인프라가 주민에게 직접 명령을 내리는 경로가 생긴다.

### 2. 집계 통계로 개인을 추정하지 않는다

공개 인구통계는 지역의 잠재적 취약성만 보여준다. 고령인구 비율이 높다는 사실이 특정 주민의 보행 곤란을 뜻하지 않는다.

**강제 방식:** `assert_not_individual_inference(purpose)`가 **보호 속성**과 **개인·가구 단위 표현**이 함께 나타나는지 본다. 키워드 하나로 판단하지 않는다.

위험한 것은 어휘가 아니라 질문의 단위다. "마을별 고령인구 비율"과 "누가 혼자 못
걷는지"는 같은 데이터에서 나오지만 후자만 개인 식별로 이어진다. 그래서 단위를
본다.

- 보호 속성: 장애·질병·이동능력뿐 아니라 완곡 표현까지 포함한다 ("스스로 대피",
  "도움이 필요", "struggle to leave", "vulnerable", "health condition")
- 개인·가구 단위: "각자", "가구별", "명단", "누구인지", "per household",
  "the ones who", "residents who"
- 단위를 밝히지 않은 요청은 **거부한다.** 통과시켜 개인 추정에 쓰이는 것보다
  되묻는 편이 안전하다.

```python
service.population_guidance("주민 각자의 장애 여부를 추정")
# {"allowed": false, "reason": "[no_individual_inference] 집계 인구통계로
#  개인 속성(장애)을 추정할 수 없습니다. ..."}
```

개인별 지원 필요 여부는 공개 데이터에 없다. 기관의 주민 명부에서 확인해야 한다.

### 3. AI가 대피명령을 승인하지 않는다

이 시스템은 근거와 후보를 제시한다. 어느 마을을 먼저 대피시킬지, 어느 대피소로 보낼지는 담당 공무원이 검토·승인한다.

**강제 방식:** `require_human_approval(action)`은 항상 예외를 던진다. 승인 경로가 코드에 존재하지 않는다.

MCP 서버 instructions와 Skill이 AI에게 같은 규칙을 전달한다.

### 4. 출처 없는 값을 판단 근거로 쓰지 않는다

**강제 방식:** `Record`가 `provenance`를 필수 필드로 갖는다. 출처 없는 `Record`는 생성할 수 없다.

`assert_citable(record)`가 판단 근거로 쓰기 전 출처와 신선도를 검사한다. 신선도가 `stale`이면 거부한다.

응답 봉투에 `citations`가 항상 들어가고, MCP 응답에는 `how_to_cite` 지침까지 포함된다.

### 5. 조회 실패를 '해당 없음'으로 표현하지 않는다

가장 중요한 경계다.

**강제 방식:** `FetchOutcome`과 `Answer`가 `records`와 `degradations`를 분리해 담는다.

| 상태 | 의미 |
| --- | --- |
| `records` 비었고 `degradations` 없음 | 실제로 해당 사항이 없음 |
| `records` 비었고 `degradations` 있음 | **조회 실패 — 위험 없음이 아니다** |

`Answer.is_complete`가 후자를 `False`로 만들고, API 응답의 `complete` 필드와 MCP 응답의 `warnings`로 전달된다.

```json
{
  "records": [],
  "complete": false,
  "degradations": [{
    "dataset_id": "15074800",
    "status": "not_authorized",
    "detail": "HTTP 403 — 「산림청 산사태 예측정보」은 개발단계가 심의승인 대상입니다...",
    "blocks_interpretation": true
  }]
}
```

MCP는 여기에 명시적 경고를 더한다.

```
"일부 원천을 조회하지 못했습니다. 결과가 비어 있어도 '위험 없음'을 의미하지 않습니다"
```

### 6. 실제 데이터와 훈련 데이터를 섞지 않는다

훈련 중 합성 강우량이 실제 대피소 정보와 섞여 실제 상황처럼 보이는 것은 심각한 사고다.

**강제 방식:** `DataMode`(`REAL` / `SNAPSHOT` / `SYNTHETIC`)가 모든 `Provenance`에 있다. `assert_mode_consistent()`가 `REAL`과 `SYNTHETIC` 혼합을 거부한다.

인용 문구에도 표시된다: `... · KOGL-1 · [SYNTHETIC]`

### 7. 재난유형이 맞지 않는 대피소를 배정하지 않는다

지진 옥외대피장소는 호우 대피소가 아니다. 야외이므로 비에 노출되고, 지하 민방위 시설은 침수 시 위험하다.

**강제 방식:** `Shelter.supported_hazards`가 **원본에 명시된 재난유형만** 담는다. 비어 있으면 `serves()`가 모든 재난에 `False`를 반환해 자동 배정 대상이 되지 않는다. 추정해서 채우지 않는다.

`assert_shelter_suitable(shelter, hazard)`가 배정 시점에 검사한다.

### 8. 라이선스가 금지한 연산을 실행하지 않는다

**강제 방식:** `licensing.require()`가 파생 연산 진입부에서 예외를 던진다.

**범위를 정확히 밝힌다.** 이 저장소는 범용 파생 연산 API를 제공하지 않으므로,
`require()`는 주로 `verify_dataset`에서 "이 연산이 허용되는가"를 판정하는 데 쓰인다.
즉 **라이브러리가 제공하는 경로에서는 위반을 막지만, 이용자가 `record.payload`를
꺼내 직접 가공하는 것까지 막지는 못한다.** Python 수준에서 그것을 봉쇄하는 것은
불가능하며, 그렇게 주장하지 않는다.

정규화(기관별 형식 → 공통 스키마)가 법적으로 '변경'에 해당하는지는 원천기관의
해석이 필요한 문제다. 이 저장소는 정규화를 수행하므로, KOGL-3·4 데이터를
가공해야 하는 용도라면 `gbsafe verify`로 확인한 뒤 기관에 문의해야 한다.

```python
require(LicenseCode.KOGL_4, Operation.DERIVE, "홍수위험지도")
# LicenseViolation: 홍수위험지도: 공공누리 제4유형 — 출처표시 + 상업적
# 이용금지 + 변경금지 조건에서 'derive' 연산은 허용되지 않습니다.
```

`derive`에는 재투영, 클리핑, 래스터화, 좌표계 변환, 조인, 파생 지표·학습 라벨 생성, 포맷 변환이 포함된다.

## 미확인을 허용으로 바꾸지 않는다

`None`이 '없음'이나 '허용'으로 해석되면 안 되는 자리들이다.

| 필드 | `None`의 의미 | 잘못된 해석 |
| --- | --- | --- |
| `Shelter.operating` | 확인되지 않음 | 운영중 |
| `Shelter.current_occupancy` | 확인되지 않음 | 0명 |
| `Shelter.wheelchair_accessible` | 확인되지 않음 | 접근 가능 |
| `Shelter.supported_hazards == ()` | 확인되지 않음 | 모든 재난 가능 |
| `Freshness.UNKNOWN` | 시점 모름 | 최신 |
| `LicenseCode.UNKNOWN` | 라이선스 모름 | 제한 없음 |

`Shelter.remaining_capacity`는 현재 인원이 미확인이면 `None`을 반환한다. 0을 반환하면 '만실'로 읽히고, `capacity`를 반환하면 '전부 비어 있음'으로 읽힌다.

## 좌표계 안전

`GeoPoint`가 한반도 범위(위도 33~39, 경도 124~132)를 타입으로 제약한다. EPSG:5179/5186 값을 위경도 칸에 넣는 실수, 위경도를 뒤집는 실수가 스키마 단계에서 거부된다.

파일데이터 정규화에서 범위를 벗어난 좌표는 `COORDINATE_OUT_OF_RANGE` 플래그가 붙고 `location`은 `None`이 된다. 잘못된 위치를 지도에 찍는 것보다 위치를 모른다고 표시하는 편이 안전하다.

## 경로 표현

현장 검증 전에는 '공식 안전경로'가 아니라 **'대피 후보 경로'**다. `route_disclaimer(verified=False)`가 문구를 제공한다.

도로망 데이터에 농로·마을길·보행로가 누락될 수 있고, 도보·휠체어·차량은 필요한 정보가 서로 다르다.

## 인증과 배치

표준 API에 인증이 없다. 공개 데이터만 다루고 개인정보를 취급하지 않기 때문이다.

**다만 공개 배치 시에는 앞단에 게이트웨이를 두어야 한다.** 인증 부재가 문제가 되는 것은 데이터 민감성이 아니라 호출 한도다. 개발계정 한도(AirKorea 일 500건)를 제3자가 소진시킬 수 있다.

CLI의 `serve` 명령이 기동 시 이 사실을 알린다.

## 무엇이 강제되고 무엇이 도우미인가

문서가 '강제된다'고 말하는 것과 실제 호출 경로에 있는 것을 구별한다.

| 함수 | 상태 |
| --- | --- |
| `assert_read_only` | **강제** — MCP 서버 기동 시 모든 도구 이름 검사 |
| `assert_not_individual_inference` | **강제** — `population_guidance` 호출 시 |
| `assert_mode_consistent` | **강제** — 모든 API 응답 생성 시 |
| `describe_shelter_caveats` | **강제** — 대피소 정규화 결과에 주의사항 부착 |
| `licensing.require` | **강제** — 라이브러리 경로 내 파생 연산 판정 |
| `assert_citable` | 도우미 — 호출자가 판단 근거로 쓰기 전 검사용 |
| `assert_shelter_suitable` | 도우미 — 배정 로직(운영 플랫폼)에서 사용 |
| `require_human_approval` | 도우미 — 승인 경로를 만들려는 코드를 막는 표지 |

뒤의 셋은 이 저장소에 배정·승인 로직이 없어서 호출 지점이 없다. 운영 플랫폼이
쓰도록 공개하며, 여기서 '강제'라고 주장하지 않는다.

## 검증

안전 경계는 테스트로 고정되어 있다.

```bash
uv run pytest tests/ -k "Safety or safety" -v
uv run pytest tests/ -q    # 전체
```

경계를 우회하는 코드를 넣으면 테스트가 실패한다.
