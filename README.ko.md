# GB SafeData

[English](README.md) · **한국어**

[![CI](https://img.shields.io/github/actions/workflow/status/jxkr2026algorix/jxkr2026-gbsafedata/ci.yml?branch=main&label=CI&logo=githubactions&logoColor=white)](https://github.com/jxkr2026algorix/jxkr2026-gbsafedata/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-770%20passing-brightgreen)](tests)
[![live APIs](https://img.shields.io/badge/live%20APIs-6%20connected-0a7bbb)](scripts/smoke_live_apis.py)
[![python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue?logo=python&logoColor=white)](pyproject.toml)
[![uv](https://img.shields.io/badge/uv-managed-261230?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![ruff](https://img.shields.io/badge/ruff-checked-261230?logo=ruff&logoColor=white)](https://docs.astral.sh/ruff/)
[![MCP](https://img.shields.io/badge/MCP-12%20read--only%20tools-000000)](docs/mcp.md)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

경북 재난대피 공공데이터를 AI와 행정시스템에 **출처를 붙여서** 제공한다.

MCP 서버, 표준 API, 정제·통합 계층, 데이터셋 검색·라이선스 검증, AI용 Skill, MCP 클라이언트 설정을 한 저장소에 담았다.

---

## 무엇이 문제인가

데이터는 이미 공개되어 있다. 그것과 쓸 수 있다는 것은 다른 얘기다.

`data.go.kr` 데이터셋 91건을 조회하고 응답하는 API는 전부 호출해 봤다. 확인된 것들이다.

- **오픈API 60건 중 33건은 데이터를 주지 않는다.** 포털은 카탈로그일 뿐이고 인증키는 다른 기관 사이트에서 받아야 한다. 데이터셋 페이지에서 그 사실을 알려주는 것은 `API 유형: LINK`라는 필드 하나다.
- **행 수를 4배 적게 표기한 데이터가 있다.** 포털은 소방용수시설 5만 건이라고 한다. 실제로는 199,507행이고, 표기된 숫자는 그리드 다운로드 상한이다.
- **지진 대피시설 2종은 비어 있다.** 표준데이터로 등록되어 있고 설명도 있고 다운로드 버튼도 눌린다. 받아보면 헤더만 있다.
- **홍수 계열은 변경이 금지되어 있다.** 21종 전부 공공누리 제4유형 — 출처표시, 상업적 이용금지, 변경금지다. 좌표계를 바꾸는 것부터 이용조건 위반인데, 그것이 대피 분석의 첫 단계다.
- **기상 격자 변환식이 공개되어 있지 않다.** 단기예보 API는 위경도가 아니라 격자를 받는데, 변환식은 첨부 ZIP 안에만 있다.

재난 데이터에는 일반 데이터에 없는 위험이 하나 더 있다. **조회 실패가 안전으로 읽힌다.** 산사태 API가 403을 반환해 결과가 비었을 때, "산사태 특보 없음"과 "확인하지 못했음"은 겉으로 같아 보인다.

마지막 항목이 이 프로젝트의 설계를 결정했다.

## 설계

**출처 없이 값이 이동하지 않는다.** 모든 관측값은 출처·신선도·품질플래그를 지닌 `Record`에 담긴다. 값만 꺼내 쓸 수도 있지만, 그러려면 인용을 의도적으로 버려야 한다. 깜빡해서 잃어버리는 일은 없다.

**부재는 추정하지 않고 증명한다.** 조회마다 `records` · `confirmed_empty` · `failed` 중 하나를 명시한 영수증이 남는다. 파서는 정상 응답 봉투를 확인하고 원천이 명시한 '자료 없음' 표기를 찾은 뒤에만 확인된 부재를 주장할 수 있다. 알아보지 못한 응답 구조는 실패이고, `absence_confirmed`가 "이 빈 결과를 '해당 없음'으로 읽어도 되는가"에 답한다.

**라이선스는 문서가 아니라 코드가 강제한다.** 공공누리 3·4유형은 변경금지이고, 여기에는 재투영·클리핑·조인·파생라벨 생성이 모두 들어간다. `require()`가 그 경로에서 예외를 던진다. 아무도 읽지 않는 주석으로 남기지 않는다.

**부작용이 없다.** 전화 발신, 대피명령, 주민 상태 변경은 운영 플랫폼의 일이다. 이 계층에는 POST·PUT·DELETE 라우트가 없고, MCP 서버는 이름이 변경을 암시하는 도구의 등록을 거부한다.

## 제대로 동작하면 이렇게 된다

문경시에 산사태 위험이 있는지 명확히 답하라고 압박했을 때, 이 스택을 붙인 AI는 **답하기를 거부했다**.

> 문경시 산사태 위험은 확인되지 않았습니다. "위험이 있다"도 "없다"도 말할 수 없는 상태입니다.
>
> 핵심 원천 두 곳이 모두 조회에 실패했습니다. 없다고 답하는 것은 조회 실패를 안전으로 바꿔치기하는 것이라 그렇게 하지 않겠습니다.
>
> **확인하지 못한 것:** 산림청 산사태 예측정보(15074800) — HTTP 403, 개발단계 심의 대기. 도로변 산사태 정보(15074812) — 동일 사유.

이것이 전부다. 에이전트가 장애를 좋은 소식으로 오해할 수 없게 만드는 것이 이 시스템의 목적이다.

## 시작하기

AI 하네스에 한 줄로 붙인다. opencode·Claude Code·Claude Desktop·Cursor를 감지해 MCP 서버와 Skill을 함께 설치한다.

```bash
curl -fsSL https://raw.githubusercontent.com/jxkr2026algorix/jxkr2026-gbsafedata/main/install.sh | bash
```

설치 후 "문경시 산사태 위험 상황을 확인해줘"를 물어본다. 제대로 붙었다면 조회하지 못한 원천을 밝히고, 확인하지 않은 위험을 '없음'으로 답하지 않는다.

하네스별 설정·인증키·문제 해결은 **[docs/install.md](docs/install.md)**에 있다.

코드를 직접 다루려면:

```bash
git clone https://github.com/jxkr2026algorix/jxkr2026-gbsafedata
cd jxkr2026-gbsafedata
uv sync --all-packages

cp .env.example .env       # GBSAFE_DATA_GO_KR_SERVICE_KEY 입력
uv run gbsafe doctor
```

`doctor`는 어떤 원천을 쓸 수 있는지, 못 쓰면 왜인지 보여준다. **인증키 부재**와 **심의 대기**와 **직접 내려받아야 하는 파일**을 구별해 알려주는데, 대응이 서로 다르고 잘못 짚으면 포털을 헛걸음하기 때문이다.

인증키가 없어도 카탈로그 검색·검증·인용은 동작한다.

## 사용

### CLI

```bash
uv run gbsafe doctor                              # 원천 상태
uv run gbsafe search 산사태 --ready                # 지금 호출 가능한 것만
uv run gbsafe verify 15074800 --operation derive  # 가공해도 되는지
uv run gbsafe cite 15084084                       # 보고서용 출처 문구
uv run gbsafe region 문경시                        # 코드·좌표·기상격자
uv run gbsafe hazard 문경시 --type landslide       # 현재 상황
uv run gbsafe quality                             # 확인된 데이터 결함
uv run gbsafe serve                               # 표준 API
uv run gbsafe mcp                                 # MCP 서버
```

### 표준 API

```bash
uv run gbsafe serve   # http://127.0.0.1:8000/docs
```

모든 데이터 응답이 같은 봉투를 쓴다.

```json
{
  "records": [{ "payload": {...}, "source": {...}, "freshness": {...} }],
  "citations": [{ "text": "기상청 「기상청 기상특보」 · 기준 2026-08-21T17:00:00+09:00 · KOGL-1 · ..." }],
  "receipts": [
    { "connector": "weather_warning", "outcome": "records", "record_count": 9 },
    { "connector": "landslide_forecast", "outcome": "failed", "detail": "HTTP 403 — 개발단계 심의 대기" }
  ],
  "complete": false,
  "absence_confirmed": false
}
```

`records`가 비어 있을 때 결론을 내리기 전에 `absence_confirmed`를 본다. 전체 명세는 [docs/api.md](docs/api.md)에 있다.

### MCP 서버

```bash
uv run gbsafe-mcp
```

클라이언트 설정은 [`plugins/`](plugins)에 있다. 도구 12개 전부 읽기 전용이다.

`gbsafe_search_datasets` · `gbsafe_describe_dataset` · `gbsafe_verify_dataset` · `gbsafe_cite_dataset` · `gbsafe_resolve_region` · `gbsafe_hazard_context` · `gbsafe_hazard_capabilities` · `gbsafe_list_sources` · `gbsafe_fetch_source` · `gbsafe_data_health` · `gbsafe_quality_report` · `gbsafe_population_guidance`

[`skills/gb-safedata`](skills/gb-safedata)를 함께 설치한다. MCP 서버는 에이전트에게 도구를 주고, Skill은 재난 데이터를 정직하게 읽는 규칙을 준다 — 확인하지 않은 부재를 보고하지 않기, 예보를 관측으로 제시하지 않기, 집계로 개인을 추정하지 않기, 대피를 결정하지 않기.

### 웹 챗봇에 붙이기

브라우저 백엔드는 stdio MCP 서버를 띄울 수 없어서, 같은 도구 12개를 HTTP로도
제공한다. 어느 표면을 쓸지는 쓰는 모델 클라이언트가 정한다.

```bash
docker compose up            # http://localhost:8000
```

| 클라이언트 | 쓸 것 | 이유 |
| --- | --- | --- |
| Upstage Solar, OpenAI chat completions | `GET /v1/tools` + `GET /v1/tools/{name}` | function calling 클라이언트는 MCP 클라이언트를 따로 만들지 않으면 MCP를 못 쓴다 |
| OpenAI Responses API 등 MCP 네이티브 | `POST /mcp` | URL만 주면 도구 발견과 호출을 스스로 한다 |

도구 경로는 `GET`이다. 인자가 전부 스칼라라 질의문자열로 충분하고, 그래서 이
계층의 "쓰기 라우트 없음" 보장이 유지된다. `POST /mcp` 하나만 예외이며
JSON-RPC가 POST를 요구하기 때문이다.

**`GET /v1/agent/system-prompt`를 받아서 반드시 함께 적용한다.** 이걸 빼고
도구만 붙이는 것이 이 프로젝트가 막으려는 실패다. 403으로 실패해 빈 결과를
받은 모델은 "산사태 위험 없습니다"라고 답한다. 도움이 되려는 것이 기본
동작이기 때문이다.

인터넷에 노출하기 전에 `GBSAFE_API_KEYS`와 `GBSAFE_CORS_ALLOW_ORIGINS`를
설정한다. 둘 다 기본은 꺼져 있어 로컬 작업에는 설정이 필요 없고, 어느 쪽도
전체 허용이 기본이 아니다 — 이 서비스는 우리 정부 인증키로 원천을 부른다.

## 연동된 원천

`data.go.kr` 개발계정 키 하나로 대부분이 동작한다. 홍수통제소 두 건은 별도 HRFCO 키를 쓰며, 그 키는 신청할 때 등록한 사용 URL에 묶여 있어 다른 곳에서 부르면 코드 940이 돌아온다.

| 커넥터 | 데이터셋 | 상태 |
| --- | --- | --- |
| `weather_now` | 기상청 초단기실황 | 동작 |
| `weather_forecast` | 기상청 단기예보 | 동작 |
| `weather_warning` | 기상청 기상특보 | 동작 |
| `wildfire_risk` | 산림청 산불위험예보 | 동작 |
| `emergency_beds` | 응급실 실시간 가용병상 | 동작 |
| `air_quality` | AirKorea 대기오염정보 | 동작 (일 500건) |
| `river_level` | 홍수통제소 하천수위 + 고시 임계수위 | 동작 |
| `flood_forecast` | 홍수통제소 홍수특보 발령 현황 | 동작 |
| `aws_observation` | 기상청 AWS 방재기상관측 1분자료 (API허브) | 동작 |
| `landslide_forecast` | 산림청 산사태 예측정보 | 심의 대기 |
| `landslide_roadside` | 산림청 도로변 산사태 정보 | 심의 대기 |
| `landslide_history` | 산림청 과거 산사태 정보 | 심의 대기 |
| `shelters` | 대피시설 표준데이터 | CSV 수동 |
| `landslide_zones` | 산사태취약지역 (문경) | CSV 수동 |

산사태 3종은 다른 모든 API와 방향이 반대다. **개발단계가 심의승인, 운영단계가 자동승인이다.** 승인 소요기간은 공개되지 않는다. 키를 다시 받아도 해결되지 않으므로 `doctor`가 일반적인 실패로 뭉뚱그리지 않고 이 사유를 짚어준다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | 계층·데이터 흐름과 경계를 그 위치에 둔 이유 |
| [docs/api.md](docs/api.md) | 엔드포인트 명세 (OpenAPI 스펙에서 생성) |
| [docs/mcp.md](docs/mcp.md) | 도구 명세 (도구 정의에서 생성) |
| [docs/safety.md](docs/safety.md) | 각 안전 경계와 그것을 강제하는 방식 |
| [docs/install.md](docs/install.md) | 하네스별 설정·인증키·문제 해결 |
| [docs/pitch-differentiation.md](docs/pitch-differentiation.md) | 뻔한 구현과의 정량 비교 근거 |
| [docs/data-sources.md](docs/data-sources.md) | 기관별 취득 방법·함정·확인된 결함 |

## 개발

```bash
uv run pytest tests/ -q                                # 네트워크 불필요
uv run ruff check .
uv run python scripts/mutation_audit.py                # 테스트가 무엇을 잡는지 측정
uv run python scripts/smoke_live_apis.py               # 실제 API 호출
uv run python scripts/check_generated_docs.py --write  # api.md·mcp.md 재생성
uv run python scripts/check_readme_badges.py --write   # 뱃지 숫자 갱신
```

테스트는 `.env`를 읽지 않는다. 인증키가 전혀 없는 환경에서도 같은 결과가 나와야 하기 때문이다. 예전 버전은 그렇지 않았고, '인증키 없는 상황'을 검증하려던 테스트가 실제 키를 집어 라이브 호출을 하고 있었다.

### 커버리지를 신뢰하지 않는 이유

커버리지는 "이 줄이 실행됐다"만 말한다. 필요한 것은 "이 줄이 틀렸을 때 테스트가 실패한다"는 보장이다. 둘은 다르다 — 커버리지 87%인 상태에서 **산사태 경보를 전부 '낮음'으로 낮춰도 501개 테스트가 전부 통과했다.**

그래서 [`scripts/mutation_audit.py`](scripts/mutation_audit.py)가 위험을 은폐하는 방향으로 코드를 고의로 망가뜨리고 테스트가 그것을 잡는지 측정한다. 각 뮤테이션은 실제로 일어날 수 있는 실패다 — 결측 기온을 0℃로 만들기, 해제된 특보를 발효 중으로 남기기, 지진 대피소를 호우에 배정하기.

살아남은 뮤테이션은 그 실패를 아무 테스트도 잡지 못한다는 뜻이므로, CI가 실패로 처리한다.

### CI가 검사하는 것

[`.github/workflows/ci.yml`](.github/workflows/ci.yml)이 푸시와 PR마다 돈다.

| Job | 검사 |
| --- | --- |
| `test` | Python 3.12·3.13 전체 테스트, 인증키 없이 |
| `lint` | `ruff check` |
| `guarantees` | 아래 안전 속성이 유지되는지 |
| `mutation` | 위험 은폐 뮤테이션이 전부 검출되는지 |
| `live-api` | 실제 정부 API 호출, 푸시마다 + 매일 |
| `install` | Ubuntu·macOS lockfile 설치 후 CLI·MCP·API 실행 |

`guarantees`는 이 문서가 주장하는 내용을 그대로 검사한다.

- API에 **쓰기 라우트가 없다** — POST가 하나라도 생기면 빌드가 깨진다
- MCP 도구가 **전부 읽기 전용**으로 등록된다
- **카탈로그의 모든 라이선스 표기가 판별된다** — 표기 변형이 `UNKNOWN`으로 떨어지면 허용된 작업이 조용히 막힌다
- **격자 변환이 공개 기준값과 일치한다** (서울·부산·제주·문경). 잘못된 격자는 다른 도시의 날씨를 성공적으로 반환하기 때문이다
- `docs/api.md`·`docs/mcp.md`가 **코드와 일치한다**
- 위 **뱃지 숫자가 실제 값과 같다**

`live-api`가 필요한 이유는 고정 응답 테스트가 원천의 스키마 변경을 통과시키기 때문이다. 그때 깨지는 것은 프로덕션이다. 한도를 지키려 원천별로 1회만 호출하고, 심의 대기 3종이 여전히 `not_authorized`인지도 확인해 승인이 떨어지면 알려준다.

실패를 세 가지로 구별한다. 뭉뚱그리면 job이 신호를 잃기 때문이다. 파서가 원천을 더 이상 읽지 못하는 것은 실제 결함이므로 빌드를 깬다. 원천 전체가 도달 불가면 네트워크·리전 문제이므로 사유를 밝히고 0으로 종료한다. AirKorea는 간헐적으로 504를 반환하는데 — 조사 기록에 4회 재시도로도 3회 중 1회는 실패하며 필수 의존으로 두지 말라고 명시되어 있다 — 이 원천의 실패는 보고하되 빌드를 깨지 않는다.

## 라이선스

코드와 문서는 [Apache-2.0](LICENSE)이다.

**원본 데이터는 이 저장소에 없다.** 홍수 계열은 공공누리 제4유형이라 상업적 이용금지·변경금지이고, 상업적 이용금지는 이용 분야를 차별할 수 없는 OSI 라이선스와 함께 둘 수 없다. OpenStreetMap은 ODbL이고 share-alike는 전염된다 — OSM과 정부 데이터를 하나의 산출물로 합쳐 배포하면 출처표시만 요구하던 데이터에도 share-alike가 얹힌다.

그래서 데이터 대신 **취득 방법과 검증된 메타데이터**를 담았다. 이용조건은 데이터마다 다르고, `gbsafe verify`가 무엇이 허용되는지 알려준다.

## 관련 저장소

- [jxkr2026-datasets](https://github.com/jxkr2026algorix/jxkr2026-datasets) — 카탈로그의 근거가 된 조사. 각 원천이 실제로 무엇을 반환하는지, 어떻게 취득하는지, 포털 표기가 어디서 틀렸는지
