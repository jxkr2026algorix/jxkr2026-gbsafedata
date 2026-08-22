# GB SafeData — 플랫폼팀 인수인계

경북 재난 공공데이터를 **출처와 함께** 제공하는 읽기 전용 인프라. 배포돼 있고 지금 호출하면 응답합니다.

```
https://datainfra.salgil.gyeongbuk.kr
```

이 문서는 붙이는 데 필요한 것만 담습니다. 필드 단위 상세는 [platform-integration.md](platform-integration.md), 전체 엔드포인트는 `/docs`(Swagger UI)와 `/openapi.json`에 있습니다.

---

## 1. 30초 확인

```bash
curl https://datainfra.salgil.gyeongbuk.kr/v1/health
curl https://datainfra.salgil.gyeongbuk.kr/v1/hazards/capabilities
curl -G https://datainfra.salgil.gyeongbuk.kr/v1/hazards/context \
     --data-urlencode "region=문경시" --data-urlencode "hazard=heavy_rain"
```

---

## 2. 챗봇 붙이기 — 두 경로 중 하나

**어느 쪽을 쓸지는 여러분의 LLM 클라이언트가 정합니다.**

| 클라이언트 | 쓸 것 | 이유 |
| --- | --- | --- |
| Upstage Solar, OpenAI chat completions | `GET /v1/tools` + `GET /v1/tools/{name}` | function calling 클라이언트는 MCP 클라이언트를 따로 구현하지 않으면 MCP를 못 씁니다 |
| OpenAI Responses API 등 MCP 네이티브 | `POST /mcp/` | URL만 주면 도구 발견과 호출을 스스로 합니다 |

### 경로 A — function calling (Solar 포함)

```python
import httpx
BASE = "https://datainfra.salgil.gyeongbuk.kr"

tools = httpx.get(f"{BASE}/v1/tools").json()["tools"]          # OpenAI 형식 그대로
system = httpx.get(f"{BASE}/v1/agent/system-prompt").json()["system_prompt"]

messages = [{"role": "system", "content": system},
            {"role": "user", "content": "문경 산사태 위험 알려줘"}]

while True:
    reply = llm.chat.completions.create(model=..., messages=messages, tools=tools)
    msg = reply.choices[0].message
    messages.append(msg)
    if not msg.tool_calls:
        break
    for call in msg.tool_calls:
        args = json.loads(call.function.arguments)
        result = httpx.get(f"{BASE}/v1/tools/{call.function.name}", params=args, timeout=120).text
        messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
```

도구는 `GET`입니다. 인자가 전부 스칼라라 질의문자열로 충분하고, 덕분에 이 계층의 "쓰기 라우트 없음" 보장이 유지됩니다.

### 경로 B — MCP

```bash
curl -X POST https://datainfra.salgil.gyeongbuk.kr/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

**Cloudflare를 통과합니다.** 실제로 확인했습니다 — stateless + JSON 응답 모드라 SSE를 쓰지 않아서 버퍼링·유휴 타임아웃 문제가 생기지 않습니다. `initialize`가 서버 지침(`instructions`)도 함께 돌려줍니다.

### 도구 12개

```
gbsafe_search_datasets      gbsafe_describe_dataset   gbsafe_verify_dataset
gbsafe_cite_dataset         gbsafe_resolve_region     gbsafe_hazard_context
gbsafe_hazard_capabilities  gbsafe_list_sources       gbsafe_fetch_source
gbsafe_data_health          gbsafe_quality_report     gbsafe_population_guidance
```

접두어 `gbsafe_`가 이름의 일부입니다. 빼면 "그런 도구 없음"이 납니다.

---

## 3. 화면은 3상태여야 합니다 ← 가장 중요

모든 데이터 응답에 `complete`와 `absence_confirmed`가 있습니다.

**대시보드가 "데이터 있음 / 없음" 2상태로 렌더하면 장애가 초록 타일로 보입니다.**

```ts
function state(res) {
  if (res.records.length > 0)                    return "DATA";        // 표시
  if (res.complete && res.absence_confirmed)     return "NONE";        // ✅ "발효 중 없음"
  return "UNVERIFIED";                                                 // ⚠️ "확인 불가" — 초록 금지
}
```

`UNVERIFIED`를 안심시키는 색으로 칠하면 안 됩니다. 실패한 원천은 `sources_checked`(= `receipts`, 같은 값)에서 `outcome: "failed"`로 나오고 `detail`에 사유가 있습니다.

**지금 실제로 그 상태인 예시** — 문경 산사태:

```json
{
  "complete": false,
  "absence_confirmed": false,
  "sources_checked": [
    {"connector": "landslide_forecast", "outcome": "failed",
     "detail": "HTTP 403 — 개발단계 심의승인 대상 ..."},
    {"connector": "landslide_roadside", "outcome": "failed", "detail": "..."},
    {"connector": "weather_warning",    "outcome": "records"},
    {"connector": "weather_now",        "outcome": "records"}
  ]
}
```

기상 데이터는 왔지만 **산사태 데이터는 못 읽었습니다.** 이걸 "산사태 위험 없음"으로 그리면 안 됩니다.

### 레코드 단위로 더 볼 것

| 필드 | 뜻 |
| --- | --- |
| `freshness.usable_for_decision: false` | 오래된 값 — 시점을 함께 표시 |
| `source.mode: "synthetic"` | 훈련 데이터 — 훈련 표시 유지 |
| `caveats`, `notes` | 관측지점 거리 등 — 버리지 말고 표시 |

---

## 4. 시스템 프롬프트는 필수입니다

```
GET /v1/agent/system-prompt
```

**도구만 붙이면 사고가 납니다.** 모델은 기본적으로 도움이 되려 하고, 산사태 조회가 403으로 실패해 결과가 비면 그냥 **"산사태 위험 없습니다"** 라고 답합니다.

복붙하지 말고 위 엔드포인트에서 받아 쓰세요. 우리가 고치면 자동으로 따라옵니다.

---

## 5. 재난 13종 — 무엇이 되고 무엇이 안 되는가

```
GET /v1/hazards/capabilities
```

세 축(**탐지** 지금 났는가 / **위험도** 어디가 위험한가 / **대피소** 어디로 가는가) 기준입니다.

| 상태 | 재난 |
| --- | --- |
| **ready** (세 축 완비) | 호우, 홍수, 산사태, 산불, 태풍 |
| **partial** | 지진, 지진해일, 폭염, 한파, 대설, 가뭄 |
| **blocked** (탐지 없음) | 화학사고, 원전 |

**`partial`을 `ready`처럼 보이게 하면 안 됩니다.** 지진은 발생을 알려주지만 **어느 대피소로 보낼지 모릅니다.** 그래서 `can_detect`와 `can_say_where_to_go`가 따로 있습니다.

`ready`가 아닌 재난은 답변의 `caveats[0]`에 한계가 자동으로 실립니다. 그 문장을 화면에 그대로 띄우면 됩니다.

---

## 6. 지금 안 되는 것

| 원천 | 상태 | 우리가 할 수 있는 일 |
| --- | --- | --- |
| 산사태 예측·도로변·이력 (3종) | data.go.kr **심의 대기** (403) | 없음 — 승인 대기 |
| 대피시설·산사태취약지역 CSV (2종) | 포털 수동 다운로드 필요 | 파일 받으면 즉시 반영 |

**403은 정상 동작입니다.** 인증키 문제가 아니고, 새 키를 발급받아도 해결되지 않습니다. `/v1/health`가 이 셋을 구별해서 알려줍니다 — 인증키 없음 / 심의 대기 / 파일 필요는 대응 방법이 다릅니다.

커넥터 18개 중 **13개 가용**입니다.

---

## 7. 운영

### 인증·CORS

지금은 **무인증**입니다. 서버가 tailnet 전용이고 앞단이 Cloudflare라 그렇습니다.

브라우저에서 직접 부르려면 알려주세요 — `GBSAFE_CORS_ALLOW_ORIGINS`에 도메인을 넣어야 헤더가 나갑니다. 키 인증이 필요하면 `GBSAFE_API_KEYS`를 채우면 `x-api-key` 또는 `Authorization: Bearer`로 걸립니다.

**브라우저가 이 API를 직접 부르지 않는 것을 권합니다.** 우리 정부 인증키로 원천을 호출하므로, 여러분 백엔드가 프록시하는 편이 안전합니다.

### Cloudflare

- **SSL/TLS 모드는 반드시 `Full`** — `Full (strict)`는 실패합니다. 오리진 인증서가 Caddy 자체서명이라 CF가 검증하지 못합니다. Origin CA 인증서를 주시면 strict로 올릴 수 있습니다.
- 오리진에 공인 IPv4가 없어 CF가 **IPv6(AAAA)로** 붙습니다.
- 무료 플랜 100초 요청 타임아웃 — 재난 조회 팬아웃이 보통 2초라 여유가 큽니다.

### 지연

재난 조회는 여러 정부 API로 팬아웃합니다. 보통 1~3초, AirKorea가 느릴 때 최대 30초입니다. **원천별 로딩 상태**를 따로 두는 것을 권합니다 — 하나가 느리다고 전체를 막을 이유가 없고, 실패해도 나머지는 옵니다.

### 서버

```bash
ssh ubuntu@salgil-aws

docker ps                                    # gbsafedata-api, salgil-postgres, salgil-redis
docker logs -f gbsafedata-api
journalctl -u caddy -f                       # 리버스 프록시

cd /opt/gbsafedata
docker compose -f docker-compose.deploy.yml restart
```

Caddy는 시스템 서비스입니다. 다른 서비스를 붙일 때는 `/etc/caddy/sites/<이름>.caddy` 파일만 추가하면 되고, 이 서비스를 재시작할 필요가 없습니다.

공통 인프라(Postgres·Redis)는 `/opt/salgil-infra`에 따로 떠 있습니다. 접속 정보는 그쪽 `README.md`에 있고, **서비스마다 DB를 나눠 쓰는 것**을 전제합니다.

---

## 8. 이 API가 하지 않는 것

- 전화 발신, 대피명령, 주민 상태 변경 — 운영 플랫폼 책임입니다
- 개인정보 취급 — 공개 집계 데이터만 다룹니다
- 대피 여부 결정 — 근거와 후보를 제시하고 결정은 사람이 합니다
- **위험 점수 생성** — 기관 고시값(산림청 등급, 홍수통제소 임계수위)을 그대로 노출합니다. 자체 가중치로 "위험도 87점" 같은 걸 만들면 어느 기관도 보증하지 않는 숫자가 됩니다. 파생 지표를 만드시면 **자체 모델임을 표시**해 주세요.

쓰기 라우트가 없습니다. 데이터 경로는 전부 `GET`이고, 유일한 `POST`는 MCP 전송입니다(JSON-RPC가 요구).

---

## 9. 막히면

| 증상 | 원인 |
| --- | --- |
| `521` | 오리진이 죽었거나 Caddy 정지 — `journalctl -u caddy` |
| `526` | CF SSL 모드가 `Full (strict)` — `Full`로 |
| 도구 호출에 "그런 도구 없음" | 이름에 `gbsafe_` 접두어 |
| MCP `400` | `/mcp` 대신 `/mcp/` (307 리다이렉트는 되지만 따라가지 않는 클라이언트가 있음) |
| 산사태가 항상 비어 있음 | 정상 — 심의 대기 403. `sources_checked`에 사유 있음 |
| 응답에 인증키가 보임 | 버그입니다. 즉시 알려주세요 (현재는 `<redacted>`로 나갑니다) |
