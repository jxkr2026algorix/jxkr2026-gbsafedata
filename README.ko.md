# GB SafeData

[English](README.md) · **한국어**

[![CI](https://img.shields.io/github/actions/workflow/status/jxkr2026algorix/jxkr2026-gbsafedata/ci.yml?branch=main&label=CI&logo=githubactions&logoColor=white)](https://github.com/jxkr2026algorix/jxkr2026-gbsafedata/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-814%20passing-brightgreen)](tests)
[![live APIs](https://img.shields.io/badge/live%20APIs-6%20connected-0a7bbb)](scripts/smoke_live_apis.py)
[![python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue?logo=python&logoColor=white)](pyproject.toml)
[![uv](https://img.shields.io/badge/uv-managed-261230?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![ruff](https://img.shields.io/badge/ruff-checked-261230?logo=ruff&logoColor=white)](https://docs.astral.sh/ruff/)
[![MCP](https://img.shields.io/badge/MCP-12%20read--only%20tools-000000)](docs/mcp.md)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

경북 재난대피 공공데이터를 AI와 행정시스템에 **출처를 붙여서** 제공한다.

MCP 서버, 표준 API, 정제·통합 계층, 데이터셋 검색·라이선스 검증, AI용 Skill, MCP 클라이언트 설정을 한 저장소에 담았다.

---

## 어떤 형태로 쓸 수 있나

배포돼 있다. 아래는 전부 **지금 그대로 동작하는 주소와 명령**이다.

```
https://datainfra.salgil.gyeongbuk.kr
```

### 1. MCP 서버 — 인증 없음

AI가 도구 12개로 재난 데이터를 조회하고, 답변마다 출처를 인용한다.

```
https://datainfra.salgil.gyeongbuk.kr/mcp/
```

**인증키가 필요 없다.** 정부 인증키는 서버가 들고 있고, 공개된 집계 데이터만 읽는다.
도구는 전부 읽기 전용(`readOnlyHint`)이라 AI가 무언가를 바꾸거나 지울 수 없다.

도구 12개는 전부 읽기 전용이다.

`gbsafe_search_datasets` · `gbsafe_describe_dataset` · `gbsafe_verify_dataset` · `gbsafe_cite_dataset` · `gbsafe_resolve_region` · `gbsafe_hazard_context` · `gbsafe_hazard_capabilities` · `gbsafe_list_sources` · `gbsafe_fetch_source` · `gbsafe_data_health` · `gbsafe_quality_report` · `gbsafe_population_guidance`

끝의 슬래시를 빼지 않는다. `/mcp`도 307로 넘겨주지만 POST에서 리다이렉트를 따라가지
않는 클라이언트가 있다.

### 2. 플러그인 — 클릭 한 번

**[▶ Claude에 추가하기](https://datainfra.salgil.gyeongbuk.kr/add-claude)**

이름과 주소가 채워진 창이 열린다. 확인만 누르면 끝이고 **무료 플랜에서도 된다.**
claude.ai · 데스크톱 · 모바일에서 모두 쓸 수 있다.

**ChatGPT**는 개발자 모드와 유료 플랜이 필요하고 웹 전용이라 절차가 길다 →
**[docs/chatgpt.md](docs/chatgpt.md)**

그 밖의 클라이언트(Claude Code · Cursor · VS Code · opencode)는
[docs/connect.md](docs/connect.md)에 있다.

### 3. 오픈소스 Skill — 한 줄 설치

도구가 *무엇을 할 수 있는지*를 주는 게 MCP라면, Skill은 *어떻게 써야 안전한지*를 준다 —
확인하지 않은 부재를 보고하지 말 것, 예보를 관측으로 제시하지 말 것, 집계로 개인을
추정하지 말 것, 대피를 결정하지 말 것.

```bash
npx skills add jxkr2026algorix/jxkr2026-gbsafedata
```

opencode · Claude Code · Codex · Cursor 등을 알아서 감지한다. 원본은
[`skills/gb-safedata`](skills/gb-safedata).

### 4. 표준 API — 기존 행정 시스템용

MCP를 모르는 시스템도 그냥 HTTP로 쓴다. 모든 데이터 응답이 하나의 봉투를 쓴다.

```bash
curl -G https://datainfra.salgil.gyeongbuk.kr/v1/hazards/context \
     --data-urlencode "region=문경시" --data-urlencode "hazard=heavy_rain"
```

```json
{
  "records": [...],
  "citations": [...],
  "sources_checked": [
    { "connector": "weather_warning",    "outcome": "records" },
    { "connector": "landslide_forecast", "outcome": "failed",
      "detail": "HTTP 403 — 개발단계 심의승인 대상" }
  ],
  "complete": false,
  "absence_confirmed": false
}
```

**`records`가 비었을 때 `absence_confirmed`를 먼저 읽어야 한다.** 이 둘을 구별하지
않으면 조회 실패가 "위험 없음"으로 보인다.

스키마 41개와 전체 라우트: [`/docs`](https://datainfra.salgil.gyeongbuk.kr/docs) ·
[docs/api.md](docs/api.md) · 화면 연동 계약은 [docs/handoff.md](docs/handoff.md).

### 5. 데이터 정제·통합 Layer

기관마다 다른 것을 하나로 맞춘다.

| 흩어진 것 | 통합 결과 |
| --- | --- |
| 시군명 표기 · 옛 지명 · 이관 구역 | 행정표준코드 5자리 + 대표 좌표 |
| 위경도 · 기상청 격자 · ASOS 지점번호 | 지역 하나로 상호 변환 (`gbsafe_resolve_region`) |
| 기관별 시각 표기 · 시간대 누락 | UTC 정규화 + 신선도 판정 |
| 결측 표기 (`-`, `-999`, 빈 문자열) | 실측 센티널로 판별해 0으로 오독하지 않음 |
| 기관별 라이선스 문구 | KOGL 코드로 정규화 후 허용 연산 판정 |

### 6. 검색 · 검증 · 인용 도구

```bash
uv run gbsafe search 산사태 --ready                # 지금 호출 가능한 것만
uv run gbsafe verify 15074800 --operation derive  # 이 데이터를 변형해도 되나
uv run gbsafe cite 15084084                       # 보고서에 넣을 출처 문구
uv run gbsafe doctor                              # 원천별 상태와 막힌 이유
```

`verify`는 문서가 아니라 **코드로 막는다.** KOGL 3·4는 변형을 금지하는데 재투영·클리핑·
조인·파생 라벨이 전부 여기 걸린다.

---

## 서비스로 쓰기 vs 직접 띄우기

### 서비스로 쓰기 (권장)

위 주소를 그대로 쓰면 된다. 설치·인증키·가입이 없다. 정부 API 호출과 인증키 관리,
캐싱, 할당량 보호를 서버가 맡는다.

브라우저에서 직접 부르는 건 권하지 않는다 — 우리 정부 인증키로 원천을 호출하므로
여러분 백엔드가 프록시하는 편이 안전하다.

### 직접 띄우기

데이터 주권이 필요하거나 자체 인증키로 돌리려면:

```bash
git clone https://github.com/jxkr2026algorix/jxkr2026-gbsafedata
cd jxkr2026-gbsafedata
uv sync --all-packages

cp .env.example .env       # GBSAFE_DATA_GO_KR_SERVICE_KEY 입력
uv run gbsafe doctor       # 무엇이 되고 무엇이 왜 막혔는지
uv run gbsafe serve        # http://127.0.0.1:8000/docs
```

또는 컨테이너로:

```bash
docker compose up
```

**인증키가 하나도 없어도** 카탈로그 검색 · 검증 · 인용 · 지역 해석은 그대로 동작한다.

서버 배포(Caddy · Cloudflare · 컨테이너)는 [deploy/README.md](deploy/README.md),
클라이언트별 설정은 [docs/install.md](docs/install.md).

---

## 제대로 동작하면 이렇게 된다

문경시에 산사태 위험이 있는지 명확히 답하라고 압박했을 때, 이 스택을 붙인 AI는 **답하기를 거부했다**.

> 문경시 산사태 위험은 확인되지 않았습니다. "위험이 있다"도 "없다"도 말할 수 없는 상태입니다.
>
> 핵심 원천 두 곳이 모두 조회에 실패했습니다. 없다고 답하는 것은 조회 실패를 안전으로 바꿔치기하는 것이라 그렇게 하지 않겠습니다.
>
> **확인하지 못한 것:** 산림청 산사태 예측정보(15074800) — HTTP 403, 개발단계 심의 대기. 도로변 산사태 정보(15074812) — 동일 사유.

이것이 전부다. 에이전트가 장애를 좋은 소식으로 오해할 수 없게 만드는 것이 이 시스템의 목적이다.

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
| [docs/rationale.md](docs/rationale.ko.md) | **왜 이렇게 만들었나 — 조사·설계·검증** |
| [docs/chatgpt.md](docs/chatgpt.md) | **ChatGPT에 연결하는 절차** |
| [docs/connect.md](docs/connect.md) | **Claude·ChatGPT·코딩 에이전트에 연결하기** |
| [docs/handoff.md](docs/handoff.md) | **배포된 인스턴스와 다른 팀이 붙이는 법** |
| [docs/pitch-differentiation.md](docs/pitch-differentiation.md) | 뻔한 구현과의 정량 비교 근거 |
| [docs/data-sources.md](docs/data-sources.md) | 기관별 취득 방법·함정·확인된 결함 |

## 라이선스

코드와 문서는 [Apache-2.0](LICENSE)이다.

**원본 데이터는 이 저장소에 없다.** 홍수 계열은 공공누리 제4유형이라 상업적 이용금지·변경금지이고, 상업적 이용금지는 이용 분야를 차별할 수 없는 OSI 라이선스와 함께 둘 수 없다. OpenStreetMap은 ODbL이고 share-alike는 전염된다 — OSM과 정부 데이터를 하나의 산출물로 합쳐 배포하면 출처표시만 요구하던 데이터에도 share-alike가 얹힌다.

그래서 데이터 대신 **취득 방법과 검증된 메타데이터**를 담았다. 이용조건은 데이터마다 다르고, `gbsafe verify`가 무엇이 허용되는지 알려준다.

## 관련 저장소

- [jxkr2026-datasets](https://github.com/jxkr2026algorix/jxkr2026-datasets) — 카탈로그의 근거가 된 조사. 각 원천이 실제로 무엇을 반환하는지, 어떻게 취득하는지, 포털 표기가 어디서 틀렸는지
