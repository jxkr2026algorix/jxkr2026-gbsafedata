# Integrating GB SafeData into a dashboard with a chatbot

Everything below was run against a live server at `http://127.0.0.1:8910` (GB SafeData API 0.1.0). Commands, URLs and JSON are copied from real responses. Korean text in the responses is reproduced as-is — the API answers in Korean.

If you read only one section, read [section 3](#3-the-three-state-ui-contract). It is the part that will bite you.

---

## 1. What this service is, and is not

GB SafeData is a read-only aggregator over Korean public disaster-data APIs, scoped to Gyeongsangbuk-do (경상북도). You ask it "what is the current landslide situation in 문경시" and it fans out to the relevant government APIs, normalises the results, and tells you where every number came from and when.

It is:

- read-only
- scoped to the 22 cities and counties of Gyeongsangbuk-do
- sourced from public government APIs (기상청, 산림청, 한강홍수통제소, 환경공단, 국립중앙의료원)
- explicit about what it could not read

It does not, and has no endpoint to:

- place phone calls or send messages
- issue evacuation orders or approve evacuation plans
- change resident records or any other state
- identify individual residents

The server states this itself:

```console
$ curl -s http://127.0.0.1:8910/
{"name":"GB SafeData API","version":"0.1.0","docs":"/docs","openapi":"/openapi.json","read_only":true,
 "note":"이 API는 조회만 제공합니다. 전화·대피명령·상태변경 기능이 없습니다."}
```

Decisions stay with the human officer. Your dashboard presents evidence; it must not present conclusions.

---

## 2. Two integration paths, and how to choose

| Your LLM | Path | Work involved |
|---|---|---|
| Upstage Solar, OpenAI chat completions, Anthropic messages | REST: `GET /v1/tools` + `GET /v1/tools/{name}` | Write a tool-calling loop (~40 lines) |
| OpenAI Responses API, or any MCP-capable client | MCP: `POST /mcp/` | Configure a URL |

Pick by what your model speaks, not by preference.

**Solar cannot use the MCP path** unless you write an MCP client yourself. Solar speaks OpenAI-compatible chat completions with `tools=`, so the REST path is strictly less work there. Conversely, if you are on the OpenAI Responses API, the MCP path needs a URL and nothing else — writing a REST loop would be wasted effort.

Both paths execute the same 11 tools over the same data. The only differences are transport and two key names (see [section 3](#the-two-surfaces-differ-in-one-key-name)).

### 2a. REST path — chat-completions clients

Two endpoints. `GET /v1/tools` returns OpenAI-shaped function schemas; `GET /v1/tools/{name}` executes one.

```console
$ curl -s http://127.0.0.1:8910/v1/tools | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['tools']))"
11
```

The response is `{"tools": [...], "invoke": ..., "note": ...}`. Pass `tools` straight through to your model. `invoke` is `"GET /v1/tools/{name}?<인자>"`. `note` says the tool output must not be handed to the user without the system prompt applied.

One tool schema, verbatim from the live response:

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

Arguments go in the query string. Both a name and a 5-digit code work for `region`:

```console
$ curl -s -G http://127.0.0.1:8910/v1/tools/gbsafe_hazard_context \
    --data-urlencode "region=문경시" --data-urlencode "hazard=landslide"
```

The 11 tools:

| Tool | Required args | Purpose |
|---|---|---|
| `gbsafe_search_datasets` | — | Search the dataset catalogue |
| `gbsafe_describe_dataset` | `dataset_id` | Dataset detail, including how to obtain it |
| `gbsafe_verify_dataset` | `dataset_id` | Check whether an operation is licensed |
| `gbsafe_cite_dataset` | `dataset_id` | Citation text for a dataset |
| `gbsafe_resolve_region` | `region` | Region name → code, grid, weather station |
| `gbsafe_hazard_context` | `region` | Current hazard picture, fanned out over sources |
| `gbsafe_list_sources` | — | Available connectors |
| `gbsafe_fetch_source` | `source` | One connector directly |
| `gbsafe_data_health` | — | Which sources are up, and why others are not |
| `gbsafe_quality_report` | — | Known dataset defects |
| `gbsafe_population_guidance` | `purpose` | Permitted uses of population data |

An unknown name gives a 404 that lists all of them:

```console
$ curl -s http://127.0.0.1:8910/v1/tools/does_not_exist
{"detail":{"error":"'does_not_exist' 도구가 없습니다","available":["gbsafe_search_datasets", ...]}}
```

#### Python — complete loop

```python
import json
import urllib.parse
import urllib.request

from openai import OpenAI  # Solar is OpenAI-compatible

GBSAFE = "http://127.0.0.1:8910"
GBSAFE_KEY = None  # set if GBSAFE_API_KEYS is configured

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


# Fetch both at startup. Do not hardcode either one.
tools = _get("/v1/tools")["tools"]
system_prompt = _get("/v1/agent/system-prompt")["system_prompt"]


def ask(question: str) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    for _ in range(6):  # bound the loop
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

    return "Tool loop did not converge."


print(ask("문경시 산사태 위험 상황 알려줘"))
```

Two things that matter here. Feed the tool result back **whole** — `warnings`, `sources_checked` and `caveats` are what stop the model from inventing reassurance. And send the fetched system prompt; without it the loop is unsafe (see [section 5](#5-the-system-prompt-is-mandatory)).

#### TypeScript — same loop

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

  return "Tool loop did not converge.";
}
```

### 2b. MCP path — MCP-native clients

`POST /mcp/` speaks MCP Streamable HTTP. **Note the trailing slash** — `/mcp` answers `307 Temporary Redirect` to `/mcp/`, and clients that do not follow redirects on POST will appear to hang or fail.

```console
$ curl -s -i -X POST http://127.0.0.1:8910/mcp \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize", ...}'
HTTP/1.1 307 Temporary Redirect
location: /mcp/
```

Send `Accept: application/json, text/event-stream`. Real `initialize` exchange:

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

**`initialize` also returns `instructions`.** That field carries the same safety rules as the system prompt: cite sources, treat `complete: false` as incomplete, do not present stale data as current, do not infer individuals from aggregates, do not make evacuation decisions. Most MCP clients surface `instructions` to the model automatically — confirm yours does. If it does not, fetch `GET /v1/agent/system-prompt` and inject it manually.

`tools/list` returns the same 11 tools, with MCP annotations (`readOnlyHint: true`, `destructiveHint: false`). `tools/call` returns the envelope as JSON inside `result.content[0].text`:

```console
$ curl -s -X POST http://127.0.0.1:8910/mcp/ \
    -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"gbsafe_hazard_context","arguments":{"region":"문경시","hazard":"landslide"}}}'
```

Observed: `result` has keys `content` and `isError`; `content[0].type` is `"text"`; parsing that text yields the tool envelope with `complete: false`, `absence_confirmed: false`. There is no `structuredContent` — parse `content[0].text`.

For OpenAI Responses API, that is the whole integration:

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

The server must be reachable from OpenAI, so this needs a deployed host, not `127.0.0.1`.

---

## 3. The three-state UI contract

**This is the section that matters.** Every data response carries `complete` and `absence_confirmed`. A dashboard that renders two states — data, or no data — is wrong and dangerous.

Render three:

| Condition | State | Appearance |
|---|---|---|
| `records.length > 0` | data | Show the records |
| `complete && absence_confirmed && records.length === 0` | verified empty | "Nothing currently in effect" — may look reassuring |
| everything else | unverified | "Could not verify" — **must not look reassuring** |

In state 3, list the sources that failed. Filter the receipts for `outcome === "failed"` and show each `connector` with its `detail`.

### Why: 문경시 landslide, right now

Two of the four sources for landslide are Korea Forest Service APIs returning HTTP 403, because the data.go.kr application is pending review. Live:

```console
$ curl -s -G http://127.0.0.1:8910/v1/hazards/context \
    --data-urlencode "region=문경시" --data-urlencode "hazard=landslide"
```

Abridged to the fields that decide the rendering — `records` and `citations` omitted:

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

Read what happened. Eleven records came back — but not one of them is landslide data. They are weather warnings and current observations. **Both actual landslide sources returned nothing because both are locked behind HTTP 403.** `complete: false` and `absence_confirmed: false` are the API telling you: the landslide question was not answered.

A two-state dashboard renders a green "no landslide risk" tile here, because it found no landslide records. That is presenting an outage as safety. If a slope fails, the tile was still green — and nobody was told the data was never checked.

The correct rendering: "Landslide risk could not be verified — 산림청 산사태 예측정보 and 산림청 도로변 산사태 정보 are both returning HTTP 403 (application pending review)."

### The contrast: what a verified empty looks like

Same endpoint shape, genuinely empty, and safe to show as such:

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

`outcome: "confirmed_empty"`, so `complete` and `absence_confirmed` are both `true`. `records` is empty and that emptiness is a fact, not a gap. Even so, read the caveat: it says there are no gauging stations in 울릉군, and that this means water level is unknown, not that the rivers are safe. Show state 2 as "no data currently in effect" — never as "safe".

`outcome` has exactly three values: `records`, `confirmed_empty`, `failed`. Only `confirmed_empty` licenses an empty read.

One more failure mode worth knowing: an unresolvable region gives `complete: false` with an **empty** `receipts` array — the reason lives in `degradations`:

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

Note this returns **HTTP 200**. Do not rely on status codes to detect this; branch on `complete` and `absence_confirmed`. If your "unverified" branch only renders `failed` receipts, this case shows an empty explanation — fall back to `degradations` when `receipts` is empty.

### The two surfaces differ in one key name

| | REST (`/v1/hazards/context`, `/v1/sources/{c}`) | Tool / MCP (`/v1/tools/{name}`) |
|---|---|---|
| Per-source results | `receipts` | `sources_checked` |
| Also present | `citations`, `generated_at`, `modes` | `citations`, `warnings`, `how_to_cite` |

Identical shape, same underlying data. `complete`, `absence_confirmed`, `records`, `caveats` and `degradations` are on both. Read whichever key exists.

The tool surface additionally pre-composes `warnings` for the model:

```json
"warnings": [
  "[조회 실패] 15074800: HTTP 403 — 인증 실패 — 「산림청 산사태 예측정보」은 개발단계가 심의승인 대상입니다. ...",
  "[조회 실패] 15074812: HTTP 403 — 인증 실패 — 「산림청 도로변 산사태 정보」은 개발단계가 심의승인 대상입니다. ...",
  "1건이 오래된 자료입니다 — 판단 근거로 제시할 때 시점을 함께 밝히세요",
  "일부 원천을 조회하지 못했습니다. 결과가 비어 있어도 '위험 없음'을 의미하지 않습니다",
  "조회하지 못한 원천: landslide_forecast, landslide_roadside — 이 원천이 다루는 위험은 확인되지 않았습니다"
]
```

Pass these to the model verbatim, and consider showing them in the UI too.

### Ready-to-paste rendering function

Types are in `packages/gbsafe-api/types/gbsafe.d.ts`.

```ts
import type {
  GbSafeEnvelope,
  GbSafeSourceReceipt,
  GbSafeViewState,
} from "./gbsafe";

/** Reads `receipts` (REST) or `sources_checked` (tool/MCP). */
export function getReceipts(envelope: GbSafeEnvelope): GbSafeSourceReceipt[] {
  return "receipts" in envelope ? envelope.receipts : envelope.sources_checked;
}

/**
 * The only correct way to classify a GB SafeData response for display.
 * Never branch on `records.length` alone.
 */
export function toViewState(envelope: GbSafeEnvelope): GbSafeViewState {
  const caveats = envelope.caveats ?? [];

  if (envelope.records.length > 0) {
    return { kind: "data", records: envelope.records, caveats };
  }

  if (envelope.complete && envelope.absence_confirmed) {
    return { kind: "confirmed-empty", caveats };
  }

  // Empty and unverified. Explain what could not be read.
  const failed = getReceipts(envelope).filter((r) => r.outcome === "failed");

  // A region that could not be resolved yields zero receipts; the reason is in
  // `degradations`. Synthesise a receipt so the UI always has something to show.
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

And the render. The important part is that `unverified` shares no styling with `confirmed-empty`:

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
      // Safe to show calmly: every source answered, and the answer was "none".
      return (
        <section className="state-none-in-effect">
          <p>Nothing currently in effect.</p>
          <p className="muted">All sources responded. Absence is verified.</p>
          <CaveatList caveats={state.caveats} />
        </section>
      );

    case "unverified":
      // MUST NOT look reassuring. No green, no checkmark, no "all clear".
      return (
        <section className="state-unverified" role="alert">
          <p>
            <strong>Could not verify.</strong> This is not a statement that
            there is no hazard — some sources could not be read.
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

Give `.state-unverified` the visual weight of a warning. If a reviewer glancing at the dashboard could mistake it for "fine", it is wrong.

---

## 4. Per-record rules the UI must honour

Three fields on each record change how it may be displayed. Dropping them silently converts qualified data into unqualified claims.

### `freshness.usable_for_decision === false` → show the timestamp

A stale record from the 문경시 landslide response:

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

24 hours old against a 1-hour cycle. Never render this as the current situation. Show the value with `freshness.as_of` beside it; `freshness.reason` is display-ready Korean. `status` is one of `fresh`, `aging`, `stale`, `unknown` — but branch on `usable_for_decision`, which is the computed verdict.

### `source.mode === "synthetic"` → keep the drill label

`mode` is `real`, `snapshot`, or `synthetic`. `synthetic` is drill/training data. It must remain visibly labelled wherever it appears — a synthetic record that looks real is a serious failure. All records observed on this server were `mode: "real"`; the envelope also reports `modes: ["real"]`. Handle `synthetic` anyway, since drill mode can be switched on.

### `caveats` and `notes` → render, never drop

`caveats` (envelope) and `notes` (per record) carry qualifications that change what a number means.

The clearest case is weather-station distance. 영양군's nearest station is 27 km away, in another county:

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

Localised downpours differ substantially over 27 km. Presenting that reading as 영양군's own measurement is a misrepresentation, and the caveat says so. Check `asos_station_detail.is_local`; when `false`, show `distance_km`. Compare 문경시, where the station is 5.6 km away and `is_local: true`.

Other real caveats you will receive:

- `"발표관서 단위 특보입니다 — 관할 구역 전체가 대상이며 특정 마을 상태가 아닙니다"` — the warning covers a whole meteorological office jurisdiction, not one village
- `"타 지역 특보 86건을 제외했습니다"` — rows for other regions were filtered out
- `"캐시된 응답입니다 (호출 한도 보호)"` — served from cache to protect rate limits
- `"울릉군에는 홍수통제소 수위관측소가 없습니다 — 수위 정보가 없는 것이지 하천이 안전한 것이 아닙니다"` — no gauge, therefore unknown, not safe
- `"'flood_forecast'는 지역 지정을 받지 않습니다 — region='문경시'이 적용되지 않았고 결과는 더 넓은 범위입니다"` — **your region filter was not applied**; the result is wider than you asked for

That last one is easy to miss and materially changes interpretation. Render caveats near the data, not in a collapsed panel.

`notes` is per record, e.g. `["강수형태: 없음"]` decoding a raw `0.0` in the `precipitation_type` payload. Without it the number is meaningless.

`quality_flags` carries defects confirmed by validation (`missing_coordinates`, `encoding_cp949`, `partial_response`, and others). Empty on the records observed here; surface it when present.

---

## 5. The system prompt is mandatory

Fetch it at runtime. Do not paste it into your codebase, or it will go stale:

```console
$ curl -s http://127.0.0.1:8910/v1/agent/system-prompt
{"system_prompt":"당신은 경상북도 재난 상황에서 공공데이터를 조회해 근거를 제시하는 도우미입니다.\n...","source":"skills/gb-safedata/SKILL.md"}
```

Two fields: `system_prompt` (send as your system message) and `source` (provenance).

It encodes eight rules. Rule 1 is the one that earns its place: do not report unverified absence. Also: cite every number, distinguish forecast from observation, disclose staleness, keep synthetic data labelled, never infer individuals from aggregate statistics, never decide evacuations, never hide observation-station distance.

**The failure it prevents.** Hand a model an empty result caused by an HTTP 403 and ask "is there landslide risk in 문경시". The model sees no landslide records. Being helpful is its default, so it answers "there is no landslide risk in 문경시." That sentence is false — nothing was checked — and it is the most dangerous thing this system could emit. The prompt instructs the model to answer "확인되지 않았습니다" (could not be verified) and name the sources that failed instead.

The prompt also holds under pressure. It states that even when the user pushes with "so is it dangerous or not", answering "I don't know" is better than false reassurance.

The tools carry the same warning in their own descriptions, and `GET /v1/tools` returns a `note` saying so:

```
"도구 출력을 그대로 사용자에게 전달하면 안 됩니다. system_prompt를 함께 적용해야
 조회 실패가 '위험 없음'으로 읽히지 않습니다 — GET /v1/agent/system-prompt"
```

On the MCP path, `initialize` returns equivalent rules in `instructions`. Verify your client passes them to the model; inject the system prompt manually if not.

---

## 6. Auth and CORS

Both are **off by default**. An unconfigured server accepts unauthenticated requests from anywhere.

### API keys

Set `GBSAFE_API_KEYS` to a comma-separated list. Whitespace is trimmed.

```bash
GBSAFE_API_KEYS=key1,key2
```

Both header forms work:

```console
$ curl -s -o /dev/null -w "%{http_code}\n" -H "x-api-key: key1" http://127.0.0.1:8911/v1/tools
200
$ curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer key2" http://127.0.0.1:8911/v1/tools
200
```

Missing or wrong key gives `401`:

```console
$ curl -s -i http://127.0.0.1:8911/v1/tools
HTTP/1.1 401 Unauthorized
content-type: application/json

{"error":"API 키가 필요합니다","how":"x-api-key 헤더 또는 Authorization: Bearer <키>"}
```

These paths stay open so health checks and debugging keep working — verified with auth enabled and no key supplied:

```console
$ for U in /v1/health /docs /openapi.json / /redoc; do ...; done
  /v1/health     200
  /docs          200
  /openapi.json  200
  /              200
  /redoc         200
```

Point your load balancer at `/v1/health`. Everything else needs a key — including `/mcp/`, which returns `401` without one (verified). `OPTIONS` requests are also exempt, so CORS preflight works.

### CORS

```bash
GBSAFE_CORS_ALLOW_ORIGINS=https://your.dashboard
```

Comma-separated for multiple origins. When unset, the CORS middleware is not installed and no CORS headers are sent. When set, the server allows `GET, POST, OPTIONS`, any request header, and **not** credentials. Verified preflight:

```console
$ curl -s -i -X OPTIONS http://127.0.0.1:8911/v1/tools \
    -H "Origin: https://dash.example.org" -H "Access-Control-Request-Method: GET"
HTTP/1.1 200 OK
vary: Origin
access-control-allow-methods: GET, POST, OPTIONS
access-control-max-age: 600
access-control-allow-origin: https://dash.example.org
```

Because credentials are disabled, cookie-based auth from the browser will not work. Use the `x-api-key` header.

### The browser must not call this API directly in production

GB SafeData calls government APIs using **our** credentials (`GBSAFE_DATA_GO_KR_SERVICE_KEY` and others). Exposing the service to browsers means:

- any GB SafeData API key shipped to the browser is readable by anyone, and it authorises use of our government quota
- request volume is unbounded, and data.go.kr rate limits are per-credential — a busy dashboard can exhaust the quota for every consumer
- you cannot audit or shape traffic

**Proxy through your backend.** Your frontend calls your server; your server holds the GB SafeData key and calls GB SafeData. Then `GBSAFE_CORS_ALLOW_ORIGINS` is only needed for local development. The CORS setting exists to make development convenient, not to make browser-direct a supported production design.

---

## 7. Latency and failure behaviour

One hazard query fans out to several upstream government APIs. `GET /v1/hazard-types` shows the fan-out:

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

Measured on this server, warm cache, three regions with `hazard=heavy_rain`: 0.83 s, 0.58 s, 0.64 s. Cold upstream calls are slower — a government API can be slow or hang, and the per-request HTTP timeout defaults to 20 s with up to 3 retries (`GBSAFE_HTTP_TIMEOUT_SECONDS`, `GBSAFE_HTTP_MAX_RETRIES`). **Budget seconds, not milliseconds.** Set client timeouts at 30 s or more; do not use a 5 s timeout and call the service broken.

Two behaviours to build around.

**Sources fail independently, and a failure never becomes an empty success.** In the 문경시 landslide response two connectors failed and two succeeded; the envelope still returned 11 records, with `complete: false` recording the shortfall. You always learn per-source what happened.

**Recommend a per-source loading state.** Since each connector resolves separately, a single global spinner throws that away. Call `GET /v1/hazard-types` to learn which connectors a hazard will consult, render one row per connector as pending, then fill each row from the receipts: records, verified empty, or failed with a reason. The user sees "산사태 예보: unavailable (403)" next to "기상특보: 3 records" instead of one ambiguous panel.

Also worth knowing: caching interacts with outcome classification. The same `river_level` / 울릉군 query returned `outcome: "confirmed_empty"` (`absence_confirmed: true`) when freshly fetched, and `outcome: "failed"` with `detail: "원천이 '해당 없음'을 명시하지 않았습니다 — 응답을 해석하지 못했을 수 있습니다"` when served from cache (caveat `"캐시된 응답입니다 (호출 한도 보호)"`). The system degrades toward "could not verify" rather than toward false confidence — which is the right direction, but it means **the same query can move between state 2 and state 3**. Do not cache the view state on your side; re-derive it from each response.

`GET /v1/health` reports connector availability, and distinguishes causes — missing credentials, review pending, requires a local file:

```json
{
  "summary": {
    "connectors": 14, "available": 8, "blocked_by_credentials": 0,
    "pending_review": 4, "requires_local_file": 2, "offline_mode": false
  }
}
```

Four connectors are `pending_review` — the 산림청 403s and the KMA API Hub AWS feed. Use this endpoint to explain outages instead of guessing. It is unauthenticated, so it is also your health check.

---

## 8. Environment variables, and running it

### Running locally

```bash
# from the repository root, with a populated .env
.venv/bin/python -m uvicorn gbsafe_api.app:app --port 8910
```

Verify:

```console
$ curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8910/v1/health
200
```

Interactive docs at `/docs`, ReDoc at `/redoc`, schema at `/openapi.json` (OpenAPI 3.1.0). Endpoints beyond those covered here: `/v1/datasets`, `/v1/datasets/{id}`, `/v1/datasets/{id}/verify`, `/v1/datasets/{id}/citation`, `/v1/quality`, `/v1/regions`, `/v1/licenses`.

### Variables

All read with the prefix `GBSAFE_`. Never commit real values.

| Variable | Default | Purpose |
|---|---|---|
| `GBSAFE_DATA_GO_KR_SERVICE_KEY` | — | **Required.** data.go.kr key; drives most connectors |
| `GBSAFE_DATA_GO_KR_SERVICE_KEY_ENCODED` | — | URL-encoded variant, if your SDK needs it |
| `GBSAFE_KMA_APIHUB_AUTH_KEY` | — | KMA API Hub: radar composite, 1-minute AWS |
| `GBSAFE_HRFCO_SERVICE_KEY` | — | Han River Flood Control Office: river level, flood warnings |
| `GBSAFE_SAFETYDATA_SERVICE_KEY` | — | safetydata.go.kr |
| `GBSAFE_SAFEMAP_API_KEY` | — | safemap.go.kr (bound to the domain registered at issue time) |
| `GBSAFE_ITS_API_KEY` | — | ITS: real-time incidents and road closures |
| `GBSAFE_VWORLD_API_KEY` | — | VWorld |
| `GBSAFE_SGIS_CONSUMER_KEY` | — | SGIS |
| `GBSAFE_SGIS_CONSUMER_SECRET` | — | SGIS |
| `GBSAFE_STORE_DIR` | `var/gbsafe` | Snapshot and cache directory |
| `GBSAFE_CACHE_TTL_FACTOR` | `1.0` | Cache TTL multiplier (`1.0` = the dataset's own cycle) |
| `GBSAFE_OFFLINE` | `false` | Serve only stored snapshots — offline demos and drills |
| `GBSAFE_HTTP_TIMEOUT_SECONDS` | `20.0` | Per-request upstream timeout |
| `GBSAFE_HTTP_MAX_RETRIES` | `3` | Upstream retry ceiling |
| `GBSAFE_CORS_ALLOW_ORIGINS` | `""` (off) | Comma-separated browser origins |
| `GBSAFE_API_KEYS` | `""` (off) | Comma-separated accepted API keys |
| `GBSAFE_CATALOG_DIR` | auto | Override the dataset catalogue directory |

A missing credential does not fake data. The affected connector reports itself unavailable, `/v1/health` records the reason, and hazard responses degrade to `complete: false`. You do not need every key to start — but you must render [state 3](#3-the-three-state-ui-contract) correctly, because with a partial key set you will hit it constantly.

### Production checklist

- Set `GBSAFE_API_KEYS`; issue a distinct key per consumer
- Do not expose the service to browsers; proxy through your backend
- Point health checks at `/v1/health`
- Set client timeouts to 30 s or more
- Route every data view through `toViewState()`, never `records.length`
- Send the fetched system prompt (REST) or verify `instructions` reaches the model (MCP)
- Confirm your "could not verify" state does not look reassuring — have someone else glance at it and tell you what it means
