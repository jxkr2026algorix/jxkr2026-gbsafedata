# Why it is built this way

The survey behind the catalogue, the design decisions that followed, and how
the test suite is held to them. Usage lives in the [README](../README.md).

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

So [`scripts/mutation_audit.py`](../scripts/mutation_audit.py) deliberately breaks the code in the direction that hides danger, then checks whether the suite notices. Each mutation is a failure that could really happen: a missing temperature becoming 0°C, a cancelled advisory left active, an earthquake shelter assigned to a flood.

A surviving mutation means no test catches that failure, so CI treats it as a build failure.

### What CI checks

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every push and pull request.

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

It distinguishes three kinds of failure, because treating them alike makes the job useless. A parser that can no longer read a source is a real defect and fails the build. Every source being unreachable is a network or region problem, reported and exited zero. And AirKorea returns intermittent 504s — the source survey measured roughly one failure in three even after four retries and warns against making it a hard dependency — so its failure is reported without breaking the build.
