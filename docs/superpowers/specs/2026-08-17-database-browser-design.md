# Database Browser — Design

## 1. Goal

A single, generic "view every table in the bundled local database, edit
any row" capability, exposed via both the admin panel and Telegram —
distinct from and complementary to the app's existing per-store UIs
(Supplemental Records, Pipeline Overrides, Bot-Added Facilities, Custom
Data Tables each already have their own dedicated add/delete UX, which
this feature does not change) and from Database Ingestion (`/dbconnect`
etc., which browses an *external* database the admin configures, never
the bundled one).

User explicitly confirmed, via `AskUserQuestion`: editing is allowed on
every table with no exceptions, **including** the two internal registry
tables (`custom_tables`, `custom_table_columns`) that track Custom Data
Tables' own structure — accepting the risk that a careless edit there
(e.g. changing `custom_tables.table_name` without renaming the real
Postgres table) can desync the registry from reality and break the
Custom Data Tables feature. This is a deliberate choice on the user's
own local instance, not an oversight.

## 2. Non-goals

- Not a browser for an *external* database (that's Database Ingestion's
  job, and it's read-only by design — writing to a user's external
  production database is a materially bigger, more dangerous capability
  than editing the bundled local one, and wasn't asked for).
- Not a schema editor — no adding/renaming/dropping tables or columns
  through this feature (Custom Data Tables' own `/newtable`, "Add
  Column", "Delete Table" already own that for the tables it created;
  this feature only ever touches row *values*, via `UPDATE ... SET`).
- Not a bulk/multi-row editor — one row edited at a time, matching every
  other mutation this project already has (add one record, delete one
  record).
- No change to the existing dedicated per-store admin sections
  (Supplemental Records/Overrides/Bot Facilities/Custom Data Tables) —
  they keep their current add/delete-only UX. The new Database Browser
  section is a separate, additional, unified view that happens to cover
  the exact same underlying tables (and every other table) generically,
  so "view/edit every already-present table" is satisfied without
  touching already-shipped, already-tested code.

## 3. Architecture

**Core primitives — `scripts/lib/local_db.py` (extends the existing
Part C, the only genuinely new code at this layer):**

```python
def list_all_tables():
    """Every real table in the bundled database's public schema - both
    the app's own known overlay/registry tables and any dynamically-
    created custom_<slug> table. Queries information_schema directly
    rather than any app-level registry, so it can never be stale."""

def list_columns(table):
    """[{"name": str, "type": str}, ...] for `table`, via
    information_schema.columns - `type` is the raw Postgres type name
    (e.g. "text", "numeric", "date"), used for lightweight edit-value
    coercion (see section 5)."""
```

Both query `information_schema` with the table name as a bound
parameter (`WHERE table_name = %s`), never interpolated — no injection
risk regardless of caller. Everything else needed already exists:
`fetch_all(table)` (no `order_by` required — works on any table with an
`id` column, which every table in this schema has) and
`update_by_id(table, record_id, fields)` (already generic, already used
internally by `custom_data.py` for one thing).

**Table-name validation, once, at the boundary:** `fetch_all`/
`update_by_id` build their SQL via plain f-string interpolation into the
table name (the established, already-audited pattern for this module —
safe today only because every existing caller sources the table name
from its own hardcoded constant or a validated registry). The Database
Browser is the first caller to accept a table name **from user input**
(admin form field or Telegram command argument), so the new server-layer
module below checks it against a fresh `list_all_tables()` call before
ever passing it to `fetch_all`/`update_by_id`/`list_columns` — the same
"validate once, at the one place raw input enters the system" principle
already documented for Custom Data Tables' own DDL safety, applied here
per-request instead of per-creation since there's no registry to trust.

**New `server/db_browser.py`:**

```python
def list_tables():
    """Every real table name - server.db_browser is the only module
    that ever exposes list_all_tables() outward."""

def get_table_columns(table):
    """[{"name", "type"}, ...] or None if `table` isn't real."""

def get_table_rows(table):
    """List of row dicts (via local_db.fetch_all(table, order_by="id"))
    or None if `table` isn't real. Ordered by `id` - not a timestamp,
    since not every table in this schema has one (custom_table_columns
    has no added_at/created_at column at all) - but every table's own
    "id TEXT PRIMARY KEY" (see local_db.create_table()) makes ordering
    by it fully deterministic: the same set of existing rows always
    sorts identically across separate calls, which /localedit's row-
    number resolution (section 4) depends on. The resulting order is
    stable but not meaningful (ids are random uuid hex, not sequential)
    - a real trade-off for a browser that must work uniformly across
    tables with and without a timestamp column, unlike the fixed-store
    listings elsewhere in this app which order by added_at/created_at
    and so show newest-first."""

def update_row(table, record_id, fields):
    """fields: {column_name: raw_value} from the browser/bot - coerced
    per-column via _coerce_value() (section 5) before update_by_id().
    Returns True/False/None (None = table doesn't exist)."""
```

**Admin routes (`server/routes/admin.py`), auth-gated exactly like every
existing route:**
- `GET /admin/api/db-browser/tables` → `{"tables": [...]}`
- `GET /admin/api/db-browser/tables/{table}/rows` → `{"columns": [...], "rows": [...]}` (404 if `table` isn't real)
- `PUT /admin/api/db-browser/tables/{table}/rows/{record_id}` → applies
  `fields` from the request body, triggers `rebuild_report()` (matching
  every other data-mutating admin route), returns `{"updated": true}` +
  optional `rebuild_warning`, or 404 if the table/row doesn't exist.

**Admin UI (`server/admin_ui.py`):** new "Database Browser" section — a
`<select>` populated from the tables route; choosing one loads its
columns+rows and renders a plain HTML table with one editable `<input>`
per column, except `id` (present on every table, and the primary key
`update_by_id`'s own `WHERE id = %s` depends on) and `added_at`/
`created_at` where present (not every table has one -
`custom_table_columns` has neither - but where they exist they're
provenance timestamps no table's own workflow ever lets you hand-edit
elsewhere in this app) - both shown read-only, since editing them here
would be new, one-off, inconsistent-with-everywhere-else behavior with
no real benefit. A "Save" button per row `PUT`s only the fields whose
value actually changed.

**Telegram (`server/telegram_admin_db.py`, new commands alongside the
existing `/dbconnect`/`/dbtables`/`/dbpreview`/`/dbingest`):**

- **`/localtables`** — lists every real table (reusing the existing
  20-row-cap-with-note convention this project's other Telegram listings
  already use, here capped on *tables* not rows, though no table list is
  remotely likely to exceed 20 given this app's own schema size).
- **`/localview <table>`** — same shape as `/tables <name>` (section 4.4
  of the original Telegram Admin Parity spec): lists rows (20-row cap),
  no delete button here (deleting a row from an arbitrary table,
  possibly a registry table, is a different and riskier operation than
  editing a value — out of scope; existing per-store delete commands
  already cover the tables where delete makes sense).
- **`/localedit <table> <row#>`** — conversation. `<row#>` is the
  1-based index from that table's most recent `/localview` listing in
  *this chat* (Telegram has no natural "click this specific row" outside
  inline buttons, and generating one inline button per editable column
  per row would be unwieldy for wide tables — a typed row number keeps
  the UX simple and is consistent with how `/dbpreview`/`/localview`
  already number their listings). Bot shows the row's current values,
  asks for `column=value` pairs (**one per line**, not comma-separated -
  comma-separating would break the moment any single value contains a
  comma itself, e.g. editing a Custom Data Table's own
  `report_narrative` text or any free-text column; a value can still
  contain an `=` sign safely, since each line is split on only the
  *first* `=`) for only the columns to change, confirms the diff,
  applies on "yes".

Naming check against every existing command: `/localtables`,
`/localview`, `/localedit` share no prefix collision with `/tables`
(Custom Data Tables), `/dbtables`/`/dbpreview`/`/dbconnect`/`/dbingest`
(external Database Ingestion), or any other existing command.

## 4. `/localedit` conversation detail

1. `/localedit <table> <row#>` — resolves `table` via `db_browser.get_table_rows()`
   (404-equivalent chat reply if not real), re-fetches that table's
   current rows fresh (not cached from a prior `/localview` — the row
   *content* comes from the live table right now; the *row number*
   resolves to the same row `/localview` would have shown at that
   position because both call the same `get_table_rows()`, deterministically
   ordered by `id` - section 3). `<row#>` out of range → clear error + END.
2. Bot shows the row's current `column=value` pairs (one per line), asks
   "Send the columns to change, one per line as column=value (only
   include what's different)."
3. User's reply parsed by a new pure function `parse_field_updates(text)`
   - splits on newline, then each non-blank line on the *first* `=`
   (so a value may itself contain `=`; only a literal comma-per-line
   separator would have been unsafe, which is exactly why this uses
   newlines instead - see section 3) → `{column: new_value}`. A line
   with no `=` at all, or a column name not in that table's real column
   list → clear error + re-prompt, never silently ignored.
4. Bot shows "Update these fields? `column: old → new`" per changed
   field, yes/no inline buttons (same `confirm:yes`/`confirm:no`
   convention `/newtable`'s AI-schema step and `/addrow` already use).
5. "yes" → `db_browser.update_row(table, record_id, fields)` →
   `telegram_rebuild.rebuild_report()` → reply with result + any
   rebuild warning. "no" → "Cancelled."

## 5. Value coercion

Arbitrary/registry tables have no app-level "text/number/date"
declaration the way a Custom Data Table's own dynamic table does (via
`custom_table_columns`) — only Postgres's own `information_schema`
type. `_coerce_value(raw_value, pg_type)` (in `server/db_browser.py`):

- `pg_type` starting with `int` or equal to `bigint`/`smallint` → `int(raw_value)`
- `pg_type` in `("numeric", "real", "double precision")` → `float(raw_value)`
- everything else (`text`, `date`, `timestamp*`, etc.) → `raw_value` as-is
  (a string) — Postgres accepts ISO-format text for date/timestamp
  columns via implicit cast on assignment, matching how every existing
  HTML-form-submitted value in this app already reaches these same
  columns as a plain string.

A coercion failure (e.g. `int("abc")`) raises `ValueError`, caught at
the call site (admin route → 400 with the real message; Telegram →
inline error reply, conversation stays in the same state for a retry)
— never silently dropped or passed through as garbage.

## 6. Error handling conventions

- Every admin route: existing `_require_auth()` check, exactly like
  every other route in `server/routes/admin.py`.
- Every Telegram command/conversation step: existing `_authorized()`
  check, exactly like every other command.
- A table name that doesn't exist (typo, or a table dropped between
  listing and acting on it): 404 from the admin routes, a clear chat
  reply from Telegram — never a raw traceback either way.
- A row id that doesn't exist (deleted by something else between listing
  and editing): `update_by_id` already returns `False` for zero rows
  affected — surfaced as "No row with that id" (admin) / "That row no
  longer exists" (Telegram), not silently treated as success.

## 7. Testing plan

- `local_db.list_all_tables()`/`list_columns()`: mocked-`psycopg2` unit
  tests, matching `tests/lib/test_local_db.py`'s already-established
  `FakeCursor`/`FakeConnection` pattern exactly (this module *does* have
  real unit coverage today - correcting an earlier, wrong assumption
  made before checking).
- `server/db_browser.py`'s `_coerce_value()` and `parse_field_updates()`
  (the Telegram mini-DSL parser): real unit tests, pure functions, no
  database/network dependency — same "pure functions get unit tests"
  split this project uses everywhere else.
- Admin routes: mocked-`db_browser` route tests, matching the existing
  pattern for every other `/admin/api/*` route in this codebase
  (`tests/server/test_*_route.py`).
- Telegram conversation handlers: mocked-`AsyncMock` unit tests matching
  `tests/server/test_telegram_admin_*.py`'s now-established pattern.
- Live verification against the real admin panel and real bot: view
  every table type (a fixed overlay store, a Custom Data Table, and one
  of the two registry tables), edit a row of each, confirm the change
  is real (`psql`/direct `local_db` check) and the report rebuilds
  where applicable.
