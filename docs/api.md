# 표준 API 명세

`uv run gbsafe serve` 로 기동하고 `/docs`에서 대화형으로 확인할 수 있다.
이 문서는 실제 OpenAPI 스펙에서 생성했다 —
`uv run python scripts/check_generated_docs.py --write` 로 갱신한다.

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

### `POST /mcp`

MCP Streamable HTTP 전송

`/mcp`도 받는다.

마운트는 `/mcp/`만 받아 슬래시 없는 요청이 400으로 떨어진다. 연동하는
쪽에서 원인을 알기 어려운 실패라 여기서 넘겨준다.

### `GET /v1/agent/system-prompt`

이 도구를 안전하게 쓰기 위한 시스템 프롬프트

도구만 붙이면 생기는 사고를 막는 지침.

모델은 기본적으로 도움이 되려 한다. 산사태 조회가 403으로 실패해
결과가 비면, 지침이 없는 모델은 "산사태 위험 없습니다"라고 답한다.
이 프롬프트가 그 답을 금지한다.

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

### `GET /v1/datasets/{dataset_id}/citation`

출처 표기 문구

보고서에 붙일 인용 문구를 만듭니다.

실제 관측값을 인용할 때는 조회 응답의 `citations`를 쓰는 편이 정확합니다 —
관측 시각이 포함됩니다.

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

### `GET /v1/hazards/capabilities`

재난별로 지금 어디까지 답할 수 있는지

탐지·위험도·대피소 세 축의 가용성.

재난 유형 목록만 보면 13종 전부 대응 가능한 것처럼 보인다. 실제로는
다섯만 세 축이 다 있고, 지진은 발생을 알려주지만 어느 대피소로 보낼지
모른다. 그 차이를 화면에서 지우면 갈 곳 없는 안내가 나간다.

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

### `GET /v1/tools`

OpenAI 호환 도구 정의

LLM에 그대로 넘길 수 있는 function calling 스키마.

MCP 서버와 **같은 정의**에서 나온다. 웹 챗봇은 MCP(stdio)를 붙이기
어려우므로 같은 도구를 HTTP로 노출하되, 정의가 갈라지면 두 표면의
동작이 달라지므로 출처를 하나로 둔다.

Upstage Solar와 OpenAI 모두 이 형식을 받는다.

### `GET /v1/tools/{name}`

도구 실행 (조회 전용)

도구를 실행한다.

POST가 아니라 GET인 이유가 있다. 이 계층은 쓰기 라우트가 없다는 것을
보장하고 CI가 그것을 검사한다. 도구 인자가 전부 스칼라라서 질의문자열로
충분하므로, RPC를 위해 그 보장을 깨지 않는다.

| 파라미터 | 위치 | 필수 | 설명 |
| --- | --- | --- | --- |
| `name` | path | 예 |  |

