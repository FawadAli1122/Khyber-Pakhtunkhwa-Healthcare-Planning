# KP Healthcare Plan — AI-Extracted Supplemental Facility & District Data (Phase 4b)

Status: Approved design, pre-implementation
Date: 2026-08-15

## 1. Purpose

Let the admin upload a document (text/CSV/Excel/Word/PDF/HTML) describing
things the pipeline's structured data has no column for — equipment at a
facility, medicine availability, diseases treated, outbreak records,
departments and clinical facilities — plus an optional free-text
instruction giving context the document itself doesn't state (e.g. "this
equipment list is for Peshawar's DHQ Hospital"). The AI extracts
structured records from the two combined, appends them to a new store, the
report gains a section rendering them, and the "Ask AI" chat panel can
answer questions grounded in them too. Full autonomy, no review/approval
step, per the project's standing decision from the original brainstorm.

This is a different subsystem from the phase 4a roadmap note's "AI writes
to the pipeline's raw source CSVs" idea (population, dev-stats health
figures, etc., which feed the deterministic gap-score computation) — that
piece remains undesigned and deferred. This phase never touches
`district_metrics.csv`, `gap_score`, `need_tier`, or any other computed
column; it only ever appends to its own new, separate store.

## 2. Scope Decisions From Brainstorming

- **One flexible record shape, not five fixed schemas.** Equipment,
  medicine, departments, diseases treated, and outbreaks are structurally
  different (per-facility lists vs. per-district timestamped events), but
  forcing five separate typed tables would mean any genuinely new category
  needs a code change — directly against "allow to add any further data."
  Every fact becomes one row of `{district, facility, category, label,
  detail, source_document, added_at}`; `category` is free text the AI
  assigns, not an enum.
- **A free-text instruction box alongside the file upload.** Not
  auto-detection alone — the admin can supply context the document itself
  doesn't state.
- **Append-only, no dedup/update-in-place.** Simplest safe behavior for a
  first cut; revisit only if duplicate records become a real problem in
  practice.
- **`district` is validated against the real 35-district list** (read from
  `data/processed/district_metrics.csv`, the existing authoritative list
  used throughout the report). An AI-proposed district that doesn't match
  is rejected with a typed error rather than silently creating a bogus
  district in the report. This is a sanity guardrail, not a human review
  gate — it fires on structurally invalid output, not on content the admin
  would disagree with.
- **Reuses `ai_client.ask()` as-is**, rather than duplicating all five
  providers' dispatch logic in a new module. The "chat question" is a
  strict instruction to return only a JSON array; the "context" is the
  extracted document text + the admin's instruction + the valid district
  list.
- **`document_extraction.py` gains `.txt` and `.csv` parsers** (phase 4a
  only supported `.xlsx`/`.xls`/`.pdf`/`.docx`/`.html`/`.htm`), so "text,
  csv, excel, word, pdf" are all covered as requested.
- **Report regeneration is a plain `scripts/14_build_html_report.py`
  subprocess re-run**, not a full pipeline re-run — no other stage
  consumes the new supplemental-records file, and 14 is pure local
  computation (no network calls), matching `scripts/run_all.py`'s own
  subprocess pattern.

## 3. Data Model & Storage

`data/processed/supplemental_records.csv`, columns:

| Column | Meaning |
|---|---|
| `district` | Canonical KP district name (validated against the 35-district list) |
| `facility` | Optional; blank for a district-level fact (e.g. an outbreak), a facility name for a facility-level fact |
| `category` | Free text the AI assigns: `equipment`, `medicine`, `department`, `disease_treated`, `outbreak`, or anything else |
| `label` | Short name of the fact, e.g. "MRI Machine", "Amoxicillin", "Cardiology" |
| `detail` | Free text elaboration, e.g. "1 unit, operational", "In stock as of Aug 2026", "12 confirmed cases, reported 2026-08-10" |
| `source_document` | The uploaded filename this record came from |
| `added_at` | ISO 8601 UTC timestamp of when the record was appended |

Append-only; created with a header row on first write if it doesn't exist
yet.

## 4. AI Extraction & Validation

`server/supplemental_data.py`:

- `SupplementalDataError(Exception)` — typed exception, message safe to
  show the admin directly, same posture as `ai_client.AIProviderError` and
  `document_extraction.ExtractionError`.
- `load_known_districts() -> list[str]` — reads the `district` column of
  `data/processed/district_metrics.csv`.
- `load_records() -> list[dict]` — reads all rows of
  `supplemental_records.csv` (empty list if the file doesn't exist yet).
- `append_records(records: list[dict]) -> None` — appends rows, writing
  the header first if the file is new.
- `build_extraction_prompt(document_text, instruction, known_districts) -> str`
  — the instruction sent as `ai_client.ask()`'s `question` argument:
  states the exact JSON shape expected (a JSON array of objects with keys
  `district`, `facility`, `category`, `label`, `detail`), the list of
  valid district names, the admin's free-text instruction (if any), and an
  explicit "respond with the JSON array and nothing else" directive.
- `parse_ai_response(raw_text, known_districts) -> list[dict]` — extracts
  a JSON array from the response (tolerating a wrapping code fence, since
  models sometimes add one despite instructions), validates each record
  has non-empty `district` (normalized via
  `scripts.lib.districts.normalize_district`, then checked against
  `known_districts`), `category`, and `label`; `facility` and `detail`
  default to `""` if absent. Raises `SupplementalDataError` with a clear
  message on invalid JSON, wrong shape, an unknown district, or an empty
  result.
- `add_from_document(provider, key, document_text, instruction) -> list[dict]`
  — orchestrates: build the prompt, call `ai_client.ask(provider, key,
  question, context)`, parse and validate the response, append the valid
  records with `source_document`/`added_at` filled in, return the newly
  added records (for the admin UI's summary). Lets `ai_client.AIProviderError`
  propagate as-is (the route already knows how to turn that into a 502);
  raises `SupplementalDataError` itself for validation failures.

## 5. Route & Admin UI

`POST /admin/api/supplemental-data` — auth-gated like every other
`/admin/api/*` route. Multipart form: `file` (the document), `instruction`
(optional text), `provider` (which of the five configured providers to
use). Server-side: `document_extraction.extract()` on the file (reusing
phase 4a's module and its existing error handling as-is), then
`supplemental_data.add_from_document(...)`, then a
`subprocess.run([sys.executable, "scripts/14_build_html_report.py"])`
re-run so the change shows up on the next dashboard load. Returns
`{"added": [...]}` (the newly added records) on success; a 4xx with
`{"detail": ...}` on an unsupported/corrupt file (same codes as 4a), a 400
on validation failure (`SupplementalDataError`), a 502 on a provider
failure (`AIProviderError`), matching the status-code conventions already
established by the `/api/ask` and `/admin/api/extract` routes. The
records are appended to the CSV *before* the rebuild subprocess runs, so
if the rebuild itself fails (non-zero exit code), the route still returns
`{"added": [...], "rebuild_warning": "..."}` with a 200 — the data was
saved successfully even if regenerating the HTML failed; a subsequent
successful rebuild (manual or from the next upload) will pick it up. It
never returns a failure status for a rebuild problem after data was
genuinely saved, matching this project's "typed exception, safe message,
never a raw error" posture without hiding that partial success happened.

The admin panel's existing "Extract Document" section gains: an optional
instruction `<textarea>`, a provider `<select>` (same five options as the
chat widget's `PROVIDER_OPTIONS`), and a new "Add to Report" button next
to the existing "Extract" (preview-only) button — both act on the same
file input, so the admin can preview extracted text first and separately
decide whether to add it to the report. A result area shows the returned
records (district/facility/category/label) on success, or the error
message on failure.

## 6. Report Rendering & AI Grounding

- `scripts/14_build_html_report.py` reads `supplemental_records.csv` (via
  a new small helper, `scripts/lib/supplemental_records.py`,
  `load_records()` mirroring `supplemental_data.load_records()`'s reading
  logic but living in `scripts/lib/` since the report-build script can't
  import from `server/`) and renders a new "Additional Facility & District
  Information" section: grouped district → facility → category, each
  record showing label + detail + source filename. Rendered even when the
  file doesn't exist or is empty — "No additional information has been
  added yet." — never a blank gap, consistent with the standard this
  project has held since the very first review of this report.
- `server/report_context.py`'s `build_context()` gets a new trailing
  section summarizing supplemental records (same district → facility →
  category grouping, condensed to fit the digest), so a chat question like
  "what equipment does Peshawar's DHQ Hospital have?" can be answered from
  it.

## 7. Testing Strategy

- `tests/server/test_supplemental_data.py` — `parse_ai_response()` unit
  tests (valid JSON, JSON wrapped in a code fence, invalid JSON, unknown
  district, missing required field, empty array); `append_records()` /
  `load_records()` round-trip using a `tmp_path`-based CSV, not the real
  data file; `add_from_document()` with `ai_client.ask` mocked (no real
  provider calls), covering success and a validation failure that surfaces
  as `SupplementalDataError`.
- `tests/server/test_document_extraction.py` gains two new format tests
  (`.txt`, `.csv`), inline-built fixtures, same pattern as phase 4a's
  existing tests.
- `tests/server/test_supplemental_data_route.py` — FastAPI `TestClient`
  against `/admin/api/supplemental-data`, with `document_extraction.extract`,
  `supplemental_data.add_from_document`, and the `subprocess.run` report
  rebuild all mocked — covering success, unauthenticated access, an
  extraction failure, and a validation failure, mirroring
  `test_extract_route.py`'s and `test_ask_route.py`'s established mocking
  patterns.
- `scripts/lib/supplemental_records.py`'s `load_records()` and the new
  report section get a unit test in the existing report-build test
  coverage style, covering both a populated and an empty/missing file.
- No test depends on a real AI provider call or a real
  `14_build_html_report.py` subprocess run in the route-test layer; the
  underlying pieces (`document_extraction`, `ai_client`,
  `14_build_html_report.py` itself) already have their own real-behavior
  coverage from earlier phases.

## 8. Roadmap (context for later phases — not this spec's scope)

- **Prompt-guided updates to existing pipeline data** (population,
  dev-stats health/roads/budget figures feeding `district_metrics.csv`)
  remains a separate, undesigned, deferred piece — this phase never writes
  to those files or to any computed column.
- **Phase 4c — Database ingestion**, unchanged from the phase 4a roadmap
  note: a distinct input mechanism needing its own credential-storage
  design.
- **Phase 1b — Methodology upgrade** (2SFCA/p-median), unchanged, still
  independent of the document-ingestion phases.
