# Bundled Local PostgreSQL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CSV storage of the three admin-writable overlay stores (`supplemental_records`, `metric_overrides`, `bot_facilities`) with a PostgreSQL database this project bootstraps, starts, and stops itself, so `python -m server` remains the single command that runs everything.

**Architecture:** A new `scripts/lib/local_db.py` owns a dedicated, private Postgres instance (its own data directory, port, and generated credentials — never the machine's existing PostgreSQL 16 service) and exposes generic `fetch_all`/`insert_many`/`delete_by_id` helpers on top of `psycopg2`. The three store modules (`server/supplemental_data.py`, `server/metric_overrides.py`, `server/bot_facilities.py`) keep their exact public functions and callers unchanged; only their internals swap from `csv`/`Path` calls to these generic helpers. `scripts/07_merge_facilities.py` reads the `bot_facilities` table the same way it currently reads the CSV — directly, via `local_db`, never importing `server.bot_facilities` (preserving the one-way import boundary).

**Tech Stack:** Python 3.12, `psycopg2` (already a dependency, used by `server/db_ingestion.py`), PostgreSQL 16 (already installed on this machine at `C:\Program Files\PostgreSQL\16\bin\` — reused, never modified; this project's bundled instance is entirely separate), pytest + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-08-16-bundled-local-database-design.md`

## Global Constraints

- The bundled instance is fully private to this project: its own data directory (`data/pgdata/`, gitignored), its own port (`5544`), its own generated credentials — never touches the machine's existing PostgreSQL 16 service or any of its data.
- Every column in the 3 new tables is `TEXT` (deliberate — every existing caller already treats every field as a string; no behavior change outside the storage modules themselves).
- `load_records()`, `append_records(records)`, `delete_record(record_id)` keep their exact names and return shapes in all three store modules. The `path=` parameter they currently take is removed (verified: no production call site ever passes it, only tests, for CSV-file isolation that no longer applies).
- **Implementation resolution beyond the spec's literal wording**: the spec's section 3 describes a `keystore.set_local_db_password()`/`get_local_db_password()` pair, but `scripts/lib/local_db.py` cannot import `server.keystore` (the established one-way constraint: `server/` imports from `scripts/lib/`, never the reverse) and nothing in `server/` currently needs to read the raw password directly (server-side code only ever needs `local_db.get_connection()`, never the password itself). So `local_db.py` manages this one credential with its own direct `keyring` calls (same `SERVICE_NAME = "kp-healthcare-plan"` constant duplicated, same underlying keyring entry) rather than a `server/keystore.py` wrapper — the spec's actual intent (OS-keyring-backed, matching every other secret) is fully preserved; only which module owns the accessor function changes, for the same reason `KP_BBOX` is already duplicated across `05_fetch_facilities_osm.py`/`06_fetch_roads_osm.py`/`11_suggest_new_sites.py`/`22_geocode_marham_facilities.py` rather than centralized.
- Every `psycopg2` call in every automated test is mocked (matching `server/db_ingestion.py`'s established `tests/server/test_db_ingestion.py` pattern exactly) — no test touches a real database. Real integration is verified live, against the actual bundled instance, in the final task.
- `scripts/lib/local_db.py`'s bootstrap functions (`initialize`, `start`, `stop`, `ensure_running`) get no automated tests — they orchestrate real `initdb`/`pg_ctl` subprocesses, matching this project's established precedent that `run_downstream.py`-style external-process orchestration has never had dedicated tests either, verified manually instead.

---

### Task 1: `scripts/lib/local_db.py` Part A — generic `fetch_all`/`insert_many`/`delete_by_id`

**Files:**
- Create: `scripts/lib/local_db.py`
- Test: `tests/lib/test_local_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `local_db.get_connection()`, `local_db.fetch_all(table, order_by=None, column_map=None) -> list[dict]`, `local_db.insert_many(table, fieldnames, records, column_map=None)`, `local_db.delete_by_id(table, record_id) -> bool`, `local_db.LocalDbError`, `local_db._get_password()` — consumed by Tasks 2 (bootstrap), 4-6 (the three migrated store modules), and Task 6's `07_merge_facilities.py` update.

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/test_local_db.py`:

```python
"""Unit tests for scripts/lib/local_db.py's generic fetch_all/insert_many/
delete_by_id helpers. Every psycopg2 call is mocked, matching
tests/server/test_db_ingestion.py's established pattern exactly - no test
here touches a real database. See docs/superpowers/specs/
2026-08-16-bundled-local-database-design.md section 5.
"""
import importlib

import pytest

local_db = importlib.import_module("scripts.lib.local_db")
# scripts/lib isn't a normal importable package path from the test's own
# working directory in every context this suite runs from; importlib
# mirrors the exact pattern tests/test_merge_facilities.py and
# tests/test_apply_metric_overrides.py already use for numbered pipeline
# scripts - used here for consistency even though local_db.py's own name
# has no leading digit, since scripts.lib is the same kind of
# not-a-normal-package import boundary.


@pytest.fixture(autouse=True)
def fake_password(monkeypatch):
    # get_connection() always calls _get_password() before psycopg2.connect
    # - every test in this file mocks psycopg2.connect but never actually
    # cares what password is passed, so this fixture keeps _get_password()
    # from hitting the real OS credential store (which has no entry for
    # this project's local DB password in any test environment).
    monkeypatch.setattr(local_db.keyring, "get_password", lambda service, key: "fake-password")


class FakeCursor:
    def __init__(self, rows=None, rowcount=1):
        self._rows = rows or []
        self.executed = []
        self.rowcount = rowcount

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def executemany(self, query, seq_of_params):
        self.executed.append((query, list(seq_of_params)))

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_fetch_all_returns_rows_as_plain_dicts(monkeypatch):
    cursor = FakeCursor(rows=[{"id": "a1", "name": "X"}])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    records = local_db.fetch_all("bot_facilities")
    assert records == [{"id": "a1", "name": "X"}]
    assert conn.closed is True
    assert "SELECT * FROM bot_facilities" in cursor.executed[0][0]


def test_fetch_all_orders_results(monkeypatch):
    cursor = FakeCursor(rows=[])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.fetch_all("bot_facilities", order_by="added_at")
    assert "ORDER BY added_at" in cursor.executed[0][0]


def test_fetch_all_applies_column_map(monkeypatch):
    cursor = FakeCursor(rows=[{"id": "a1", "column_name": "population_2023"}])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    records = local_db.fetch_all("metric_overrides", column_map={"column": "column_name"})
    assert records == [{"id": "a1", "column": "population_2023"}]


def test_insert_many_builds_correct_insert(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.insert_many("bot_facilities", ("id", "name"), [{"id": "a1", "name": "Clinic"}])
    query, values = cursor.executed[0]
    assert "INSERT INTO bot_facilities (id, name)" in query
    assert values == [("a1", "Clinic")]
    assert conn.committed is True
    assert conn.closed is True


def test_insert_many_applies_column_map(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.insert_many(
        "metric_overrides", ("id", "column"), [{"id": "a1", "column": "population_2023"}],
        column_map={"column": "column_name"},
    )
    query, values = cursor.executed[0]
    assert "column_name" in query
    assert values == [("a1", "population_2023")]


def test_insert_many_missing_field_defaults_to_empty_string(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.insert_many("bot_facilities", ("id", "name"), [{"id": "a1"}])
    _query, values = cursor.executed[0]
    assert values == [("a1", "")]


def test_delete_by_id_returns_true_when_deleted(monkeypatch):
    cursor = FakeCursor(rowcount=1)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    assert local_db.delete_by_id("bot_facilities", "a1") is True
    assert conn.committed is True
    assert conn.closed is True


def test_delete_by_id_returns_false_when_not_found(monkeypatch):
    cursor = FakeCursor(rowcount=0)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    assert local_db.delete_by_id("bot_facilities", "does-not-exist") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_local_db.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'scripts.lib.local_db'`).

- [ ] **Step 3: Implement**

Create `scripts/lib/local_db.py`:

```python
"""This project's own bundled, private PostgreSQL instance - never the
machine's existing PostgreSQL 16 service. Owns the instance's lifecycle
(scripts/lib/local_db.py Part B, added in Task 2 of this plan) and
exposes generic fetch_all/insert_many/delete_by_id helpers on top of
psycopg2, used by the three admin-overlay store modules
(server/supplemental_data.py, server/metric_overrides.py,
server/bot_facilities.py) and by scripts/07_merge_facilities.py's direct
read of the bot_facilities table. Lives in scripts/lib/, not server/, so
both server/ and plain pipeline scripts can import it directly - matching
this project's established one-way import constraint (server/ imports
from scripts/lib/, never the reverse). See docs/superpowers/specs/
2026-08-16-bundled-local-database-design.md.
"""
import keyring
import psycopg2
from psycopg2.extras import RealDictCursor

SERVICE_NAME = "kp-healthcare-plan"  # matches server/keystore.py's own SERVICE_NAME exactly - see this plan's Global Constraints for why this module manages its own keyring access rather than importing server.keystore
LOCAL_DB_PASSWORD_KEY = "local_db_password"

DB_NAME = "kp_healthcare"
DB_USER = "kp_admin"
PORT = 5544


class LocalDbError(Exception):
    """Raised when bootstrapping or connecting to the bundled database
    fails - message safe to show the admin directly, never a raw
    traceback."""


def _get_password():
    value = keyring.get_password(SERVICE_NAME, LOCAL_DB_PASSWORD_KEY)
    if value is None:
        raise LocalDbError("Local database not initialized yet - call local_db.ensure_running() first")
    return value


def get_connection():
    return psycopg2.connect(
        host="localhost", port=PORT, dbname=DB_NAME, user=DB_USER,
        password=_get_password(), cursor_factory=RealDictCursor,
    )


def fetch_all(table, order_by=None, column_map=None):
    column_map = column_map or {}
    reverse_map = {v: k for k, v in column_map.items()}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            query = f"SELECT * FROM {table}"
            if order_by:
                query += f" ORDER BY {order_by}"
            cur.execute(query)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{reverse_map.get(k, k): v for k, v in dict(row).items()} for row in rows]


def insert_many(table, fieldnames, records, column_map=None):
    column_map = column_map or {}
    db_columns = [column_map.get(f, f) for f in fieldnames]
    columns_sql = ", ".join(db_columns)
    placeholders = ", ".join(["%s"] * len(db_columns))
    values = [tuple(r.get(f, "") for f in fieldnames) for r in records]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders})", values)
        conn.commit()
    finally:
        conn.close()


def delete_by_id(table, record_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table} WHERE id = %s", (record_id,))
            deleted = cur.rowcount == 1
        conn.commit()
    finally:
        conn.close()
    return deleted
```

`_get_password()` is defined here rather than deferred to Task 2, despite bootstrap/credential-*generation* logic otherwise belonging there: `get_connection()`'s call to `_get_password()` is a real Python expression evaluated *before* the mocked `psycopg2.connect()` is ever reached, so every one of this task's own tests would raise `LocalDbError`/`NameError` without a working `_get_password()` already present - the `fake_password` fixture in Step 1's test file exists specifically to keep it from hitting the real OS credential store.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_local_db.py -v`
Expected: PASS (all 8 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/local_db.py tests/lib/test_local_db.py
git commit -m "feat: add local_db.py generic fetch_all/insert_many/delete_by_id helpers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `scripts/lib/local_db.py` Part B — bootstrap and lifecycle

**Files:**
- Modify: `scripts/lib/local_db.py`

**Interfaces:**
- Consumes: `local_db.LocalDbError`, `local_db._get_password()`, `local_db.insert_many()` (Task 1).
- Produces: `local_db.is_initialized()`, `local_db.initialize()`, `local_db.start()`, `local_db.stop()`, `local_db.ensure_running()` — consumed by Task 7 (`server/app.py`'s lifespan).

No automated tests (see Global Constraints - this orchestrates real `initdb`/`pg_ctl` subprocesses). Verified manually in Task 8.

- [ ] **Step 1: Implement**

Add to `scripts/lib/local_db.py`. `keyring`/`psycopg2`/`RealDictCursor` are already imported (Task 1), as are `SERVICE_NAME`/`LOCAL_DB_PASSWORD_KEY`/`DB_NAME`/`DB_USER`/`PORT`/`LocalDbError`/`_get_password` - do not redefine any of those, only add what's below. Add these new imports alongside Task 1's existing ones:

```python
import csv
import secrets
import subprocess
from pathlib import Path
```

Then add these new module-level constants and functions (after Task 1's existing `get_connection`/`fetch_all`/`insert_many`/`delete_by_id`):

```python
ROOT = Path(__file__).resolve().parent.parent.parent
PG_BIN = Path(r"C:\Program Files\PostgreSQL\16\bin")
DATA_DIR = ROOT / "data" / "pgdata"
PROCESSED = ROOT / "data" / "processed"

SCHEMA_SQL = """
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
CREATE TABLE bot_facilities (
    id TEXT PRIMARY KEY,
    name TEXT, district TEXT, lat TEXT, lon TEXT, category TEXT,
    added_at TEXT, added_by TEXT
);
"""

# Legacy CSV fieldnames, duplicated here (not imported from the server
# modules that own them today - scripts/lib/ never imports server/) so
# the one-time migration below can read them without crossing that
# boundary. Only used once, at first bootstrap.
_LEGACY_CSV_MIGRATIONS = [
    ("supplemental_records.csv", "supplemental_records",
     ("id", "district", "facility", "category", "label", "detail", "source_document", "added_at"), None),
    ("metric_overrides.csv", "metric_overrides",
     ("id", "district", "file", "column", "value", "reason", "source", "added_at"), {"column": "column_name"}),
    ("bot_facilities.csv", "bot_facilities",
     ("id", "name", "district", "lat", "lon", "category", "added_at", "added_by"), None),
]


def is_initialized():
    return DATA_DIR.exists()


def _run(args, error_message):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise LocalDbError(f"{error_message}: {result.stderr[-500:]}")
    return result


def initialize():
    password = secrets.token_urlsafe(32)
    keyring.set_password(SERVICE_NAME, LOCAL_DB_PASSWORD_KEY, password)

    DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
    pwfile = DATA_DIR.parent / ".local_db_initdb_pw.tmp"
    pwfile.write_text(password, encoding="utf-8")
    try:
        _run(
            [str(PG_BIN / "initdb.exe"), "-D", str(DATA_DIR), "-U", DB_USER,
             "--auth=scram-sha-256", f"--pwfile={pwfile}"],
            "Failed to initialize the local database",
        )
    finally:
        pwfile.unlink(missing_ok=True)

    start()

    bootstrap_conn = psycopg2.connect(
        host="localhost", port=PORT, dbname="postgres", user=DB_USER, password=password,
    )
    bootstrap_conn.autocommit = True
    try:
        with bootstrap_conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE {DB_NAME}")
    finally:
        bootstrap_conn.close()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()

    _migrate_legacy_csvs()


def _migrate_legacy_csvs():
    for filename, table, fieldnames, column_map in _LEGACY_CSV_MIGRATIONS:
        path = PROCESSED / filename
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            records = list(csv.DictReader(f))
        if records:
            insert_many(table, fieldnames, records, column_map=column_map)


def start():
    status = subprocess.run(
        [str(PG_BIN / "pg_ctl.exe"), "status", "-D", str(DATA_DIR)],
        capture_output=True, text=True,
    )
    if status.returncode == 0:
        return  # already running
    _run(
        [str(PG_BIN / "pg_ctl.exe"), "start", "-D", str(DATA_DIR), "-o", f"-p {PORT}",
         "-l", str(DATA_DIR / "server.log"), "-w"],
        "Failed to start the local database",
    )


def stop():
    if not DATA_DIR.exists():
        return
    subprocess.run([str(PG_BIN / "pg_ctl.exe"), "stop", "-D", str(DATA_DIR), "-m", "fast"],
                    capture_output=True, text=True)


def ensure_running():
    if not is_initialized():
        initialize()
    else:
        start()
```

- [ ] **Step 2: Smoke-test the module still imports cleanly and Task 1's tests still pass**

Run: `pytest tests/lib/test_local_db.py -v`
Expected: PASS (all 8 tests - Task 2's additions don't change `get_connection`/`fetch_all`/`insert_many`/`delete_by_id`'s behavior, only add new functions alongside them).

- [ ] **Step 3: Commit**

```bash
git add scripts/lib/local_db.py
git commit -m "feat: add local_db.py bootstrap and lifecycle (initdb/pg_ctl orchestration)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `.gitignore` — exclude the bundled database's data directory

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add the entry**

Add to `.gitignore`, following the existing pattern for other generated/runtime directories:

```gitignore

# This project's own bundled, private PostgreSQL data directory
# (scripts/lib/local_db.py) - pure runtime state, regenerated on first
# app startup if missing. Never the machine's existing PostgreSQL 16
# service or its data.
data/pgdata/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore the bundled local database's data directory

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Migrate `server/supplemental_data.py` to the bundled database

**Files:**
- Modify: `server/supplemental_data.py`
- Modify: `tests/server/test_supplemental_data.py`

**Interfaces:**
- Consumes: `local_db.fetch_all`, `local_db.insert_many`, `local_db.delete_by_id` (Task 1).
- Produces: `load_records()`, `append_records(records)`, `delete_record(record_id)` keep their existing names/shapes (no `path=` parameter anymore) - consumed unchanged by `server/routes/admin.py`, `server/report_context.py`, `server/telegram_bot.py`.

- [ ] **Step 1: Write the failing tests**

Replace every storage-related test in `tests/server/test_supplemental_data.py` (`test_append_and_load_records_round_trip`, `test_append_records_appends_without_duplicating_header`, `test_load_records_returns_empty_list_when_file_missing`, `test_append_records_writes_header_into_existing_empty_file`, `test_load_records_normalizes_none_fields_from_ragged_row`, `test_delete_record_removes_only_matching_row`, `test_delete_record_returns_false_for_unknown_id`, `test_delete_record_on_missing_file_returns_false`, `test_load_records_backfills_missing_ids_and_persists`) with:

```python
def test_load_records_calls_fetch_all(monkeypatch):
    fake_records = [{"id": "aaa111", "district": "Peshawar", "facility": "DHQ Hospital",
                      "category": "equipment", "label": "MRI Machine", "detail": "1 unit",
                      "source_document": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data.local_db, "fetch_all",
                         lambda table, order_by=None: fake_records if table == "supplemental_records" else [])
    assert supplemental_data.load_records() == fake_records


def test_append_records_calls_insert_many(monkeypatch):
    calls = []
    monkeypatch.setattr(supplemental_data.local_db, "insert_many",
                         lambda table, fieldnames, records: calls.append((table, fieldnames, records)))
    records = [{"id": "aaa111", "district": "Peshawar", "facility": "", "category": "equipment",
                "label": "X-ray", "detail": "", "source_document": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    supplemental_data.append_records(records)
    assert calls == [("supplemental_records", supplemental_data.FIELDNAMES, records)]


def test_delete_record_calls_delete_by_id(monkeypatch):
    monkeypatch.setattr(supplemental_data.local_db, "delete_by_id",
                         lambda table, record_id: table == "supplemental_records" and record_id == "aaa111")
    assert supplemental_data.delete_record("aaa111") is True
    assert supplemental_data.delete_record("does-not-exist") is False
```

Also update `test_add_from_document_success` and `test_add_from_document_stamps_distinct_id_per_record` (they currently monkeypatch `supplemental_data.RECORDS_PATH` and check `supplemental_data.load_records(path=records_path)` afterward - that no longer applies). Replace the tail of `test_add_from_document_success`:

```python
def test_add_from_document_success(monkeypatch):
    monkeypatch.setattr(supplemental_data, "load_known_districts", lambda path=None: KNOWN_DISTRICTS)
    inserted = []
    monkeypatch.setattr(supplemental_data.local_db, "insert_many",
                         lambda table, fieldnames, records: inserted.append(records))

    raw_response = json.dumps([
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit"},
    ])
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw_response)

    added = supplemental_data.add_from_document(
        "anthropic", "sk-ant-real", "some document text",
        "this is for Peshawar's DHQ Hospital", "equipment.pdf",
    )
    assert len(added) == 1
    assert added[0]["district"] == "Peshawar"
    assert added[0]["source_document"] == "equipment.pdf"
    assert "added_at" in added[0]
    assert inserted == [added]
```

And `test_add_from_document_stamps_distinct_id_per_record`'s setup (remove the `monkeypatch.setattr(supplemental_data, "RECORDS_PATH", records_path)` line - no longer applies; the rest of the test, which only inspects `added[0]["id"]`/`added[1]["id"]`/`added_at`, is unchanged since `add_from_document` still stamps these itself before storage).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_supplemental_data.py -v`
Expected: FAIL (`AttributeError: module 'server.supplemental_data' has no attribute 'local_db'`).

- [ ] **Step 3: Implement**

Replace the storage section of `server/supplemental_data.py` (everything from `PROCESSED = ...` / `RECORDS_PATH = ...` through the end of `delete_record`) with:

```python
from scripts.lib import local_db

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
METRICS_PATH = PROCESSED / "district_metrics.csv"

FIELDNAMES = ("id", "district", "facility", "category", "label", "detail", "source_document", "added_at")

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class SupplementalDataError(Exception):
    """Raised when the AI's extracted records fail validation - message
    safe to show the admin directly, never a raw traceback."""


def load_known_districts(path=METRICS_PATH):
    with open(path, newline="", encoding="utf-8") as f:
        return [row["district"] for row in csv.DictReader(f)]


def load_records():
    return local_db.fetch_all("supplemental_records", order_by="added_at")


def append_records(records):
    local_db.insert_many("supplemental_records", FIELDNAMES, records)


def delete_record(record_id):
    return local_db.delete_by_id("supplemental_records", record_id)
```

(`RECORDS_PATH` and `_write_records` are removed entirely - no longer needed.) Update `add_from_document`'s final two lines from `append_records(records, path=RECORDS_PATH)` to `append_records(records)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_supplemental_data.py -v`
Expected: PASS.

- [ ] **Step 5: Check for any other test file referencing `supplemental_data.RECORDS_PATH` or the removed `path=` parameter**

Run: `grep -rn "supplemental_data.RECORDS_PATH\|supplemental_data\.load_records(path=\|supplemental_data\.delete_record(.*path=" tests/`
Expected: no matches (Task 4 Step 1 already covered every call site in `test_supplemental_data.py`; this confirms no other test file - e.g. `test_supplemental_data_route.py`, `test_report_context.py` - relies on the removed parameter). If any match is found, update it the same way.

- [ ] **Step 6: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass (any route/report-context test that mocks `supplemental_data.load_records`/`add_from_document` entirely, rather than calling the real storage functions, is unaffected by this change).

- [ ] **Step 7: Commit**

```bash
git add server/supplemental_data.py tests/server/test_supplemental_data.py
git commit -m "feat: migrate supplemental_data.py storage to the bundled database

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Migrate `server/metric_overrides.py` to the bundled database

> **Scope note (added during execution):** the plan as originally written
> covered only `server/metric_overrides.py`, but `scripts/07b_apply_metric_overrides.py`
> reads `metric_overrides.csv` directly via its own `load_overrides()`
> function - a gap analogous to the one Task 6 already accounts for with
> `07_merge_facilities.py`/`bot_facilities.csv`, just missed here. Left
> unfixed, that CSV would go permanently stale once this task lands,
> silently breaking admin-entered overrides ever reaching the pipeline -
> a direct violation of this plan's own Global Constraint ("no behavior
> change outside the storage modules themselves"). Confirmed with the
> user during execution; fixed as part of this task, mirroring Task 6's
> treatment exactly (see Step 6 below).

**Files:**
- Modify: `server/metric_overrides.py`
- Modify: `tests/server/test_metric_overrides.py`
- Modify: `scripts/07b_apply_metric_overrides.py`
- Modify: `tests/test_apply_metric_overrides.py`

**Interfaces:**
- Consumes: `local_db.fetch_all`, `local_db.insert_many`, `local_db.delete_by_id` (Task 1), with `column_map={"column": "column_name"}`.
- Produces: `load_records()`, `append_records(records)`, `delete_record(record_id)` unchanged in name/shape - consumed unchanged by `server/routes/admin.py`, `server/telegram_bot.py`. `scripts/07b_apply_metric_overrides.py`'s own `load_overrides()` also switches to `local_db.fetch_all` directly (never importing `server.metric_overrides` - preserves the one-way import boundary, same reasoning as Task 6's `07_merge_facilities.py`).

- [ ] **Step 1: Write the failing tests**

Replace every storage-related test in `tests/server/test_metric_overrides.py` (`test_append_and_load_records_round_trip`, `test_append_records_writes_header_into_existing_empty_file`, `test_load_records_returns_empty_list_when_file_missing`, `test_delete_record_removes_only_matching_row`, `test_delete_record_returns_false_for_unknown_id`, `test_delete_record_on_missing_file_returns_false`, `test_load_records_backfills_missing_ids_and_persists`) with:

```python
def test_load_records_calls_fetch_all_with_column_map(monkeypatch):
    fake_records = [{"id": "aaa111", "district": "Peshawar", "file": "population",
                      "column": "population_2023", "value": "5000000", "reason": "estimate",
                      "source": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(
        metric_overrides.local_db, "fetch_all",
        lambda table, order_by=None, column_map=None: fake_records if table == "metric_overrides" else [],
    )
    assert metric_overrides.load_records() == fake_records


def test_append_records_calls_insert_many_with_column_map(monkeypatch):
    calls = []
    monkeypatch.setattr(
        metric_overrides.local_db, "insert_many",
        lambda table, fieldnames, records, column_map=None: calls.append((table, fieldnames, records, column_map)),
    )
    records = [{"id": "aaa111", "district": "Peshawar", "file": "population", "column": "population_2023",
                "value": "5000000", "reason": "estimate", "source": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    metric_overrides.append_records(records)
    assert calls == [("metric_overrides", metric_overrides.FIELDNAMES, records, {"column": "column_name"})]


def test_delete_record_calls_delete_by_id(monkeypatch):
    monkeypatch.setattr(metric_overrides.local_db, "delete_by_id",
                         lambda table, record_id: table == "metric_overrides" and record_id == "aaa111")
    assert metric_overrides.delete_record("aaa111") is True
    assert metric_overrides.delete_record("does-not-exist") is False
```

Update `test_add_from_document_success`'s tail (replace the `monkeypatch.setattr(metric_overrides, "OVERRIDES_PATH", overrides_path)` + final `load_records(path=overrides_path)` check):

```python
def test_add_from_document_success(fake_fields, monkeypatch):
    monkeypatch.setattr(metric_overrides, "load_known_districts", lambda: KNOWN_DISTRICTS)
    inserted = []
    monkeypatch.setattr(metric_overrides.local_db, "insert_many",
                         lambda table, fieldnames, records, column_map=None: inserted.append(records))

    raw_response = json.dumps([
        {"district": "Peshawar", "file": "population", "column": "population_2023",
         "value": 5000000, "reason": "New estimate"},
    ])
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw_response)

    added = metric_overrides.add_from_document(
        "anthropic", "sk-ant-real", "some document text", "update Peshawar's population", "census.pdf",
    )
    assert len(added) == 1
    assert added[0]["district"] == "Peshawar"
    assert added[0]["source"] == "census.pdf"
    assert "added_at" in added[0]
    assert inserted == [added]
```

And remove the `monkeypatch.setattr(metric_overrides, "OVERRIDES_PATH", overrides_path)` line from `test_add_from_document_stamps_distinct_id_per_record` (its assertions on `added[0]["id"]`/`added[1]["id"]`/`added_at` are otherwise unchanged).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_metric_overrides.py -v`
Expected: FAIL (`AttributeError: module 'server.metric_overrides' has no attribute 'local_db'`).

- [ ] **Step 3: Implement**

Replace the storage section of `server/metric_overrides.py` (everything from `PROCESSED = ...` / `OVERRIDES_PATH = ...` through the end of `delete_record`) with:

```python
from scripts.lib import local_db

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

FIELDNAMES = ("id", "district", "file", "column", "value", "reason", "source", "added_at")
_COLUMN_MAP = {"column": "column_name"}

# {file_key: (csv_path, overridable_columns, swing_threshold_fraction)}
OVERRIDABLE_FIELDS = {
    "population": (
        PROCESSED / "kp_district_population_2023.csv",
        {"population_2023", "population_prior", "growth_rate_pct"},
        0.5,
    ),
    "health": (
        PROCESSED / "dev_stats_health.csv",
        {"govt_institutions", "govt_beds", "pvt_hospitals", "pvt_beds",
         "medical_staff", "paramedical_staff", "pvt_practitioners"},
        1.0,
    ),
}

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class MetricOverrideError(Exception):
    """Raised when an AI-proposed pipeline-data override fails validation -
    message safe to show the admin directly, never a raw traceback."""


def load_records():
    return local_db.fetch_all("metric_overrides", order_by="added_at", column_map=_COLUMN_MAP)


def append_records(records):
    local_db.insert_many("metric_overrides", FIELDNAMES, records, column_map=_COLUMN_MAP)


def delete_record(record_id):
    return local_db.delete_by_id("metric_overrides", record_id)
```

(`OVERRIDES_PATH` and `_write_records` are removed entirely - `OVERRIDABLE_FIELDS` and `_read_current_value` are unchanged, since those still correctly read the *pipeline's* `kp_district_population_2023.csv`/`dev_stats_health.csv` files, which stay CSV-based - only the *overrides log itself* moves to the database.) Update `add_from_document`'s final two lines from `append_records(records, path=OVERRIDES_PATH)` to `append_records(records)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_metric_overrides.py -v`
Expected: PASS.

- [ ] **Step 5: Check for any other test file referencing `metric_overrides.OVERRIDES_PATH` or the removed `path=` parameter**

Run: `grep -rn "metric_overrides.OVERRIDES_PATH\|metric_overrides\.load_records(path=\|metric_overrides\.delete_record(.*path=" tests/`
Expected: no matches. Update any found.

- [ ] **Step 6: Fix `scripts/07b_apply_metric_overrides.py`'s direct CSV read (scope addition, see note above)**

Update `tests/test_apply_metric_overrides.py`: replace `test_load_overrides_returns_empty_list_when_file_missing` with:

```python
def test_load_overrides_calls_fetch_all(monkeypatch):
    fake_records = [{"id": "aaa111", "district": "Peshawar", "file": "population",
                      "column": "population_2023", "value": "5000000", "reason": "estimate",
                      "source": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(
        apply_mod.local_db, "fetch_all",
        lambda table, order_by=None, column_map=None: fake_records if table == "metric_overrides" else [],
    )
    assert apply_mod.load_overrides() == fake_records
```

In `scripts/07b_apply_metric_overrides.py`, add near the top (alongside the existing imports, matching `07_merge_facilities.py`'s established pattern):

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib import local_db
```

Remove the `OVERRIDES_PATH = PROCESSED / "metric_overrides.csv"` constant, and replace `load_overrides()`:

```python
def load_overrides():
    return local_db.fetch_all("metric_overrides", order_by="added_at", column_map={"column": "column_name"})
```

`csv`/`Path` stay imported - still used by `apply_overrides_to_file`/`load_baseline`/`save_baseline`, which remain CSV-based (only the overrides log itself moves to the database).

Run: `pytest tests/test_apply_metric_overrides.py -q`
Expected: PASS (all tests).

- [ ] **Step 7: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add server/metric_overrides.py tests/server/test_metric_overrides.py scripts/07b_apply_metric_overrides.py tests/test_apply_metric_overrides.py
git commit -m "feat: migrate metric_overrides.py storage to the bundled database

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Migrate `server/bot_facilities.py` and update `07_merge_facilities.py`

**Files:**
- Modify: `server/bot_facilities.py`
- Modify: `tests/server/test_bot_facilities.py`
- Modify: `scripts/07_merge_facilities.py`
- Modify: `tests/test_merge_facilities.py`

**Interfaces:**
- Consumes: `local_db.fetch_all`, `local_db.insert_many`, `local_db.delete_by_id` (Task 1).
- Produces: `load_records()`, `append_records(records)`, `delete_record(record_id)`, `add_facility(...)` unchanged in name/shape - consumed unchanged by `server/routes/admin.py`, `server/telegram_bot.py`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/server/test_bot_facilities.py`'s storage-related tests (`test_append_and_load_records_round_trip`, `test_load_records_returns_empty_list_when_file_missing`, `test_load_records_backfills_missing_ids_and_persists`, `test_delete_record_removes_only_matching_row`, `test_delete_record_returns_false_for_unknown_id`) with:

```python
def test_load_records_calls_fetch_all(monkeypatch):
    fake_records = [{"id": "aaa111", "name": "Field Clinic", "district": "Peshawar", "lat": "34.01",
                      "lon": "71.58", "category": "Clinic", "added_at": "2026-08-16T00:00:00+00:00",
                      "added_by": "555"}]
    monkeypatch.setattr(bot_facilities.local_db, "fetch_all",
                         lambda table, order_by=None: fake_records if table == "bot_facilities" else [])
    assert bot_facilities.load_records() == fake_records


def test_append_records_calls_insert_many(monkeypatch):
    calls = []
    monkeypatch.setattr(bot_facilities.local_db, "insert_many",
                         lambda table, fieldnames, records: calls.append((table, fieldnames, records)))
    records = [{"id": "aaa111", "name": "Field Clinic", "district": "Peshawar", "lat": "34.01",
                "lon": "71.58", "category": "Clinic", "added_at": "2026-08-16T00:00:00+00:00", "added_by": "555"}]
    bot_facilities.append_records(records)
    assert calls == [("bot_facilities", bot_facilities.FIELDNAMES, records)]


def test_delete_record_calls_delete_by_id(monkeypatch):
    monkeypatch.setattr(bot_facilities.local_db, "delete_by_id",
                         lambda table, record_id: table == "bot_facilities" and record_id == "aaa111")
    assert bot_facilities.delete_record("aaa111") is True
    assert bot_facilities.delete_record("does-not-exist") is False
```

Update `test_add_facility_writes_a_record` (remove `monkeypatch.setattr(bot_facilities, "RECORDS_PATH", path)` and the trailing `load_records(path=path)` check):

```python
def test_add_facility_writes_a_record(monkeypatch):
    inserted = []
    monkeypatch.setattr(bot_facilities.local_db, "insert_many",
                         lambda table, fieldnames, records: inserted.append(records))
    record = bot_facilities.add_facility(
        name="Field Clinic", district="Peshawar", lat=34.01, lon=71.58,
        category="Clinic", added_by="555",
    )
    assert record["name"] == "Field Clinic"
    assert record["id"]
    assert "added_at" in record
    assert inserted == [[record]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_bot_facilities.py -v`
Expected: FAIL (`AttributeError: module 'server.bot_facilities' has no attribute 'local_db'`).

- [ ] **Step 3: Implement**

Replace `server/bot_facilities.py`'s storage section with:

```python
"""Facilities added via the Telegram bot's /addpoint command - a fourth
facility source alongside KPHCC/OSM/Marham, merged in by
scripts/07_merge_facilities.py (which reads the bot_facilities table
directly via scripts.lib.local_db, not through this module - see that
script for why). See docs/superpowers/specs/2026-08-16-manage-records-design.md,
docs/superpowers/specs/2026-08-16-telegram-connector-design.md section 8,
and docs/superpowers/specs/2026-08-16-bundled-local-database-design.md.
"""
import uuid
from datetime import datetime, timezone

from scripts.lib import local_db

FIELDNAMES = ("id", "name", "district", "lat", "lon", "category", "added_at", "added_by")


def load_records():
    return local_db.fetch_all("bot_facilities", order_by="added_at")


def append_records(records):
    local_db.insert_many("bot_facilities", FIELDNAMES, records)


def delete_record(record_id):
    return local_db.delete_by_id("bot_facilities", record_id)


def add_facility(name, district, lat, lon, category, added_by):
    record = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "district": district,
        "lat": lat,
        "lon": lon,
        "category": category,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "added_by": added_by,
    }
    append_records([record])
    return record
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_bot_facilities.py -v`
Expected: PASS.

- [ ] **Step 5: Update `scripts/07_merge_facilities.py`'s bot-facilities read**

Write the failing test first - update `test_merge_adds_bot_records_with_own_source_label` and the other two bot-related tests in `tests/test_merge_facilities.py` to pass a plain list of dicts (they already do - `merge()` itself is unaffected by this change, since `merge()` takes `bot` as a parameter, not a file path; nothing in `tests/test_merge_facilities.py` needs to change). Only `main()` changes, which those tests don't exercise. No new test needed for `main()`'s own read logic, matching `main()`'s existing lack of dedicated test coverage elsewhere in this file.

In `scripts/07_merge_facilities.py`, replace:

```python
    bot_path = PROCESSED / "bot_facilities.csv"
    bot = list(csv.DictReader(bot_path.open(newline="", encoding="utf-8"))) if bot_path.exists() else []
```

with:

```python
    bot = local_db.fetch_all("bot_facilities", order_by="added_at")
```

(`order_by="added_at"` preserves the original CSV read's behavior - `csv.DictReader` naturally returned rows in append order, which the dedup loop in `merge()` relies on: records are compared against every *earlier*-appended record, so read order affects which record ends up flagged as a duplicate of which.)

Add the import near the top, alongside the existing `scripts.lib` imports:

```python
from scripts.lib import local_db
```

- [ ] **Step 6: Run the merge_facilities tests**

Run: `pytest tests/test_merge_facilities.py -v`
Expected: PASS (all 12 tests - unaffected, since they all call `merge()` directly with an in-memory `bot` list, never `main()`).

- [ ] **Step 7: Check for any other test file referencing `bot_facilities.RECORDS_PATH` or the removed `path=` parameter**

Run: `grep -rn "bot_facilities.RECORDS_PATH\|bot_facilities\.load_records(path=\|bot_facilities\.append_records(.*path=\|bot_facilities\.delete_record(.*path=" tests/`
Expected (found during execution - the plan's original grep omitted `append_records(...path=`): two matches in `tests/server/test_telegram_bot.py`
(`test_addpoint_location_inside_kp_adds_facility`, `test_addpoint_location_outside_kp_rejected_without_writing`),
both monkeypatching `bot_facilities.RECORDS_PATH` and calling `load_records(path=...)`. Update both to mock
`bot_facilities.local_db.insert_many` instead (capture the inserted records into a list; assert on that list rather
than re-loading), matching this task's other mocked-`local_db` tests.

- [ ] **Step 8: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add server/bot_facilities.py tests/server/test_bot_facilities.py scripts/07_merge_facilities.py tests/test_merge_facilities.py
git commit -m "feat: migrate bot_facilities.py storage to the bundled database

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Wire the bundled database into `server/app.py`'s lifespan

**Files:**
- Modify: `server/app.py`
- Modify: `tests/server/test_app_lifespan.py`

**Interfaces:**
- Consumes: `local_db.ensure_running()`, `local_db.stop()` (Task 2).
- Produces: nothing new for later tasks - this is the final wiring point.

- [ ] **Step 1: Write the failing test**

Add to `tests/server/test_app_lifespan.py`:

```python
from scripts.lib import local_db


def test_lifespan_starts_and_stops_the_local_database(monkeypatch):
    ensure_running_calls = []
    stop_calls = []
    monkeypatch.setattr(local_db, "ensure_running", lambda: ensure_running_calls.append(1))
    monkeypatch.setattr(local_db, "stop", lambda: stop_calls.append(1))
    monkeypatch.setattr(telegram_bot, "start_bot_task", AsyncMock(return_value=True))
    monkeypatch.setattr(telegram_bot, "stop_bot_task", AsyncMock())

    with TestClient(create_app()) as client:
        client.get("/")

    assert ensure_running_calls == [1]
    assert stop_calls == [1]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/server/test_app_lifespan.py -v`
Expected: FAIL (`ensure_running_calls`/`stop_calls` never populated - `local_db` isn't wired into the lifespan yet).

- [ ] **Step 3: Implement**

Replace `server/app.py`'s contents:

```python
"""FastAPI application factory. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md,
2026-08-16-telegram-connector-design.md section 5, and
2026-08-16-bundled-local-database-design.md section 3.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from scripts.lib import local_db
from server import telegram_bot
from server.routes import admin, dashboard


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(local_db.ensure_running)
    await telegram_bot.start_bot_task()
    yield
    await telegram_bot.stop_bot_task()
    await asyncio.to_thread(local_db.stop)


def create_app():
    app = FastAPI(title="KP Healthcare Plan", lifespan=lifespan)
    app.include_router(dashboard.router)
    app.include_router(admin.router)
    return app


app = create_app()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/server/test_app_lifespan.py -v`
Expected: PASS (both tests in the file).

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass - confirms no pre-existing `TestClient(create_app())` call anywhere in the suite triggers the lifespan (same reasoning already established when the Telegram bot task was added to this same lifespan).

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/server/test_app_lifespan.py
git commit -m "feat: start/stop the bundled local database in server/app.py's lifespan

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Full test suite and live manual verification

**Files:** none (verification only).

This feature bootstraps and manages a real external process (PostgreSQL) for the first time in this project's own lifecycle (as opposed to `db_ingestion.py`, which only ever connects *outward* to a database the admin already runs), so per this project's established cadence it needs careful manual verification against the real running server - not just mocks.

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 2: Confirm no port/data-directory collision with anything already running**

Run: `netstat -ano | grep ":5544"` (or the PowerShell equivalent) - expect no output (port free). Confirm `data/pgdata/` doesn't already exist from an earlier throwaway verification cluster in a past session (checked via `Get-CimInstance`/`ls` - past sessions' own throwaway clusters used a scratchpad-local data directory, never this project's own `data/pgdata/`, so this should already be clean, but confirm before proceeding).

- [ ] **Step 3: Start the server and confirm first-run bootstrap**

Run: `python -m server`. Confirm in the server's own log output that no `LocalDbError` was raised, `data/pgdata/` now exists, and the app finishes startup normally (reaches "Uvicorn running on http://127.0.0.1:8420").

- [ ] **Step 4: Confirm the database and schema exist for real**

Run: `"C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -p 5544 -U kp_admin -d kp_healthcare -c "\dt"` (password prompt - read it from `keyring` first via `python -c "import keyring; print(keyring.get_password('kp-healthcare-plan', 'local_db_password'))"`, matching this project's established pattern of reading secrets back out for verification, never hardcoding them). Expected: `supplemental_records`, `metric_overrides`, `bot_facilities` tables listed.

- [ ] **Step 5: Exercise a full add/list/delete cycle through the admin panel for all three stores**

Log into `http://127.0.0.1:8420/admin` (same admin-password-reset-with-permission pattern as every prior manual-verification session if the current password isn't known). Upload a small test document via "Extract Document"/"Add to Report" - confirm it appears in the "Supplemental Records" table. Repeat for "Update Pipeline Data" (a modest, plausible override) - confirm it appears in "Pipeline Overrides". Delete both via the two-step confirm - confirm both tables return to "No records yet." Confirm via `psql` (or a quick `SELECT COUNT(*)`) that the underlying tables are actually empty again, not just the UI.

- [ ] **Step 6: Exercise `/addpoint` through the real Telegram bot**

If the bot is currently configured and reachable: send `/addpoint`, complete the flow with a real KP location. Confirm the facility appears in the admin panel's "Bot-Added Facilities" table and in `data/processed/facilities_merged.csv` after the rebuild (confirming `07_merge_facilities.py`'s `local_db.fetch_all("bot_facilities")` read works against the real database, not just a mock). Delete it via the admin panel afterward.

- [ ] **Step 7: Confirm persistence across a restart**

Add one real test record to any of the three stores. Stop the server (`Stop-Process`), start it again (`python -m server`). Confirm the record is still there (via the admin panel or `psql`) - this is the entire point of moving off CSVs onto a real database that survives independently of the app process, so this step is the one that actually proves the feature does what it's for. Delete the test record afterward.

- [ ] **Step 8: Clean up**

Confirm all test records added during this verification pass are deleted (Steps 5-7's own cleanup should already cover this - re-check via `psql` `SELECT COUNT(*) FROM <table>` for all three tables, expect 0 each). Confirm `git status`/`git diff` shows no unexpected changes to committed pipeline output (the CSV migration in Task 2's `initialize()` only runs once, against whatever was already committed - if `supplemental_records.csv`/`metric_overrides.csv`/`bot_facilities.csv` were empty/absent going into this verification, as they should be per this project's current state, the one-time migration is a no-op and there's nothing to reconcile). Restore the admin password if it was reset. Stop the server.

- [ ] **Step 9: Report findings**

If everything above checks out clean, this task (and the whole plan) is done. If anything looks wrong (bootstrap fails, a table's data doesn't persist across restart, `07_merge_facilities.py` doesn't see bot-added facilities), that's a real bug to fix with its own test (where automatable) before considering this complete.
