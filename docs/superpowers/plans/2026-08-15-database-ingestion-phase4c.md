# Database Ingestion (Phase 4c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the admin connect to a PostgreSQL database, browse its tables, and pull a table's rows through the same AI-extraction pipeline phase 4b built for document upload — producing the same kind of supplemental facility/district record, appended to the same store, shown in the same report section, grounding the same "Ask AI" chat — full autonomy, no review step.

**Architecture:** One new core module, `server/db_ingestion.py` (connect/list-tables/fetch-and-render, using `psycopg2` — already installed, no new dependency), reusing `supplemental_data.add_from_document()` exactly as phase 4b built it rather than duplicating AI-extraction/validation/storage logic. `keystore.py` gains a single reserved credential entry for one saved database connection (same pattern as `ADMIN_PASSWORD_KEY`/`SESSION_SECRET_KEY`). Three new admin routes mirror phase 4b's `/admin/api/supplemental-data` route shape exactly (save+test, list, preview-or-ingest), and the admin panel gains a "Database Ingestion" section alongside the existing "Extract Document" one.

**Tech Stack:** Python 3.12, existing project dependencies plus `psycopg2-binary` (already installed in this environment, not a new install) — reuses `supplemental_data`, `keystore`, `ai_client`, FastAPI, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-15-database-ingestion-phase4c-design.md`

## Global Constraints

- No new dependencies — `psycopg2-binary` is already installed in this environment.
- Reuse `supplemental_data.add_from_document()` exactly as it exists today — no changes to it, `ai_client.py`, `report_context.py`, or the report-rendering section (`scripts/14_build_html_report.py`, `scripts/lib/supplemental_records.py`) in this phase.
- PostgreSQL only, via `psycopg2`.
- One database connection at a time; saving a new one overwrites the old one.
- No app-level read-only enforcement — the module itself only ever issues `SELECT` statements; there is no code path, admin input, or AI output that reaches `INSERT`/`UPDATE`/`DELETE`/DDL.
- Table names are always validated against `list_tables()`'s own output before being used in any query — never raw admin or AI input taken on trust.
- Row-capped table pulls: 200 rows fixed (`ROW_LIMIT`), not admin-configurable in this phase.
- Public schema only (`information_schema.tables` filtered to `table_schema = 'public'`), no schema picker in this phase.
- Every typed exception (`DbIngestionError`, reused `SupplementalDataError`/`AIProviderError`) carries a message safe to show the admin directly, never a raw traceback or raw `psycopg2` exception.
- Every `psycopg2` call in every test is mocked — no test may require a real database connection, same posture as every AI provider call being mocked throughout this project.
- If the report-rebuild subprocess fails or times out after records were already appended, the route still returns 200 with the added records plus a `rebuild_warning` — data that was genuinely saved is never reported as a failure.
- The database password lives in the OS credential store via `keystore`/`keyring`, never in a file, never re-sent to the browser after saving.

---

### Task 1: `server/keystore.py` — single saved database connection

**Files:**
- Modify: `server/keystore.py`
- Modify: `tests/server/test_keystore.py`

**Interfaces:**
- Produces: `keystore.DB_CONNECTION_KEY` (constant); `keystore.get_db_connection() -> dict | None`; `keystore.set_db_connection(conn_info: dict) -> None`; `keystore.delete_db_connection() -> None`.
- Consumes: nothing new — extends the existing `keyring`-backed pattern already used by `get_admin_password_hash`/`set_admin_password_hash`/`get_session_secret`/`set_session_secret`.

- [ ] **Step 1: Write the failing tests**

Find (the end of `tests/server/test_keystore.py`):

```python
def test_session_secret_roundtrip(fake_store):
    assert keystore.get_session_secret() is None
    keystore.set_session_secret("deadbeef")
    assert keystore.get_session_secret() == "deadbeef"
```

Replace with:

```python
def test_session_secret_roundtrip(fake_store):
    assert keystore.get_session_secret() is None
    keystore.set_session_secret("deadbeef")
    assert keystore.get_session_secret() == "deadbeef"


DB_CONN_INFO = {
    "host": "localhost", "port": 5432, "database": "kp_health",
    "user": "admin", "password": "s3cret", "sslmode": "prefer",
}


def test_db_connection_roundtrip(fake_store):
    assert keystore.get_db_connection() is None
    keystore.set_db_connection(DB_CONN_INFO)
    assert keystore.get_db_connection() == DB_CONN_INFO


def test_set_db_connection_overwrites_previous(fake_store):
    keystore.set_db_connection(DB_CONN_INFO)
    other = dict(DB_CONN_INFO, host="otherhost", database="other_db")
    keystore.set_db_connection(other)
    assert keystore.get_db_connection() == other


def test_delete_db_connection_removes_it(fake_store):
    keystore.set_db_connection(DB_CONN_INFO)
    keystore.delete_db_connection()
    assert keystore.get_db_connection() is None


def test_delete_db_connection_missing_is_a_noop(fake_store):
    keystore.delete_db_connection()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_keystore.py -v`
Expected: 4 new FAILs with `AttributeError: module 'server.keystore' has no attribute 'get_db_connection'` (or similar for `set_db_connection`/`delete_db_connection`)

- [ ] **Step 3: Implement the change**

Find:

```python
"""Wraps the `keyring` package for storing AI-provider API keys and admin
auth secrets in OS-level secret storage (Windows Credential Manager on this
platform) - never in a plaintext file. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md section 5.
"""
import keyring

SERVICE_NAME = "kp-healthcare-plan"

PROVIDERS = ("anthropic", "openai", "gemini", "grok", "groq")

# Reserved keyring usernames for admin auth secrets - never listed as AI
# providers, and never accepted by get_key/set_key/delete_key.
ADMIN_PASSWORD_KEY = "admin_password_hash"
SESSION_SECRET_KEY = "session_secret"
```

Replace with:

```python
"""Wraps the `keyring` package for storing AI-provider API keys, admin
auth secrets, and the single saved database connection in OS-level secret
storage (Windows Credential Manager on this platform) - never in a
plaintext file. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md section 5 and
2026-08-15-database-ingestion-phase4c-design.md section 4.
"""
import json

import keyring

SERVICE_NAME = "kp-healthcare-plan"

PROVIDERS = ("anthropic", "openai", "gemini", "grok", "groq")

# Reserved keyring usernames for admin auth secrets and the single saved
# database connection - never listed as AI providers, and never accepted
# by get_key/set_key/delete_key.
ADMIN_PASSWORD_KEY = "admin_password_hash"
SESSION_SECRET_KEY = "session_secret"
DB_CONNECTION_KEY = "db_connection"
```

Find:

```python
def get_session_secret():
    return keyring.get_password(SERVICE_NAME, SESSION_SECRET_KEY)


def set_session_secret(value):
    keyring.set_password(SERVICE_NAME, SESSION_SECRET_KEY, value)
```

Replace with:

```python
def get_session_secret():
    return keyring.get_password(SERVICE_NAME, SESSION_SECRET_KEY)


def set_session_secret(value):
    keyring.set_password(SERVICE_NAME, SESSION_SECRET_KEY, value)


def get_db_connection():
    raw = keyring.get_password(SERVICE_NAME, DB_CONNECTION_KEY)
    if raw is None:
        return None
    return json.loads(raw)


def set_db_connection(conn_info):
    keyring.set_password(SERVICE_NAME, DB_CONNECTION_KEY, json.dumps(conn_info))


def delete_db_connection():
    try:
        keyring.delete_password(SERVICE_NAME, DB_CONNECTION_KEY)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent - deleting a non-existent entry is a no-op
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_keystore.py -v`
Expected: 14 passed (10 already there plus the 4 new ones)

- [ ] **Step 5: Commit**

```bash
git add server/keystore.py tests/server/test_keystore.py
git commit -m "feat: add single saved database connection to keystore

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `server/db_ingestion.py` — connect, list tables, fetch and render

**Files:**
- Create: `server/db_ingestion.py`
- Test: `tests/server/test_db_ingestion.py`

**Interfaces:**
- Produces: `db_ingestion.DbIngestionError(Exception)`; `db_ingestion.ROW_LIMIT` (int, 200); `db_ingestion.test_connection(conn_info: dict) -> (bool, str)`; `db_ingestion.list_tables(conn_info: dict) -> list[str]`; `db_ingestion.fetch_table_text(conn_info: dict, table_name: str, row_limit: int = ROW_LIMIT) -> str`.
- Consumes: `psycopg2` (already installed) — called as `psycopg2.connect(...)`, a module-attribute lookup so tests can monkeypatch it the same way `test_ask_route.py`/`test_supplemental_data.py` monkeypatch `ai_client.ask`.

This task does not depend on Task 1 — `db_ingestion.py` takes a plain `conn_info` dict as a parameter everywhere; it never reads from `keystore` itself (only the route layer in Task 3 does).

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_db_ingestion.py`:

```python
"""Unit tests for server/db_ingestion.py. Every psycopg2 call in every test
is mocked via monkeypatching db_ingestion.psycopg2.connect - no test
requires a real database connection. See docs/superpowers/specs/
2026-08-15-database-ingestion-phase4c-design.md section 5.
"""
import pytest

from server import db_ingestion

CONN_INFO = {
    "host": "localhost", "port": 5432, "database": "kp_health",
    "user": "admin", "password": "s3cret", "sslmode": "prefer",
}


class FakeCursor:
    def __init__(self, rows, description):
        self._rows = rows
        self.description = description
        self.executed = None

    def execute(self, query, params=None):
        self.executed = (query, params)

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

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def test_test_connection_success(monkeypatch):
    fake_conn = FakeConnection(FakeCursor([], []))
    monkeypatch.setattr(db_ingestion.psycopg2, "connect", lambda **kwargs: fake_conn)
    ok, detail = db_ingestion.test_connection(CONN_INFO)
    assert ok is True
    assert detail == "Connected"
    assert fake_conn.closed is True


def test_test_connection_failure(monkeypatch):
    def raise_connect(**kwargs):
        raise Exception("password authentication failed")
    monkeypatch.setattr(db_ingestion.psycopg2, "connect", raise_connect)
    ok, detail = db_ingestion.test_connection(CONN_INFO)
    assert ok is False
    assert "password authentication failed" in detail


def test_list_tables_returns_sorted_names(monkeypatch):
    cursor = FakeCursor([("districts",), ("facilities",)], [])
    fake_conn = FakeConnection(cursor)
    monkeypatch.setattr(db_ingestion.psycopg2, "connect", lambda **kwargs: fake_conn)
    tables = db_ingestion.list_tables(CONN_INFO)
    assert tables == ["districts", "facilities"]
    assert fake_conn.closed is True
    assert "information_schema.tables" in cursor.executed[0]
    assert "public" in cursor.executed[0]


def test_list_tables_connection_failure_raises(monkeypatch):
    def raise_connect(**kwargs):
        raise Exception("could not connect to server")
    monkeypatch.setattr(db_ingestion.psycopg2, "connect", raise_connect)
    with pytest.raises(db_ingestion.DbIngestionError, match="could not connect to server"):
        db_ingestion.list_tables(CONN_INFO)


def test_fetch_table_text_renders_pipe_delimited_rows_with_row_cap_note(monkeypatch):
    list_cursor = FakeCursor([("equipment",)], [])
    data_cursor = FakeCursor(
        [("Peshawar", "DHQ Hospital", 1), ("Chitral", None, 2)],
        [("district",), ("facility",), ("count",)],
    )
    connections = [FakeConnection(list_cursor), FakeConnection(data_cursor)]
    monkeypatch.setattr(db_ingestion.psycopg2, "connect", lambda **kwargs: connections.pop(0))

    text = db_ingestion.fetch_table_text(CONN_INFO, "equipment", row_limit=200)

    assert "(showing first 200 rows)" in text
    assert "district | facility | count" in text
    assert "Peshawar | DHQ Hospital | 1" in text
    assert "Chitral |  | 2" in text  # None cell renders as an empty string, not "None"
    assert 'SELECT * FROM "equipment" LIMIT' in data_cursor.executed[0]


def test_fetch_table_text_unknown_table_raises(monkeypatch):
    cursor = FakeCursor([("districts",)], [])
    fake_conn = FakeConnection(cursor)
    monkeypatch.setattr(db_ingestion.psycopg2, "connect", lambda **kwargs: fake_conn)
    with pytest.raises(db_ingestion.DbIngestionError, match="bogus_table"):
        db_ingestion.fetch_table_text(CONN_INFO, "bogus_table")


def test_fetch_table_text_query_failure_raises(monkeypatch):
    list_cursor = FakeCursor([("equipment",)], [])

    class FailingCursor(FakeCursor):
        def execute(self, query, params=None):
            raise Exception("relation does not exist")

    connections = [FakeConnection(list_cursor), FakeConnection(FailingCursor([], []))]
    monkeypatch.setattr(db_ingestion.psycopg2, "connect", lambda **kwargs: connections.pop(0))
    with pytest.raises(db_ingestion.DbIngestionError, match="relation does not exist"):
        db_ingestion.fetch_table_text(CONN_INFO, "equipment")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_db_ingestion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.db_ingestion'`

- [ ] **Step 3: Implement `server/db_ingestion.py`**

```python
"""Connects to a PostgreSQL database, lists its tables, and renders a
table's rows as pipe-delimited text - the database-specific counterpart to
document_extraction.py. No AI, no validation, no storage logic of its own;
its output feeds supplemental_data.add_from_document() exactly the way an
uploaded CSV file's extracted text does. See docs/superpowers/specs/
2026-08-15-database-ingestion-phase4c-design.md.
"""
import psycopg2

ROW_LIMIT = 200


class DbIngestionError(Exception):
    """Raised when a database connection or query fails - message safe to
    show the admin directly, never a raw psycopg2 exception or traceback."""


def _connect(conn_info):
    try:
        return psycopg2.connect(
            host=conn_info["host"],
            port=conn_info["port"],
            dbname=conn_info["database"],
            user=conn_info["user"],
            password=conn_info["password"],
            sslmode=conn_info.get("sslmode") or "prefer",
            connect_timeout=5,
        )
    except Exception as exc:
        raise DbIngestionError(str(exc)) from exc


def test_connection(conn_info):
    try:
        conn = _connect(conn_info)
    except DbIngestionError as exc:
        return False, str(exc)
    conn.close()
    return True, "Connected"


def list_tables(conn_info):
    conn = _connect(conn_info)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            return [row[0] for row in cur.fetchall()]
    except DbIngestionError:
        raise
    except Exception as exc:
        raise DbIngestionError(str(exc)) from exc
    finally:
        conn.close()


def fetch_table_text(conn_info, table_name, row_limit=ROW_LIMIT):
    known_tables = list_tables(conn_info)
    if table_name not in known_tables:
        raise DbIngestionError(f"Unknown table: {table_name!r}")

    conn = _connect(conn_info)
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT * FROM "{table_name}" LIMIT %s', (row_limit,))
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
    except Exception as exc:
        raise DbIngestionError(str(exc)) from exc
    finally:
        conn.close()

    lines = [f"(showing first {row_limit} rows)", " | ".join(columns)]
    for row in rows:
        lines.append(" | ".join("" if cell is None else str(cell) for cell in row))
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_db_ingestion.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add server/db_ingestion.py tests/server/test_db_ingestion.py
git commit -m "feat: add db_ingestion module for PostgreSQL table browsing/ingestion

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Wire it in — `/admin/api/db/*` routes, admin panel UI, end-to-end tests, manual verification

**Files:**
- Modify: `server/routes/admin.py`
- Modify: `server/admin_ui.py`
- Modify: `tests/server/test_routes.py`
- Create: `tests/server/test_db_ingestion_route.py`

**Interfaces:**
- Consumes: `db_ingestion.test_connection`/`list_tables`/`fetch_table_text`/`DbIngestionError` (Task 2), `keystore.get_db_connection`/`set_db_connection` (Task 1), `supplemental_data.add_from_document`/`SupplementalDataError` (phase 4b), `ai_client.AIProviderError` (phase 3), `keystore.PROVIDERS`/`get_key` (phase 2).
- Produces: the final, verified phase-4c feature.

- [ ] **Step 1: Update imports in `server/routes/admin.py`**

Find:

```python
"""GET/POST /admin, /admin/setup, /admin/login, /admin/logout, the
/admin/api/keys* JSON API, /admin/api/extract for document upload, and
/admin/api/supplemental-data for AI-extracted facility/district records.
See docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md
sections 6 and 8, 2026-08-15-document-upload-phase4a-design.md section 4,
and 2026-08-15-supplemental-facility-data-phase4b-design.md section 5.
"""
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Body, Cookie, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from server import admin_ui, ai_client, auth, document_extraction, keystore, providers, supplemental_data

REPORT_BUILD_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "14_build_html_report.py"
```

Replace with:

```python
"""GET/POST /admin, /admin/setup, /admin/login, /admin/logout, the
/admin/api/keys* JSON API, /admin/api/extract for document upload,
/admin/api/supplemental-data for AI-extracted facility/district records,
and /admin/api/db/* for PostgreSQL table browsing/ingestion. See
docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md
sections 6 and 8, 2026-08-15-document-upload-phase4a-design.md section 4,
2026-08-15-supplemental-facility-data-phase4b-design.md section 5, and
2026-08-15-database-ingestion-phase4c-design.md section 6.
"""
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Body, Cookie, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from server import admin_ui, ai_client, auth, db_ingestion, document_extraction, keystore, providers, supplemental_data

REPORT_BUILD_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "14_build_html_report.py"
```

- [ ] **Step 2: Append the three new routes to `server/routes/admin.py`**

Find (the end of the file):

```python
@router.post("/admin/api/extract")
async def extract_document(
    file: UploadFile = File(...),
    kp_admin_session: str | None = Cookie(default=None),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    content_bytes = await file.read()
    try:
        result = document_extraction.extract(file.filename or "upload", content_bytes)
    except document_extraction.UnsupportedFormatError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=415)
    except document_extraction.ExtractionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(result.to_dict())
```

Replace with:

```python
@router.post("/admin/api/extract")
async def extract_document(
    file: UploadFile = File(...),
    kp_admin_session: str | None = Cookie(default=None),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    content_bytes = await file.read()
    try:
        result = document_extraction.extract(file.filename or "upload", content_bytes)
    except document_extraction.UnsupportedFormatError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=415)
    except document_extraction.ExtractionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(result.to_dict())


@router.post("/admin/api/db/connection")
def save_db_connection(
    kp_admin_session: str | None = Cookie(default=None),
    host: str = Body(...),
    port: int = Body(5432),
    database: str = Body(...),
    user: str = Body(...),
    password: str = Body(...),
    sslmode: str = Body(""),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    conn_info = {
        "host": host, "port": port, "database": database,
        "user": user, "password": password, "sslmode": sslmode or "prefer",
    }
    keystore.set_db_connection(conn_info)
    ok, detail = db_ingestion.test_connection(conn_info)
    return JSONResponse({"ok": ok, "detail": detail})


@router.get("/admin/api/db/tables")
def list_db_tables(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    conn_info = keystore.get_db_connection()
    if not conn_info:
        return JSONResponse(
            {"detail": "No database connection configured - save one in the admin panel first."},
            status_code=400,
        )
    try:
        tables = db_ingestion.list_tables(conn_info)
    except db_ingestion.DbIngestionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse({"tables": tables})


@router.post("/admin/api/db/ingest")
def ingest_from_db(
    kp_admin_session: str | None = Cookie(default=None),
    table: str = Body(...),
    provider: str = Body(""),
    instruction: str = Body(""),
    preview: bool = Body(False),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    conn_info = keystore.get_db_connection()
    if not conn_info:
        return JSONResponse(
            {"detail": "No database connection configured - save one in the admin panel first."},
            status_code=400,
        )

    try:
        text = db_ingestion.fetch_table_text(conn_info, table)
    except db_ingestion.DbIngestionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    if preview:
        return JSONResponse({"text": text})

    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    key = keystore.get_key(provider)
    if not key:
        return JSONResponse(
            {"detail": f"No API key configured for {provider} - add one in the admin panel first."},
            status_code=400,
        )

    try:
        added = supplemental_data.add_from_document(provider, key, text, instruction, f"db:{table}")
    except supplemental_data.SupplementalDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"added": added, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"added": added, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"added": added})
```

- [ ] **Step 3: Add the new section's CSS to `server/admin_ui.py`**

Find:

```python
#supplemental-status { display: none; }
#supplemental-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }
"""
```

Replace with:

```python
#supplemental-status { display: none; }
#supplemental-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }
#db-table-select {
  display: block;
  width: 100%;
  margin: 0.5rem 0;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-size: 0.85rem;
  font-family: inherit;
}
#db-connection-status { display: none; }
#db-connection-status.ok { color: var(--accent-2); display: block; }
#db-connection-status.bad { color: var(--danger); display: block; }
#db-ingest-status { display: none; }
#db-preview-result {
  display: block;
  width: 100%;
  margin-top: 0.75rem;
  padding: 0.6rem 0.7rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.8rem;
  resize: vertical;
}
#db-ingest-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }
"""
```

- [ ] **Step 4: Add the new section's JS to `server/admin_ui.py`**

Find:

```python
    var addToReportBtn = byId("add-to-report-btn");
    if (addToReportBtn) {
      addToReportBtn.addEventListener("click", function () {
        var fileInput = byId("extract-file-input");
        var instructionInput = byId("supplemental-instruction");
        var providerSelect = byId("supplemental-provider");
        var statusEl = byId("supplemental-status");
        var resultEl = byId("supplemental-result");
        var file = fileInput.files[0];
        statusEl.style.display = "none";
        resultEl.innerHTML = "";

        if (!file) {
          statusEl.textContent = "Choose a file first";
          statusEl.style.display = "block";
          return;
        }

        var formData = new FormData();
        formData.append("file", file);
        formData.append("provider", providerSelect.value);
        formData.append("instruction", instructionInput.value);
        addToReportBtn.disabled = true;
        addToReportBtn.textContent = "Adding...";

        fetch("/admin/api/supplemental-data", { method: "POST", body: formData })
          .then(function (res) {
            return res.json().then(function (data) { return { ok: res.ok, data: data }; });
          })
          .then(function (result) {
            addToReportBtn.disabled = false;
            addToReportBtn.textContent = "Add to Report";
            if (result.ok) {
              var added = (result.data && result.data.added) || [];
              var summary = added.map(function (r) {
                var facility = r.facility ? " / " + escapeHtml(r.facility) : "";
                return escapeHtml(r.district) + facility + " - " + escapeHtml(r.category) + ": " + escapeHtml(r.label);
              }).join("<br>");
              resultEl.innerHTML = "<p>Added " + added.length + " record(s):</p><p>" + summary + "</p>";
              if (result.data && result.data.rebuild_warning) {
                resultEl.innerHTML += "<p class='error'>" + escapeHtml(result.data.rebuild_warning) + "</p>";
              }
            } else {
              statusEl.textContent = (result.data && result.data.detail) || "Request failed";
              statusEl.style.display = "block";
            }
          })
          .catch(function (err) {
            addToReportBtn.disabled = false;
            addToReportBtn.textContent = "Add to Report";
            statusEl.textContent = "Request failed: " + err;
            statusEl.style.display = "block";
          });
      });
    }
  });
})();
"""
```

Replace with:

```python
    var addToReportBtn = byId("add-to-report-btn");
    if (addToReportBtn) {
      addToReportBtn.addEventListener("click", function () {
        var fileInput = byId("extract-file-input");
        var instructionInput = byId("supplemental-instruction");
        var providerSelect = byId("supplemental-provider");
        var statusEl = byId("supplemental-status");
        var resultEl = byId("supplemental-result");
        var file = fileInput.files[0];
        statusEl.style.display = "none";
        resultEl.innerHTML = "";

        if (!file) {
          statusEl.textContent = "Choose a file first";
          statusEl.style.display = "block";
          return;
        }

        var formData = new FormData();
        formData.append("file", file);
        formData.append("provider", providerSelect.value);
        formData.append("instruction", instructionInput.value);
        addToReportBtn.disabled = true;
        addToReportBtn.textContent = "Adding...";

        fetch("/admin/api/supplemental-data", { method: "POST", body: formData })
          .then(function (res) {
            return res.json().then(function (data) { return { ok: res.ok, data: data }; });
          })
          .then(function (result) {
            addToReportBtn.disabled = false;
            addToReportBtn.textContent = "Add to Report";
            if (result.ok) {
              var added = (result.data && result.data.added) || [];
              var summary = added.map(function (r) {
                var facility = r.facility ? " / " + escapeHtml(r.facility) : "";
                return escapeHtml(r.district) + facility + " - " + escapeHtml(r.category) + ": " + escapeHtml(r.label);
              }).join("<br>");
              resultEl.innerHTML = "<p>Added " + added.length + " record(s):</p><p>" + summary + "</p>";
              if (result.data && result.data.rebuild_warning) {
                resultEl.innerHTML += "<p class='error'>" + escapeHtml(result.data.rebuild_warning) + "</p>";
              }
            } else {
              statusEl.textContent = (result.data && result.data.detail) || "Request failed";
              statusEl.style.display = "block";
            }
          })
          .catch(function (err) {
            addToReportBtn.disabled = false;
            addToReportBtn.textContent = "Add to Report";
            statusEl.textContent = "Request failed: " + err;
            statusEl.style.display = "block";
          });
      });
    }

    function loadDbTables() {
      var select = byId("db-table-select");
      if (!select) return;
      apiCall("GET", "/admin/api/db/tables").then(function (result) {
        if (result.status === 200 && result.data && result.data.tables) {
          select.innerHTML = '<option value="">Select a table...</option>';
          result.data.tables.forEach(function (t) {
            var opt = document.createElement("option");
            opt.value = t;
            opt.textContent = t;
            select.appendChild(opt);
          });
        }
      });
    }

    var dbConnectBtn = byId("db-connect-btn");
    if (dbConnectBtn) {
      loadDbTables();

      dbConnectBtn.addEventListener("click", function () {
        var statusEl = byId("db-connection-status");
        statusEl.style.display = "none";
        var body = {
          host: byId("db-host").value.trim(),
          port: parseInt(byId("db-port").value, 10) || 5432,
          database: byId("db-database").value.trim(),
          user: byId("db-user").value.trim(),
          password: byId("db-password").value,
          sslmode: byId("db-sslmode").value.trim(),
        };
        dbConnectBtn.disabled = true;
        dbConnectBtn.textContent = "Testing...";
        apiCall("POST", "/admin/api/db/connection", body).then(function (result) {
          dbConnectBtn.disabled = false;
          dbConnectBtn.textContent = "Save & Test Connection";
          statusEl.textContent = (result.data && result.data.detail) || "Unknown error";
          statusEl.className = result.data && result.data.ok ? "ok" : "bad";
          if (result.data && result.data.ok) {
            loadDbTables();
          }
        });
      });
    }

    var dbPreviewBtn = byId("db-preview-btn");
    if (dbPreviewBtn) {
      dbPreviewBtn.addEventListener("click", function () {
        var table = byId("db-table-select").value;
        var resultEl = byId("db-preview-result");
        var statusEl = byId("db-ingest-status");
        resultEl.value = "";
        statusEl.style.display = "none";
        if (!table) {
          statusEl.textContent = "Choose a table first";
          statusEl.style.display = "block";
          return;
        }
        dbPreviewBtn.disabled = true;
        dbPreviewBtn.textContent = "Previewing...";
        apiCall("POST", "/admin/api/db/ingest", { table: table, preview: true }).then(function (result) {
          dbPreviewBtn.disabled = false;
          dbPreviewBtn.textContent = "Preview";
          if (result.status === 200) {
            resultEl.value = result.data.text;
          } else {
            statusEl.textContent = (result.data && result.data.detail) || "Preview failed";
            statusEl.style.display = "block";
          }
        });
      });
    }

    var dbIngestBtn = byId("db-ingest-btn");
    if (dbIngestBtn) {
      dbIngestBtn.addEventListener("click", function () {
        var table = byId("db-table-select").value;
        var instruction = byId("db-instruction").value;
        var provider = byId("db-provider").value;
        var statusEl = byId("db-ingest-status");
        var resultEl = byId("db-ingest-result");
        statusEl.style.display = "none";
        resultEl.innerHTML = "";
        if (!table) {
          statusEl.textContent = "Choose a table first";
          statusEl.style.display = "block";
          return;
        }
        dbIngestBtn.disabled = true;
        dbIngestBtn.textContent = "Adding...";
        apiCall("POST", "/admin/api/db/ingest", { table: table, instruction: instruction, provider: provider })
          .then(function (result) {
            dbIngestBtn.disabled = false;
            dbIngestBtn.textContent = "Add to Report";
            if (result.status === 200) {
              var added = (result.data && result.data.added) || [];
              var summary = added.map(function (r) {
                var facility = r.facility ? " / " + escapeHtml(r.facility) : "";
                return escapeHtml(r.district) + facility + " - " + escapeHtml(r.category) + ": " + escapeHtml(r.label);
              }).join("<br>");
              resultEl.innerHTML = "<p>Added " + added.length + " record(s):</p><p>" + summary + "</p>";
              if (result.data && result.data.rebuild_warning) {
                resultEl.innerHTML += "<p class='error'>" + escapeHtml(result.data.rebuild_warning) + "</p>";
              }
            } else {
              statusEl.textContent = (result.data && result.data.detail) || "Request failed";
              statusEl.style.display = "block";
            }
          });
      });
    }
  });
})();
"""
```

- [ ] **Step 5: Add the new section's markup to `admin_ui.py`**

Find:

```python
  <button type="button" class="primary" id="add-to-report-btn">Add to Report</button>
  <p id="supplemental-status" class="error"></p>
  <div id="supplemental-result"></div>
</div>
<p style="margin-top:1.5rem"><button type="button" class="secondary" id="logout-btn">Log Out</button></p>
</div>
<script>{ADMIN_JS}</script>
</body>
</html>
"""
```

Replace with:

```python
  <button type="button" class="primary" id="add-to-report-btn">Add to Report</button>
  <p id="supplemental-status" class="error"></p>
  <div id="supplemental-result"></div>
</div>
<div class="upload-section">
  <h2>Database Ingestion</h2>
  <p class="hint">Connect to a PostgreSQL database, browse its tables, and add a table's rows to the report as supplemental facility/district information (equipment, medicine, departments, diseases treated, outbreaks, or anything else) - same AI extraction as document upload, one connection at a time.</p>
  <label for="db-host">Host</label>
  <input type="text" id="db-host" placeholder="localhost">
  <label for="db-port">Port</label>
  <input type="text" id="db-port" placeholder="5432">
  <label for="db-database">Database</label>
  <input type="text" id="db-database" placeholder="kp_health">
  <label for="db-user">Username</label>
  <input type="text" id="db-user" placeholder="db username">
  <label for="db-password">Password</label>
  <input type="password" id="db-password" placeholder="db password">
  <label for="db-sslmode">SSL mode (optional)</label>
  <input type="text" id="db-sslmode" placeholder="prefer">
  <button type="button" class="primary" id="db-connect-btn">Save &amp; Test Connection</button>
  <p id="db-connection-status"></p>
  <label for="db-table-select">Table</label>
  <select id="db-table-select">
    <option value="">Select a table...</option>
  </select>
  <button type="button" class="secondary" id="db-preview-btn">Preview</button>
  <textarea id="db-preview-result" readonly rows="8" placeholder="Previewed rows will appear here"></textarea>
  <label for="db-instruction">Instruction (optional)</label>
  <textarea id="db-instruction" rows="2" placeholder="e.g. this table lists equipment per facility"></textarea>
  <label for="db-provider">AI provider</label>
  <select id="db-provider">
{provider_options}
  </select>
  <button type="button" class="primary" id="db-ingest-btn">Add to Report</button>
  <p id="db-ingest-status" class="error"></p>
  <div id="db-ingest-result"></div>
</div>
<p style="margin-top:1.5rem"><button type="button" class="secondary" id="logout-btn">Log Out</button></p>
</div>
<script>{ADMIN_JS}</script>
</body>
</html>
"""
```

- [ ] **Step 6: Write `tests/server/test_db_ingestion_route.py`**

```python
"""End-to-end /admin/api/db/* tests via FastAPI's TestClient. db_ingestion's
psycopg2 calls, supplemental_data.add_from_document, and the report-rebuild
subprocess call are all mocked - no real database connection, AI provider
call, or report-build script run. keyring is mocked too, same pattern as
tests/server/test_supplemental_data_route.py.
"""
import subprocess

import pytest
from fastapi.testclient import TestClient

from server import db_ingestion, keystore, supplemental_data
from server.app import create_app
from server.routes import admin as admin_route


class FakeStore:
    def __init__(self):
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        del self.data[(service, username)]


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(keystore.keyring, "get_password", store.get_password)
    monkeypatch.setattr(keystore.keyring, "set_password", store.set_password)
    monkeypatch.setattr(keystore.keyring, "delete_password", store.delete_password)
    return store


@pytest.fixture
def client(fake_store):
    return TestClient(create_app())


SETUP_FORM = {"password": "hunter2hunter2", "confirm": "hunter2hunter2"}

CONN_INFO = {
    "host": "localhost", "port": 5432, "database": "kp_health",
    "user": "admin", "password": "s3cret", "sslmode": "prefer",
}


def _login(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})


class FakeCompletedProcess:
    returncode = 0
    stderr = ""


def test_db_connection_requires_authentication(client):
    response = client.post("/admin/api/db/connection", json=CONN_INFO)
    assert response.status_code == 401


def test_db_connection_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(db_ingestion, "test_connection", lambda conn_info: (True, "Connected"))
    response = client.post("/admin/api/db/connection", json=CONN_INFO)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "detail": "Connected"}
    assert keystore.get_db_connection()["host"] == "localhost"


def test_db_connection_failure_still_saves_and_reports_detail(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(db_ingestion, "test_connection", lambda conn_info: (False, "Could not connect: timeout"))
    response = client.post("/admin/api/db/connection", json=CONN_INFO)
    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "Could not connect: timeout"}
    assert keystore.get_db_connection()["host"] == "localhost"


def test_db_tables_requires_authentication(client):
    response = client.get("/admin/api/db/tables")
    assert response.status_code == 401


def test_db_tables_without_configured_connection_returns_400(client):
    _login(client)
    response = client.get("/admin/api/db/tables")
    assert response.status_code == 400


def test_db_tables_success(client, monkeypatch):
    _login(client)
    keystore.set_db_connection(CONN_INFO)
    monkeypatch.setattr(db_ingestion, "list_tables", lambda conn_info: ["districts", "facilities"])
    response = client.get("/admin/api/db/tables")
    assert response.status_code == 200
    assert response.json() == {"tables": ["districts", "facilities"]}


def test_db_tables_connection_error_returns_400(client, monkeypatch):
    _login(client)
    keystore.set_db_connection(CONN_INFO)

    def failing_list(conn_info):
        raise db_ingestion.DbIngestionError("Could not connect: timeout")

    monkeypatch.setattr(db_ingestion, "list_tables", failing_list)
    response = client.get("/admin/api/db/tables")
    assert response.status_code == 400


def test_db_ingest_requires_authentication(client):
    response = client.post("/admin/api/db/ingest", json={"table": "equipment"})
    assert response.status_code == 401


def test_db_ingest_without_configured_connection_returns_400(client):
    _login(client)
    response = client.post("/admin/api/db/ingest", json={"table": "equipment"})
    assert response.status_code == 400


def test_db_ingest_preview_returns_text_without_ai_call(client, monkeypatch):
    _login(client)
    keystore.set_db_connection(CONN_INFO)
    monkeypatch.setattr(db_ingestion, "fetch_table_text", lambda conn_info, table: "district | count\nPeshawar | 5")
    response = client.post("/admin/api/db/ingest", json={"table": "equipment", "preview": True})
    assert response.status_code == 200
    assert response.json() == {"text": "district | count\nPeshawar | 5"}


def test_db_ingest_unknown_table_returns_400(client, monkeypatch):
    _login(client)
    keystore.set_db_connection(CONN_INFO)

    def failing_fetch(conn_info, table):
        raise db_ingestion.DbIngestionError(f"Unknown table: {table!r}")

    monkeypatch.setattr(db_ingestion, "fetch_table_text", failing_fetch)
    response = client.post("/admin/api/db/ingest", json={"table": "bogus", "preview": True})
    assert response.status_code == 400


def test_db_ingest_success(client, monkeypatch):
    _login(client)
    keystore.set_db_connection(CONN_INFO)
    keystore.set_key("anthropic", "sk-ant-real")
    monkeypatch.setattr(db_ingestion, "fetch_table_text", lambda conn_info, table: "district | count\nPeshawar | 5")
    fake_added = [{"district": "Peshawar", "facility": "", "category": "equipment",
                   "label": "X-ray", "detail": "5 units", "source_document": "db:equipment",
                   "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data, "add_from_document", lambda *args, **kwargs: fake_added)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())

    response = client.post(
        "/admin/api/db/ingest",
        json={"table": "equipment", "instruction": "", "provider": "anthropic"},
    )
    assert response.status_code == 200
    assert response.json() == {"added": fake_added}


def test_db_ingest_without_configured_key_returns_400(client, monkeypatch):
    _login(client)
    keystore.set_db_connection(CONN_INFO)
    monkeypatch.setattr(db_ingestion, "fetch_table_text", lambda conn_info, table: "district | count\nPeshawar | 5")
    response = client.post(
        "/admin/api/db/ingest",
        json={"table": "equipment", "instruction": "", "provider": "anthropic"},
    )
    assert response.status_code == 400


def test_db_ingest_validation_failure_returns_400(client, monkeypatch):
    _login(client)
    keystore.set_db_connection(CONN_INFO)
    keystore.set_key("anthropic", "sk-ant-real")
    monkeypatch.setattr(db_ingestion, "fetch_table_text", lambda conn_info, table: "district | count\nPeshawar | 5")

    def failing_add(*args, **kwargs):
        raise supplemental_data.SupplementalDataError("AI response was not valid JSON")

    monkeypatch.setattr(supplemental_data, "add_from_document", failing_add)
    response = client.post(
        "/admin/api/db/ingest",
        json={"table": "equipment", "instruction": "", "provider": "anthropic"},
    )
    assert response.status_code == 400


def test_db_ingest_provider_failure_returns_502(client, monkeypatch):
    _login(client)
    keystore.set_db_connection(CONN_INFO)
    keystore.set_key("anthropic", "sk-ant-real")
    monkeypatch.setattr(db_ingestion, "fetch_table_text", lambda conn_info, table: "district | count\nPeshawar | 5")

    def failing_add(*args, **kwargs):
        raise supplemental_data.ai_client.AIProviderError("Anthropic API returned 500")

    monkeypatch.setattr(supplemental_data, "add_from_document", failing_add)
    response = client.post(
        "/admin/api/db/ingest",
        json={"table": "equipment", "instruction": "", "provider": "anthropic"},
    )
    assert response.status_code == 502


def test_db_ingest_rebuild_failure_still_returns_added_records(client, monkeypatch):
    _login(client)
    keystore.set_db_connection(CONN_INFO)
    keystore.set_key("anthropic", "sk-ant-real")
    monkeypatch.setattr(db_ingestion, "fetch_table_text", lambda conn_info, table: "district | count\nPeshawar | 5")
    fake_added = [{"district": "Peshawar", "facility": "", "category": "equipment", "label": "X-ray",
                   "detail": "", "source_document": "db:equipment", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data, "add_from_document", lambda *args, **kwargs: fake_added)

    class FailedProcess:
        returncode = 1
        stderr = "Traceback: something broke"

    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FailedProcess())

    response = client.post(
        "/admin/api/db/ingest",
        json={"table": "equipment", "instruction": "", "provider": "anthropic"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == fake_added
    assert "rebuild_warning" in body


def test_db_ingest_rebuild_timeout_still_returns_added_records(client, monkeypatch):
    _login(client)
    keystore.set_db_connection(CONN_INFO)
    keystore.set_key("anthropic", "sk-ant-real")
    monkeypatch.setattr(db_ingestion, "fetch_table_text", lambda conn_info, table: "district | count\nPeshawar | 5")
    fake_added = [{"district": "Peshawar", "facility": "", "category": "equipment", "label": "X-ray",
                   "detail": "", "source_document": "db:equipment", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data, "add_from_document", lambda *args, **kwargs: fake_added)

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["python", "14_build_html_report.py"], timeout=300)

    monkeypatch.setattr(admin_route.subprocess, "run", raise_timeout)

    response = client.post(
        "/admin/api/db/ingest",
        json={"table": "equipment", "instruction": "", "provider": "anthropic"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == fake_added
    assert "rebuild_warning" in body
    assert "timed out" in body["rebuild_warning"].lower()
```

Note: `test_db_ingest_provider_failure_returns_502` reaches `ai_client.AIProviderError` via `supplemental_data.ai_client.AIProviderError` (the `ai_client` module as imported inside `supplemental_data.py`) rather than importing `ai_client` directly in the test file — this keeps the test file's imports minimal; either path resolves to the identical exception class since it's the same module object.

- [ ] **Step 7: Add a UI-presence assertion to `tests/server/test_routes.py`**

Find (the end of the file):

```python
def test_admin_panel_js_escapes_ai_derived_supplemental_content(client):
    # Regression guard: the "Add to Report" success handler renders
    # AI-extracted record fields (facility/category/label) and the
    # rebuild_warning subprocess output via innerHTML. Those values are
    # untrusted (only district is whitelist-validated), so they must be
    # run through escapeHtml() before interpolation - see
    # docs/superpowers/sdd/2026-08-15-supplemental-facility-data-phase4b
    # task 6 review finding.
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    assert "function escapeHtml(str)" in panel.text
    for hook in (
        "escapeHtml(r.district)", "escapeHtml(r.facility)",
        "escapeHtml(r.category)", "escapeHtml(r.label)",
        "escapeHtml(result.data.rebuild_warning)",
    ):
        assert hook in panel.text, f"missing escaping call: {hook}"
```

Replace with:

```python
def test_admin_panel_js_escapes_ai_derived_supplemental_content(client):
    # Regression guard: the "Add to Report" success handler renders
    # AI-extracted record fields (facility/category/label) and the
    # rebuild_warning subprocess output via innerHTML. Those values are
    # untrusted (only district is whitelist-validated), so they must be
    # run through escapeHtml() before interpolation - see
    # docs/superpowers/sdd/2026-08-15-supplemental-facility-data-phase4b
    # task 6 review finding.
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    assert "function escapeHtml(str)" in panel.text
    for hook in (
        "escapeHtml(r.district)", "escapeHtml(r.facility)",
        "escapeHtml(r.category)", "escapeHtml(r.label)",
        "escapeHtml(result.data.rebuild_warning)",
    ):
        assert hook in panel.text, f"missing escaping call: {hook}"


def test_admin_panel_includes_database_ingestion_section(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    for hook in (
        'id="db-host"', 'id="db-port"', 'id="db-database"', 'id="db-user"',
        'id="db-password"', 'id="db-sslmode"', 'id="db-connect-btn"',
        'id="db-table-select"', 'id="db-preview-btn"', 'id="db-instruction"',
        'id="db-provider"', 'id="db-ingest-btn"',
        "/admin/api/db/connection", "/admin/api/db/tables", "/admin/api/db/ingest",
    ):
        assert hook in panel.text, f"missing hook: {hook}"


def test_admin_panel_js_escapes_ai_derived_db_ingest_content(client):
    # Same regression class as test_admin_panel_js_escapes_ai_derived_supplemental_content,
    # for the database-ingestion "Add to Report" handler, which renders
    # the identical shape of AI-derived record fields via innerHTML.
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    db_ingest_js = panel.text.split('id="db-ingest-btn"')[1]
    for hook in (
        "escapeHtml(r.district)", "escapeHtml(r.facility)",
        "escapeHtml(r.category)", "escapeHtml(r.label)",
        "escapeHtml(result.data.rebuild_warning)",
    ):
        assert hook in db_ingest_js, f"missing escaping call in db-ingest handler: {hook}"
```

- [ ] **Step 8: Run the new and modified tests**

Run: `pytest tests/server/test_db_ingestion_route.py tests/server/test_routes.py -v`
Expected: 32 passed (15 in `test_db_ingestion_route.py`, 17 in `test_routes.py` — the 14 already there plus the 3 new ones)

- [ ] **Step 9: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass — the existing 176 plus this phase's 4 (`test_keystore.py`) + 7 (`test_db_ingestion.py`) + 15 (`test_db_ingestion_route.py`) + 3 (`test_routes.py`) = 205; exact count isn't load-bearing, "all pass" is.

- [ ] **Step 10: Manual browser verification**

Start the server: `python -m server`

**First, check whether a real PostgreSQL database is available to test against** (either a local instance, or one the human partner can provide connection details for). If none is available in this environment, note that explicitly in the report and still complete every check below that doesn't require a real database (auth-gating, the "no connection configured" 400 paths, the UI presence checks) — this is a real limitation to flag, not something to silently skip. If a real database is available, prepare a small test table first (e.g. `CREATE TABLE equipment_test (district text, facility text, item text, quantity int); INSERT INTO equipment_test VALUES ('Peshawar', 'DHQ Hospital', 'MRI Machine', 1);`).

In a browser at `http://127.0.0.1:8420/admin` (log in):
- The admin panel now shows a "Database Ingestion" section below "Extract Document", with host/port/database/username/password/sslmode fields, a "Save & Test Connection" button, a table dropdown, "Preview" and "Add to Report" buttons.
- Confirm `GET /admin/api/db/tables` before any connection is saved returns 400 (e.g. via `curl -H "Cookie: kp_admin_session=..." http://127.0.0.1:8420/admin/api/db/tables` using a real session cookie from the logged-in browser, or by clicking "Preview" with no connection saved and confirming a clear error, not a crash).
- If a real database is available: enter its connection details, click "Save & Test Connection", confirm it shows "Connected" and the table dropdown populates. Select the test table, click "Preview", confirm the rendered pipe-delimited text (including the "(showing first N rows)" line) looks correct. Pick a provider you have a real key configured for, click "Add to Report", confirm a summary of added record(s) appears, and confirm the report page and "Ask AI" chat reflect the new record(s) — same checks as phase 4b's manual verification, now via the database path instead of file upload.
- Confirm `POST /admin/api/db/connection`, `GET /admin/api/db/tables`, and `POST /admin/api/db/ingest` without a session all return 401 (e.g. via `curl` with no cookie).

If any of these fail, this is a real bug to fix before committing — do not report success without having actually driven the browser (and, if a database was available, the real ingestion flow) through each step. Clean up afterward: if a real database was used, drop the test table; delete any test records this created that shouldn't remain in the real data files (matching phase 4b's manual-verification cleanup discipline — remove them from `data/processed/supplemental_records.csv` and rebuild the report so the committed report file doesn't carry throwaway test content); delete the saved test database connection from the keystore (`keystore.delete_db_connection()`) so it doesn't linger as stale credentials.

- [ ] **Step 11: Final commit**

```bash
git add server/routes/admin.py server/admin_ui.py tests/server/test_db_ingestion_route.py tests/server/test_routes.py
git commit -m "feat: wire PostgreSQL database ingestion into the admin panel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
