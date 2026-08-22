# GB SafeData

**English** · [한국어](README.ko.md)

[![CI](https://img.shields.io/github/actions/workflow/status/jxkr2026algorix/jxkr2026-gbsafedata/ci.yml?branch=main&label=CI&logo=githubactions&logoColor=white)](https://github.com/jxkr2026algorix/jxkr2026-gbsafedata/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-814%20passing-brightgreen)](tests)
[![live APIs](https://img.shields.io/badge/live%20APIs-6%20connected-0a7bbb)](scripts/smoke_live_apis.py)
[![python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue?logo=python&logoColor=white)](pyproject.toml)
[![uv](https://img.shields.io/badge/uv-managed-261230?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![ruff](https://img.shields.io/badge/ruff-checked-261230?logo=ruff&logoColor=white)](https://docs.astral.sh/ruff/)
[![MCP](https://img.shields.io/badge/MCP-12%20read--only%20tools-000000)](docs/mcp.md)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Public disaster data for Gyeongsangbuk-do, South Korea, served to AI agents and administrative systems **with its provenance attached**.

One monorepo: an MCP server, a REST API, a normalisation layer, dataset search and licence verification, an AI skill, and MCP client configs.

---

## How you can use it

It is deployed. Everything below is a live address or a command that works now.

```
https://datainfra.salgil.gyeongbuk.kr
```

### 1. MCP server — no credential

Twelve tools an AI calls to look up disaster data, citing its sources in the answer.

```
https://datainfra.salgil.gyeongbuk.kr/mcp/
```

**No credential is needed.** The server holds the government keys and reads only
published aggregate data. Every tool declares `readOnlyHint`, so an AI cannot
change or delete anything through it.

All twelve tools are read-only.

`gbsafe_search_datasets` · `gbsafe_describe_dataset` · `gbsafe_verify_dataset` · `gbsafe_cite_dataset` · `gbsafe_resolve_region` · `gbsafe_hazard_context` · `gbsafe_hazard_capabilities` · `gbsafe_list_sources` · `gbsafe_fetch_source` · `gbsafe_data_health` · `gbsafe_quality_report` · `gbsafe_population_guidance`

Keep the trailing slash. `/mcp` redirects with a 307, which not every client
follows on a POST.

### 2. Plugin — one click

**[▶ Add to Claude](https://datainfra.salgil.gyeongbuk.kr/add-claude)**

Opens the dialog with the name and URL filled in. Confirm and you are done, and
it works on the free plan — on claude.ai, desktop, and mobile alike.

**ChatGPT** needs developer mode and a paid plan and is web only, so it has its
own walkthrough → **[docs/chatgpt.md](docs/chatgpt.md)**

Claude Code, Cursor, VS Code, and opencode are in
[docs/connect.md](docs/connect.md).

### 3. Open-source skill — one line

MCP gives an agent *what it can do*. The skill gives it *how to do it safely* —
never report an absence you did not verify, never present a forecast as an
observation, never infer an individual from aggregate statistics, never decide
an evacuation.

```bash
npx skills add jxkr2026algorix/jxkr2026-gbsafedata
```

It detects opencode, Claude Code, Codex, Cursor, and others. Source:
[`skills/gb-safedata`](skills/gb-safedata).

### 4. Standard API — for systems that never heard of MCP

Plain HTTP. Every data response uses one envelope.

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
      "detail": "HTTP 403 — development-stage review pending" }
  ],
  "complete": false,
  "absence_confirmed": false
}
```

**Read `absence_confirmed` before drawing anything from an empty `records`.**
Conflate the two and a failed lookup reads as safety.

41 schemas and the full route list:
[`/docs`](https://datainfra.salgil.gyeongbuk.kr/docs) · [docs/api.md](docs/api.md).
The UI contract is in [docs/handoff.md](docs/handoff.md).

### 5. Normalisation layer

What each agency does differently, reconciled.

| Scattered | Unified |
| --- | --- |
| Municipality spellings, former names, transferred districts | 5-digit administrative code + representative coordinate |
| Lat/lon, KMA forecast grid, ASOS station id | one region, converted between all three (`gbsafe_resolve_region`) |
| Per-agency timestamps, missing time zones | UTC, with a freshness verdict |
| Missing-value markers (`-`, `-999`, empty string) | detected from real responses, never read as zero |
| Per-agency licence wording | KOGL codes, then a ruling on what you may do |

### 6. Search, verification, citation

```bash
uv run gbsafe search 산사태 --ready                # only what is callable now
uv run gbsafe verify 15074800 --operation derive  # may I transform this?
uv run gbsafe cite 15084084                       # attribution for a report
uv run gbsafe doctor                              # per-source status and cause
```

`verify` enforces rather than documents. KOGL Types 3 and 4 forbid modification,
which covers reprojection, clipping, joins, and derived labels.

---

## As a service, or self-hosted

### As a service (recommended)

Use the addresses above. Nothing to install, no credential, no signup — the
server handles government API calls, key custody, caching, and quota protection.

Calling it straight from a browser is discouraged: it queries sources with our
government credentials, so proxy it through your own backend.

### Self-hosted

If you need data sovereignty or want to run it on your own credentials:

```bash
git clone https://github.com/jxkr2026algorix/jxkr2026-gbsafedata
cd jxkr2026-gbsafedata
uv sync --all-packages

cp .env.example .env       # add GBSAFE_DATA_GO_KR_SERVICE_KEY
uv run gbsafe doctor       # what works, and why the rest does not
uv run gbsafe serve        # http://127.0.0.1:8000/docs
```

Or in a container:

```bash
docker compose up
```

**With no credential at all**, catalogue search, verification, citation, and
region resolution still work.

Server deployment (Caddy, Cloudflare, containers) is in
[deploy/README.md](deploy/README.md); per-client setup is in
[docs/install.md](docs/install.md).

---

## What it looks like when it works

Asked pointedly whether Mungyeong has landslide risk, an AI client with this stack **declined to answer**:

> Mungyeong's landslide risk is unconfirmed. I can say neither "there is risk" nor "there is none."
>
> Both primary sources failed. Answering "no risk" would substitute safety for a lookup failure, so I won't.
>
> **Could not check:** Korea Forest Service Landslide Prediction (15074800) — HTTP 403, development-stage review pending. Roadside Landslide Information (15074812) — same cause.

That is the entire point. The system is built so an agent cannot mistake an outage for good news.

## Connected sources

One `data.go.kr` development key drives most of these. The two flood-control sources use a separate HRFCO key, and that key is bound to the URL you register it against — it returns code 940 anywhere else.

| Connector | Dataset | Status |
| --- | --- | --- |
| `weather_now` | KMA ultra-short nowcast | live |
| `weather_forecast` | KMA short-term forecast | live |
| `weather_warning` | KMA weather advisories | live |
| `wildfire_risk` | KFS wildfire risk index | live |
| `emergency_beds` | Real-time emergency beds | live |
| `air_quality` | AirKorea | live (500/day) |
| `river_level` | HRFCO river gauges + gazetted alert levels | live |
| `flood_forecast` | HRFCO flood alerts in effect | live |
| `aws_observation` | KMA AWS minute observations (API Hub) | live |
| `landslide_forecast` | KFS landslide prediction | review pending |
| `landslide_roadside` | KFS roadside landslide | review pending |
| `landslide_history` | KFS past landslides | review pending |
| `shelters` | Shelter standard data | manual CSV |
| `landslide_zones` | Landslide risk zones (Mungyeong) | manual CSV |

The three landslide APIs have it backwards from everything else: **development-stage access requires review, operational access is automatic.** Approval time is not published. A new key won't fix it, so `doctor` names that specific cause rather than reporting a generic failure.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Layers, data flow, and why the boundaries sit where they do |
| [docs/api.md](docs/api.md) | Endpoint reference (generated from the OpenAPI spec) |
| [docs/mcp.md](docs/mcp.md) | Tool reference (generated from the definitions) |
| [docs/safety.md](docs/safety.md) | Each boundary and the mechanism enforcing it |
| [docs/install.md](docs/install.md) | Per-harness setup, credentials, and troubleshooting |
| [docs/rationale.md](docs/rationale.md) | **Why it is built this way — the survey, the design, the testing** |
| [docs/chatgpt.md](docs/chatgpt.md) | **Connecting it to ChatGPT, step by step** |
| [docs/connect.md](docs/connect.md) | **Connecting it to Claude, ChatGPT, or a coding agent** |
| [docs/handoff.md](docs/handoff.md) | **Deployed instance, and how another team wires it in** |
| [docs/pitch-differentiation.md](docs/pitch-differentiation.md) | Measured comparison against a naive integration |
| [docs/data-sources.md](docs/data-sources.md) | Per-agency acquisition, quirks, and known defects |

## Licence

Code and documentation are [Apache-2.0](LICENSE).

**No source data is committed here.** The flood series is KOGL Type 4 — non-commercial and no derivatives — and a non-commercial restriction cannot coexist with an OSI licence, which may not discriminate against fields of use. OpenStreetMap is ODbL, and share-alike spreads: merge OSM with government data into one artefact and the attribution-only data inherits share-alike.

So this repository ships **acquisition methods and verified metadata** instead. Terms differ per dataset; `gbsafe verify` will tell you what a given one permits.

## Related

- [jxkr2026-datasets](https://github.com/jxkr2026algorix/jxkr2026-datasets) — the survey behind the catalogue: what each source actually returns, how to obtain it, and where the portal is wrong
