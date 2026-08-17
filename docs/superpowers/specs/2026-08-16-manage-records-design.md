# KP Healthcare Plan — Manage Supplemental Records & Overrides

Status: Approved design, pre-implementation
Date: 2026-08-16

## 1. Purpose

Both AI-editable data stores this project has built so far — supplemental
facility/district facts (phase 4b, extended by DB ingestion in phase 4c)
and pipeline data overrides (phase 4d) — are pure append-only logs.
Nothing in the admin panel lets an admin see what's actually in either
store, or remove a record that turned out to be wrong (a bad AI
extraction, a duplicate upload, a stale override). The only fix today is
hand-editing the CSV on disk, which this project has never asked an admin
to do and which bypasses every safeguard (auth, validation, rebuild) the
rest of the admin panel provides.

This phase adds a read/delete view for both stores directly in the admin
panel: a table per store, a delete button per row, and the same
automatic-rebuild behavior every other write path in this project already
has.

## 2. Scope Decisions From Brainstorming

- **Both stores, not just one.** `supplemental_records.csv` (facility/
  district facts) and `metric_overrides.csv` (population/health-number
  overrides) are structurally identical problems — append-only CSV, no
  way to inspect or remove a row — so building the same CRUD shape for
  both now is barely more work than building it for one.
- **View + delete only, no in-place field editing.** If a record is
  wrong, the admin deletes it and re-adds a corrected one through the
  existing upload/DB-ingestion flow, which re-runs the full AI-extraction
  validation. In-place editing would let an admin write a value into the
  CSV that never went through that validation at all — a smaller, safer
  surface for this phase.
- **Delete triggers the same automatic rebuild add already does.**
  Deleting a supplemental record reruns the report build
  (`14_build_html_report.py`), matching `add_supplemental_data`'s existing
  behavior. Deleting an override reruns the downstream pipeline
  (`run_downstream.py`), matching `apply_metric_overrides`'s existing
  behavior. Without this, the report/pipeline output would silently drift
  out of sync with what the store actually contains until the next
  unrelated upload happened to trigger a rebuild.
- **A synthetic per-record `id`, not row position.** Neither CSV has a
  reliable per-row identifier today — `added_at` is written once per
  upload batch (`add_from_document` stamps `now` onto every record in a
  single call), so two records from the same upload share a timestamp.
  Row position is fragile: it silently points "delete" at the wrong row
  after any concurrent write or manual CSV edit. A generated `id` column,
  stable once written, avoids both problems.
- **Out of scope:** editing fields in place (above), pagination (record
  counts are small enough that a full table listing is fine — YAGNI), and
  any change to *how* records get added — AI extraction and DB ingestion
  keep working exactly as they do today, they just also stamp an `id`
  now.

## 3. Data Model Change

Both `server/supplemental_data.py` and `server/metric_overrides.py` gain
an `id` field, first in each module's `FIELDNAMES` tuple:

- `server/supplemental_data.py`: `("id", "district", "facility",
  "category", "label", "detail", "source_document", "added_at")`
- `server/metric_overrides.py`: `("id", "district", "file", "column",
  "value", "reason", "source", "added_at")`

**Generation, per record, not per batch.** Each module's `add_from_document`
currently stamps a shared `now` onto every record from one upload; it now
also stamps a fresh `uuid.uuid4().hex[:12]` onto *each* record
individually, so two records from the same upload get the same
`added_at` but different `id`s.

**Backfill for existing rows.** Both CSVs already have rows from earlier
sessions with no `id` column. `load_records()` in each module checks
whether the loaded rows are missing `id`; if so, it generates an id for
each such row **and rewrites the file immediately** so the backfilled ids
are persisted, not regenerated (and thus different) on the next load.
This keeps `id` stable across requests without a separate manual
migration step — consistent with this project's "no manual steps"
posture elsewhere (e.g. `07b_apply_metric_overrides.py` runs
unconditionally as part of the pipeline, it doesn't need to be invoked
by hand).

## 4. New Functions

One pair per module, same shape both places:

- `delete_record(record_id, path=<default>) -> bool` — loads all records
  via the existing `load_records`, filters out the one whose `id` matches
  `record_id`, and rewrites the file via the existing `append_records`-
  style writer (a full rewrite, not an append, since this is a removal).
  Returns `True` if a matching record was found and removed, `False` if
  no record had that id (lets the route return 404 without a separate
  existence check).

No new typed exception is needed — a missing id is a normal "nothing to
delete" outcome, not a validation failure, so it's surfaced as a 404 with
a plain `{"detail": "..."}` body, the same shape every other 404 in
`server/routes/admin.py` already uses (e.g. the unknown-provider case in
`test_key_route`).

## 5. New Routes

Added to `server/routes/admin.py`, following the existing per-feature-
area endpoint style (this project has never used a generic `/records/
{store}` dispatcher — each data store gets its own explicit route pair,
matching how `/admin/api/supplemental-data`, `/admin/api/metric-
overrides`, and `/admin/api/db/*` are already separate):

- `GET /admin/api/supplemental-data/records` → `{"records": [...]}`,
  every field including `id`, in file order.
- `DELETE /admin/api/supplemental-data/records/{id}` → deletes, then runs
  the same rebuild subprocess call `add_supplemental_data` already makes
  (`14_build_html_report.py`, same 300s timeout, same
  `{"deleted": true, "rebuild_warning": "..."}` shape on a rebuild
  failure/timeout as the existing `{"added": ..., "rebuild_warning":
  ...}` shape). Returns 404 (`{"detail": "No record with that id"}`) if
  `delete_record` returns `False` — no rebuild attempted in that case.
- `GET /admin/api/metric-overrides/records` → same shape, overrides
  store.
- `DELETE /admin/api/metric-overrides/records/{id}` → same pattern,
  rebuild step is `run_downstream.py` (matching `apply_metric_overrides`'s
  existing 600s timeout), same 404 behavior.

All four routes are auth-gated exactly like every other `/admin/api/*`
route: `_require_auth(kp_admin_session)` first, unauthorized short-circuit
returned unchanged.

## 6. Admin Panel UI

Two new sections in `server/admin_ui.py`'s `render_admin_panel`, using the
existing `.upload-section` block styling already defined for "Extract
Document" / "Update Pipeline Data" / "Database Ingestion":

- **"Supplemental Records"**, placed directly after "Extract Document".
- **"Pipeline Overrides"**, placed directly after "Update Pipeline Data".

Each section is a plain HTML table (one row per record, one column per
field except `id`, which is used only as the row's delete-button target,
never displayed) populated by a `GET .../records` call fired on admin
panel page load. Each row has a Delete button on the right.

**Two-step inline confirm, not a native `confirm()` dialog.** Clicking
Delete turns that row's button into "Confirm delete?"; clicking again
sends the `DELETE` request. Clicking anywhere else resets it back to
"Delete". This avoids a blocking native browser dialog (nicer UX for a
real admin, and avoids the exact dialog-blocking failure mode this
project's own browser-automation tooling has to steer around) while still
requiring a deliberate second action before data is removed.

On successful delete, the row is removed from the table immediately and
any `rebuild_warning` is shown inline in that section's status area, the
same place/style the upload sections already show theirs. On a 404 (record
already gone, e.g. deleted from another tab), the row is removed from the
table anyway and no error is shown — the end state the admin wanted is
already true.

## 7. Error Handling

Follows the pattern every other route in this project already uses:
typed/HTTP errors carry a message safe to show the admin directly, never
a raw traceback. The only new failure modes here are "not authenticated"
(401, existing `_require_auth` path, unchanged) and "no record with that
id" (404, new but shaped like the existing unknown-provider 404). Rebuild
failures reuse the existing `rebuild_warning`-in-a-200-response pattern —
the delete itself already succeeded by the time a rebuild could fail, so
it is reported as a warning, not an error, exactly like the add path
already does.

## 8. Testing

- `server/supplemental_data.py` / `server/metric_overrides.py`:
  - `delete_record` removes the correct row and leaves others untouched.
  - `delete_record` returns `False` for an unknown id.
  - `load_records` backfills missing ids on a legacy (no-`id`-column)
    file and persists them (a second `load_records` call on the same
    path returns the same ids as the first).
  - `add_from_document` stamps a distinct `id` per record within a
    single batch, while `added_at` stays shared.
- `server/routes/admin.py`:
  - Each new route 401s without a valid session, matching every existing
    `/admin/api/*` test.
  - `GET .../records` returns the full record list including `id`.
  - `DELETE .../records/{id}` on a real id: 200, record gone from a
    follow-up `GET`, rebuild subprocess invoked (mocked, matching the
    existing add-path test pattern).
  - `DELETE .../records/{id}` on an unknown id: 404, no rebuild
    subprocess call made.
  - Rebuild-failure/timeout paths surface `rebuild_warning`, matching the
    existing add-path tests for the same subprocess call.

Manual verification (per this project's established cadence): live
end-to-end against the real running admin server — upload a document to
create a couple of supplemental records, confirm they appear in the new
table, delete one via the two-step confirm, confirm the report rebuilds
and the deleted fact is gone from it. Same for an override: apply one,
confirm it appears, delete it, confirm the pipeline rebuild removes its
effect from `district_metrics.csv`/the report.
