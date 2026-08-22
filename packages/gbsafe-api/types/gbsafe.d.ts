/**
 * GB SafeData — TypeScript declarations for web/dashboard clients.
 *
 * Read-only public disaster data for Gyeongsangbuk-do, Korea.
 * Field names here were taken from live responses of GB SafeData API 0.1.0
 * (`GET /openapi.json` reports `openapi: 3.1.0`, `info.version: 0.1.0`).
 *
 * Two surfaces return the same data under different key names:
 *
 *   REST   `GET /v1/hazards/context`, `GET /v1/sources/{connector}`
 *          -> per-source results in `receipts`, plus `citations` and `generated_at`
 *   TOOL   `GET /v1/tools/{name}`, and MCP `tools/call` result text
 *          -> per-source results in `sources_checked`, plus `warnings` and `how_to_cite`
 *
 * Use `GbSafeEnvelope` when you accept either.
 *
 * See docs/platform-integration.md for the rendering contract.
 */

// ---------------------------------------------------------------------------
// Enumerations
// ---------------------------------------------------------------------------

/** Hazard types accepted by `hazard`. From the live enum on `gbsafe_hazard_context`. */
export type GbSafeHazard =
  | "heavy_rain"
  | "landslide"
  | "wildfire"
  | "flood"
  | "earthquake"
  | "heatwave";

/**
 * Per-source result classification. This is the field the whole design rests on.
 *
 * - `records`         the source answered and returned rows
 * - `confirmed_empty` the source explicitly said "nothing applies here".
 *                     This is the ONLY outcome that licenses an empty result to be
 *                     rendered as reassuring.
 * - `failed`          the source could not be read, or returned something that
 *                     could not be parsed as a definite "nothing applies".
 *                     An empty result caused by this means UNKNOWN, not SAFE.
 */
export type GbSafeSourceOutcome = "records" | "confirmed_empty" | "failed";

/** Upstream API status. Present on every receipt and every record's `source`. */
export type GbSafeUpstreamStatus =
  | "ok"
  | "cached"
  | "degraded"
  | "unavailable"
  | "not_authorized";

/**
 * Provenance of the data.
 *
 * `synthetic` is drill/training data. It must remain visibly labelled everywhere
 * it is rendered. Showing synthetic data as if it were `real` is the second-worst
 * failure this service can be involved in.
 */
export type GbSafeDataMode = "real" | "snapshot" | "synthetic";

/** Freshness bucket. `usable_for_decision` is the field to branch on, not this. */
export type GbSafeFreshnessStatus = "fresh" | "aging" | "stale" | "unknown";

/** Defects confirmed by validation, surfaced per record in `quality_flags`. */
export type GbSafeQualityFlag =
  | "row_count_mismatch"
  | "empty_dataset"
  | "format_mismatch"
  | "provider_mismatch"
  | "update_claim_mismatch"
  | "not_machine_readable"
  | "admin_boundary_drift"
  | "missing_coordinates"
  | "coordinate_out_of_range"
  | "encoding_cp949"
  | "partial_response"
  | "no_data_returned";

/**
 * The 11 tool names, taken verbatim from live `GET /v1/tools`.
 * Same 11 names are returned by MCP `tools/list`.
 */
export type GbSafeToolName =
  | "gbsafe_search_datasets"
  | "gbsafe_describe_dataset"
  | "gbsafe_verify_dataset"
  | "gbsafe_cite_dataset"
  | "gbsafe_resolve_region"
  | "gbsafe_hazard_context"
  | "gbsafe_list_sources"
  | "gbsafe_fetch_source"
  | "gbsafe_data_health"
  | "gbsafe_quality_report"
  | "gbsafe_population_guidance";

// ---------------------------------------------------------------------------
// Per-source receipt
// ---------------------------------------------------------------------------

/**
 * One fetch attempt against one upstream source.
 *
 * Named `receipts[]` on the REST surface and `sources_checked[]` on the tool/MCP
 * surface. Identical shape; both are serialized from the same collection.
 *
 * To tell the user which sources failed, filter for `outcome === "failed"` and
 * show `connector` plus `detail`.
 */
export interface GbSafeSourceReceipt {
  /** Internal connector id, e.g. `"landslide_forecast"`, `"weather_warning"`. */
  connector: string;
  /** Upstream dataset id, e.g. `"15074800"`, `"hrfco-waterlevel"`. */
  dataset_id: string;
  outcome: GbSafeSourceOutcome;
  record_count: number;
  /** ISO 8601 UTC. */
  checked_at: string;
  upstream_status: GbSafeUpstreamStatus;
  /**
   * Human-readable reason. Empty string when there is nothing to report.
   * For a failure this holds the actual cause (e.g. the HTTP 403 review-pending
   * text from the Korea Forest Service APIs). Show it; do not swallow it.
   * Text is Korean.
   */
  detail: string;
}

// ---------------------------------------------------------------------------
// Per-record structures
// ---------------------------------------------------------------------------

export interface GbSafeGeoPoint {
  lat: number;
  lon: number;
}

/** Provenance and licence block. Appears as `source` on each record. */
export interface GbSafeSourceInfo {
  dataset_id: string;
  dataset_name: string;
  provider: string;
  /** e.g. `"KOGL-1"`, `"KOGL-3"`, `"unrestricted"`, `"unknown"`. */
  license: string;
  license_summary: string;
  /** Ready-to-display attribution line. Required by KOGL for KOGL-licensed data. */
  attribution: string;
  source_url: string;
  endpoint: string;
  mode: GbSafeDataMode;
  upstream_status: GbSafeUpstreamStatus;
  /** ISO 8601. When GB SafeData fetched it. */
  retrieved_at: string;
  /** ISO 8601. When the phenomenon was observed. Null for non-observations. */
  observed_at: string | null;
  /** ISO 8601. When the issuing agency published it. Null when not applicable. */
  published_at: string | null;
  snapshot_id: string;
  may_modify: boolean;
  may_redistribute: boolean;
}

export interface GbSafeFreshnessInfo {
  status: GbSafeFreshnessStatus;
  age_seconds: number | null;
  expected_cycle_seconds: number | null;
  /** ISO 8601. The timestamp to display next to the value. */
  as_of: string;
  /** Korean explanation of the freshness verdict. */
  reason: string;
  /**
   * `false` means: do not present this value as the current situation on its own.
   * Render the value together with `as_of`.
   */
  usable_for_decision: boolean;
}

/**
 * One data point. `payload` is source-specific and deliberately untyped here —
 * a weather warning and a river level share no fields. Narrow it yourself per
 * connector if you need to.
 */
export interface GbSafeRecord<TPayload = Record<string, unknown>> {
  payload: TPayload;
  source: GbSafeSourceInfo;
  freshness: GbSafeFreshnessInfo;
  quality_flags: GbSafeQualityFlag[];
  /**
   * Per-record annotations, e.g. decoded precipitation type. Korean text.
   * Display them; they carry meaning the raw number does not.
   */
  notes: string[];
  /** Stable hash of value + source + time. Use it to dedupe idempotently. */
  fingerprint: string;
}

// ---------------------------------------------------------------------------
// Supporting envelope members
// ---------------------------------------------------------------------------

/** Ready-made citation. Quote `text` verbatim rather than composing your own. */
export interface GbSafeCitation {
  dataset_id: string;
  dataset_name: string;
  provider: string;
  license: string;
  source_url: string;
  /** ISO 8601. */
  as_of: string;
  mode: GbSafeDataMode;
  /** Full citation line, ready to render. */
  text: string;
}

/** A source that is degraded or unreadable, with the reason. */
export interface GbSafeDegradation {
  /** Dataset id, or a pseudo-id such as `"region"` when the query itself failed. */
  dataset_id: string;
  status: GbSafeUpstreamStatus;
  detail: string;
  /** ISO 8601. */
  occurred_at: string;
  /** ISO 8601, or null when this source has never been read successfully. */
  last_known_good_at: string | null;
  /** `true` means this outage prevents interpreting the result as complete. */
  blocks_interpretation: boolean;
}

// ---------------------------------------------------------------------------
// The envelope
// ---------------------------------------------------------------------------

/** Fields common to both surfaces. */
export interface GbSafeEnvelopeCommon {
  /** Echo of the query parameters that were applied. */
  query: Record<string, string | null>;
  records: GbSafeRecord[];
  citations: GbSafeCitation[];
  degradations: GbSafeDegradation[];
  /**
   * Envelope-level qualifications: how far away the weather station is, that the
   * warning covers a whole administrative office area, that rows for other
   * regions were filtered out, that the response came from cache. Korean text.
   * These change what the numbers mean. Render them; never drop them.
   */
  caveats: string[];
  record_count: number;

  /**
   * `false` means at least one upstream source could not be read.
   * Do not decide anything from `records` alone when this is `false`.
   */
  complete: boolean;

  /**
   * The single most important field in this API.
   *
   * `true`  -> every source answered, and an empty `records` genuinely means
   *            "nothing is currently in effect". Safe to render reassuringly.
   * `false` -> absence was NOT verified. An empty `records` means UNKNOWN.
   *
   * Ignoring this field is the exact mistake this whole project exists to
   * prevent. A dashboard that renders `records.length === 0` as a green
   * "no hazard" tile will, during an upstream outage, present an outage as
   * safety — which is the failure mode GB SafeData was built to make
   * impossible. The API keeps the distinction all the way to your code; only
   * your renderer can throw it away.
   */
  absence_confirmed: boolean;
}

/** REST surface: `GET /v1/hazards/context`, `GET /v1/sources/{connector}`. */
export interface GbSafeRestEnvelope extends GbSafeEnvelopeCommon {
  /** Per-source results. Same data the tool surface calls `sources_checked`. */
  receipts: GbSafeSourceReceipt[];
  /** ISO 8601. */
  generated_at: string;
  /** Distinct `source.mode` values present in `records`. */
  modes: GbSafeDataMode[];
}

/** Tool surface: `GET /v1/tools/{name}`, and MCP `tools/call` result text. */
export interface GbSafeToolEnvelope extends GbSafeEnvelopeCommon {
  /** Per-source results. Same data the REST surface calls `receipts`. */
  sources_checked: GbSafeSourceReceipt[];
  /**
   * Pre-composed Korean warnings for the model and for the user, including the
   * explicit "an empty result does not mean no hazard" line. Pass these through.
   */
  warnings: string[];
  how_to_cite: string;
}

/** Either surface. Use `getReceipts()` to read the per-source results. */
export type GbSafeEnvelope = GbSafeRestEnvelope | GbSafeToolEnvelope;

// ---------------------------------------------------------------------------
// The three-state UI contract
// ---------------------------------------------------------------------------

/**
 * The only three states a dashboard may render for a data response.
 *
 * Rendering two states (data / no data) is wrong and unsafe, because it merges
 * `"nothing is in effect"` with `"we could not check"`.
 */
export type GbSafeViewState =
  /** Records came back. Render them, honouring per-record freshness and mode. */
  | { kind: "data"; records: GbSafeRecord[]; caveats: string[] }
  /**
   * Verified empty: `complete && absence_confirmed && records.length === 0`.
   * This is the only state that may look reassuring.
   */
  | { kind: "confirmed-empty"; caveats: string[] }
  /**
   * Everything else. MUST NOT look reassuring — no green, no checkmark, no
   * "all clear". Show `failed` with each failing source and its reason.
   */
  | {
      kind: "unverified";
      failed: GbSafeSourceReceipt[];
      caveats: string[];
    };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Reads `receipts` or `sources_checked`, whichever the surface provided. */
export declare function getReceipts(
  envelope: GbSafeEnvelope,
): GbSafeSourceReceipt[];

/**
 * Classifies a response into exactly one of the three renderable states.
 * Route every data view through this instead of testing `records.length`.
 */
export declare function toViewState(envelope: GbSafeEnvelope): GbSafeViewState;

/** `true` when the record must be displayed with its `freshness.as_of`. */
export declare function needsTimestamp(record: GbSafeRecord): boolean;

/** `true` when the record is drill data and must stay labelled as such. */
export declare function isSynthetic(record: GbSafeRecord): boolean;

// ---------------------------------------------------------------------------
// Other endpoint shapes
// ---------------------------------------------------------------------------

/** `GET /v1/tools` — OpenAI-compatible function schemas for all 11 tools. */
export interface GbSafeToolCatalog {
  tools: GbSafeToolSchema[];
  /** Invocation hint, e.g. `"GET /v1/tools/{name}?<인자>"`. */
  invoke: string;
  /** Reminder that the system prompt must be applied alongside these tools. */
  note: string;
}

/** One entry of `GET /v1/tools`. Pass the array straight to `tools:`. */
export interface GbSafeToolSchema {
  type: "function";
  function: {
    name: GbSafeToolName;
    /** Korean description. Contains the `complete === false` warning. */
    description: string;
    parameters: {
      type: "object";
      properties: Record<string, unknown>;
      required?: string[];
      additionalProperties?: boolean;
    };
  };
}

/** `GET /v1/agent/system-prompt`. Fetch at runtime; do not hardcode. */
export interface GbSafeSystemPrompt {
  /** Korean system prompt. Send as the `system` message. */
  system_prompt: string;
  /** Provenance, e.g. `"skills/gb-safedata/SKILL.md"`. */
  source: string;
}

/** `GET /v1/regions/resolve?q=`, and tool `gbsafe_resolve_region`. */
export interface GbSafeRegionResolved {
  found: true;
  /** 5-digit administrative code, e.g. `"47280"` for 문경시. */
  code: string;
  name: string;
  full_name: string;
  /** Approximate centre, based on the city/county office. Not a boundary. */
  center: GbSafeGeoPoint;
  /** KMA forecast grid coordinates. */
  kma_grid: { nx: number; ny: number };
  asos_station: number | null;
  asos_station_detail: {
    station_id: number;
    name: string;
    /** Distance from the region centre. Show this when `is_local` is false. */
    distance_km: number;
    /** `false` means the nearest station is outside the region. */
    is_local: boolean;
  } | null;
  /** First entry of `caveats`, kept for convenience. */
  caveat: string;
  caveats: string[];
}

/** Body of the 404 returned by `GET /v1/regions/resolve` for an unknown region. */
export interface GbSafeRegionNotFound {
  detail: {
    found: false;
    query: string;
    message: string;
    /** The 22 cities and counties of Gyeongsangbuk-do. */
    available: string[];
  };
}

/** `GET /v1/hazard-types` — which connectors each hazard fans out to. */
export interface GbSafeHazardTypes {
  hazards: Array<{
    /** Includes `"other"`, which is not accepted as a `hazard` argument. */
    value: GbSafeHazard | "other";
    connectors: string[];
  }>;
}

/** Body of the 401 returned when `GBSAFE_API_KEYS` is set and the key is bad. */
export interface GbSafeAuthError {
  error: string;
  how: string;
}
