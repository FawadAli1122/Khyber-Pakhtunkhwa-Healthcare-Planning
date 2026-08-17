# Admin-Defined Custom Data Tables — Design Spec

## 1. Problem

The report and dashboard currently only capture data that fits one of a
small number of fixed, hardcoded shapes: district/health metrics, three
admin-overlay tables (`supplemental_records`, `metric_overrides`,
`bot_facilities`), and facilities. If the admin has data that doesn't fit
any existing column set (e.g. cold-chain equipment status per facility,
staff training records, vehicle fleet per district), there is currently
no way to capture it as real structured data — at best it gets crammed
into `supplemental_records`' free-text `detail` field.

This feature lets the admin define entirely new tables (with their own
named, typed columns) directly from the admin panel, populate them the
same way existing data is populated (document upload + AI extraction),
and have that data appear in the generated HTML report — with the AI
deciding how best to title, summarize, and place each new section,
while the report's actual markup generation stays fully deterministic.

## 2. Scope

**In scope:**
- Admin creates/deletes real Postgres tables and columns from the admin
  panel, either via an explicit form or from a natural-language prompt
  (AI proposes a schema, admin reviews/edits before it's created).
- Admin adds data to a custom table via document upload + AI extraction,
  exactly like the existing `supplemental_records`/`metric_overrides`
  flow, generalized to the table's own column set.
- The HTML report gains a new section per custom table, with the AI
  choosing the section's title, a short narrative summary, and where in
  the report it's placed (a new top-level section, or attached after an
  existing section) — validated against a fixed allowlist of real
  section anchors, never trusted blindly.
- The "Ask AI" chat/bot digest (`report_context.build_context`) includes
  a summary of each custom table, so questions about this data can be
  answered the same way questions about existing data already are.
- Deleting a row, a whole table, or a column reverts the report/
  dashboard immediately via the same lightweight rebuild every other
  admin-panel delete already triggers (re-running only
  `14_build_html_report.py`, not the full pipeline) — this is the
  feature's entire "revert" mechanism; there is no separate version-
  history/snapshot system (explicitly decided against — see open
  questions in prior discussion).

**Out of scope (explicit non-goals for this pass):**
- No GIS/map/choropleth integration, even for tables with lat/lon-
  looking columns — same reasoning as the Facility Readiness feature:
  partial map coverage would mislead more than help.
- No column *type changes* or *renames* after creation (only add-column,
  drop-column, drop-table, and delete-row are supported mutations) —
  YAGNI; can be added later if actually needed.
- No true version-history/snapshot/rollback of the whole report or
  dashboard independent of the underlying data — explicitly decided
  against in favor of the simpler, already-established "delete data →
  rebuild" pattern.
- No free-form AI-authored HTML in the report — the AI only ever
  returns bounded structured JSON (title/narrative/placement); Python
  renders all markup deterministically, matching every other AI usage
  in this app.

## 3. Data Model

Two new **fixed-schema system tables**, created once in
`scripts/lib/local_db.py`'s `SCHEMA_SQL` alongside the existing three:

```sql
CREATE TABLE custom_tables (
    id TEXT PRIMARY KEY,
    label TEXT,        -- admin-facing display name, e.g. "Cold Chain Equipment"
    table_name TEXT,    -- real Postgres identifier, e.g. "custom_cold_chain_equipment"
    created_at TEXT,
    report_title TEXT,      -- AI-chosen section title (see section 6) - "" until data is first added
    report_narrative TEXT,  -- AI-chosen narrative summary - "" until data is first added
    report_placement TEXT   -- "new_section" or "after:<anchor>" - "" until data is first added
);
CREATE TABLE custom_table_columns (
    id TEXT PRIMARY KEY,
    custom_table_id TEXT,   -- references custom_tables.id (app-level FK, not a DB constraint - matches this project's existing tables, which don't use DB-level FKs either)
    label TEXT,              -- admin-facing display name, e.g. "Last Checked"
    column_name TEXT,        -- real Postgres identifier, e.g. "last_checked"
    column_type TEXT         -- one of: "text" | "number" | "date"
);
```

Registry rows are plain `TEXT` data manipulated via the *existing*
generic `local_db.fetch_all`/`insert_many`/`delete_by_id` helpers —
these two tables need no new code beyond their `CREATE TABLE` in the
schema; they're shaped exactly like `supplemental_records` etc.

**Each admin-defined table is a real, separately created Postgres
table:**

```sql
CREATE TABLE custom_<slug> (
    id TEXT PRIMARY KEY,
    added_at TEXT,
    <column_name> <SQL_TYPE>,   -- one column per admin-defined field
    ...
);
```

`<SQL_TYPE>` is always one of exactly `TEXT` / `NUMERIC` / `DATE` — the
admin/AI choose a type from `{text, number, date}`, never raw SQL.
Row-level CRUD against these dynamic tables also reuses the *existing*
generic `local_db.fetch_all`/`insert_many`/`delete_by_id` helpers
unchanged (they already accept any table name and field list).

## 4. Identifier Safety

New `scripts/lib/local_db.py` Part C, alongside the existing Part A
(generic CRUD) and Part B (bootstrap):

```python
def create_table(table_name, columns):
    """columns: list of (column_name, sql_type) tuples, already validated."""

def add_column(table_name, column_name, sql_type):
    ...

def drop_column(table_name, column_name):
    ...

def drop_table(table_name):
    ...
```

Every identifier (`table_name`, `column_name`) is:
1. Derived from the admin's/AI's human-readable label via a strict slug
   function (lowercase, `[a-z0-9_]` only, collapse whitespace/
   punctuation to underscores, truncate to stay well within Postgres's
   63-byte identifier limit once prefixed).
2. Validated against `^[a-z][a-z0-9_]*$` before use.
3. Prefixed (`custom_` for tables) so it can never collide with
   `supplemental_records`/`metric_overrides`/`bot_facilities`/
   `custom_tables`/`custom_table_columns` or any future system table.
4. Checked for a real collision against the `custom_tables`/
   `custom_table_columns` registry (case-insensitive) — a genuine
   duplicate name is rejected with a clear error, never silently
   deduplicated.
5. Every DDL statement (`create_table`/`add_column`/`drop_column`/
   `drop_table`) is built with `psycopg2.sql.Identifier`/`sql.SQL`,
   never Python string interpolation of a name.

   **Note on the reused row-level CRUD helpers**: `fetch_all`/
   `insert_many`/`delete_by_id` (reused unchanged for row data, per
   section 3) predate this feature and build their queries via plain
   f-string interpolation of the `table` argument, not
   `sql.Identifier` — safe today only because every existing caller
   passes one of a small set of names the app itself hardcodes, never
   external input. This feature does not change that; it relies
   instead on the fact that a `table_name`/`column_name` can *only*
   ever be produced by step 1-4 above (derived, validated, and
   registered once, at creation/`add_column` time) — every later
   read/insert/delete call sources the name from the `custom_tables`/
   `custom_table_columns` registry, never freshly re-derived from raw
   admin/AI text. The DDL functions get the stronger `sql.Identifier`
   treatment because they are both the highest-consequence operation
   (schema changes) and the one place a `sql_type` string is also
   interpolated.

`sql_type` itself is never taken from admin/AI text directly — it's
mapped from the fixed `{text, number, date}` choice to `{TEXT, NUMERIC,
DATE}` via a lookup dict, so no arbitrary SQL type can ever reach a
DDL statement either.

A new `CustomDataError` (matching every other module's typed-exception
pattern in this app — message always safe to show the admin directly)
is raised for: invalid/empty names, name collisions, unknown column
types, and AI-extracted values that don't match a column's declared
type (e.g. non-numeric text for a `number` column).

## 5. Admin UI

New **"Custom Data"** section in the admin panel:

- **Create Table** — two entry points into the same underlying
  create-table action:
  - *Explicit form*: table label + repeatable (column label, column
    type) rows.
  - *From a prompt*: admin types a natural-language description (e.g.
    "track cold-chain equipment status per facility"); AI proposes a
    table label + column list as the same structured shape the form
    produces; **shown as an editable preview — never auto-created**.
    Admin can edit any name/column, then clicks "Create Table" to
    commit the real `CREATE TABLE`.
- **Add Column** — available on any existing custom table at any time
  (label + type → real `ALTER TABLE ADD COLUMN`). Postgres allows this
  on a table that already has rows without requiring a default — the
  new column is simply `NULL` on every pre-existing row, which the
  report/extraction/digest code all already treat as "no value" (same
  as an empty CSV cell elsewhere in this app).
- **Add Data** — pick a target custom table, upload a document (or type
  an instruction); AI extracts rows matching that table's actual
  columns (same two-step "preview extraction → Add to Table" pattern as
  `supplemental_data`'s existing "Extract → Add to Report" flow). This
  same request's AI provider/key is also used for the report-placement
  decision (section 6) for that table, computed once here and stored -
  not a separate admin action.
- **Browse / Delete** — each custom table is listed with its rows in a
  data table; two-step-confirm delete (matching every existing
  admin-panel delete) is available at three levels: delete a row,
  delete a column, delete a whole table.

Every action triggers the same lightweight rebuild
(`add_supplemental_data`'s existing `REPORT_BUILD_SCRIPT` subprocess
pattern — re-running only `14_build_html_report.py`).

## 6. AI's Two Roles

**Role 1 — Data extraction** (same shape as existing
`supplemental_data.add_from_document`/`metric_overrides.add_from_document`):
given a document/instruction and a target table's column list, the AI
returns a JSON array of rows matching those columns. Validated against
each column's declared type before insertion.

**Role 2 — Report placement** (new, revised from an earlier draft of
this spec — see rationale below): computed by `server/custom_data.py`
**once, whenever data is added to a table** (reusing that same request's
already-selected AI provider/key), not live inside
`14_build_html_report.py`. Skipped entirely for a table with zero rows
(matches the Facility Readiness section's existing "no data yet"
precedent of simply not appearing). The AI is given the table's
columns, a sample of its rows, and the report's real list of existing
section anchors, and returns:

```json
{"title": "...", "narrative": "...", "placement": "new_section"}
```

`placement` must be exactly `"new_section"` or `"after:<anchor>"` where
`<anchor>` is one of the report's actual `<section id="...">` values
(`current-state`, `infrastructure-context`, `terrain-elevation`,
`district-data`, `findings`, `future-planning`, `supplemental-data`,
`facility-readiness`, etc.) — anything else (hallucinated anchor,
malformed JSON, wrong shape) falls back to `new_section` automatically,
matching this app's existing "implausible AI proposals are rejected,
not trusted blindly" pattern for pipeline overrides. The result is
stored on the `custom_tables` row (`report_title`/`report_narrative`/
`report_placement`) and only recomputed the next time data is added to
that specific table — not on every unrelated report rebuild.

**Why not a live call at report-build time (the original design):**
`14_build_html_report.py` is a pipeline script; this codebase has a
strict one-way import rule (`server/` may import `scripts/lib/`, never
the reverse) and `ai_client`/`keystore` (the only things that know how
to call an AI provider or hold its key) live under `server/`. A live
call from inside the report-build script is architecturally impossible
without violating that boundary. It would also be wasteful and
provider-less in practice: that script reruns on *every* admin action
across *all* admin-overlay tables (e.g. deleting an unrelated bot
facility), which would mean re-calling the AI for every existing custom
table on every unrelated rebuild, with no admin-selected provider/key
even available in that context. Computing it once, at the moment data
is actually added (where a provider/key is already part of the
request), avoids both problems.

At report-build time, `14_build_html_report.py` (via a new
`scripts/lib/custom_tables.py`) just reads each custom table's stored
`report_title`/`report_narrative`/`report_placement` plus its current
rows, and deterministically renders an HTML table of the real row data
(properly escaped) under that title, with the stored narrative text
(escaped) above it, inserted at the resolved anchor point. **No AI
import, and no AI call, happens inside `scripts/` at all.** The AI
never authors HTML directly — same safety property as every other AI
usage in this codebase.

## 7. Ask AI / Telegram Bot Digest

`server/report_context.build_context()` gains a summary of each custom
table (table label, column names, row count) so "Ask AI" chat and the
Telegram bot's `/ask` can answer questions about this data — consistent
with the standing direction that the assistant should be able to answer
"any kind of planning" question, not just what's hardcoded.

## 8. Testing

- `local_db.py`'s new `create_table`/`add_column`/`drop_table` and the
  identifier-validation/slug functions are plain `psycopg2` calls (not
  subprocess orchestration like Part B's bootstrap functions) — they
  get mocked unit tests the same way `fetch_all`/`insert_many`/
  `delete_by_id` already do.
- `server/custom_data.py`'s AI-prompt-building and validation logic
  (schema inference, row-type validation, placement-anchor validation/
  fallback) gets unit tests the same way `supplemental_data.py`'s
  equivalents do — every AI provider call mocked.
- `scripts/lib/custom_tables.py` (registry reads, row reads via
  `local_db.fetch_all`, and the deterministic HTML rendering function)
  gets unit tests the same way `scripts/lib/supplemental_records.py`/
  `14_build_html_report.py`'s existing render functions already do —
  `local_db` calls mocked, no AI involved at all on this side.
- Real end-to-end DDL execution (an actual `CREATE TABLE`/`ALTER
  TABLE`/`DROP TABLE` against the bundled database) is verified
  manually against the real running server, matching this project's
  established pattern for anything that touches the real database
  process.

## 9. Open Questions / Risks Explicitly Accepted

- A trusted, already-authenticated admin can create an unbounded number
  of tables/columns — no quota is enforced. Acceptable for a
  single-admin local tool; not exposed to untrusted users.
- The AI's schema-inference (Role 1 above, table-creation path) and
  report-placement (Role 2) are both real LLM calls whose *quality*
  (a sensible title, a reasonable narrative, a sensible placement
  choice) is not something this design can fully guarantee — only its
  *safety* (never arbitrary DDL, never arbitrary HTML) is enforced by
  construction. A bad AI suggestion is always reviewable/editable
  (creation) or simply a slightly awkward report section (placement) —
  never a corrupted report or an unsafe database operation.
