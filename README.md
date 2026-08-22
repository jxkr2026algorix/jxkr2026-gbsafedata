# GB SafeData

**English** · [한국어](README.ko.md)

[![CI](https://img.shields.io/github/actions/workflow/status/jxkr2026algorix/jxkr2026-gbsafedata/ci.yml?branch=main&label=CI&logo=githubactions&logoColor=white)](https://github.com/jxkr2026algorix/jxkr2026-gbsafedata/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-573%20passing-brightgreen)](tests)
[![live APIs](https://img.shields.io/badge/live%20APIs-6%20connected-0a7bbb)](scripts/smoke_live_apis.py)
[![python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue?logo=python&logoColor=white)](pyproject.toml)
[![uv](https://img.shields.io/badge/uv-managed-261230?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![ruff](https://img.shields.io/badge/ruff-checked-261230?logo=ruff&logoColor=white)](https://docs.astral.sh/ruff/)
[![MCP](https://img.shields.io/badge/MCP-11%20read--only%20tools-000000)](docs/mcp.md)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Public disaster data for Gyeongsangbuk-do, South Korea, served to AI agents and administrative systems **with its provenance attached**.

One monorepo: an MCP server, a REST API, a normalisation layer, dataset search and licence verification, an AI skill, and MCP client configs.

---

## The problem this solves

The data is already public. That is not the same as usable.

We queried 91 datasets on `data.go.kr` and called every API that would answer. What we found:

- **33 of 60 open APIs don't serve data.** The portal is a catalogue entry; the credential comes from a different agency's site entirely. Nothing on the dataset page says so except one field labelled `API 유형: LINK`.
- **One dataset under-reports its row count by 4×.** The portal says 50,000 fire hydrants. There are 199,507 — the number shown is the grid download cap.
- **Two earthquake shelter datasets are empty.** Registered as standard data, documented, downloadable. The CSV contains only a header row.
- **The flood series forbids modification.** All 21 datasets are KOGL Type 4: attribution, non-commercial, and no derivative works. Reprojecting the coordinate system violates the terms — which is the first thing any evacuation analysis does.
- **The weather grid formula isn't published.** The forecast API takes grid cells, not latitude and longitude, and the conversion ships inside an attachment ZIP.

Disaster data carries one more hazard that ordinary data does not. **A failed lookup reads as safety.** When the landslide API returns 403 and the result set comes back empty, "no landslide advisories" is indistinguishable from "we could not check."

That last one shaped the whole design.

## Design

**A value cannot travel without its origin.** Every observation is wrapped in a `Record` carrying provenance, freshness, and quality flags. Extracting the bare value is possible — but you have to discard the citation deliberately, not by forgetting.

**Absence is proven, not assumed.** Every fetch returns a receipt stating `records`, `confirmed_empty`, or `failed`. A parser may only claim confirmed emptiness after validating a recognised success envelope and finding a documented no-data marker. An unrecognised response shape is a failure, and `absence_confirmed` tells a caller whether an empty result may be read as "nothing applies."

**Licence terms are enforced by code, not documented in prose.** KOGL Types 3 and 4 forbid modification, which covers reprojection, clipping, joins, and derived labels. `require()` raises on those paths instead of letting them through with a comment nobody reads.

**Nothing here has side effects.** Placing calls, issuing evacuation orders, and changing resident status belong to the operations platform. This layer has no POST, PUT, or DELETE route, and the MCP server refuses to register a tool whose name implies mutation.

## What it looks like when it works

Asked pointedly whether Mungyeong has landslide risk, an AI client with this stack **declined to answer**:

> Mungyeong's landslide risk is unconfirmed. I can say neither "there is risk" nor "there is none."
>
> Both primary sources failed. Answering "no risk" would substitute safety for a lookup failure, so I won't.
>
> **Could not check:** Korea Forest Service Landslide Prediction (15074800) — HTTP 403, development-stage review pending. Roadside Landslide Information (15074812) — same cause.

That is the entire point. The system is built so an agent cannot mistake an outage for good news.

## Quick start

Attach it to your AI harness in one line — it detects opencode, Claude Code, Claude Desktop, or Cursor, and installs both the MCP server and the skill:

```bash
curl -fsSL https://raw.githubusercontent.com/jxkr2026algorix/jxkr2026-gbsafedata/main/install.sh | bash
```

Then ask: *문경시 산사태 위험 상황을 확인해줘* (what is the landslide risk in Mungyeong). A correct setup names the sources it could not read and refuses to call unverified risk "none."

Per-harness config, credential setup, and troubleshooting: **[docs/install.md](docs/install.md)**.

Working on the code instead:

```bash
git clone https://github.com/jxkr2026algorix/jxkr2026-gbsafedata
cd jxkr2026-gbsafedata
uv sync --all-packages

cp .env.example .env       # add GBSAFE_DATA_GO_KR_SERVICE_KEY
uv run gbsafe doctor
```

`doctor` reports which sources are usable and, for the rest, why. It separates **a missing credential** from **a pending review** from **a file you must download by hand**, because those need different responses and guessing wrong costs a trip to the portal.

Without any credential the catalogue, search, verification, and citation tools still work.

## Usage

### CLI

```bash
uv run gbsafe doctor                              # source status
uv run gbsafe search 산사태 --ready                # only what's callable now
uv run gbsafe verify 15074800 --operation derive  # may I transform this?
uv run gbsafe cite 15084084                       # attribution for a report
uv run gbsafe region 문경시                        # code, coordinates, weather grid
uv run gbsafe hazard 문경시 --type landslide       # current conditions
uv run gbsafe quality                             # verified data defects
uv run gbsafe serve                               # REST API
uv run gbsafe mcp                                 # MCP server
```

### REST API

```bash
uv run gbsafe serve   # http://127.0.0.1:8000/docs
```

Every data response uses one envelope:

```json
{
  "records": [{ "payload": {...}, "source": {...}, "freshness": {...} }],
  "citations": [{ "text": "기상청 「기상청 기상특보」 · 기준 2026-08-21T17:00:00+09:00 · KOGL-1 · ..." }],
  "receipts": [
    { "connector": "weather_warning", "outcome": "records", "record_count": 9 },
    { "connector": "landslide_forecast", "outcome": "failed", "detail": "HTTP 403 — development-stage review pending" }
  ],
  "complete": false,
  "absence_confirmed": false
}
```

Read `absence_confirmed` before concluding anything from an empty `records`. Full reference: [docs/api.md](docs/api.md).

### MCP server

```bash
uv run gbsafe-mcp
```

Client configs are in [`plugins/`](plugins). All 11 tools are read-only:

`search_datasets` · `describe_dataset` · `verify_dataset` · `cite_dataset` · `resolve_region` · `hazard_context` · `list_sources` · `fetch_source` · `data_health` · `quality_report` · `population_guidance`

Install [`skills/gb-safedata`](skills/gb-safedata) alongside it. The MCP server gives an agent the tools; the skill gives it the rules for reading disaster data honestly — never report absence you did not verify, never present a forecast as an observation, never infer an individual from aggregate statistics, never decide an evacuation.

## Connected sources

One `data.go.kr` development key drives all of these.

| Connector | Dataset | Status |
| --- | --- | --- |
| `weather_now` | KMA ultra-short nowcast | live |
| `weather_forecast` | KMA short-term forecast | live |
| `weather_warning` | KMA weather advisories | live |
| `wildfire_risk` | KFS wildfire risk index | live |
| `emergency_beds` | Real-time emergency beds | live |
| `air_quality` | AirKorea | live (500/day) |
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
| [docs/data-sources.md](docs/data-sources.md) | Per-agency acquisition, quirks, and known defects |

## Development

```bash
uv run pytest tests/ -q                                # no network required
uv run ruff check .
uv run python scripts/mutation_audit.py                # measure what tests catch
uv run python scripts/smoke_live_apis.py               # call the real APIs
uv run python scripts/check_generated_docs.py --write  # regenerate api.md, mcp.md
uv run python scripts/check_readme_badges.py --write   # refresh badge counts
```

The suite never reads your `.env`. It has to produce the same result on a machine with no credentials at all — an earlier version didn't, and a test meant to cover the no-credential path was quietly exercising a real key and making live calls.

### Why coverage isn't the metric

Coverage tells you a line executed. What matters is whether a test fails when that line is *wrong*. Those are different questions — at 87% coverage, **flattening every landslide advisory to "low" left all 501 tests passing.**

So [`scripts/mutation_audit.py`](scripts/mutation_audit.py) deliberately breaks the code in the direction that hides danger, then checks whether the suite notices. Each mutation is a failure that could really happen: a missing temperature becoming 0°C, a cancelled advisory left active, an earthquake shelter assigned to a flood.

A surviving mutation means no test catches that failure, so CI treats it as a build failure.

### What CI checks

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and pull request.

| Job | Checks |
| --- | --- |
| `test` | Full suite on Python 3.12 and 3.13, with no credential supplied |
| `lint` | `ruff check` |
| `guarantees` | That the safety properties below still hold |
| `mutation` | That every danger-hiding mutation is caught |
| `live-api` | Real government API calls, on push and daily |
| `install` | Frozen install on Ubuntu and macOS, then CLI, MCP, and API exercised |

`guarantees` asserts the claims this README makes:

- the API exposes **no write routes** — a POST fails the build
- every MCP tool registers **read-only**
- **every catalogue licence string resolves** — a spelling variant degrading to `UNKNOWN` silently blocks permitted work
- **grid conversion matches the published cells** for Seoul, Busan, Jeju, and Mungyeong, because a wrong cell returns another city's weather successfully
- `docs/api.md` and `docs/mcp.md` are **current with the code**
- the **badge numbers above match reality**

`live-api` exists because recorded responses keep passing when an agency changes its schema — production is what breaks. It calls each source once to respect the quotas and asserts the review-pending sources still return `not_authorized`, so we learn when approval lands.

**It cannot fully run on GitHub.** `apis.data.go.kr` blocks non-Korean IPs, and hosted runners are in the US, so every call times out. The script distinguishes that from a real defect: if *every* source is unreachable it reports the geographic restriction and exits zero rather than crying wolf. Schema verification therefore has to happen from a Korean IP — run `uv run python scripts/smoke_live_apis.py` locally, or point the job at a self-hosted runner in a Korean region.

## Licence

Code and documentation are [Apache-2.0](LICENSE).

**No source data is committed here.** The flood series is KOGL Type 4 — non-commercial and no derivatives — and a non-commercial restriction cannot coexist with an OSI licence, which may not discriminate against fields of use. OpenStreetMap is ODbL, and share-alike spreads: merge OSM with government data into one artefact and the attribution-only data inherits share-alike.

So this repository ships **acquisition methods and verified metadata** instead. Terms differ per dataset; `gbsafe verify` will tell you what a given one permits.

## Related

- [jxkr2026-datasets](https://github.com/jxkr2026algorix/jxkr2026-datasets) — the survey behind the catalogue: what each source actually returns, how to obtain it, and where the portal is wrong
