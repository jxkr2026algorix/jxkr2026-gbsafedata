# 표준 API 명세

`uv run gbsafe serve` 로 기동하고 `/docs`에서 대화형으로 확인할 수 있다.
이 문서는 실제 OpenAPI 스펙에서 생성했다.

## 공통 응답 봉투

데이터를 반환하는 엔드포인트는 모두 같은 구조를 쓴다.

| 필드 | 의미 |
| --- | --- |
| `records[]` | 값 + 출처 + 신선도 |
| `records[].payload` | 정규화된 값 |
| `records[].source` | 기관·데이터셋·라이선스·시각·스냅샷·가공 허용 여부 |
| `records[].freshness` | 나이와 `usable_for_decision` |
| `records[].quality_flags` | 확인된 결함 (좌표 누락, CP949 등) |
| `citations[]` | 그대로 인용할 수 있는 완성된 문구 |
| `receipts[]` | 조회한 원천별 결과 — `records` / `confirmed_empty` / `failed` |
| `degradations[]` | 조회하지 못한 원천과 사유 |
| `complete` | `false`면 일부 원천 실패 |
| `absence_confirmed` | `true`면 빈 결과를 '해당 없음'으로 읽어도 된다 |
| `caveats[]` | 해석 시 주의사항 |
| `modes[]` | `real` / `snapshot` / `synthetic` |

### 빈 결과를 읽는 방법

`records`가 비었을 때 그 의미는 `absence_confirmed`가 결정한다.

| `complete` | `absence_confirmed` | 의미 |
| --- | --- | --- |
| `true` | `true` | 조회 성공, 실제로 해당 사항이 없다 |
| `true` | `false` | 원천이 '해당 없음'을 확인해 주지 않았다 — 위험 없음으로 읽으면 안 된다 |
| `false` | `false` | 일부 원천 조회 실패 — `receipts[].outcome == "failed"` 확인 |

**`records`만 읽고 `absence_confirmed`를 무시하면 조회 실패가 '위험 없음'이 된다.**

## 엔드포인트

### `GET /v1/datasets`

데이터셋 검색

자연어로 데이터셋을 찾는다.

`must_allow=derive`를 주면 변경금지(KOGL 3·4) 데이터가 결과에서 빠집니다.
재투영이나 파생 지표 생성이 필요한 작업에서 미리 걸러내는 데 씁니다.

| 파라미터 | 위치 | 필수 | 설명 |
| --- | --- | --- | --- |
| `q` | query | 아니오 | 검색어 (예: 산사태 대피소) |
| `hazard` | query | 아니오 | 재난 유형 (heavy_rain, landslide, wildfire ...) |
| `dev_ready_only` | query | 아니오 | 개발계정으로 지금 착수 가능한 것만 |
| `usable_only` | query | 아니오 | 빈 등록물·기계판독 불가를 제외 |
| `must_allow` | query | 아니오 | 이 연산이 라이선스상 허용되는 것만 (derive = 재투영·클리핑 등) (`read` / `derive` / `redistribute` / `commercial`) |
| `limit` | query | 아니오 |  |

### `GET /v1/datasets/{dataset_id}`

데이터셋 상세 — 취득 방법·라이선스·결함

| 파라미터 | 위치 | 필수 | 설명 |
| --- | --- | --- | --- |
| `dataset_id` | path | 예 |  |

### `GET /v1/datasets/{dataset_id}/verify`

이 용도로 써도 되는지 판정

라이선스와 심의 상태, 데이터 품질을 함께 보고 판정합니다.

라이선스가 허용해도 개발단계 심의 대기 중이면 `allowed`가 false입니다.
지금 호출할 수 없기 때문입니다.

| 파라미터 | 위치 | 필수 | 설명 |
| --- | --- | --- | --- |
| `dataset_id` | path | 예 |  |
| `operation` | query | 아니오 | 확인할 연산. derive는 재투영·클리핑·조인·파생라벨을 포함 (`read` / `derive` / `redistribute` / `commercial`) |

### `GET /v1/hazard-types`

지원하는 재난 유형

### `GET /v1/hazards/context`

특정 지역의 현재 위험 상황

재난 유형에 맞는 여러 원천을 병렬로 조회해 합칩니다.

일부 원천이 실패해도 나머지를 돌려주고 `complete=false`로 알립니다.

| 파라미터 | 위치 | 필수 | 설명 |
| --- | --- | --- | --- |
| `region` | query | 예 | 경북 시군 (예: 문경시) |
| `hazard` | query | 아니오 | 재난 유형 (heavy_rain, landslide, wildfire, flood) |

### `GET /v1/health`

원천 상태와 인증 정보 현황

어떤 데이터 원천이 지금 쓸 수 있는지, 못 쓰면 왜인지.

키가 없거나 심의 대기 중인 상태는 오류가 아니라 정상적인 운영 상태로
보고됩니다.

### `GET /v1/licenses`

라이선스별 허용 연산

어떤 라이선스에서 무엇이 금지되는지.

변경금지(KOGL 3·4)가 이 프로젝트에서 특히 중요합니다. 재투영·클리핑·
래스터화·파생 라벨 생성이 모두 여기 걸립니다.

### `GET /v1/quality`

검증으로 확인된 데이터 품질 결함

포털 메타데이터가 틀린 사례 목록.

행 수 과소 표기, 빈 등록물, 확장자 불일치 등 실제 다운로드·호출로
확인된 것만 담습니다.

### `GET /v1/regions`

경북 시군 목록

### `GET /v1/regions/resolve`

지역명 → 코드·좌표·기상격자

기관마다 지역 식별자가 달라서 필요한 변환입니다.

기상청은 격자(nx/ny), ASOS는 지점번호, 다른 API는 시군구 코드를 씁니다.

| 파라미터 | 위치 | 필수 | 설명 |
| --- | --- | --- | --- |
| `q` | query | 예 | 시군명, 시군구 코드, 또는 '문경시 산북면' |

### `GET /v1/sources/{connector}`

원천 하나를 직접 조회

커넥터 이름으로 특정 원천을 조회합니다.

사용 가능한 이름은 `/v1/health`의 `connectors[].name`에 있습니다.

| 파라미터 | 위치 | 필수 | 설명 |
| --- | --- | --- | --- |
| `connector` | path | 예 |  |
| `region` | query | 아니오 | 경북 시군 |
| `rows` | query | 아니오 |  |

