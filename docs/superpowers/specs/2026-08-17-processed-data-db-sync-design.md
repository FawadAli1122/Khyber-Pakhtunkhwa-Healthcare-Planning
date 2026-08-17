# Processed Data → Database Sync — Design

## 1. Goal

Give this app one centralized, properly-designed Postgres home for every
file currently living in `data/processed/` — the pipeline's computed
outputs (district metrics, merged facilities, terrain, land cover, dev
stats, suggested sites, cross-validation) and its raw/cache inputs
(district boundary geometry, the two facility geocoding caches) alike —
so the admin panel and the Telegram bot can browse (and, incidentally,
edit) this data the same generic way they already browse the app's
other database tables, instead of it only ever existing as scattered
CSV/JSON files on disk.

User confirmed via `AskUserQuestion`: (1) the goal is central browsing,
not making pipeline-computed data durable/hand-editable — the pipeline
stays the source of truth and keeps recomputing wholesale; (2) scope is
"all the files possible to be hosted... should be in the database," i.e.
every file in `data/processed/`, not just the tabular CSVs; (3) the
pipeline scripts themselves stay file-based (lower risk, no rewrite of
~15 already-shipped, already-tested numbered scripts) — Postgres is
populated as a properly-typed, queryable copy refreshed after every
pipeline run.

## 2. Non-goals

- **Not making the database the pipeline's working storage.** No
  numbered script (`01`–`24`) changes its own file I/O. They keep
  reading/writing `data/processed/*.csv`/`*.json` exactly as today —
  this is confirmed, not a lower-priority deferral.
- **Not a durability/versioning layer for pipeline outputs.** A row
  edited via the Database Browser or `/localedit` in one of the new
  tables will be silently overwritten the next time any pipeline run
  (full or downstream) resyncs it — same as hand-editing a CSV today.
  This is the same trade-off already made and documented for
  `custom_tables`/`custom_table_columns` in the Database Browser spec:
  editing is allowed with no special-casing, the risk is the admin's to
  accept, not code-enforced.
- **Not a relational schema with foreign keys across tables.** Every
  table gets its own `id TEXT PRIMARY KEY` (matching every other table
  in this schema, and what lets the already-shipped generic browser code
  work against them unmodified) but no cross-table FK constraints — this
  app never does server-side joins across these tables (every consumer
  is a per-table `fetch_all`), and FK integrity across a per-table
  drop-and-recreate resync would add real ordering/constraint-management
  risk for no exercised benefit. "Properly designed" here means correct
  per-column *types*, not a normalized/joined relational model.
- **No new report/UI section.** The report (`14_build_html_report.py`)
  keeps reading straight from `data/processed/*` exactly as today — it
  has no reason to read its own just-computed data back out of Postgres
  a few seconds later. This feature's whole value is the admin/bot
  browsing surface, which already exists generically.

## 3. Architecture

**One new pipeline stage, `scripts/25_sync_processed_to_db.py`,** run
last — appended after `14_build_html_report.py` in the `STAGES` list of
all three existing runner scripts (`run_all.py`, `run_downstream.py`,
`run_downstream_facilities.py`), since all three already end at that
same stage and all three can leave `data/processed/` in a new state.

The script does one thing, unconditionally, every time it runs: read
every file currently in `data/processed/` off disk and fully reload it
into its corresponding Postgres table(s). It does not know or care which
upstream stage actually ran — always-resync-everything is simpler and
strictly correct (a table whose source file didn't change this run just
gets reloaded with identical data — cheap, since the largest source
file is ~1,600 rows), matching this pipeline's existing "idempotent,
safe to rerun" convention stated in `run_all.py`'s own docstring.

Internally, structured as one pure "build typed rows" function per
source file (`_rows_from_district_metrics(path)`, `_rows_from_boundaries(path)`,
etc. — each takes a file path, returns `(columns, rows)` where `columns`
is `[(name, type), ...]` and `rows` is `[{...}, ...]` with values already
cast to real Python types) plus a thin `main()` that calls each builder
and passes its result to `local_db.replace_table()`. This mirrors the
"pure function + thin I/O orchestration" split used throughout this
codebase (`custom_data.py`, `facility_readiness.py`, `landcover.py`,
`routing.py`, `24_compute_landcover_zonal_stats.py`) and is what makes
the builders unit-testable without a real database — same technique as
`tests/test_landcover_zonal.py`, which imports a numbered script
directly via `importlib.import_module("scripts.24_compute_landcover_zonal_stats")`
since a leading digit makes normal `from X import Y` syntax invalid.

**One new generic primitive in `scripts/lib/local_db.py`:**

```python
def replace_table(table_name, columns, rows):
    """columns: [(column_name, column_type), ...], column_type one of
    COLUMN_TYPE_SQL's keys (now including "boolean"/"json" alongside the
    existing "text"/"number"/"date"). rows: [{"id": ..., <column>: ...}, ...] -
    every row must include "id" (the caller generates it, matching every
    other table's convention). Validates table_name and every column
    name via validate_identifier() (existing function, unchanged) before
    building any SQL. One transaction: DROP TABLE IF EXISTS -> CREATE
    TABLE (id TEXT PRIMARY KEY + the given typed columns) -> bulk INSERT
    every row -> commit. A drop-and-recreate, not an ALTER-in-place
    migration, matching this stage's own "wholesale recompute" nature -
    there is no existing data in these tables worth preserving across a
    schema change, unlike the admin-overlay tables which accumulate
    real, otherwise-unrecoverable user input over time."""
```

Unlike `create_table()` (which always prepends both `id TEXT PRIMARY
KEY` and `added_at TEXT`), `replace_table()` only ever prepends `id` —
these source files have no natural "row added" timestamp, and forcing
one on every row would just mean 19 tables' worth of a fabricated,
meaningless `added_at` value. This isn't a new gap: `custom_table_columns`
already lacks `added_at`/`created_at` today, so every consumer of this
schema (the Database Browser chief among them) already has to tolerate
a table without one.

`COLUMN_TYPE_SQL` gains two entries: `"boolean": "BOOLEAN"` (for
`pipeline_facilities_marham_raw.has_real_coords`) and `"json": "JSONB"`
(for the two geometry columns). JSONB values are passed through
`psycopg2.extras.Json(...)` at insert time — the one new wrinkle
`replace_table()` needs beyond what `insert_many()` already does, since
a plain `dict` isn't otherwise adapted correctly by `psycopg2.executemany`.

**Reading `replace_table()`'s output back out already works today with
zero changes** — `local_db.fetch_all()`, `list_all_tables()`,
`list_columns()`, `update_by_id()` are all fully generic over table
name; the admin panel's Database Browser section and the bot's
`/localtables`/`/localview`/`/localedit` already query
`information_schema` directly rather than any hardcoded table list, so
every new `pipeline_*` table appears there automatically the next time
the sync stage runs. This is the entire "allow admin and Telegram bot to
access that database" half of the request.

## 4. Table mapping

18 files -> 19 tables, each prefixed `pipeline_` (distinct from the
admin-overlay tables `supplemental_records`/`metric_overrides`/
`bot_facilities`, the registry tables `custom_tables`/
`custom_table_columns`, and any admin-created `custom_<slug>` table —
no collision risk, `pipeline_` is not a prefix any existing feature
uses or reserves). Types below were determined by inspecting the real
files' real headers/values, not assumed.

| Source file | Table(s) | Notable typing |
|---|---|---|
| `district_metrics.csv` | `pipeline_district_metrics` | `district`/`division`/`terrain`/`need_tier` TEXT, all 21 remaining columns NUMERIC |
| `kp_district_population_2023.csv` | `pipeline_population` | `source_url` TEXT, `prior_census_year` NUMERIC |
| `facilities_merged.csv` | `pipeline_facilities` | `beds` NUMERIC nullable (blank -> NULL, not 0); `public_private`/`is_duplicate_of` TEXT nullable |
| `facility_cross_validation.csv` | `pipeline_facility_cross_validation` | straightforward TEXT/NUMERIC |
| `district_terrain.csv` | `pipeline_district_terrain` | all NUMERIC except `district` |
| `district_travel_time.csv` | `pipeline_district_travel_time` | `point_source` TEXT, rest NUMERIC |
| `district_landcover.csv` | `pipeline_district_landcover` | `district`/`dominant_class` TEXT, 12 percentage/area columns NUMERIC |
| `landcover_composition.csv` | `pipeline_landcover_composition` | `class_value` NUMERIC, `label` TEXT |
| `suggested_sites.csv` | `pipeline_suggested_sites` | `priority` NUMERIC, `rationale` TEXT |
| `dev_stats_health.csv` | `pipeline_dev_stats_health` | all NUMERIC except `district` |
| `dev_stats_immunization.csv` | `pipeline_dev_stats_immunization` | all NUMERIC except `district` |
| `dev_stats_malaria.csv` | `pipeline_dev_stats_malaria` | all NUMERIC except `district` |
| `dev_stats_patients_treated.csv` | `pipeline_dev_stats_patients_treated` | all NUMERIC except `district` |
| `dev_stats_roads.csv` | `pipeline_dev_stats_roads` | all NUMERIC except `district` |
| `dev_stats_budget.json` (dict keyed by fiscal year) | `pipeline_dev_stats_budget` | 2 rows; `fiscal_year` TEXT (the dict key, e.g. `"fy2024_25"`), 6 NUMERIC columns |
| `boundaries.json.districts[]` | `pipeline_district_boundaries` | `district`/`division` TEXT, `area_km2` NUMERIC, `geometry` JSONB |
| `boundaries.json.province_geometry` | `pipeline_province_boundary` | 1 row; `label` TEXT (`"Khyber Pakhtunkhwa"`), `geometry` JSONB |
| `kphcc_facilities_geocoded.json` | `pipeline_facilities_kphcc_raw` | `issue_date`/`expire_date` DATE, `beds` NUMERIC nullable (115/276 rows are `null`), rest TEXT/NUMERIC |
| `marham_facilities_geocoded.json` | `pipeline_facilities_marham_raw` | `has_real_coords` BOOLEAN, rest TEXT/NUMERIC |

## 5. Null and blank-value handling

CSV blanks are the empty string `""`, which Postgres rejects for
NUMERIC/DATE columns (`invalid input syntax`). Every builder function
coerces `""` (CSV) or Python `None` (JSON, e.g. `kphcc`'s `beds: null`)
to SQL `NULL` for any non-TEXT column, and leaves TEXT columns as `""`/
`None` as-is (TEXT accepts both). This is the same "known missing
value, not zero" distinction `local_db._normalize_value()` already
makes on the *read* side (its `None -> ""` normalization) — here applied
on the *write* side, in the opposite direction, because these source
files can have real gaps (e.g. a facility with no bed count on record)
that a NUMERIC `0` would misrepresent as "confirmed zero beds."

## 6. `/localview` cell-length cap (small, generic fix)

`pipeline_district_boundaries`/`pipeline_province_boundary`'s geometry
column holds full polygon coordinate data — thousands of characters for
a single value. `telegram_admin_db.py`'s `localview_command` currently
caps the number of *rows* shown (`MAX_LISTED_LOCAL_ROWS`) but not the
length of any individual cell, so a single boundary row could push one
Telegram message past the platform's 4096-character limit and fail to
send outright.

Fix: a small `_truncate_cell(value, limit=200)` helper, applied to every
cell value `localview_command` renders (not just geometry columns) —
values longer than `limit` are cut to `value[:limit] + "… (N more
chars, see admin panel)"`. This is a generically useful guard for *any*
future large-value column (a Custom Data Table's own free-text narrative
column could already be long today), not a special case wired to this
one table.

## 7. Wiring

- `scripts/run_all.py`, `scripts/run_downstream.py`,
  `scripts/run_downstream_facilities.py`: each `STAGES` list gets
  `"25_sync_processed_to_db.py"` appended after
  `"14_build_html_report.py"`.
- `scripts/25_sync_processed_to_db.py` assumes the bundled local
  database is already running when invoked, exactly like
  `07_merge_facilities.py`'s existing direct `local_db.fetch_all()` call
  — no numbered script calls `local_db.ensure_running()` itself
  (only `server/app.py`'s lifespan does); running the pipeline standalone
  without the server (or a manually-started bundled Postgres) already
  requires the database to be up for the existing `07`/`07b` stages
  today, so this isn't a new operational requirement, just one more
  stage that shares it.

## 8. Error handling

- `replace_table()`'s single transaction means a failure partway through
  one table's reload (e.g. a bad cast) rolls that one table back to its
  prior state rather than leaving it half-populated — but does not roll
  back tables already committed earlier in the same `main()` run. This
  matches every other multi-stage script in this pipeline: a mid-run
  failure stops the run (`run_all.py`'s existing `STAGES` loop already
  aborts on any non-zero exit code) and is safe to simply re-run from
  the top once fixed, not a new failure mode this feature introduces.
- A malformed/missing source file (e.g. `data/processed/` partially
  cleaned) raises a clear `LocalDbError`-style message identifying which
  file, not a raw traceback — matching every other typed exception in
  this codebase.

## 9. Testing plan

- Each `_rows_from_*(path)` builder: real unit tests against tiny
  fixture CSV/JSON files (`tmp_path`), asserting correct typing
  (blank/`null` -> `None`, numeric strings -> `int`/`float`, the two
  JSON geometry builders produce the right dict shape) — pure functions,
  no database dependency.
- `local_db.replace_table()`: mocked-`psycopg2` unit tests matching
  `tests/lib/test_local_db.py`'s existing `FakeCursor`/`FakeConnection`
  pattern — asserts the right `DROP`/`CREATE`/`INSERT` sequence, correct
  type SQL per column, `Json(...)` wrapping for `"json"`-typed values,
  and rejection of an invalid table/column name before any SQL runs.
- `telegram_admin_db.py`'s new `_truncate_cell()`: pure function, direct
  unit test (under limit unchanged, over limit cut with the note,
  boundary-length edge case).
- Live verification: run the real sync against the real
  `data/processed/`, confirm via `psql` that all 19 tables exist with
  the expected row counts and spot-checked correct values (including a
  boundary row's geometry and a null-beds `kphcc` row), confirm all 19
  appear in the real admin Database Browser and `/localtables`, view a
  large table (`pipeline_facilities`, ~1,584 rows) via both surfaces to
  confirm the existing row-count cap still degrades gracefully, view
  `pipeline_district_boundaries` via `/localview` to confirm the new
  cell-truncation guard actually prevents the send failure it's meant
  to prevent, edit one row via each surface and confirm the change is
  real, then re-run the sync stage and confirm that edit is (correctly,
  expectedly) overwritten back to the pipeline's real computed value.
