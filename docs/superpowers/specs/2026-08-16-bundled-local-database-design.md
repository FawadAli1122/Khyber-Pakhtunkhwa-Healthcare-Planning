# KP Healthcare Plan — Bundled Local PostgreSQL (Phase 1: Admin-Overlay Stores)

Status: Approved design, pre-implementation
Date: 2026-08-16

## 1. Purpose

Replace the storage layer of the three admin-writable, append-only
overlay stores (`server/supplemental_data.py`, `server/metric_overrides.py`,
`server/bot_facilities.py` — currently flat CSV files) with a real
PostgreSQL database that this project bundles, bootstraps, and manages
itself, so the admin never runs or configures a database server
separately from `python -m server`.

This is Phase 1 of a larger, explicitly out-of-scope-for-now idea: moving
the entire ~20-script deterministic pipeline (population, facilities
merge, district metrics, gap scores, dev-stats, GIS output) off CSV files
too. That's a much larger, much riskier undertaking (every pipeline
script and every server module currently reads/writes CSVs directly) and
is deliberately deferred - this phase targets only the three stores that
already share an identical, self-contained `load_records()`/
`append_records()`/`delete_record()` interface (built during the Manage
Records and Telegram Connector features this same session), making them
the natural, lowest-risk starting point.

## 2. Scope Decisions From Brainstorming

- **PostgreSQL, not SQLite** — the user's explicit choice, despite SQLite
  being the simpler "single file, no server process" option. This means
  the local Postgres server process itself must be fully automated
  (start/stop tied to `python -m server`'s own lifecycle) or the "one
  command, nothing to run separately" goal is defeated.
- **A dedicated, private, bundled instance — never the machine's existing
  PostgreSQL 16 service** (already installed, already running on port
  5433 for unrelated purposes, confirmed via past sessions' memory not
  to be this project's database). This project's bundled instance gets
  its own data directory, its own port, its own generated credentials,
  entirely separate from and never touching that existing service or its
  data.
- **Only the 3 admin-overlay stores this phase** — the deterministic
  pipeline (`02_compile_population.py` through `20_cross_validate_facility_counts.py`)
  keeps using CSVs exactly as today, explicitly confirmed by the user's
  own scoping answer.
- **Columns stay `TEXT`, not typed** — every existing caller already
  treats every field as a string (e.g. `float(r["lat"])` at the call
  site, not `r["lat"]` already being a float) - keeping columns `TEXT`
  means zero behavior change anywhere outside the 3 storage modules
  themselves, deliberately conservative for a first migration.
- **Public function signatures stay identical** — `load_records()`,
  `append_records()`, `delete_record()` keep their exact names and
  return shapes in all three modules, so nothing else in the codebase
  (admin routes, the Telegram bot's handlers, `07_merge_facilities.py`'s
  consumption of `bot_facilities`) needs to change. Only each module's
  internals swap from `csv.DictReader`/`csv.DictWriter` to `psycopg2`
  calls.
- **The `path=` parameter these three modules currently take is
  removed** — verified: every production call site (admin routes, the
  bot's handlers, `add_from_document`) calls these functions with no
  `path=` argument; only tests supply it, purely for CSV-file test
  isolation. A database has no equivalent "point me at a different file"
  need in production, so this isn't a feature being dropped, just a
  test-only parameter that no longer applies.
- **Test isolation follows `server/db_ingestion.py`'s already-established
  pattern exactly**: every `psycopg2` call mocked with fake
  connection/cursor objects in unit tests (this project's only existing
  Postgres-touching module already does this, and its tests are the
  template), real integration verified live against the actual bundled
  instance - matching this project's "every AI/DB call in automated
  tests is mocked, real behavior verified manually" discipline used
  throughout every prior feature.
- **One-time data migration, not deletion** — if any of the 3 CSVs
  already have real rows on disk at first bootstrap, they're imported
  into the new tables once. The CSV files themselves are left on disk
  untouched afterward (not deleted) but no code path reads or writes
  them again after this phase ships.
- **Shared low-level DB module lives in `scripts/lib/`, not `server/`**
  — matching this project's established one-way import constraint
  (`server/` imports from `scripts/lib/`, never the reverse). This
  matters concretely here: `scripts/07_merge_facilities.py` (a pipeline
  script) needs to read the `bot_facilities` table directly, the same
  way it currently does its own `csv.DictReader` on `bot_facilities.csv`
  rather than importing `server.bot_facilities`. Putting the connection/
  bootstrap logic in `scripts/lib/local_db.py` lets both `server/` and
  `scripts/07_merge_facilities.py` import it directly, resolving that
  tension the same way `scripts/lib/districts.py`/`geo_utils.py`/
  `facility_readiness.py` already do for other shared logic.

## 3. Bundled Postgres Lifecycle

New `scripts/lib/local_db.py`:

```
PG_BIN = Path(r"C:\Program Files\PostgreSQL\16\bin")   # this machine's existing PG 16 install - reused, never modified
DATA_DIR = <repo root> / "data" / "pgdata"              # this project's own dedicated data directory - gitignored
PORT = 5544                                              # private port, matching the port already used for throwaway verification clusters in past sessions - never the existing service's 5433 or the default 5432
DB_NAME = "kp_healthcare"
DB_USER = "kp_admin"
```

- `is_initialized() -> bool` — `DATA_DIR.exists()`.
- `initialize()` — runs once, the first time the app ever starts with no
  existing `DATA_DIR`:
  1. Generates a strong random password (`secrets.token_urlsafe(32)`),
     stores it via a new `keystore.set_local_db_password()` (same
     `keyring` OS-credential-store pattern every other secret in this
     project already uses - never written to a file that persists).
  2. Writes that password to a short-lived temp file, runs
     `initdb.exe -D <DATA_DIR> -U kp_admin --auth=scram-sha-256 --pwfile=<temp file>`,
     then deletes the temp file immediately.
  3. Starts the server (see `start()` below), connects via `psycopg2` to
     the default `postgres` database, runs `CREATE DATABASE kp_healthcare`,
     reconnects to it, and creates the three tables (section 4).
  4. If any of `data/processed/supplemental_records.csv`,
     `metric_overrides.csv`, `bot_facilities.csv` already have real rows,
     imports them into the new tables (one-time; a `_migrated` marker
     row or file prevents re-running this on every subsequent start).
- `start()` — `pg_ctl.exe start -D <DATA_DIR> -o "-p 5544" -l <DATA_DIR>/server.log -w` (waits for readiness). A no-op (checked via `pg_ctl status`) if already running.
- `stop()` — `pg_ctl.exe stop -D <DATA_DIR> -m fast`.
- `get_connection()` — `psycopg2.connect(host="localhost", port=PORT, dbname=DB_NAME, user=DB_USER, password=keystore.get_local_db_password())`.
- `ensure_running()` — calls `initialize()` if `not is_initialized()`, then `start()`. This is the one function `server/app.py`'s lifespan and the three storage modules actually call.

Wired into `server/app.py`'s existing lifespan (added in the Telegram
Connector feature), **before** the Telegram bot task starts:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(local_db.ensure_running)
    await telegram_bot.start_bot_task()
    yield
    await telegram_bot.stop_bot_task()
    await asyncio.to_thread(local_db.stop)
```

(`asyncio.to_thread` because `initdb`/`pg_ctl` are blocking subprocess
calls - acceptable here since this only runs once at process startup/
shutdown, not per-request, matching the same reasoning already used for
the Telegram bot's own subprocess calls.)

## 4. Schema

```sql
CREATE TABLE supplemental_records (
    id TEXT PRIMARY KEY,
    district TEXT, facility TEXT, category TEXT, label TEXT, detail TEXT,
    source_document TEXT, added_at TEXT
);

CREATE TABLE metric_overrides (
    id TEXT PRIMARY KEY,
    district TEXT, file TEXT, column_name TEXT, value TEXT, reason TEXT,
    source TEXT, added_at TEXT
);
-- "column" is a reserved-adjacent identifier that's awkward quoted
-- everywhere it's used - stored as column_name, translated back to
-- "column" in FIELDNAMES-facing code so callers never see the rename.

CREATE TABLE bot_facilities (
    id TEXT PRIMARY KEY,
    name TEXT, district TEXT, lat TEXT, lon TEXT, category TEXT,
    added_at TEXT, added_by TEXT
);
```

Every column `TEXT`, matching section 2's decision. `id` is the real
primary key (already globally unique - `uuid.uuid4().hex[:12]`,
unchanged).

## 5. Migrating the Three Modules

Each of `server/supplemental_data.py`, `server/metric_overrides.py`,
`server/bot_facilities.py` keeps its exact public functions
(`load_records()`, `append_records(records)`, `delete_record(record_id)`,
plus each module's own convenience entry point -
`add_from_document()`/`add_facility()`). Internals become:

- `load_records()` → `SELECT * FROM <table> ORDER BY added_at` via
  `local_db.get_connection()`, returns a list of dicts (via
  `psycopg2.extras.RealDictCursor`, matching column names to dict keys
  automatically - no manual zip-with-column-names needed). For
  `metric_overrides` specifically, the query selects
  `column_name AS "column"` so the returned dict key matches
  `FIELDNAMES`'s `"column"` exactly - callers outside this module never
  see the `column_name` rename; it's purely a storage-layer detail
  worked around inside `metric_overrides.py`.
- `append_records(records)` → a single `executemany` `INSERT INTO
  <table> (...) VALUES (...)` inside one transaction.
- `delete_record(record_id)` → `DELETE FROM <table> WHERE id = %s`,
  returns `True` if `cur.rowcount == 1` else `False` (replaces the
  current "load everything, filter, rewrite the whole file" approach -
  a real simplification the CSV version never had available).

`bot_facilities.py`'s `07_merge_facilities.py` call site changes from
its current direct `csv.DictReader` on `bot_facilities.csv` to
`scripts.lib.local_db.get_connection()` + a plain `SELECT * FROM
bot_facilities` (still not importing `server.bot_facilities` itself,
consistent with the one-way constraint - it already didn't import
`server.bot_facilities` even when the data lived in a CSV).

## 6. Error Handling

New `local_db.LocalDbError` (mirroring `db_ingestion.DbIngestionError`'s
"safe to show the admin directly" contract), raised by `ensure_running()`
if `initdb`/`pg_ctl` fail or a connection can't be established. The
FastAPI lifespan re-raises this on startup (a genuinely fatal condition
for a feature the whole admin panel now depends on - the app should not
silently start with a broken storage layer for these three stores).

## 7. Testing

- `scripts/lib/local_db.py`: `initialize()`/`start()`/`stop()` are
  subprocess-invoking and not unit-tested directly (matching this
  project's established precedent for `pg_ctl`/`initdb`-style external-
  binary orchestration - `run_downstream.py` and its siblings have never
  had dedicated tests either, verified via manual runs instead).
  `get_connection()`'s argument-building and the schema-creation SQL get
  narrow unit tests with a mocked `psycopg2.connect`.
- `server/supplemental_data.py`, `server/metric_overrides.py`,
  `server/bot_facilities.py`: every existing test gets rewritten from
  CSV-file-based (`tmp_path`, `path=` arguments) to mocked-`psycopg2`-
  based, following `tests/server/test_db_ingestion.py`'s `FakeConnection`/
  `FakeCursor` pattern exactly. Every existing test's *intent* (round-
  trip, backfill-was-id-specific-so-no-longer-applicable-see-below,
  delete-removes-only-matching-row, delete-returns-false-for-unknown-id)
  carries over; the mechanism changes.
- **The existing "backfill missing ids on load" tests and behavior are
  dropped, not carried over** — that logic existed specifically to
  handle legacy CSV rows written before the `id` column was added
  (Manage Records feature). A fresh Postgres table has no such legacy
  rows; `id` is simply always present, generated at insert time, exactly
  like today's `add_from_document`/`add_facility` already do.
- **Manual verification** (this project's established cadence for
  anything touching a real external system): delete `data/pgdata/`
  (simulating a fresh machine), start `python -m server`, confirm the
  first-run bootstrap completes (data directory created, database and
  tables exist, checked via `psql`), exercise a full add→list→delete
  cycle through the admin panel for all three stores and through the
  Telegram bot's `/addpoint` for `bot_facilities`, restart the server
  and confirm data persisted (the whole point of a real database over
  CSVs), and confirm `07_merge_facilities.py` still correctly reads
  `bot_facilities` rows into the merged facility set.
