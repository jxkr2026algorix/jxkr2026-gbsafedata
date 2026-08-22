# GB SafeData를 챗봇 있는 대시보드에 붙이기

아래 내용은 전부 `http://127.0.0.1:8910`에 실제로 띄운 서버(GB SafeData API 0.1.0)에 직접 호출해 확인한 것이다. 명령어, URL, JSON은 실제 응답을 그대로 옮겼다.

한 절만 읽어야 한다면 [3절](#3-세-가지-상태-ui-계약)을 읽으면 된다. 사고가 나는 지점이 거기다.

---

## 1. 이 서비스가 하는 일과 하지 않는 일

GB SafeData는 경상북도로 범위를 한정한 국내 재난 공공데이터 API 조회 전용 집계기다. "문경시 산사태 상황이 어떤가"를 물으면 해당 재난에 맞는 정부 API들을 동시에 조회해 형식을 통일하고, 각 값이 어디서 언제 온 것인지까지 함께 돌려준다.

이런 서비스다.

- 조회 전용
- 경북 22개 시군 범위
- 출처는 공공 API (기상청, 산림청, 한강홍수통제소, 한국환경공단, 국립중앙의료원)
- 못 읽은 원천을 숨기지 않고 명시

이런 기능은 없고, 해당 엔드포인트 자체가 없다.

- 전화 발신, 문자 발송
- 대피명령 발령, 대피계획 승인
- 주민 정보 등 어떤 상태 변경
- 개별 주민 식별

서버가 직접 그렇게 답한다.

```console
$ curl -s http://127.0.0.1:8910/
{"name":"GB SafeData API","version":"0.1.0","docs":"/docs","openapi":"/openapi.json","read_only":true,
 "note":"이 API는 조회만 제공합니다. 전화·대피명령·상태변경 기능이 없습니다."}
```

판단은 담당 공무원이 한다. 대시보드는 근거를 보여주는 것이고, 결론을 보여주면 안 된다.

---

## 2. 연동 방식 두 가지와 선택 기준

| 쓰는 LLM | 방식 | 작업량 |
|---|---|---|
| Upstage Solar, OpenAI chat completions, Anthropic messages | REST: `GET /v1/tools` + `GET /v1/tools/{name}` | 툴 호출 루프 작성 (40줄 정도) |
| OpenAI Responses API, 그 외 MCP 지원 클라이언트 | MCP: `POST /mcp/` | URL 설정 |

취향이 아니라 모델이 뭘 지원하느냐로 정하면 된다.

**Solar는 MCP로 못 붙인다.** MCP 클라이언트를 직접 구현하지 않는 한 안 된다. Solar는 OpenAI 호환 chat completions에 `tools=`를 쓰므로 REST가 확실히 일이 적다. 반대로 OpenAI Responses API를 쓰면 MCP는 URL만 넣으면 끝이라, REST 루프를 짜는 건 헛수고다.

두 방식 모두 같은 데이터로 같은 도구 11개를 실행한다. 차이는 전송 방식과 키 이름 두 개뿐이다([3절](#두-표면의-키-이름-차이) 참고).

### 2a. REST — chat completions 클라이언트

엔드포인트 두 개다. `GET /v1/tools`가 OpenAI 형식 함수 스키마를 주고, `GET /v1/tools/{name}`이 실행한다.

```console
$ curl -s http://127.0.0.1:8910/v1/tools | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['tools']))"
11
```

응답은 `{"tools": [...], "invoke": ..., "note": ...}` 형태다. `tools`를 모델에 그대로 넘기면 된다. `invoke`는 `"GET /v1/tools/{name}?<인자>"`이고, `note`는 system_prompt 없이 도구 출력을 사용자에게 그대로 전달하면 안 된다는 경고다.

실제 응답에서 가져온 스키마 하나다.

```json
{
  "type": "function",
  "function": {
    "name": "gbsafe_hazard_context",
    "description": "특정 지역의 현재 위험 상황을 재난 유형에 맞는 여러 원천에서 모아 옵니다. 기상특보, 실황 강우, 산사태 예보, 산불위험지수 등이 재난 유형에 따라 선택됩니다.\n\n**complete가 false이면 일부 원천 조회에 실패한 것이고, 결과가 비어 있어도 '위험 없음'을 의미하지 않습니다.** 사유는 warnings에 있습니다.",
    "parameters": {
      "type": "object",
      "properties": {
        "region": {
          "type": "string",
          "description": "경북 시군 (예: 문경시, 안동시). 시군구 코드(47280)도 가능"
        },
        "hazard": {
          "type": "string",
          "description": "재난 유형",
          "enum": ["heavy_rain", "landslide", "wildfire", "flood", "earthquake", "heatwave"]
        }
      },
      "required": ["region"],
      "additionalProperties": false
    }
  }
}
```

인자는 쿼리스트링으로 넘긴다. `region`은 시군명과 5자리 코드 모두 받는다.

```console
$ curl -s -G http://127.0.0.1:8910/v1/tools/gbsafe_hazard_context \
    --data-urlencode "region=문경시" --data-urlencode "hazard=landslide"
```

도구 11개다.

| 도구 | 필수 인자 | 용도 |
|---|---|---|
| `gbsafe_search_datasets` | — | 데이터셋 카탈로그 검색 |
| `gbsafe_describe_dataset` | `dataset_id` | 데이터셋 상세, 취득 방법 포함 |
| `gbsafe_verify_dataset` | `dataset_id` | 해당 이용행위가 라이선스상 되는지 확인 |
| `gbsafe_cite_dataset` | `dataset_id` | 인용 문구 |
| `gbsafe_resolve_region` | `region` | 시군명 → 코드·격자·관측지점 |
| `gbsafe_hazard_context` | `region` | 여러 원천을 묶어서 조회한 현재 위험 상황 |
| `gbsafe_list_sources` | — | 사용 가능한 커넥터 목록 |
| `gbsafe_fetch_source` | `source` | 특정 커넥터 단독 조회 |
| `gbsafe_data_health` | — | 어떤 원천이 살아 있고, 나머지는 왜 안 되는지 |
| `gbsafe_quality_report` | — | 확인된 데이터 결함 |
| `gbsafe_population_guidance` | `purpose` | 인구 데이터 허용 용도 |

없는 이름을 부르면 404에 전체 목록이 들어온다.

```console
$ curl -s http://127.0.0.1:8910/v1/tools/does_not_exist
{"detail":{"error":"'does_not_exist' 도구가 없습니다","available":["gbsafe_search_datasets", ...]}}
```

#### Python 전체 루프

```python
import json
import urllib.parse
import urllib.request

from openai import OpenAI  # Solar는 OpenAI 호환

GBSAFE = "http://127.0.0.1:8910"
GBSAFE_KEY = None  # GBSAFE_API_KEYS를 설정했다면 채운다

client = OpenAI(
    api_key="<UPSTAGE_API_KEY>",
    base_url="https://api.upstage.ai/v1",
)


def _get(path: str, params: dict[str, str] | None = None) -> dict:
    url = f"{GBSAFE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    if GBSAFE_KEY:
        req.add_header("x-api-key", GBSAFE_KEY)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


# 둘 다 기동 시점에 받아온다. 하드코딩하면 안 된다.
tools = _get("/v1/tools")["tools"]
system_prompt = _get("/v1/agent/system-prompt")["system_prompt"]


def ask(question: str) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    for _ in range(6):  # 루프 상한
        completion = client.chat.completions.create(
            model="solar-pro2",
            messages=messages,
            tools=tools,
        )
        message = completion.choices[0].message
        messages.append(message)

        if not message.tool_calls:
            return message.content or ""

        for call in message.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            args = {k: str(v) for k, v in args.items() if v is not None}
            try:
                result = _get(f"/v1/tools/{call.function.name}", args)
            except urllib.error.HTTPError as exc:
                result = {"error": exc.read().decode("utf-8", "replace")}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return "도구 호출 루프가 수렴하지 않았습니다."


print(ask("문경시 산사태 위험 상황 알려줘"))
```

여기서 중요한 건 두 가지다. 도구 결과는 **통째로** 되돌려줘야 한다. 모델이 없는 안심을 만들어내지 않게 막아주는 게 `warnings`·`sources_checked`·`caveats`다. 그리고 받아온 system_prompt를 반드시 넣어야 한다. 없으면 이 루프는 안전하지 않다([5절](#5-system_prompt는-필수다) 참고).

#### TypeScript, 같은 루프

```ts
import OpenAI from "openai";
import type {
  GbSafeToolCatalog,
  GbSafeSystemPrompt,
  GbSafeToolEnvelope,
} from "./gbsafe";

const GBSAFE = "http://127.0.0.1:8910";
const GBSAFE_KEY: string | undefined = undefined;

const client = new OpenAI({
  apiKey: process.env.UPSTAGE_API_KEY,
  baseURL: "https://api.upstage.ai/v1",
});

async function gbsafeGet<T>(
  path: string,
  params?: Record<string, string>,
): Promise<T> {
  const url = new URL(path, GBSAFE);
  for (const [k, v] of Object.entries(params ?? {})) {
    url.searchParams.set(k, v);
  }
  const res = await fetch(url, {
    headers: GBSAFE_KEY ? { "x-api-key": GBSAFE_KEY } : {},
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

const { tools } = await gbsafeGet<GbSafeToolCatalog>("/v1/tools");
const { system_prompt } = await gbsafeGet<GbSafeSystemPrompt>(
  "/v1/agent/system-prompt",
);

export async function ask(question: string): Promise<string> {
  const messages: OpenAI.ChatCompletionMessageParam[] = [
    { role: "system", content: system_prompt },
    { role: "user", content: question },
  ];

  for (let turn = 0; turn < 6; turn++) {
    const completion = await client.chat.completions.create({
      model: "solar-pro2",
      messages,
      tools: tools as OpenAI.ChatCompletionTool[],
    });

    const message = completion.choices[0].message;
    messages.push(message);

    if (!message.tool_calls?.length) return message.content ?? "";

    for (const call of message.tool_calls) {
      const raw = JSON.parse(call.function.arguments || "{}");
      const args: Record<string, string> = {};
      for (const [k, v] of Object.entries(raw)) {
        if (v != null) args[k] = String(v);
      }

      let result: GbSafeToolEnvelope | { error: string };
      try {
        result = await gbsafeGet<GbSafeToolEnvelope>(
          `/v1/tools/${call.function.name}`,
          args,
        );
      } catch (err) {
        result = { error: String(err) };
      }

      messages.push({
        role: "tool",
        tool_call_id: call.id,
        content: JSON.stringify(result),
      });
    }
  }

  return "도구 호출 루프가 수렴하지 않았습니다.";
}
```

### 2b. MCP — MCP 지원 클라이언트

`POST /mcp/`가 MCP Streamable HTTP를 처리한다. **끝의 슬래시를 빠뜨리면 안 된다.** `/mcp`는 `307 Temporary Redirect`로 `/mcp/`를 가리키고, POST에서 리다이렉트를 따라가지 않는 클라이언트는 멈춘 것처럼 보이거나 그냥 실패한다.

```console
$ curl -s -i -X POST http://127.0.0.1:8910/mcp \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize", ...}'
HTTP/1.1 307 Temporary Redirect
location: /mcp/
```

`Accept: application/json, text/event-stream`을 붙인다. 실제 `initialize` 응답이다.

```console
$ curl -s -X POST http://127.0.0.1:8910/mcp/ \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0.0.1"}}}'
```

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "capabilities": { "experimental": {}, "tools": { "listChanged": false } },
    "instructions": "경북(경상북도) 재난대피 공공데이터를 출처와 함께 조회하는 도구입니다.\n\n## 사용 원칙\n\n1. **출처 없이 답하지 마세요.** ...",
    "protocolVersion": "2025-06-18",
    "serverInfo": { "name": "gbsafedata", "title": "GB SafeData", "version": "0.1.0" }
  }
}
```

**`initialize`는 `instructions`도 같이 준다.** system_prompt와 같은 안전 규칙이 들어 있다. 출처를 밝힐 것, `complete: false`는 불완전한 결과로 취급할 것, 오래된 자료를 최신처럼 제시하지 말 것, 집계 통계로 개인을 추정하지 말 것, 대피 결정을 내리지 말 것. 대부분의 MCP 클라이언트는 `instructions`를 모델에 자동으로 전달하는데, 쓰는 클라이언트가 실제로 그렇게 하는지 확인해야 한다. 안 한다면 `GET /v1/agent/system-prompt`를 받아 직접 넣는다.

`tools/list`는 같은 도구 11개를 MCP 어노테이션(`readOnlyHint: true`, `destructiveHint: false`)과 함께 준다. `tools/call`은 `result.content[0].text` 안에 JSON 문자열로 봉투를 담아 준다.

```console
$ curl -s -X POST http://127.0.0.1:8910/mcp/ \
    -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"gbsafe_hazard_context","arguments":{"region":"문경시","hazard":"landslide"}}}'
```

확인한 결과는 이렇다. `result`의 키는 `content`와 `isError`, `content[0].type`은 `"text"`이고, 그 text를 파싱하면 `complete: false`, `absence_confirmed: false`인 봉투가 나온다. `structuredContent`는 없으니 `content[0].text`를 파싱해야 한다.

OpenAI Responses API라면 연동은 이게 전부다.

```python
response = client.responses.create(
    model="gpt-4.1",
    tools=[{
        "type": "mcp",
        "server_label": "gbsafedata",
        "server_url": "https://your-gbsafe-host/mcp/",
        "require_approval": "never",
    }],
    input="문경시 산사태 위험 상황 알려줘",
)
```

OpenAI 쪽에서 서버에 접근할 수 있어야 하므로 `127.0.0.1`이 아니라 배포된 호스트가 필요하다.

---

## 3. 세 가지 상태 UI 계약

**이 절이 핵심이다.** 모든 데이터 응답에 `complete`와 `absence_confirmed`가 실려 온다. 데이터 있음/없음 두 가지만 그리는 대시보드는 틀린 것이고 위험하다.

세 가지를 그려야 한다.

| 조건 | 상태 | 표시 |
|---|---|---|
| `records.length > 0` | 데이터 | 레코드를 보여준다 |
| `complete && absence_confirmed && records.length === 0` | 확인된 없음 | "현재 발효 중인 것 없음" — 안심되게 보여도 된다 |
| 그 외 전부 | 미확인 | "확인 못 함" — **안심되게 보이면 안 된다** |

3번 상태에서는 실패한 원천을 나열해야 한다. 영수증에서 `outcome === "failed"`를 걸러 `connector`와 `detail`을 같이 보여준다.

### 왜 그런가 — 지금 시점의 문경시 산사태

산사태에 쓰이는 원천 4개 중 2개가 산림청 API인데, data.go.kr 활용신청이 심의 대기 상태라 HTTP 403이 떨어진다. 실제 호출이다.

```console
$ curl -s -G http://127.0.0.1:8910/v1/hazards/context \
    --data-urlencode "region=문경시" --data-urlencode "hazard=landslide"
```

렌더링을 결정하는 필드만 남기고 줄였다. `records`와 `citations`는 생략했다.

```json
{
  "query": { "region": "문경시", "hazard": "landslide" },
  "receipts": [
    {
      "connector": "landslide_forecast",
      "dataset_id": "15074800",
      "outcome": "failed",
      "record_count": 0,
      "checked_at": "2026-08-22T07:29:40.027460Z",
      "upstream_status": "not_authorized",
      "detail": "HTTP 403 — 인증 실패 — 「산림청 산사태 예측정보」은 개발단계가 심의승인 대상입니다. 활용신청이 승인되기 전까지 호출이 거부됩니다 (신청: https://www.data.go.kr/data/15074800/openapi.do). 승인 소요기간은 공개되지 않아 미리 신청해 두어야 합니다."
    },
    {
      "connector": "weather_warning",
      "dataset_id": "15000415",
      "outcome": "records",
      "record_count": 3,
      "checked_at": "2026-08-22T07:29:40.027470Z",
      "upstream_status": "ok",
      "detail": ""
    },
    {
      "connector": "weather_now",
      "dataset_id": "15084084",
      "outcome": "records",
      "record_count": 8,
      "checked_at": "2026-08-22T07:29:40.027474Z",
      "upstream_status": "ok",
      "detail": ""
    },
    {
      "connector": "landslide_roadside",
      "dataset_id": "15074812",
      "outcome": "failed",
      "record_count": 0,
      "checked_at": "2026-08-22T07:29:40.027479Z",
      "upstream_status": "not_authorized",
      "detail": "HTTP 403 — 인증 실패 — 「산림청 도로변 산사태 정보」은 개발단계가 심의승인 대상입니다. 활용신청이 승인되기 전까지 호출이 거부됩니다 (신청: https://www.data.go.kr/data/15074812/openapi.do). 승인 소요기간은 공개되지 않아 미리 신청해 두어야 합니다."
    }
  ],
  "degradations": [
    {
      "dataset_id": "15074800",
      "status": "not_authorized",
      "detail": "HTTP 403 — 인증 실패 — 「산림청 산사태 예측정보」은 개발단계가 심의승인 대상입니다. ...",
      "occurred_at": "2026-08-22T07:29:39.776607Z",
      "last_known_good_at": null,
      "blocks_interpretation": true
    },
    {
      "dataset_id": "15074812",
      "status": "not_authorized",
      "detail": "HTTP 403 — 인증 실패 — 「산림청 도로변 산사태 정보」은 개발단계가 심의승인 대상입니다. ...",
      "occurred_at": "2026-08-22T07:29:39.777029Z",
      "last_known_good_at": null,
      "blocks_interpretation": true
    }
  ],
  "caveats": [
    "발표관서 단위 특보입니다 — 관할 구역 전체가 대상이며 특정 마을 상태가 아닙니다",
    "경북 관할 관서만 필터했습니다 (대구지방기상청 (대구·경북), 안동기상대 (경북 북부내륙), 포항기상대 (경북 동해안))",
    "타 지역 특보 86건을 제외했습니다",
    "해제·대체된 통보문 11건을 반영해 제외했습니다 (발표 후 해제된 특보는 발효 중이 아닙니다)"
  ],
  "complete": false,
  "absence_confirmed": false,
  "record_count": 11,
  "generated_at": "2026-08-22T07:29:40.027521Z",
  "modes": ["real"]
}
```

무슨 일이 벌어졌는지 보자. 레코드는 11건 왔다. 그런데 그중 산사태 데이터는 한 건도 없다. 전부 기상특보와 실황 관측이다. **정작 산사태 원천 두 개는 둘 다 HTTP 403에 막혀 아무것도 못 가져왔다.** `complete: false`와 `absence_confirmed: false`가 그 사실을 말하고 있다. 산사태 질문에는 답이 안 나왔다는 뜻이다.

두 가지 상태만 그리는 대시보드는 여기서 초록색 "산사태 위험 없음" 타일을 띄운다. 산사태 레코드를 못 찾았으니까. 그건 장애를 안전으로 바꿔 보여주는 것이다. 실제로 사면이 무너져도 타일은 초록이었고, 데이터를 애초에 확인하지 못했다는 사실은 아무도 듣지 못한다.

맞는 표시는 이렇다. "산사태 위험 확인 불가 — 산림청 산사태 예측정보, 산림청 도로변 산사태 정보 모두 HTTP 403 (활용신청 심의 대기)."

### 대조 — 확인된 없음은 이렇게 생겼다

같은 형태의 응답이고, 진짜로 비어 있으며, 그렇게 보여줘도 되는 경우다.

```console
$ curl -s -G http://127.0.0.1:8910/v1/sources/river_level --data-urlencode "region=울릉군"
```

```json
{
  "query": { "connector": "river_level", "region": "울릉군" },
  "records": [],
  "citations": [],
  "receipts": [
    {
      "connector": "river_level",
      "dataset_id": "hrfco-waterlevel",
      "outcome": "confirmed_empty",
      "record_count": 0,
      "checked_at": "2026-08-22T07:37:04.829053Z",
      "upstream_status": "ok",
      "detail": ""
    }
  ],
  "degradations": [],
  "caveats": [
    "울릉군에는 홍수통제소 수위관측소가 없습니다 — 수위 정보가 없는 것이지 하천이 안전한 것이 아닙니다"
  ],
  "complete": true,
  "absence_confirmed": true,
  "record_count": 0,
  "generated_at": "2026-08-22T07:37:04.829083Z",
  "modes": []
}
```

`outcome: "confirmed_empty"`이므로 `complete`와 `absence_confirmed`가 둘 다 `true`다. `records`는 비었지만 그 비어 있음이 사실이고, 조회 실패로 인한 공백이 아니다. 그래도 caveat은 읽어야 한다. 울릉군에 수위관측소가 없다는 뜻이고, 수위를 모른다는 것이지 하천이 안전하다는 게 아니라고 적혀 있다. 2번 상태는 "현재 발효 중인 것 없음"으로 보여주고, "안전"으로 보여주면 안 된다.

`outcome` 값은 정확히 세 개다. `records`, `confirmed_empty`, `failed`. 이 중 `confirmed_empty`만 빈 결과를 "해당 없음"으로 읽을 자격을 준다.

알아둘 실패 유형이 하나 더 있다. 해석 불가한 지역을 넣으면 `complete: false`인데 `receipts`가 **빈 배열**로 온다. 사유는 `degradations`에 있다.

```console
$ curl -s -G http://127.0.0.1:8910/v1/hazards/context \
    --data-urlencode "region=서울시" --data-urlencode "hazard=flood"
{"query":{"region":"서울시","hazard":"flood"},"records":[],"citations":[],"receipts":[],
 "degradations":[{"dataset_id":"region","status":"unavailable",
   "detail":"'서울시'을 경북 시군으로 해석할 수 없습니다",
   "occurred_at":"2026-08-22T07:33:21.463661Z","last_known_good_at":null,"blocks_interpretation":true}],
 "caveats":[],"complete":false,"absence_confirmed":false,"record_count":0,
 "generated_at":"2026-08-22T07:33:21.463719Z","modes":[]}
```

이게 **HTTP 200**으로 온다는 점을 보라. 상태 코드로 판별하려 하지 말고 `complete`와 `absence_confirmed`로 분기해야 한다. 그리고 "미확인" 분기에서 `failed` 영수증만 그리면 이 경우엔 설명이 빈 화면이 된다. `receipts`가 비었으면 `degradations`로 넘어가야 한다.

### 두 표면의 키 이름 차이

| | REST (`/v1/hazards/context`, `/v1/sources/{c}`) | 도구·MCP (`/v1/tools/{name}`) |
|---|---|---|
| 원천별 결과 | `receipts` | `sources_checked` |
| 추가 필드 | `citations`, `generated_at`, `modes` | `citations`, `warnings`, `how_to_cite` |

형태는 같고 근거 데이터도 같다. `complete`, `absence_confirmed`, `records`, `caveats`, `degradations`는 양쪽 다 있다. 있는 키를 읽으면 된다.

도구 표면은 모델용 `warnings`를 미리 만들어 준다.

```json
"warnings": [
  "[조회 실패] 15074800: HTTP 403 — 인증 실패 — 「산림청 산사태 예측정보」은 개발단계가 심의승인 대상입니다. ...",
  "[조회 실패] 15074812: HTTP 403 — 인증 실패 — 「산림청 도로변 산사태 정보」은 개발단계가 심의승인 대상입니다. ...",
  "1건이 오래된 자료입니다 — 판단 근거로 제시할 때 시점을 함께 밝히세요",
  "일부 원천을 조회하지 못했습니다. 결과가 비어 있어도 '위험 없음'을 의미하지 않습니다",
  "조회하지 못한 원천: landslide_forecast, landslide_roadside — 이 원천이 다루는 위험은 확인되지 않았습니다"
]
```

모델에 그대로 넘기고, UI에도 같이 노출하는 걸 권한다.

### 그대로 붙여 쓰는 렌더링 함수

타입은 `packages/gbsafe-api/types/gbsafe.d.ts`에 있다.

```ts
import type {
  GbSafeEnvelope,
  GbSafeSourceReceipt,
  GbSafeViewState,
} from "./gbsafe";

/** REST면 `receipts`, 도구·MCP면 `sources_checked`를 읽는다. */
export function getReceipts(envelope: GbSafeEnvelope): GbSafeSourceReceipt[] {
  return "receipts" in envelope ? envelope.receipts : envelope.sources_checked;
}

/**
 * GB SafeData 응답을 화면 상태로 분류하는 유일하게 맞는 방법.
 * `records.length`만 보고 분기하면 안 된다.
 */
export function toViewState(envelope: GbSafeEnvelope): GbSafeViewState {
  const caveats = envelope.caveats ?? [];

  if (envelope.records.length > 0) {
    return { kind: "data", records: envelope.records, caveats };
  }

  if (envelope.complete && envelope.absence_confirmed) {
    return { kind: "confirmed-empty", caveats };
  }

  // 비었고 확인도 안 됐다. 무엇을 못 읽었는지 설명한다.
  const failed = getReceipts(envelope).filter((r) => r.outcome === "failed");

  // 지역 해석 실패는 영수증이 0건으로 오고 사유가 `degradations`에 있다.
  // UI가 항상 보여줄 것이 있도록 영수증 형태로 만들어 준다.
  if (failed.length === 0) {
    for (const d of envelope.degradations) {
      if (!d.blocks_interpretation) continue;
      failed.push({
        connector: d.dataset_id,
        dataset_id: d.dataset_id,
        outcome: "failed",
        record_count: 0,
        checked_at: d.occurred_at,
        upstream_status: d.status,
        detail: d.detail,
      });
    }
  }

  return { kind: "unverified", failed, caveats };
}
```

렌더링이다. 핵심은 `unverified`가 `confirmed-empty`와 스타일을 공유하지 않는 것이다.

```tsx
export function HazardPanel({ envelope }: { envelope: GbSafeEnvelope }) {
  const state = toViewState(envelope);

  switch (state.kind) {
    case "data":
      return (
        <section>
          {state.records.map((r) => (
            <RecordRow key={r.fingerprint} record={r} />
          ))}
          <CaveatList caveats={state.caveats} />
        </section>
      );

    case "confirmed-empty":
      // 차분하게 보여줘도 된다. 모든 원천이 응답했고 답이 '없음'이었다.
      return (
        <section className="state-none-in-effect">
          <p>현재 발효 중인 것이 없습니다.</p>
          <p className="muted">모든 원천이 응답했습니다. 부재가 확인되었습니다.</p>
          <CaveatList caveats={state.caveats} />
        </section>
      );

    case "unverified":
      // 안심되게 보이면 안 된다. 초록색, 체크표시, '이상 없음' 금지.
      return (
        <section className="state-unverified" role="alert">
          <p>
            <strong>확인하지 못했습니다.</strong> 위험이 없다는 뜻이 아니라,
            일부 원천을 읽지 못했다는 뜻입니다.
          </p>
          <ul>
            {state.failed.map((r) => (
              <li key={`${r.connector}-${r.checked_at}`}>
                <strong>{r.connector}</strong> ({r.dataset_id}):{" "}
                {r.detail || r.upstream_status}
              </li>
            ))}
          </ul>
          <CaveatList caveats={state.caveats} />
        </section>
      );
  }
}
```

`.state-unverified`에는 경고 수준의 시각적 무게를 줘야 한다. 대시보드를 지나가며 보는 사람이 "괜찮다"로 읽을 수 있으면 잘못 만든 것이다.

---

## 4. 레코드 단위로 지켜야 하는 규칙

레코드마다 표시 방법을 바꾸는 필드가 세 개 있다. 이걸 조용히 버리면 조건이 붙은 데이터가 조건 없는 주장으로 바뀐다.

### `freshness.usable_for_decision === false` → 시점을 함께 표시

문경시 산사태 응답에 들어 있던 오래된 레코드다.

```json
{
  "payload": {
    "hazard": "heavy_rain",
    "severity": "advisory",
    "headline": "[특보] 제08-65호 : 2026.08.21.16:10 / 호우경보 변경·호우주의보 발표 (*)",
    "area_name": "대구지방기상청 (대구·경북)",
    "action": "extended",
    "issued_at": "2026-08-21T16:10:00+09:00"
  },
  "freshness": {
    "status": "stale",
    "age_seconds": 87580,
    "expected_cycle_seconds": 3600,
    "as_of": "2026-08-21T16:10:00+09:00",
    "reason": "갱신주기 3600초의 24.3배 경과 — 판단 근거로 쓰기 부적합",
    "usable_for_decision": false
  },
  "quality_flags": [],
  "notes": [],
  "fingerprint": "b6facababe36a110"
}
```

갱신주기 1시간인데 24시간 지난 값이다. 이걸 현재 상황으로 그리면 안 된다. 값 옆에 `freshness.as_of`를 붙이고, `freshness.reason`은 그대로 화면에 쓸 수 있는 한국어다. `status`는 `fresh`, `aging`, `stale`, `unknown` 중 하나지만, 분기는 계산된 판정인 `usable_for_decision`으로 해야 한다.

### `source.mode === "synthetic"` → 훈련 표시를 유지

`mode`는 `real`, `snapshot`, `synthetic` 중 하나다. `synthetic`은 훈련·시연용 데이터다. 어디에 표시되든 훈련 표시가 남아 있어야 한다. 훈련 데이터가 실데이터처럼 보이는 순간이 심각한 실패다. 이 서버에서 확인한 레코드는 모두 `mode: "real"`이었고 봉투의 `modes`도 `["real"]`이었다. 훈련 모드를 켤 수 있으니 `synthetic` 처리는 미리 넣어둬야 한다.

### `caveats`와 `notes` → 반드시 표시, 버리지 말 것

`caveats`(봉투 단위)와 `notes`(레코드 단위)에는 숫자의 의미를 바꾸는 조건이 담긴다.

가장 분명한 건 관측지점 거리다. 영양군의 최근접 관측지점은 27km 떨어진 다른 군에 있다.

```console
$ curl -s -G http://127.0.0.1:8910/v1/regions/resolve --data-urlencode "q=영양군"
```

```json
{
  "found": true,
  "code": "47760",
  "name": "영양군",
  "full_name": "경상북도 영양군",
  "center": { "lat": 36.6667, "lon": 129.1125 },
  "kma_grid": { "nx": 97, "ny": 108 },
  "asos_station": 276,
  "asos_station_detail": {
    "station_id": 276,
    "name": "청송군",
    "distance_km": 26.6,
    "is_local": false
  },
  "caveat": "대표 좌표는 시군 청사 기준 근사값입니다 — 경계 판정이나 거리 계산의 근거로 쓰면 안 됩니다",
  "caveats": [
    "대표 좌표는 시군 청사 기준 근사값입니다 — 경계 판정이나 거리 계산의 근거로 쓰면 안 됩니다",
    "가장 가까운 관측지점이 시군 청사에서 27km 떨어진 「청송군」입니다 — 국지성 호우는 이 거리에서 크게 달라지므로 이 지역의 실측으로 제시하면 안 됩니다."
  ]
}
```

국지성 호우는 27km 사이에서 크게 달라진다. 그 값을 영양군의 실측처럼 제시하는 건 사실을 왜곡하는 것이고, caveat이 바로 그 말을 하고 있다. `asos_station_detail.is_local`을 확인하고 `false`면 `distance_km`을 같이 보여준다. 문경시는 5.6km에 `is_local: true`라 상황이 다르다.

실제로 받게 되는 다른 caveat들이다.

- `"발표관서 단위 특보입니다 — 관할 구역 전체가 대상이며 특정 마을 상태가 아닙니다"` — 관서 관할 전체 대상이고 특정 마을 상태가 아니다
- `"타 지역 특보 86건을 제외했습니다"` — 다른 지역 건을 걸러냈다
- `"캐시된 응답입니다 (호출 한도 보호)"` — 호출 한도 보호를 위해 캐시에서 응답했다
- `"울릉군에는 홍수통제소 수위관측소가 없습니다 — 수위 정보가 없는 것이지 하천이 안전한 것이 아닙니다"` — 관측소가 없으니 모르는 것이고, 안전한 게 아니다
- `"'flood_forecast'는 지역 지정을 받지 않습니다 — region='문경시'이 적용되지 않았고 결과는 더 넓은 범위입니다"` — **지역 필터가 적용되지 않았다.** 요청한 범위보다 넓은 결과다

마지막 건 놓치기 쉬운데 해석을 완전히 바꾼다. caveat은 데이터 옆에 그려야 하고, 접힌 패널에 숨기면 안 된다.

`notes`는 레코드 단위다. 예를 들어 `precipitation_type` 페이로드의 원값 `0.0`에 대해 `["강수형태: 없음"]`이 붙는다. 이게 없으면 숫자만으로는 뜻이 통하지 않는다.

`quality_flags`에는 검증으로 확인된 결함이 담긴다(`missing_coordinates`, `encoding_cp949`, `partial_response` 등). 여기서 확인한 레코드에는 비어 있었지만, 있을 때는 노출해야 한다.

---

## 5. system_prompt는 필수다

런타임에 받아온다. 코드에 붙여넣으면 낡는다.

```console
$ curl -s http://127.0.0.1:8910/v1/agent/system-prompt
{"system_prompt":"당신은 경상북도 재난 상황에서 공공데이터를 조회해 근거를 제시하는 도우미입니다.\n...","source":"skills/gb-safedata/SKILL.md"}
```

필드는 두 개다. `system_prompt`(system 메시지로 넣는다)와 `source`(출처).

규칙 8개가 들어 있다. 그중 1번이 이 프롬프트의 존재 이유다. 확인하지 않은 부재를 보고하지 않는다. 나머지는 모든 수치에 출처를 붙일 것, 예보와 관측을 구별할 것, 신선도를 밝힐 것, 훈련 데이터 표시를 유지할 것, 집계 통계로 개인을 추정하지 않을 것, 대피를 결정하지 않을 것, 관측지점 거리를 숨기지 않을 것.

**이 프롬프트가 막는 실패는 이렇다.** HTTP 403 때문에 비어 버린 결과를 모델에 넘기고 "문경시 산사태 위험 있어?"라고 묻는다. 모델은 산사태 레코드를 하나도 못 본다. 도움이 되려는 게 기본 성향이니 "문경시에 산사태 위험은 없습니다"라고 답한다. 그 문장은 거짓이다. 확인된 게 아무것도 없다. 그리고 이 시스템이 낼 수 있는 가장 위험한 출력이다. 프롬프트는 이럴 때 "확인되지 않았습니다"라고 답하고 실패한 원천을 밝히도록 지시한다.

압박 상황도 다룬다. 사용자가 "그래서 위험한 거야 아닌 거야"라고 밀어붙여도 모른다고 답하는 것이 틀린 안심보다 낫다고 명시한다.

도구 설명에도 같은 경고가 들어 있고, `GET /v1/tools`가 `note`로도 알려준다.

```
"도구 출력을 그대로 사용자에게 전달하면 안 됩니다. system_prompt를 함께 적용해야
 조회 실패가 '위험 없음'으로 읽히지 않습니다 — GET /v1/agent/system-prompt"
```

MCP에서는 `initialize`의 `instructions`가 같은 규칙을 전달한다. 클라이언트가 그걸 모델에 실제로 넘기는지 확인하고, 안 넘기면 system_prompt를 직접 주입한다.

---

## 6. 인증과 CORS

둘 다 **기본은 꺼져 있다.** 설정하지 않은 서버는 어디서 오는 요청이든 인증 없이 받는다.

### API 키

`GBSAFE_API_KEYS`에 쉼표로 나열한다. 공백은 제거된다.

```bash
GBSAFE_API_KEYS=key1,key2
```

헤더는 두 형식 모두 된다.

```console
$ curl -s -o /dev/null -w "%{http_code}\n" -H "x-api-key: key1" http://127.0.0.1:8911/v1/tools
200
$ curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer key2" http://127.0.0.1:8911/v1/tools
200
```

키가 없거나 틀리면 `401`이다.

```console
$ curl -s -i http://127.0.0.1:8911/v1/tools
HTTP/1.1 401 Unauthorized
content-type: application/json

{"error":"API 키가 필요합니다","how":"x-api-key 헤더 또는 Authorization: Bearer <키>"}
```

헬스체크와 디버깅이 계속 되도록 아래 경로는 열려 있다. 인증을 켜고 키 없이 호출해 확인했다.

```console
$ for U in /v1/health /docs /openapi.json / /redoc; do ...; done
  /v1/health     200
  /docs          200
  /openapi.json  200
  /              200
  /redoc         200
```

로드밸런서는 `/v1/health`를 보게 하면 된다. 나머지는 전부 키가 필요하고, `/mcp/`도 키 없으면 `401`이다(확인함). `OPTIONS` 요청은 예외라서 CORS 프리플라이트는 통과한다.

### CORS

```bash
GBSAFE_CORS_ALLOW_ORIGINS=https://your.dashboard
```

여러 개면 쉼표로 나열한다. 설정하지 않으면 CORS 미들웨어 자체가 안 붙고 관련 헤더도 안 나간다. 설정하면 `GET, POST, OPTIONS`와 모든 요청 헤더를 허용하고, 크리덴셜은 **허용하지 않는다.** 프리플라이트 확인 결과다.

```console
$ curl -s -i -X OPTIONS http://127.0.0.1:8911/v1/tools \
    -H "Origin: https://dash.example.org" -H "Access-Control-Request-Method: GET"
HTTP/1.1 200 OK
vary: Origin
access-control-allow-methods: GET, POST, OPTIONS
access-control-max-age: 600
access-control-allow-origin: https://dash.example.org
```

크리덴셜이 꺼져 있으므로 브라우저에서 쿠키 인증은 안 된다. `x-api-key` 헤더를 쓴다.

### 운영에서 브라우저가 이 API를 직접 호출하면 안 된다

GB SafeData는 **우리 쪽** 인증키(`GBSAFE_DATA_GO_KR_SERVICE_KEY` 등)로 정부 API를 호출한다. 브라우저에 직접 노출하면 이렇게 된다.

- 브라우저로 내려간 GB SafeData 키는 누구나 읽을 수 있고, 그 키는 우리 정부 API 할당량을 쓸 권한이다
- 호출량에 상한이 없고 data.go.kr 한도는 인증키 단위라, 트래픽 많은 대시보드 하나가 전체 이용자의 할당량을 소진시킬 수 있다
- 트래픽 감사나 조절이 불가능하다

**백엔드에서 프록시해야 한다.** 프런트는 자기 서버를 부르고, 그 서버가 GB SafeData 키를 들고 GB SafeData를 부른다. 그러면 `GBSAFE_CORS_ALLOW_ORIGINS`는 로컬 개발에만 필요해진다. CORS 설정은 개발 편의를 위한 것이고, 브라우저 직접 호출을 운영 구성으로 지원하려고 있는 게 아니다.

---

## 7. 응답 시간과 실패 동작

위험 조회 한 번은 여러 정부 API로 퍼진다. `GET /v1/hazard-types`가 그 구성을 보여준다.

```json
{
  "hazards": [
    { "value": "heavy_rain", "connectors": ["weather_warning", "weather_now", "weather_forecast", "river_level"] },
    { "value": "landslide", "connectors": ["landslide_forecast", "weather_warning", "weather_now", "landslide_roadside"] },
    { "value": "flood", "connectors": ["flood_forecast", "river_level", "weather_warning", "weather_now", "weather_forecast"] },
    { "value": "wildfire", "connectors": ["wildfire_risk", "weather_now", "air_quality"] },
    { "value": "earthquake", "connectors": ["weather_warning"] },
    { "value": "heatwave", "connectors": ["weather_warning", "weather_now"] },
    { "value": "other", "connectors": ["weather_warning", "weather_now"] }
  ]
}
```

이 서버에서 캐시가 더워진 상태로 `hazard=heavy_rain`을 세 지역 측정했을 때 0.83초, 0.58초, 0.64초였다. 원천을 새로 부르면 더 느리다. 정부 API가 느리거나 멈출 수 있고, 원천 호출 타임아웃 기본값이 20초에 재시도가 최대 3회다(`GBSAFE_HTTP_TIMEOUT_SECONDS`, `GBSAFE_HTTP_MAX_RETRIES`). **밀리초가 아니라 초 단위로 잡아야 한다.** 클라이언트 타임아웃은 30초 이상으로 두고, 5초로 잡아놓고 서비스가 고장났다고 하지 말자.

설계에 반영해야 할 동작이 두 가지다.

**원천은 각각 독립적으로 실패하고, 실패가 빈 성공으로 바뀌는 일은 없다.** 문경시 산사태 응답에서 커넥터 2개가 실패하고 2개가 성공했으며, 봉투는 레코드 11건을 반환하면서 `complete: false`로 부족분을 기록했다. 어느 원천이 어떻게 됐는지는 항상 알 수 있다.

**로딩 상태를 원천별로 두는 걸 권한다.** 커넥터가 각각 끝나므로 전역 스피너 하나는 그 정보를 버리는 것이다. `GET /v1/hazard-types`로 해당 재난이 어떤 커넥터를 부를지 미리 받아 커넥터마다 한 줄씩 대기 상태로 그리고, 영수증이 오는 대로 각 줄을 채운다. 레코드 있음, 확인된 없음, 사유 있는 실패. 사용자는 애매한 패널 하나가 아니라 "기상특보: 3건" 옆에 "산사태 예보: 조회 불가 (403)"을 보게 된다.

캐시와 결과 분류가 상호작용하는 점도 알아둘 만하다. 같은 `river_level` / 울릉군 조회가 새로 받아올 때는 `outcome: "confirmed_empty"`(`absence_confirmed: true`)였는데, 캐시에서 응답할 때는 `outcome: "failed"`에 `detail: "원천이 '해당 없음'을 명시하지 않았습니다 — 응답을 해석하지 못했을 수 있습니다"`, caveat에 `"캐시된 응답입니다 (호출 한도 보호)"`가 붙었다. 시스템이 틀린 확신 쪽이 아니라 "확인 못 함" 쪽으로 degrade하는 것이고 방향은 맞다. 다만 **같은 조회가 2번 상태와 3번 상태 사이를 오갈 수 있다는 뜻이다.** 화면 상태를 캐시하지 말고 응답마다 다시 계산해야 한다.

`GET /v1/health`는 커넥터 가용성을 알려주고, 인증키 부재·심의 대기·로컬 파일 필요를 구별한다.

```json
{
  "summary": {
    "connectors": 14, "available": 8, "blocked_by_credentials": 0,
    "pending_review": 4, "requires_local_file": 2, "offline_mode": false
  }
}
```

`pending_review`가 4개다. 산림청 403들과 기상청 API허브 AWS 자료다. 장애 원인을 추측하지 말고 이 엔드포인트로 설명하면 된다. 인증이 필요 없으니 헬스체크로도 쓸 수 있다.

---

## 8. 환경변수와 실행

### 로컬 실행

```bash
# .env를 채운 상태에서 저장소 루트에서
.venv/bin/python -m uvicorn gbsafe_api.app:app --port 8910
```

확인.

```console
$ curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8910/v1/health
200
```

문서는 `/docs`, ReDoc은 `/redoc`, 스키마는 `/openapi.json`(OpenAPI 3.1.0)이다. 여기서 다루지 않은 엔드포인트도 있다. `/v1/datasets`, `/v1/datasets/{id}`, `/v1/datasets/{id}/verify`, `/v1/datasets/{id}/citation`, `/v1/quality`, `/v1/regions`, `/v1/licenses`.

### 변수 목록

모두 `GBSAFE_` 접두사로 읽는다. 실제 값은 절대 커밋하지 않는다.

| 변수 | 기본값 | 용도 |
|---|---|---|
| `GBSAFE_DATA_GO_KR_SERVICE_KEY` | — | **필수.** data.go.kr 키. 대부분의 커넥터가 여기에 의존 |
| `GBSAFE_DATA_GO_KR_SERVICE_KEY_ENCODED` | — | URL 인코딩본. 일부 SDK가 요구할 때 |
| `GBSAFE_KMA_APIHUB_AUTH_KEY` | — | 기상청 API허브: 레이더 합성, AWS 1분자료 |
| `GBSAFE_HRFCO_SERVICE_KEY` | — | 한강홍수통제소: 실시간 수위, 홍수특보 |
| `GBSAFE_SAFETYDATA_SERVICE_KEY` | — | safetydata.go.kr |
| `GBSAFE_SAFEMAP_API_KEY` | — | safemap.go.kr (발급 시 등록한 도메인에 고정) |
| `GBSAFE_ITS_API_KEY` | — | ITS: 실시간 돌발·통제정보 |
| `GBSAFE_VWORLD_API_KEY` | — | VWorld |
| `GBSAFE_SGIS_CONSUMER_KEY` | — | SGIS |
| `GBSAFE_SGIS_CONSUMER_SECRET` | — | SGIS |
| `GBSAFE_STORE_DIR` | `var/gbsafe` | 스냅샷·캐시 저장 위치 |
| `GBSAFE_CACHE_TTL_FACTOR` | `1.0` | 캐시 TTL 배수 (`1.0` = 데이터셋 갱신주기 그대로) |
| `GBSAFE_OFFLINE` | `false` | 저장된 스냅샷만 사용. 오프라인 시연·훈련용 |
| `GBSAFE_HTTP_TIMEOUT_SECONDS` | `20.0` | 원천 호출 타임아웃 |
| `GBSAFE_HTTP_MAX_RETRIES` | `3` | 원천 재시도 상한 |
| `GBSAFE_CORS_ALLOW_ORIGINS` | `""` (꺼짐) | 허용할 브라우저 origin, 쉼표 구분 |
| `GBSAFE_API_KEYS` | `""` (꺼짐) | 허용할 API 키, 쉼표 구분 |
| `GBSAFE_CATALOG_DIR` | 자동 | 데이터셋 카탈로그 디렉터리 재지정 |

인증키가 없으면 데이터를 지어내지 않는다. 해당 커넥터가 스스로 unavailable로 보고하고, `/v1/health`에 사유가 남고, 위험 조회는 `complete: false`로 degrade한다. 시작하려고 모든 키가 필요한 건 아니다. 다만 키가 일부만 있으면 [3번 상태](#3-세-가지-상태-ui-계약)를 계속 만나게 되므로, 그 렌더링은 제대로 만들어야 한다.

### 운영 체크리스트

- `GBSAFE_API_KEYS` 설정, 소비자별로 다른 키 발급
- 브라우저에 직접 노출하지 않고 백엔드에서 프록시
- 헬스체크는 `/v1/health`
- 클라이언트 타임아웃 30초 이상
- 모든 데이터 화면은 `records.length`가 아니라 `toViewState()`를 통과
- REST면 받아온 system_prompt를 전송, MCP면 `instructions`가 모델까지 가는지 확인
- "확인 못 함" 상태가 안심되게 보이지 않는지 확인. 다른 사람에게 보여주고 무슨 뜻으로 읽히는지 물어볼 것
