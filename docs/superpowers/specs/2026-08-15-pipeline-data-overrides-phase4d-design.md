# KP Healthcare Plan — AI-Editable Pipeline Data via Overrides (Phase 4d)

Status: Approved design, pre-implementation
Date: 2026-08-15

## 1. Purpose

Let an admin update the core deterministic pipeline's own input numbers —
population and aggregate health-facility/bed/staffing counts, per
district — through the same document-upload-plus-instruction, AI-extraction
UX phases 4b and 4c already established, with the update flowing all the
way through: `district_metrics.csv`'s computed columns (`gap_score`,
`need_tier`, etc.), the GIS shapefiles, the QGIS project, and the HTML
report.

This is the "Prompt-guided updates to existing pipeline data" item every
phase since 4a has explicitly flagged and deferred:

> "Prompt-guided updates to existing pipeline data (population, dev-stats
> health/roads/budget figures feeding `district_metrics.csv`) remains a
> separate, undesigned, deferred piece — this phase never writes to those
> files or to any computed column."

Unlike phases 4b/4c, this phase deliberately *does* touch the computed
model — that is the whole point — so its design leans harder on
guardrails than those phases needed to: real district/column validation,
sanity-bounded value swings, and an append-only audit trail, so an AI
extraction error degrades to a rejected update with a clear reason, never
a silently-corrupted gap score.

## 2. Scope Decisions From Brainstorming

- **Targets the upstream input files, not the computed output.**
  `district_metrics.csv` is generated fresh by `08_compute_district_metrics.py`
  on every pipeline run; editing it directly would mean any edit is
  silently discarded on the next run. This phase edits
  `kp_district_population_2023.csv` and `dev_stats_health.csv` instead —
  the files `08_compute_district_metrics.py` actually reads — through a
  separate overrides layer (below), never those files directly.
- **A separate, append-only overrides file, not a direct in-place edit.**
  `kp_district_population_2023.csv`, `dev_stats_health.csv`, and
  `facilities_merged.csv` are themselves regenerated from source PDFs/APIs
  by `02_compile_population.py`, `17_extract_devstats_health.py`, and
  `07_merge_facilities.py` respectively, every time `scripts/run_all.py`
  runs the full pipeline (each stage documents itself as idempotent —
  "re-fetches/recomputes into the same output paths"). A direct edit to
  any of those files would be silently discarded by the next full
  pipeline run, with no warning — a real data-loss risk this project's
  established posture ("a genuinely saved record is never silently
  reported as lost") explicitly rules out. A separate overlay file that a
  new pipeline stage applies *after* regeneration survives any number of
  future full pipeline runs, the same "separate additive store, never
  touching the base computed files directly" pattern phase 4b already
  proved out for supplemental facts — applied here to the core metrics
  instead.
- **Aggregate counts only, not new facility points.** "More health
  facilities" means adjusting the aggregate counts
  (`govt_institutions`/`govt_beds`/`pvt_hospitals`/etc. in
  `dev_stats_health.csv`) that feed the gap score's facility-density and
  bed-capacity terms — not adding a new named facility with a map
  location. Adding new facility *points* to `facilities_merged.csv` needs
  geocoding/location-resolution logic this phase doesn't build; explicitly
  deferred (see §9).
- **Only a whitelisted set of independently-settable columns per file are
  overridable**, not every column. E.g. `dev_stats_health.csv`'s
  `pop_per_bed` is itself derived (population ÷ beds) by the extraction
  script; overriding it independently would make it inconsistent with
  `govt_beds`. Metadata columns (`division`, `source_url`,
  `prior_census_year`) aren't factual values to override at all.
- **AI extracts and validates the target file/column/value from free text
  or an uploaded document**, reusing phase 4b's proven pattern — not a
  structured dropdown-only form. The admin can say "Peshawar's population
  is now 5.75 million per the new provincial estimate" or upload a short
  document, and the AI maps that to a validated `{district, file, column,
  value}` tuple, the same way it already maps a document's free text to a
  supplemental-fact record in phase 4b.
- **Sanity-bounded value swings.** An override that changes a value by
  more than a generous threshold from its *current* value is rejected
  with a clear reason (current value, proposed value, threshold) rather
  than silently accepted — catches a garbled AI read or a wrong-district
  mix-up without blocking a genuine large update (the admin sees why it
  was rejected and can resubmit with a clearer instruction). Thresholds:
  ±50% for `"population"` fields, ±100% for `"health"` count fields
  (smaller absolute numbers, more prone to a legitimately large relative
  jump — e.g. a newly-registered facility doubling a small district's
  `govt_institutions`).
- **Downstream-only rebuild, not a full `run_all.py` re-run.** Only the
  stages that actually depend on population/health numbers need to
  re-execute: applying the overrides, recomputing `district_metrics.csv`,
  gap scores, forecasts, suggested sites, cross-validation, shapefiles,
  the QGIS project, and the report. The expensive fetch/geocode/DEM stages
  (boundaries, facility fetching/geocoding, roads, DEM zonal stats) are
  untouched by this phase's overrides and don't need to re-run.
- **Append-only, no update-in-place, on the overrides file itself.** A
  second override for the same `(district, file, column)` doesn't replace
  the first row; the apply stage reads the *latest* row per
  `(district, file, column)` as the effective value — matching phase 4b's
  simplest-safe-behavior-for-a-first-cut reasoning.

## 3. Data Model & Storage

`data/processed/metric_overrides.csv`, columns:

| Column | Meaning |
|---|---|
| `district` | Canonical KP district name (validated against the 35-district list) |
| `file` | Which upstream file this targets — `"population"` or `"health"` in this phase |
| `column` | The target column name — validated against that file's whitelist of independently-settable columns |
| `value` | The new numeric value (stored as text, parsed at apply-time) |
| `reason` | Free text the AI extracts alongside the value (e.g. "Updated per 2026 provincial re-survey") |
| `source` | The uploaded filename (or synthesized name for an instruction-only submission) this override came from |
| `added_at` | ISO 8601 UTC timestamp of when the override was appended |

Append-only; created with a header row on first write if it doesn't exist
yet — same convention as `supplemental_records.csv`.

**Overridable field registry** (fixed in this phase, extensible later):

```
{
  "population": (kp_district_population_2023.csv, {
      "population_2023", "population_prior", "growth_rate_pct"
  }),
  "health": (dev_stats_health.csv, {
      "govt_institutions", "govt_beds", "pvt_hospitals", "pvt_beds",
      "medical_staff", "paramedical_staff", "pvt_practitioners"
  }),
}
```

## 4. AI Extraction & Validation

`server/metric_overrides.py` — structurally a sibling of
`server/supplemental_data.py`:

- `MetricOverrideError(Exception)` — typed exception, message safe to show
  the admin directly, same posture as every other typed exception in this
  project.
- `OVERRIDABLE_FIELDS` — the registry from §3.
- `load_known_districts() -> list[str]` — reused from `supplemental_data`'s
  existing helper (imported, not duplicated — this phase reads the same
  `district_metrics.csv` district list).
- `build_override_question(instruction, known_districts) -> str` — states
  the exact JSON shape expected (`district`, `file`, `column`, `value`,
  `reason`), lists the valid district names and the valid `(file, column)`
  pairs from the registry, an explicit "respond with ONLY a JSON array and
  nothing else" directive — same pattern as phase 4b's
  `build_extraction_question`.
- `parse_override_response(raw_text, known_districts) -> list[dict]` —
  extracts a JSON array (tolerating a wrapping code fence), validates each
  record:
  - `district`: normalized and case-insensitive-matched to the canonical
    name from `known_districts` (same fix phase 4c's final review applied
    to `supplemental_data.py`), rejected with a clear message if not a
    real district.
  - `file`: must be one of `OVERRIDABLE_FIELDS`'s keys.
  - `column`: must be in that file's whitelisted column set.
  - `value`: must parse as a non-negative number.
  - **Sanity swing check:** reads the column's *current* value live from
    the real target CSV, computes the percentage change, and rejects
    (raising `MetricOverrideError` naming the current value, proposed
    value, and the threshold) if it exceeds the file's threshold (±50%
    population, ±100% health counts).
  - `reason`: free text, defaults to `""` if absent.
  Raises `MetricOverrideError` on invalid JSON, wrong shape, an unknown
  district/file/column, a non-numeric or negative value, an
  excessive swing, or an empty result — same exhaustive-validation
  posture as `supplemental_data.parse_ai_response`.
- `add_from_document(provider, key, document_text, instruction, source_document) -> list[dict]`
  — orchestrates: build the prompt, call `ai_client.ask(provider, key,
  question, context)` (reused exactly as it exists today — no changes to
  `ai_client.py`), parse and validate the response, append the valid
  overrides with `source_document`/`added_at` filled in, return the newly
  added records.

## 5. New Pipeline Stage & Downstream Rebuild

`scripts/07b_apply_metric_overrides.py` — a new numbered/lettered stage,
following the existing `13b` insertion convention. Reads
`metric_overrides.csv` (empty list if the file doesn't exist yet, same
tolerance as `supplemental_records.load_records()`), resolves the latest
value per `(district, file, column)`, and patches that cell in
`kp_district_population_2023.csv`/`dev_stats_health.csv` in place. No
overrides present → the stage is a byte-for-byte no-op. Re-validates every
override's column against the *real* file header at apply-time — a
genuinely unknown column at this point (e.g. from schema drift between
when an override was written and when it's applied) is a hard failure
(non-zero exit), since silently skipping it would let
`district_metrics.csv` compute from stale data with nothing to signal that.

Inserted into `run_all.py`'s `STAGES` list immediately before
`"08_compute_district_metrics.py"` — after this point in the existing
order, both `02_compile_population.py` and `17_extract_devstats_health.py`
have already regenerated their files, so an override always applies after
regeneration within a single full run, never gets raced or clobbered by it.

`08_compute_district_metrics.py` and every extraction script
(`02`, `17`, `18`, `19`) are **not modified** by this phase — they
continue to read/write exactly as they do today; `07b` is the only new
code that touches the files they produce.

New `scripts/run_downstream.py` — mirrors `run_all.py`'s stage-runner but
only the subset that depends on population/health numbers:

```
STAGES = [
    "07b_apply_metric_overrides.py",
    "08_compute_district_metrics.py",
    "09_gap_score_and_clusters.py",
    "10_forecast_demand.py",
    "11_suggest_new_sites.py",
    "20_cross_validate_facility_counts.py",
    "12_write_shapefiles.py",
    "13_build_qgis_project.py",
    "14_build_html_report.py",
]
```

The expensive fetch/geocode/DEM stages (01, 03-07, 15, 16, 17-19) are not
included — nothing in this phase's overrides changes their inputs or
outputs, so re-running them would be wasted work every time an admin
updates one number.

## 6. Route & Admin UI

`POST /admin/api/metric-overrides` — auth-gated like every other
`/admin/api/*` route. Multipart form: `file` (required, same as
`/admin/api/supplemental-data` — an admin without a real source document
can upload a small `.txt` file containing just their instruction, since
`.txt` extraction already exists from phase 4b), `instruction` (optional
text), `provider` (which configured AI provider to use). Server-side:
`document_extraction.extract()` on the file (reused as-is), then
`metric_overrides.add_from_document(...)`, then
`subprocess.run([sys.executable, "scripts/run_downstream.py"], timeout=600)`
(the 600s ceiling is longer than phase 4b's 300s, since this rebuild now
includes shapefile writing and QGIS project regeneration, not just an
HTML report). Returns `{"added": [...]}` on success; 415/422 for a bad
uploaded file (same codes as 4a/4b), 400 for `MetricOverrideError` or no
configured key, 502 for `AIProviderError`, 200 + `rebuild_warning` if the
downstream rebuild fails or times out — the overrides are appended to
`metric_overrides.csv` *before* the rebuild subprocess runs, so a rebuild
problem never loses data that was genuinely saved, matching phase 4b/4c's
established rule exactly.

The admin panel gains an "Update Pipeline Data" section, the same visual
pattern as "Extract Document" and "Database Ingestion": a file input, an
instruction textarea, a provider `<select>`, and an "Apply Update" button.
The result summary renders each change in plain terms (e.g. "Peshawar /
population_2023: 4,750,388 → 5,100,000") rather than the raw tuple —
friendlier, and every interpolated field (district, file, column, and both
values) is passed through the same `escapeHtml()` JS helper before
insertion, since these are AI-derived values carrying the identical
untrusted-content-in-HTML risk phase 4b's and 4c's reviews each found and
fixed once already.

## 7. Error Handling & Security

Every typed exception (`MetricOverrideError`, reused
`UnsupportedFormatError`/`ExtractionError`/`AIProviderError`) carries a
message safe to show the admin directly, never a raw traceback. Route
status codes mirror phase 4b/4c's established conventions exactly.

Security surface:
- Column names are validated against each target file's real, whitelisted
  header set — both at AI-extraction time (§4) and again at pipeline-apply
  time (§5) — before ever being used to write a cell. No override can add
  a new column, only patch an existing whitelisted one. This is the
  pipeline-data equivalent of phase 4c's "table name validated before use
  in a query" guardrail.
- The sanity-swing check (§2, §4) is the primary defense against a
  garbled AI extraction quietly corrupting the deterministic model — an
  implausible value is rejected with a clear, actionable reason rather
  than silently accepted.
- No new secret/credential surface — this phase reuses whichever AI
  provider key the admin already has configured; nothing new is stored in
  `keystore`.
- `07b_apply_metric_overrides.py` only ever patches a cell's numeric
  value in an existing row of an existing file — it never adds or removes
  rows, columns, or files.

## 8. Testing Strategy

- `tests/server/test_metric_overrides.py` — `parse_override_response()`
  unit tests (valid JSON, code-fence-wrapped JSON, invalid JSON, unknown
  district, unknown file, unknown column, non-numeric value, negative
  value, excessive swing in both directions, empty result);
  `add_from_document()` with `ai_client.ask` mocked (no real provider
  calls), covering success and a validation failure surfacing as
  `MetricOverrideError`.
- `tests/test_apply_metric_overrides.py` — the new pipeline stage script,
  imported via `importlib.import_module("scripts.07b_apply_metric_overrides")`
  (module names starting with a digit aren't valid identifiers, same
  established convention as the report-build script's tests), covering:
  no-overrides-file is a no-op, a valid override patches the right cell
  in a `tmp_path`-based fixture CSV, "latest row wins" when the same
  `(district, file, column)` appears twice, and an unknown column at
  apply-time causes a hard failure (non-zero exit / raised exception,
  whichever the test harness for numbered stages already establishes).
- `tests/server/test_metric_overrides_route.py` — FastAPI `TestClient`
  against `/admin/api/metric-overrides`, with `document_extraction.extract`,
  `metric_overrides.add_from_document`, and the `run_downstream.py`
  subprocess call all mocked — covering success, unauthenticated access,
  an extraction failure, a validation failure, a provider failure, and
  the rebuild-failure/timeout-still-returns-200 cases, mirroring
  `test_supplemental_data_route.py`'s and `test_db_ingestion_route.py`'s
  established mocking patterns.
- No test depends on a real AI provider call or a real multi-stage
  pipeline run; the underlying pieces this phase reuses
  (`document_extraction`, `ai_client`, `08_compute_district_metrics.py`
  and every other downstream stage) already have their own real-behavior
  coverage from earlier phases.

## 9. Roadmap (context for later phases — not this spec's scope)

- **New facility points from AI** (a named facility with a map location,
  rather than an aggregate count bump) remains a separate, undesigned,
  deferred piece — needs geocoding/location-resolution logic this phase
  doesn't build.
- **Additional overridable files** (`dev_stats_roads.csv`,
  `dev_stats_budget.json`) — the registry in §3 is designed to make adding
  a third entry small, but no work is planned here until a real need
  appears.
- **Phase 4c — Database ingestion**, unchanged, feeds phase 4b's
  supplemental-records store — a separate mechanism from this phase's
  pipeline-data overrides.
- **Phase 1b — Methodology upgrade** (2SFCA/p-median), unchanged, still
  independent of every document/database/override-ingestion phase.
