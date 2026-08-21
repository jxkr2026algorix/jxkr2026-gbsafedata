# GB SafeData

경북 재난대피에 필요한 공공데이터를 **AI와 외부 시스템이 출처와 함께 즉시 활용할 수 있게** 만드는 오픈소스 데이터 인프라다.

MCP 서버, 표준 API, 정제·통합 계층, 검색·검증·인용 도구, AI용 Skill, MCP 클라이언트 플러그인을 하나의 모노레포로 제공한다.

## 이 프로젝트가 해결하는 문제

경북 재난대피에 필요한 데이터는 이미 공개되어 있다. 문제는 **그 상태로는 쓸 수 없다**는 것이다.

`data.go.kr` 데이터셋 91건을 실제로 조회해 확인한 사실들이다([조사 저장소](https://github.com/jxkr2026algorix/jxkr2026-datasets)).

- 오픈API 60건 중 **33건은 포털이 카탈로그 역할만** 한다. 인증키는 기상청 API허브·한강홍수통제소 등 원천기관에서 따로 받아야 한다.
- 포털이 신고한 행 수가 **4배 틀린 경우**가 있다(소방용수시설 5만 → 실제 199,507).
- 표준데이터로 등록됐지만 **그리드가 0행인 빈 등록물**이 있다(지진 대피시설 2종).
- 홍수 계열 21종 전부가 **공공누리 제4유형(변경금지)** — 재투영조차 이용조건 위반이다.
- 기상청 격자 변환식은 공개 페이지에 없고 **첨부 ZIP에만** 있다.

그리고 재난 데이터에는 일반 데이터에 없는 위험이 하나 더 있다. **조회 실패가 '위험 없음'으로 읽힌다.** 산사태 API가 403을 반환해 결과가 비었을 때 "산사태 위험이 없습니다"라고 답하면 위험을 은폐한 것이 된다.

GB SafeData는 이 문제들을 코드로 처리한다.

## 설계 원칙

**출처 없이 값이 이동하지 않는다.** 원천에서 온 모든 관측·경보·시설 데이터가 `Record`(값 + 출처 + 신선도 + 품질플래그)로 감싸여 다닌다. 값만 꺼내 쓰는 것은 가능하지만, 그러려면 인용을 의도적으로 버려야 한다. (지역 코드 변환이나 데이터셋 검색처럼 원천 관측이 아닌 참조 정보는 이 봉투를 쓰지 않는다.)

**실패를 숨기지 않는다.** 응답은 `records`와 함께 조회한 원천별 영수증(`receipts`)과 `degradations`를 싣는다. 각 영수증이 `records` / `confirmed_empty` / `failed` 중 하나를 명시하므로, 빈 결과가 '해당 없음'인지 '조회 실패'인지 추정하지 않고 확인할 수 있다. `absence_confirmed`가 그 판정을 한 줄로 알려준다.

**라이선스를 코드가 강제한다.** 문서에 적어두는 것으로는 지켜지지 않는다. KOGL-3·4 데이터에 재투영·클리핑을 시도하면 `LicenseViolation`이 발생한다.

**안전 경계가 타입과 예외에 있다.** 대피소는 확인된 재난유형에만 배정되고, 집계 통계로 개인을 추정하려는 시도는 거부되며, 부작용을 암시하는 MCP 도구는 등록되지 않는다.

**조회만 한다.** 전화 발신·대피명령·주민 상태 변경은 이 계층의 책임이 아니다. API에 POST/PUT/DELETE가 없다.

## 구성

| 패키지 | 역할 |
| --- | --- |
| [`gbsafe-core`](packages/gbsafe-core) | 정제·통합 계층 — 스키마, 라이선스 게이트, 신선도, 좌표변환, 스냅샷, 안전 경계 |
| [`gbsafe-connectors`](packages/gbsafe-connectors) | 원천 커넥터 11종 — 수집·정규화·장애 표면화 |
| [`gbsafe-api`](packages/gbsafe-api) | 표준 API — 외부 시스템 연계용 REST (인용 포함) |
| [`gbsafe-mcp`](packages/gbsafe-mcp) | MCP 서버 — AI용 읽기 전용 도구 10종 |
| [`gbsafe-cli`](packages/gbsafe-cli) | CLI — 검색·검증·인용·진단 |
| [`skills/gb-safedata`](skills/gb-safedata) | AI가 재난 데이터를 안전하게 쓰는 규칙 |
| [`plugins`](plugins) | Claude Desktop·opencode·범용 MCP 클라이언트 설정 |

## 시작하기

```bash
git clone https://github.com/jxkr2026algorix/jxkr2026-gbsafedata
cd jxkr2026-gbsafedata
uv sync --all-packages

cp .env.example .env
# GBSAFE_DATA_GO_KR_SERVICE_KEY 입력 — 이 키 하나로 9종 원천이 동작한다

uv run gbsafe doctor
```

`doctor`가 각 원천이 지금 쓸 수 있는지, 못 쓰면 왜인지 보여준다. **인증키 부재와 심의 대기를 구별해 알려준다** — 두 문제의 대응이 다르기 때문이다.

키가 없어도 카탈로그 검색·검증·인용은 동작한다.

### 데이터셋 카탈로그

카탈로그는 [`jxkr2026-datasets`](https://github.com/jxkr2026algorix/jxkr2026-datasets)를 원천으로 읽는다. 나란히 두면 그 저장소의 검증 결과가 실시간으로 반영된다.

```
dev/
├── jxkr2026-gbsafedata/   ← 이 저장소
└── jxkr2026-datasets/     ← 카탈로그 원천 (선택)
```

없으면 동봉된 폴백 스냅샷을 쓰고, 그 사실이 응답에 표시된다.

## 사용

### CLI

```bash
uv run gbsafe doctor                        # 원천 상태 진단
uv run gbsafe search 산사태 --ready          # 지금 호출 가능한 것만 검색
uv run gbsafe verify 15074800 --operation derive   # 가공해도 되는지 판정
uv run gbsafe region 문경시                  # 코드·좌표·기상격자 변환
uv run gbsafe hazard 문경시 --type landslide # 현재 위험 상황
uv run gbsafe quality                       # 확인된 데이터 결함
uv run gbsafe serve                         # 표준 API 기동
uv run gbsafe mcp                           # MCP 서버 기동
```

### 표준 API

```bash
uv run gbsafe serve   # http://127.0.0.1:8000/docs
```

| 엔드포인트 | 용도 |
| --- | --- |
| `GET /v1/health` | 원천 상태와 인증 정보 현황 |
| `GET /v1/datasets` | 데이터셋 검색 (`must_allow=derive`로 변경금지 제외) |
| `GET /v1/datasets/{id}` | 취득 방법·라이선스·확인된 결함 |
| `GET /v1/datasets/{id}/verify` | 이 용도로 써도 되는지 판정 |
| `GET /v1/hazards/context` | 지역 현재 위험 상황 (다중 원천 통합) |
| `GET /v1/sources/{connector}` | 원천 직접 조회 |
| `GET /v1/regions/resolve` | 지역명 → 코드·좌표·기상격자 |
| `GET /v1/quality` | 데이터 품질 결함 |
| `GET /v1/licenses` | 라이선스별 허용 연산 |

모든 데이터 응답이 같은 봉투를 쓴다.

```json
{
  "records": [{ "payload": {...}, "source": {...}, "freshness": {...} }],
  "citations": [{ "text": "기상청 「기상청 기상특보」 · 기준 2026-08-21T17:00:00+09:00 · KOGL-1 · ..." }],
  "degradations": [{ "dataset_id": "15074800", "status": "not_authorized", "detail": "...개발단계가 심의승인 대상입니다...", "blocks_interpretation": true }],
  "complete": false
}
```

**`complete`를 확인하지 않고 `records`만 읽으면 조회 실패를 '해당 없음'으로 오해한다.**

### MCP 서버

```bash
uv run gbsafe-mcp
```

설정은 [`plugins/`](plugins)에 있다. 도구 10종 전부 읽기 전용이다.

`gbsafe_search_datasets` · `gbsafe_describe_dataset` · `gbsafe_verify_dataset` · `gbsafe_resolve_region` · `gbsafe_hazard_context` · `gbsafe_list_sources` · `gbsafe_fetch_source` · `gbsafe_data_health` · `gbsafe_quality_report` · `gbsafe_population_guidance`

[`skills/gb-safedata`](skills/gb-safedata)를 함께 설치하면 AI가 데이터를 안전하게 해석하는 규칙까지 적용된다. MCP만 붙이면 도구는 쓸 수 있지만 인용 규칙과 안전 경계가 빠진다.

## 연동된 데이터 원천

data.go.kr 개발계정 키 하나로 동작하는 것들이다.

| 커넥터 | 데이터셋 | 상태 |
| --- | --- | --- |
| `weather_now` | 기상청 초단기실황 | 동작 |
| `weather_forecast` | 기상청 단기예보 | 동작 |
| `weather_warning` | 기상청 기상특보 | 동작 |
| `wildfire_risk` | 산림청 산불위험예보 | 동작 |
| `emergency_beds` | 응급실 실시간 가용병상 | 동작 |
| `air_quality` | AirKorea 대기오염정보 | 동작 (일 500건 한도) |
| `landslide_forecast` | 산림청 산사태 예측정보 | 개발단계 심의 대기 |
| `landslide_roadside` | 산림청 도로변 산사태 정보 | 개발단계 심의 대기 |
| `landslide_history` | 산림청 과거 산사태 정보 | 개발단계 심의 대기 |
| `shelters` | 대피시설 표준데이터 | CSV 수동 취득 |
| `landslide_zones` | 산사태취약지역 (문경) | CSV 수동 취득 |

산사태 3종은 **개발단계가 심의승인, 운영단계가 자동승인**인 역방향 구조라서 승인 전까지 호출이 막힌다. 키를 다시 발급받아도 해결되지 않으므로 `doctor`가 이것을 구별해 알려준다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | 계층 구조와 데이터 흐름 |
| [docs/api.md](docs/api.md) | 엔드포인트별 요청·응답 |
| [docs/mcp.md](docs/mcp.md) | MCP 도구 명세 |
| [docs/safety.md](docs/safety.md) | 안전 경계와 강제 방식 |
| [docs/data-sources.md](docs/data-sources.md) | 원천별 취득 방법과 함정 |

## 개발

```bash
uv run pytest tests/ -q        # 네트워크를 사용하지 않는다
uv run ruff check .
uv run python scripts/sync_fallback_catalog.py   # 폴백 카탈로그 갱신
```

테스트는 개발자의 `.env`를 읽지 않는다. 키가 없는 환경에서도 같은 결과가 나와야 하기 때문이다.

## 라이선스

코드와 문서는 [Apache-2.0](LICENSE)이다.

**원본 데이터는 이 저장소에 포함되지 않는다.** 홍수 계열 21종이 공공누리 제4유형(상업적 이용금지 + 변경금지)이고, 상업적 이용금지는 이용 분야를 차별할 수 없는 OSI 계열 오픈소스 라이선스와 양립하지 않는다. OSM(ODbL)은 share-alike이므로 정부 데이터와 병합해 배포하면 전염된다.

그래서 데이터 대신 **취득 방법과 검증된 메타데이터만** 담는다. 각 데이터의 이용조건은 원천기관에서 확인해야 하며, `gbsafe verify`가 판정을 돕는다.

## 관련 저장소

- [jxkr2026-datasets](https://github.com/jxkr2026algorix/jxkr2026-datasets) — 공공데이터 조사·검증 결과와 카탈로그
