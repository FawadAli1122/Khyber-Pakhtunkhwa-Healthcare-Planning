# Database Browser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** View and edit every table in the bundled local database (including internal registry tables), via both the admin panel and Telegram.

**Architecture:** Two new generic primitives in `scripts/lib/local_db.py`, a thin new `server/db_browser.py` wrapping them with table-name validation, three new admin routes, a new admin-panel section, and three new Telegram commands (`/localtables`, `/localview`, `/localedit`) added to the existing `server/telegram_admin_db.py`.

**Tech Stack:** Same as the rest of the project — FastAPI routes, plain JS in `admin_ui.py`, `python-telegram-bot` `ConversationHandler`.

**Spec:** `docs/superpowers/specs/2026-08-17-database-browser-design.md`

## Global Constraints

- Editing is allowed on every table with no exceptions, including `custom_tables`/`custom_table_columns` — this was the user's explicit choice, not a gap to fix.
- A table name from user input is always validated against a fresh `local_db.list_all_tables()` call before being used in any query — the one place raw table-name input enters the system.
- No schema changes (no adding/renaming/dropping tables or columns) through this feature — row values only.
- `id` and `added_at`/`created_at` (where present) are always read-only in every UI this plan adds.
- Every admin route uses the existing `_require_auth()` check; every Telegram command uses the existing `_authorized()` check.

---

### Task 1: `local_db.list_all_tables()` and `list_columns()`

**Files:**
- Modify: `scripts/lib/local_db.py`
- Test: `tests/lib/test_local_db.py`

**Interfaces:**
- Produces: `local_db.list_all_tables() -> [str, ...]`, `local_db.list_columns(table) -> [{"name": str, "type": str}, ...]`. Consumed by Task 2.

- [ ] **Step 1: Write the failing tests**

Add to `tests/lib/test_local_db.py`:

```python
def test_list_all_tables_returns_table_names(monkeypatch):
    cursor = FakeCursor(rows=[{"table_name": "bot_facilities"}, {"table_name": "custom_tables"}])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    result = local_db.list_all_tables()
    assert result == ["bot_facilities", "custom_tables"]
    assert conn.closed is True
    assert "information_schema.tables" in cursor.executed[0][0]


def test_list_columns_returns_name_and_type(monkeypatch):
    cursor = FakeCursor(rows=[
        {"column_name": "id", "data_type": "text"},
        {"column_name": "population_2023", "data_type": "numeric"},
    ])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    result = local_db.list_columns("kp_district_population")
    assert result == [
        {"name": "id", "type": "text"},
        {"name": "population_2023", "type": "numeric"},
    ]
    assert conn.closed is True
    query, params = cursor.executed[0]
    assert "information_schema.columns" in query
    assert params == ("kp_district_population",)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/lib/test_local_db.py -v -k "list_all_tables or list_columns"`
Expected: FAIL with `AttributeError: module 'scripts.lib.local_db' has no attribute 'list_all_tables'`

- [ ] **Step 3: Write `list_all_tables()`/`list_columns()` in `scripts/lib/local_db.py`**

Add after `update_by_id()` (the end of the file):

```python
def list_all_tables():
    """Every real table in the bundled database's public schema - both
    the app's own known overlay/registry tables and any dynamically-
    created custom_<slug> table. Queries information_schema directly
    rather than any app-level registry, so it can never be stale - the
    server/db_browser.py layer above uses this as the one place a
    user-supplied table name gets validated before it's ever used in a
    query built via f-string interpolation (fetch_all()/update_by_id()'s
    own established pattern)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [row["table_name"] for row in rows]


def list_columns(table):
    """[{"name": str, "type": str}, ...] for `table`, via
    information_schema.columns - `type` is the raw Postgres type name
    (e.g. "text", "numeric", "date"), used by server/db_browser.py for
    lightweight edit-value coercion. `table` is passed as a bound query
    parameter here, not interpolated - safe regardless of whether the
    caller has validated it against list_all_tables() yet."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"name": r["column_name"], "type": r["data_type"]} for r in rows]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/lib/test_local_db.py -v -k "list_all_tables or list_columns"`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: PASS, count increased by 2 from baseline

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/local_db.py tests/lib/test_local_db.py
git commit -m "feat: add local_db.list_all_tables()/list_columns() for the database browser"
```

---

### Task 2: `server/db_browser.py`

**Files:**
- Create: `server/db_browser.py`
- Test: `tests/server/test_db_browser.py`

**Interfaces:**
- Consumes: `local_db.list_all_tables()`, `local_db.list_columns()`, `local_db.fetch_all()`, `local_db.update_by_id()` (Task 1 + pre-existing).
- Produces: `list_tables()`, `get_table_columns(table)`, `get_table_rows(table)`, `update_row(table, record_id, fields)`, `_coerce_value(raw_value, pg_type)`. Consumed by Task 3 (admin routes) and Tasks 6-8 (Telegram).

- [ ] **Step 1: Write the failing tests**

```python
# tests/server/test_db_browser.py
import pytest

from scripts.lib import local_db
from server import db_browser


def test_list_tables_delegates_to_local_db(monkeypatch):
    monkeypatch.setattr(local_db, "list_all_tables", lambda: ["bot_facilities", "custom_tables"])
    assert db_browser.list_tables() == ["bot_facilities", "custom_tables"]


def test_get_table_columns_returns_none_for_unknown_table(monkeypatch):
    monkeypatch.setattr(local_db, "list_all_tables", lambda: ["bot_facilities"])
    assert db_browser.get_table_columns("nonexistent") is None


def test_get_table_columns_returns_columns_for_real_table(monkeypatch):
    monkeypatch.setattr(local_db, "list_all_tables", lambda: ["bot_facilities"])
    monkeypatch.setattr(local_db, "list_columns", lambda t: [{"name": "id", "type": "text"}])
    assert db_browser.get_table_columns("bot_facilities") == [{"name": "id", "type": "text"}]


def test_get_table_rows_returns_none_for_unknown_table(monkeypatch):
    monkeypatch.setattr(local_db, "list_all_tables", lambda: ["bot_facilities"])
    assert db_browser.get_table_rows("nonexistent") is None


def test_get_table_rows_orders_by_id(monkeypatch):
    calls = []
    monkeypatch.setattr(local_db, "list_all_tables", lambda: ["bot_facilities"])
    monkeypatch.setattr(local_db, "fetch_all", lambda table, order_by=None: calls.append((table, order_by)) or [])
    db_browser.get_table_rows("bot_facilities")
    assert calls == [("bot_facilities", "id")]


def test_coerce_value_integer_types():
    assert db_browser._coerce_value("42", "integer") == 42
    assert db_browser._coerce_value("42", "bigint") == 42
    assert db_browser._coerce_value("42", "smallint") == 42


def test_coerce_value_numeric_types():
    assert db_browser._coerce_value("4.5", "numeric") == 4.5
    assert db_browser._coerce_value("4.5", "real") == 4.5
    assert db_browser._coerce_value("4.5", "double precision") == 4.5


def test_coerce_value_text_and_date_pass_through():
    assert db_browser._coerce_value("Peshawar", "text") == "Peshawar"
    assert db_browser._coerce_value("2026-08-17", "date") == "2026-08-17"


def test_coerce_value_bad_integer_raises():
    with pytest.raises(ValueError):
        db_browser._coerce_value("not-a-number", "integer")


def test_update_row_unknown_table_returns_none(monkeypatch):
    monkeypatch.setattr(local_db, "list_all_tables", lambda: ["bot_facilities"])
    assert db_browser.update_row("nonexistent", "r1", {"name": "X"}) is None


def test_update_row_unknown_column_raises(monkeypatch):
    monkeypatch.setattr(local_db, "list_all_tables", lambda: ["bot_facilities"])
    monkeypatch.setattr(local_db, "list_columns", lambda t: [{"name": "name", "type": "text"}])
    with pytest.raises(ValueError, match="bogus"):
        db_browser.update_row("bot_facilities", "r1", {"bogus": "X"})


def test_update_row_empty_fields_raises(monkeypatch):
    monkeypatch.setattr(local_db, "list_all_tables", lambda: ["bot_facilities"])
    monkeypatch.setattr(local_db, "list_columns", lambda t: [{"name": "name", "type": "text"}])
    with pytest.raises(ValueError, match="No fields"):
        db_browser.update_row("bot_facilities", "r1", {})


def test_update_row_coerces_and_applies(monkeypatch):
    monkeypatch.setattr(local_db, "list_all_tables", lambda: ["bot_facilities"])
    monkeypatch.setattr(local_db, "list_columns", lambda t: [
        {"name": "name", "type": "text"}, {"name": "lat", "type": "double precision"},
    ])
    calls = []
    monkeypatch.setattr(local_db, "update_by_id", lambda table, rid, fields: calls.append((table, rid, fields)) or True)
    result = db_browser.update_row("bot_facilities", "r1", {"name": "New Name", "lat": "34.5"})
    assert result is True
    assert calls == [("bot_facilities", "r1", {"name": "New Name", "lat": 34.5})]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/server/test_db_browser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.db_browser'`

- [ ] **Step 3: Write `server/db_browser.py`**

```python
"""Generic read/edit access to every real table in the bundled local
database - both the app's own known overlay/registry tables and any
custom_<slug> table - for the "view the whole database" admin/Telegram
feature. Distinct from db_ingestion.py (read-only access to an
*external* database the admin configures) and from custom_data.py/
supplemental_data.py/etc. (which each know their own fixed table
schema) - this module only ever queries information_schema and calls
local_db's already-generic fetch_all()/update_by_id(). See
docs/superpowers/specs/2026-08-17-database-browser-design.md.
"""
from scripts.lib import local_db

_INT_TYPES = ("bigint", "smallint")
_FLOAT_TYPES = ("numeric", "real", "double precision")


def list_tables():
    return local_db.list_all_tables()


def get_table_columns(table):
    if table not in local_db.list_all_tables():
        return None
    return local_db.list_columns(table)


def get_table_rows(table):
    """Ordered by id - not every table in this schema has a timestamp
    column (custom_table_columns has neither added_at nor created_at),
    but every table has "id TEXT PRIMARY KEY", so ordering by it is
    fully deterministic (the same set of rows always sorts identically
    across separate calls) even though the resulting order isn't
    meaningful (ids are random uuid hex, not sequential) - this is what
    lets /localedit's row-number resolution trust that the Nth row
    /localview showed is still the Nth row on a later, separate fetch."""
    if table not in local_db.list_all_tables():
        return None
    return local_db.fetch_all(table, order_by="id")


def _coerce_value(raw_value, pg_type):
    if pg_type.startswith("int") or pg_type in _INT_TYPES:
        return int(raw_value)
    if pg_type in _FLOAT_TYPES:
        return float(raw_value)
    return raw_value


def update_row(table, record_id, fields):
    """fields: {column_name: raw_value}, raw_value always a string from
    the browser/bot. Returns True/False (row found/not found) or None
    (table doesn't exist). Raises ValueError for an unknown column name,
    empty `fields`, or a value that fails coercion for its column's real
    Postgres type - always caught at the call site (admin route: 400;
    Telegram: inline error reply), never silently dropped."""
    if table not in local_db.list_all_tables():
        return None
    if not fields:
        raise ValueError("No fields to update")
    columns = {c["name"]: c["type"] for c in local_db.list_columns(table)}
    coerced = {}
    for name, raw_value in fields.items():
        if name not in columns:
            raise ValueError(f"Unknown column: {name!r}")
        coerced[name] = _coerce_value(raw_value, columns[name])
    return local_db.update_by_id(table, record_id, coerced)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/server/test_db_browser.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: PASS, count increased by 15 from baseline (2 from Task 1 + 13 here)

- [ ] **Step 6: Commit**

```bash
git add server/db_browser.py tests/server/test_db_browser.py
git commit -m "feat: add server/db_browser.py for generic table read/edit access"
```

---

### Task 3: Admin routes

**Files:**
- Modify: `server/routes/admin.py`
- Test: `tests/server/test_db_browser_route.py`

**Interfaces:**
- Consumes: `db_browser.list_tables()`, `get_table_columns()`, `get_table_rows()`, `update_row()` (Task 2).
- Produces: `GET /admin/api/db-browser/tables`, `GET /admin/api/db-browser/tables/{table}/rows`, `PUT /admin/api/db-browser/tables/{table}/rows/{record_id}`. Consumed by Task 4 (admin UI).

- [ ] **Step 1: Write the failing tests**

```python
# tests/server/test_db_browser_route.py
"""End-to-end /admin/api/db-browser/* tests via FastAPI's TestClient.
The downstream-rebuild subprocess call and every db_browser call are
mocked - no real database touched in any test here. Same pattern as
tests/server/test_custom_data_route.py.
"""
import pytest
from fastapi.testclient import TestClient

from server import db_browser, keystore
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


def _login(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})


class FakeCompletedProcess:
    returncode = 0
    stderr = ""


def test_list_tables_requires_auth(client):
    response = client.get("/admin/api/db-browser/tables")
    assert response.status_code == 401


def test_list_tables_returns_table_names(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(db_browser, "list_tables", lambda: ["bot_facilities", "custom_tables"])
    response = client.get("/admin/api/db-browser/tables")
    assert response.status_code == 200
    assert response.json() == {"tables": ["bot_facilities", "custom_tables"]}


def test_get_rows_unknown_table_404s(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: None)
    response = client.get("/admin/api/db-browser/tables/nonexistent/rows")
    assert response.status_code == 404


def test_get_rows_returns_columns_and_rows(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: [{"name": "id", "type": "text"}])
    monkeypatch.setattr(db_browser, "get_table_rows", lambda t: [{"id": "r1"}])
    response = client.get("/admin/api/db-browser/tables/bot_facilities/rows")
    assert response.status_code == 200
    assert response.json() == {"columns": [{"name": "id", "type": "text"}], "rows": [{"id": "r1"}]}


def test_update_row_unknown_table_404s(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(db_browser, "update_row", lambda table, rid, fields: None)
    response = client.put("/admin/api/db-browser/tables/nonexistent/rows/r1", json={"name": "X"})
    assert response.status_code == 404


def test_update_row_missing_row_404s(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(db_browser, "update_row", lambda table, rid, fields: False)
    response = client.put("/admin/api/db-browser/tables/bot_facilities/rows/missing", json={"name": "X"})
    assert response.status_code == 404


def test_update_row_bad_value_returns_400(client, monkeypatch):
    _login(client)

    def failing_update(table, rid, fields):
        raise ValueError("Unknown column: 'bogus'")

    monkeypatch.setattr(db_browser, "update_row", failing_update)
    response = client.put("/admin/api/db-browser/tables/bot_facilities/rows/r1", json={"bogus": "X"})
    assert response.status_code == 400
    assert "bogus" in response.json()["detail"]


def test_update_row_success_rebuilds_and_returns_200(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(db_browser, "update_row", lambda table, rid, fields: True)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.put("/admin/api/db-browser/tables/bot_facilities/rows/r1", json={"name": "New Name"})
    assert response.status_code == 200
    assert response.json() == {"updated": True}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/server/test_db_browser_route.py -v`
Expected: FAIL with 404s (routes don't exist yet)

- [ ] **Step 3: Add `db_browser` to `server/routes/admin.py`'s imports**

```python
from server import (
    admin_ui,
    ai_client,
    auth,
    bot_facilities,
    custom_data,
    db_browser,
    db_ingestion,
    document_extraction,
    keystore,
    metric_overrides,
    providers,
    supplemental_data,
    telegram_bot,
)
```

- [ ] **Step 4: Add the three routes to `server/routes/admin.py`**

Add after `delete_custom_table` (the end of the custom-data routes):

```python
@router.get("/admin/api/db-browser/tables")
def list_db_browser_tables(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    return JSONResponse({"tables": db_browser.list_tables()})


@router.get("/admin/api/db-browser/tables/{table}/rows")
def get_db_browser_rows(table: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    columns = db_browser.get_table_columns(table)
    if columns is None:
        return JSONResponse({"detail": "No table with that name"}, status_code=404)
    return JSONResponse({"columns": columns, "rows": db_browser.get_table_rows(table)})


@router.put("/admin/api/db-browser/tables/{table}/rows/{record_id}")
def update_db_browser_row(
    table: str,
    record_id: str,
    kp_admin_session: str | None = Cookie(default=None),
    fields: dict = Body(...),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    try:
        updated = db_browser.update_row(table, record_id, fields)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    if updated is None:
        return JSONResponse({"detail": "No table with that name"}, status_code=404)
    if not updated:
        return JSONResponse({"detail": "No row with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"updated": True, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"updated": True, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"updated": True})
```

(This matches this file's own established inline-`subprocess.run` convention, already used identically by every other data-mutating route in this file - `telegram_rebuild.py`'s shared helper exists for the *new Telegram modules* specifically, per the Telegram Admin Parity spec's own scoping; redirecting `admin.py`'s existing 8 call sites plus this new 9th one to use it is a legitimate but separate follow-up, not part of this plan.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/server/test_db_browser_route.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Run the full test suite**

Run: `pytest -q`
Expected: PASS, count increased by 23 from baseline (2 + 13 + 8)

- [ ] **Step 7: Commit**

```bash
git add server/routes/admin.py tests/server/test_db_browser_route.py
git commit -m "feat: add /admin/api/db-browser/* routes"
```

---

### Task 4: Admin UI — "Database Browser" section

**Files:**
- Modify: `server/admin_ui.py`

**Interfaces:**
- Consumes: the three routes from Task 3.

- [ ] **Step 1: Add CSS**

Add near the existing `.records-table`/`.provider-status.ok`/`.bad` rules:

```css
.db-browser-input { width: 100%; min-width: 6rem; box-sizing: border-box; }
.db-browser-row-status { display: none; margin-left: 0.5rem; font-size: 0.8rem; }
.db-browser-row-status.ok { display: inline; color: var(--accent-2); }
.db-browser-row-status.error { display: inline; color: var(--danger); }
```

- [ ] **Step 2: Add the HTML section**

In `render_admin_panel()`, add right after Custom Data Tables' closing `</div>` and before the "Log Out" paragraph:

```html
<div class="upload-section">
  <h2>Database Browser</h2>
  <p class="hint">View and edit every table in the bundled database directly - including internal tables like custom_tables/custom_table_columns that track Custom Data Tables' own structure. Editing those directly can desync the registry from the real database and break that feature; edit them only if you know what you're doing.</p>
  <label for="db-browser-table-select">Table</label>
  <select id="db-browser-table-select">
    <option value="">Select a table...</option>
  </select>
  <div id="db-browser-content"></div>
</div>
```

- [ ] **Step 3: Add the JS**

Add near the other `refresh*`/`load*` functions:

```js
var DB_BROWSER_READONLY_COLUMNS = ["id", "added_at", "created_at"];

function refreshDbBrowserTables() {
  var select = byId("db-browser-table-select");
  if (!select) return;
  apiCall("GET", "/admin/api/db-browser/tables").then(function (result) {
    var tables = (result.data && result.data.tables) || [];
    select.innerHTML = '<option value="">Select a table...</option>';
    tables.forEach(function (t) {
      var opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      select.appendChild(opt);
    });
  });
}

function _wireDbBrowserSaveButton(saveBtn, statusEl, tr, row, table) {
  wireBusyButton(saveBtn, {
    statusEl: statusEl,
    busyText: "Saving...",
    beforeRequest: function () {
      var fields = {};
      tr.querySelectorAll(".db-browser-input").forEach(function (input) {
        var original = row[input.dataset.column] == null ? "" : String(row[input.dataset.column]);
        if (input.value !== original) fields[input.dataset.column] = input.value;
      });
      if (!Object.keys(fields).length) return false;
      tr._pendingFields = fields;
    },
    requestFn: function () {
      return apiCall(
        "PUT",
        "/admin/api/db-browser/tables/" + encodeURIComponent(table) + "/rows/" + encodeURIComponent(row.id),
        tr._pendingFields,
      );
    },
    onSuccess: function (result) {
      Object.keys(tr._pendingFields).forEach(function (col) { row[col] = tr._pendingFields[col]; });
      statusEl.textContent = (result.data && result.data.rebuild_warning) || "Saved.";
      statusEl.className = "db-browser-row-status " + (result.data && result.data.rebuild_warning ? "error" : "ok");
    },
  });
}

function loadDbBrowserTable(table) {
  var content = byId("db-browser-content");
  content.innerHTML = "";
  if (!table) return;
  content.textContent = "Loading...";
  apiCall("GET", "/admin/api/db-browser/tables/" + encodeURIComponent(table) + "/rows").then(function (result) {
    if (result.status !== 200) {
      content.textContent = (result.data && result.data.detail) || "Failed to load table";
      return;
    }
    var columns = result.data.columns;
    var rows = result.data.rows;
    content.innerHTML = "";
    if (!rows.length) {
      content.textContent = "No rows yet.";
      return;
    }
    var wrap = document.createElement("div");
    wrap.className = "records-table-wrap";
    var tableEl = document.createElement("table");
    tableEl.className = "records-table";
    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    columns.forEach(function (col) {
      var th = document.createElement("th");
      th.textContent = col.name;
      headRow.appendChild(th);
    });
    headRow.appendChild(document.createElement("th"));
    thead.appendChild(headRow);
    tableEl.appendChild(thead);
    var tbody = document.createElement("tbody");
    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      columns.forEach(function (col) {
        var td = document.createElement("td");
        if (DB_BROWSER_READONLY_COLUMNS.indexOf(col.name) !== -1) {
          td.textContent = row[col.name] == null ? "" : String(row[col.name]);
        } else {
          var input = document.createElement("input");
          input.type = "text";
          input.className = "db-browser-input";
          input.dataset.column = col.name;
          input.value = row[col.name] == null ? "" : String(row[col.name]);
          td.appendChild(input);
        }
        tr.appendChild(td);
      });
      var actionTd = document.createElement("td");
      var saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.className = "secondary";
      saveBtn.textContent = "Save";
      var statusSpan = document.createElement("span");
      statusSpan.className = "db-browser-row-status";
      _wireDbBrowserSaveButton(saveBtn, statusSpan, tr, row, table);
      actionTd.appendChild(saveBtn);
      actionTd.appendChild(statusSpan);
      tr.appendChild(actionTd);
      tbody.appendChild(tr);
    });
    tableEl.appendChild(tbody);
    wrap.appendChild(tableEl);
    content.appendChild(wrap);
  });
}
```

- [ ] **Step 4: Wire the table `<select>`'s change event and the initial table-list load**

In the `DOMContentLoaded` handler, add:

```js
    var dbBrowserSelect = byId("db-browser-table-select");
    if (dbBrowserSelect) {
      dbBrowserSelect.addEventListener("change", function () {
        loadDbBrowserTable(dbBrowserSelect.value);
      });
    }
```

And add `refreshDbBrowserTables();` alongside the existing `refreshSupplementalRecords(); refreshOverrideRecords(); refreshBotFacilities(); refreshCustomTables();` calls at the end of `DOMContentLoaded`.

- [ ] **Step 5: Sanity-check the module renders without error**

Run: `python -c "import server.admin_ui as au; html = au.render_admin_panel([]); print('OK', 'Database Browser' in html)"`
Expected: `OK True`

- [ ] **Step 6: Run the full test suite**

Run: `pytest -q`
Expected: PASS, same count as after Task 3 (this task adds no new automated tests - pure front-end JS, verified live in Task 9, matching this file's own established precedent)

- [ ] **Step 7: Commit**

```bash
git add server/admin_ui.py
git commit -m "feat: add Database Browser section to the admin panel"
```

---

### Task 5: `parse_field_updates()` pure function

**Files:**
- Modify: `server/telegram_admin_db.py`
- Test: `tests/test_telegram_admin_db_parser.py`

**Interfaces:**
- Produces: `parse_field_updates(text) -> {column: str, ...}` (raises `ValueError`). Consumed by Task 7.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_telegram_admin_db_parser.py
import pytest

from server.telegram_admin_db import parse_field_updates


def test_parse_field_updates_parses_multiple_lines():
    result = parse_field_updates("name=Fridge A\ncapacity=50")
    assert result == {"name": "Fridge A", "capacity": "50"}


def test_parse_field_updates_value_may_contain_equals_sign():
    result = parse_field_updates("formula=a=b+c")
    assert result == {"formula": "a=b+c"}


def test_parse_field_updates_value_may_contain_comma():
    result = parse_field_updates("narrative=Peshawar, Mardan, and Charsadda")
    assert result == {"narrative": "Peshawar, Mardan, and Charsadda"}


def test_parse_field_updates_ignores_blank_lines():
    result = parse_field_updates("name=Fridge A\n\n\ncapacity=50\n")
    assert result == {"name": "Fridge A", "capacity": "50"}


def test_parse_field_updates_rejects_line_without_equals():
    with pytest.raises(ValueError, match="="):
        parse_field_updates("name=Fridge A\njust some text")


def test_parse_field_updates_rejects_empty_column_name():
    with pytest.raises(ValueError):
        parse_field_updates("=some value")


def test_parse_field_updates_rejects_empty_input():
    with pytest.raises(ValueError):
        parse_field_updates("")


def test_parse_field_updates_rejects_only_blank_lines():
    with pytest.raises(ValueError):
        parse_field_updates("\n\n  \n")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_telegram_admin_db_parser.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_field_updates'`

- [ ] **Step 3: Add `parse_field_updates()` to `server/telegram_admin_db.py`**

Add near the top, after the module docstring/imports:

```python
def parse_field_updates(text):
    """text: one "column=value" pair per line (not comma-separated - a
    value may itself contain a comma, e.g. editing a narrative field;
    each line splits on only the *first* "=", so a value may safely
    contain "=" too). Returns {column: value}. Raises ValueError on a
    line with no "=", an empty column name, or no usable lines at all."""
    fields = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"{line!r} is missing '=' - use column=value")
        column, _, value = line.partition("=")
        column = column.strip()
        if not column:
            raise ValueError(f"{line!r} has no column name")
        fields[column] = value.strip()
    if not fields:
        raise ValueError("Send at least one column=value line")
    return fields
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_telegram_admin_db_parser.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: PASS, count increased by 31 from baseline (2 + 13 + 8 + 8)

- [ ] **Step 6: Commit**

```bash
git add server/telegram_admin_db.py tests/test_telegram_admin_db_parser.py
git commit -m "feat: add parse_field_updates() pure parser for /localedit"
```

---

### Task 6: `/localtables` and `/localview`

**Files:**
- Modify: `server/telegram_admin_db.py`
- Test: `tests/server/test_telegram_admin_db.py`

**Interfaces:**
- Consumes: `db_browser.list_tables()`, `db_browser.get_table_columns()`, `db_browser.get_table_rows()` (Task 2).
- Produces: `localtables_command`, `localview_command` (module-level), added to `register()` in Task 8.

- [ ] **Step 1: Add `/localtables` and `/localview` to `server/telegram_admin_db.py`**

Add `from server import db_browser` to the imports, then:

```python
MAX_LISTED_LOCAL_TABLES = 20
MAX_LISTED_LOCAL_ROWS = 20


async def localtables_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    tables = db_browser.list_tables()
    if not tables:
        await update.message.reply_text("No tables found.")
        return
    shown = tables[:MAX_LISTED_LOCAL_TABLES]
    lines = ["Database tables:"] + [f"{i}. {t}" for i, t in enumerate(shown, start=1)]
    if len(tables) > MAX_LISTED_LOCAL_TABLES:
        lines.append(f"+{len(tables) - MAX_LISTED_LOCAL_TABLES} more - use the admin panel to see the rest.")
    await update.message.reply_text("\n".join(lines))


async def localview_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /localview <table>")
        return
    table = context.args[0]
    columns = db_browser.get_table_columns(table)
    if columns is None:
        names = ", ".join(db_browser.list_tables())
        await update.message.reply_text(f"No table named {table!r}. Existing tables: {names}")
        return
    rows = db_browser.get_table_rows(table)
    if not rows:
        await update.message.reply_text(f"{table}: no rows yet.")
        return
    shown = rows[:MAX_LISTED_LOCAL_ROWS]
    lines = [f"{table}:"]
    for i, r in enumerate(shown, start=1):
        cells = ", ".join(f"{c['name']}={r.get(c['name'])}" for c in columns)
        lines.append(f"{i}. {cells}")
    if len(rows) > MAX_LISTED_LOCAL_ROWS:
        lines.append(f"+{len(rows) - MAX_LISTED_LOCAL_ROWS} more - use the admin panel to see the rest.")
    lines.append("Use /localedit <table> <row#> to change a value.")
    await update.message.reply_text("\n".join(lines))
```

- [ ] **Step 2: Write the tests**

```python
# tests/server/test_telegram_admin_db.py (new file)
"""Unit tests for server/telegram_admin_db.py's /localtables, /localview,
/localedit commands. Every Telegram API call is mocked, matching
tests/server/test_telegram_admin_records.py's established pattern.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from server import db_browser, keystore, telegram_admin_db, telegram_rebuild

TELEGRAM_CONFIG = {"token": "123456:ABC-DEF", "allowed_user_id": "987654321"}


def _make_update(user_id=987654321):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message = AsyncMock()
    return update


def _make_context(args=None):
    context = MagicMock()
    context.args = args or []
    context.user_data = {}
    return context


def _make_callback_update(data, user_id=987654321):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query = AsyncMock()
    update.callback_query.data = data
    return update


@pytest.mark.asyncio
async def test_localtables_command_lists_tables(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "list_tables", lambda: ["bot_facilities", "custom_tables"])
    update = _make_update()
    await telegram_admin_db.localtables_command(update, _make_context())
    text = update.message.reply_text.call_args.args[0]
    assert "bot_facilities" in text and "custom_tables" in text


@pytest.mark.asyncio
async def test_localtables_command_unauthorized(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111)
    await telegram_admin_db.localtables_command(update, _make_context())
    update.message.reply_text.assert_awaited_once_with("Not authorized.")


@pytest.mark.asyncio
async def test_localview_command_shows_rows(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: [{"name": "id", "type": "text"}, {"name": "name", "type": "text"}])
    monkeypatch.setattr(db_browser, "get_table_rows", lambda t: [{"id": "r1", "name": "Fridge A"}])
    update = _make_update()
    await telegram_admin_db.localview_command(update, _make_context(args=["bot_facilities"]))
    text = update.message.reply_text.call_args.args[0]
    assert "Fridge A" in text


@pytest.mark.asyncio
async def test_localview_command_unknown_table(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: None)
    monkeypatch.setattr(db_browser, "list_tables", lambda: ["bot_facilities"])
    update = _make_update()
    await telegram_admin_db.localview_command(update, _make_context(args=["nonexistent"]))
    text = update.message.reply_text.call_args.args[0]
    assert "No table named" in text


@pytest.mark.asyncio
async def test_localview_command_no_args_shows_usage(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    await telegram_admin_db.localview_command(update, _make_context(args=[]))
    update.message.reply_text.assert_awaited_once_with("Usage: /localview <table>")
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `pytest tests/server/test_telegram_admin_db.py -v`
Expected: PASS (5 tests)

- [ ] **Step 4: Run the full test suite**

Run: `pytest -q`
Expected: PASS, count increased by 36 from baseline (2 + 13 + 8 + 8 + 5)

- [ ] **Step 5: Commit**

```bash
git add server/telegram_admin_db.py tests/server/test_telegram_admin_db.py
git commit -m "feat: add /localtables and /localview Telegram commands"
```

---

### Task 7: `/localedit`

**Files:**
- Modify: `server/telegram_admin_db.py`
- Modify: `tests/server/test_telegram_admin_db.py`

**Interfaces:**
- Consumes: `parse_field_updates` (Task 5), `db_browser.get_table_rows/get_table_columns/update_row` (Task 2), `telegram_rebuild.rebuild_report()` (pre-existing).
- Produces: `localedit_conversation` (module-level `ConversationHandler`), added to `register()` in Task 8.

- [ ] **Step 1: Add the `/localedit` conversation to `server/telegram_admin_db.py`**

```python
LOCALEDIT_FIELDS, LOCALEDIT_CONFIRM = range(2)


async def localedit_start(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return ConversationHandler.END
    context.user_data.clear()
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /localedit <table> <row#>")
        return ConversationHandler.END
    table = context.args[0]
    columns = db_browser.get_table_columns(table)
    if columns is None:
        names = ", ".join(db_browser.list_tables())
        await update.message.reply_text(f"No table named {table!r}. Existing tables: {names}")
        return ConversationHandler.END
    try:
        row_number = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Row number must be a number - use /localview <table> to see them.")
        return ConversationHandler.END
    rows = db_browser.get_table_rows(table)
    if row_number < 1 or row_number > len(rows):
        await update.message.reply_text(f"No row #{row_number} - {table} has {len(rows)} row(s). Use /localview {table} to see them.")
        return ConversationHandler.END
    row = rows[row_number - 1]
    context.user_data["table"] = table
    context.user_data["columns"] = columns
    context.user_data["row"] = row
    current = "\n".join(f"{c['name']}={row.get(c['name'])}" for c in columns)
    await update.message.reply_text(
        f"Current values:\n{current}\n\nSend the columns to change, one per line as column=value "
        "(only include what's different)."
    )
    return LOCALEDIT_FIELDS


async def localedit_receive_fields(update, context):
    try:
        fields = parse_field_updates(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(f"{exc}\n\nTry again, or /cancel.")
        return LOCALEDIT_FIELDS
    row = context.user_data["row"]
    known_columns = {c["name"] for c in context.user_data["columns"]}
    unknown = [name for name in fields if name not in known_columns]
    if unknown:
        await update.message.reply_text(f"Unknown column(s): {', '.join(unknown)}\n\nTry again, or /cancel.")
        return LOCALEDIT_FIELDS
    context.user_data["fields"] = fields
    diff = "\n".join(f"{name}: {row.get(name)} -> {value}" for name, value in fields.items())
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, update", callback_data="confirm:yes"),
        InlineKeyboardButton("No, cancel", callback_data="confirm:no"),
    ]])
    await update.message.reply_text(f"Update these fields?\n{diff}", reply_markup=keyboard)
    return LOCALEDIT_CONFIRM


async def localedit_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm:no":
        await query.edit_message_text("Cancelled.")
        context.user_data.clear()
        return ConversationHandler.END
    table = context.user_data["table"]
    row = context.user_data["row"]
    try:
        updated = await asyncio.to_thread(db_browser.update_row, table, row["id"], context.user_data["fields"])
    except ValueError as exc:
        await query.edit_message_text(f"Failed: {exc}")
        context.user_data.clear()
        return ConversationHandler.END
    if not updated:
        await query.edit_message_text("That row no longer exists.")
        context.user_data.clear()
        return ConversationHandler.END
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_report)
    text = "Updated."
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await query.edit_message_text(text)
    context.user_data.clear()
    return ConversationHandler.END


async def localedit_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


localedit_conversation = ConversationHandler(
    entry_points=[CommandHandler("localedit", localedit_start)],
    states={
        LOCALEDIT_FIELDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, localedit_receive_fields)],
        LOCALEDIT_CONFIRM: [CallbackQueryHandler(localedit_confirm, pattern=r"^confirm:")],
    },
    fallbacks=[CommandHandler("cancel", localedit_cancel)],
)
```

**Note:** this file's callback-data namespace already uses `confirm:yes`/`confirm:no` for `/dbingest`... check - `/dbingest`'s own conversation (`dbingest_conversation`) only has `provider:` callbacks, not `confirm:`, so there's no collision within this file. Verify this against the actual current file content before writing this step (the file has grown across three prior tasks in the earlier Telegram Admin Parity plan) - if `dbconnect_conversation`/`dbingest_conversation` register any `CallbackQueryHandler(..., pattern=r"^confirm:")` of their own, this new one needs to stay scoped correctly (each `ConversationHandler`'s own states only match while *that* conversation is active for the chat, so identical patterns across different conversations don't collide - matches the same reasoning already established for state-integer reuse in the original Telegram Admin Parity plan).

- [ ] **Step 2: Add the tests**

Append to `tests/server/test_telegram_admin_db.py`:

```python
FAKE_TABLE_COLUMNS = [{"name": "id", "type": "text"}, {"name": "name", "type": "text"}]
FAKE_ROWS = [{"id": "r1", "name": "Fridge A"}, {"id": "r2", "name": "Fridge B"}]


@pytest.mark.asyncio
async def test_localedit_start_missing_args_shows_usage(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    state = await telegram_admin_db.localedit_start(update, _make_context(args=["bot_facilities"]))
    assert state == ConversationHandler.END
    update.message.reply_text.assert_awaited_once_with("Usage: /localedit <table> <row#>")


@pytest.mark.asyncio
async def test_localedit_start_row_out_of_range(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: FAKE_TABLE_COLUMNS)
    monkeypatch.setattr(db_browser, "get_table_rows", lambda t: FAKE_ROWS)
    update = _make_update()
    state = await telegram_admin_db.localedit_start(update, _make_context(args=["bot_facilities", "5"]))
    assert state == ConversationHandler.END
    text = update.message.reply_text.call_args.args[0]
    assert "No row #5" in text


@pytest.mark.asyncio
async def test_localedit_start_valid_row_shows_current_values(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: FAKE_TABLE_COLUMNS)
    monkeypatch.setattr(db_browser, "get_table_rows", lambda t: FAKE_ROWS)
    update = _make_update()
    context = _make_context(args=["bot_facilities", "2"])
    state = await telegram_admin_db.localedit_start(update, context)
    assert state == telegram_admin_db.LOCALEDIT_FIELDS
    assert context.user_data["row"] == {"id": "r2", "name": "Fridge B"}
    text = update.message.reply_text.call_args.args[0]
    assert "Fridge B" in text


@pytest.mark.asyncio
async def test_localedit_receive_fields_unknown_column_reprompts():
    update = _make_update()
    update.message.text = "bogus=X"
    context = _make_context()
    context.user_data = {"row": FAKE_ROWS[0], "columns": FAKE_TABLE_COLUMNS}
    state = await telegram_admin_db.localedit_receive_fields(update, context)
    assert state == telegram_admin_db.LOCALEDIT_FIELDS
    text = update.message.reply_text.call_args.args[0]
    assert "Unknown column" in text


@pytest.mark.asyncio
async def test_localedit_receive_fields_valid_shows_diff():
    update = _make_update()
    update.message.text = "name=Fridge C"
    context = _make_context()
    context.user_data = {"row": FAKE_ROWS[0], "columns": FAKE_TABLE_COLUMNS}
    state = await telegram_admin_db.localedit_receive_fields(update, context)
    assert state == telegram_admin_db.LOCALEDIT_CONFIRM
    text = update.message.reply_text.call_args.args[0]
    assert "Fridge A -> Fridge C" in text


@pytest.mark.asyncio
async def test_localedit_confirm_no_cancels_without_updating(monkeypatch):
    called = []
    monkeypatch.setattr(db_browser, "update_row", lambda *a, **k: called.append(1))
    update = _make_callback_update("confirm:no")
    context = _make_context()
    context.user_data = {"table": "bot_facilities", "row": FAKE_ROWS[0], "fields": {"name": "X"}}
    state = await telegram_admin_db.localedit_confirm(update, context)
    assert state == ConversationHandler.END
    assert called == []
    update.callback_query.edit_message_text.assert_awaited_once_with("Cancelled.")


@pytest.mark.asyncio
async def test_localedit_confirm_yes_updates_and_rebuilds(monkeypatch):
    monkeypatch.setattr(db_browser, "update_row", lambda table, rid, fields: True)
    called = []
    monkeypatch.setattr(telegram_rebuild, "rebuild_report", lambda: called.append(1) or (True, None))
    update = _make_callback_update("confirm:yes")
    context = _make_context()
    context.user_data = {"table": "bot_facilities", "row": FAKE_ROWS[0], "fields": {"name": "Fridge C"}}
    state = await telegram_admin_db.localedit_confirm(update, context)
    assert state == ConversationHandler.END
    assert called == [1]
    update.callback_query.edit_message_text.assert_awaited_once_with("Updated.")


@pytest.mark.asyncio
async def test_localedit_confirm_row_gone(monkeypatch):
    monkeypatch.setattr(db_browser, "update_row", lambda table, rid, fields: False)
    update = _make_callback_update("confirm:yes")
    context = _make_context()
    context.user_data = {"table": "bot_facilities", "row": FAKE_ROWS[0], "fields": {"name": "X"}}
    state = await telegram_admin_db.localedit_confirm(update, context)
    assert state == ConversationHandler.END
    update.callback_query.edit_message_text.assert_awaited_once_with("That row no longer exists.")
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `pytest tests/server/test_telegram_admin_db.py -v`
Expected: PASS (12 tests total in this file)

- [ ] **Step 4: Run the full test suite**

Run: `pytest -q`
Expected: PASS, count increased by 43 from baseline (2 + 13 + 8 + 8 + 5 + 7)

- [ ] **Step 5: Commit**

```bash
git add server/telegram_admin_db.py tests/server/test_telegram_admin_db.py
git commit -m "feat: add /localedit Telegram command"
```

---

### Task 8: `register()` wiring and `HELP_TEXT`

**Files:**
- Modify: `server/telegram_admin_db.py`
- Modify: `server/telegram_bot.py`

**Interfaces:**
- Consumes: `localtables_command`, `localview_command` (Task 6), `localedit_conversation` (Task 7).

- [ ] **Step 1: Add the three new handlers to `telegram_admin_db.py`'s existing `register()`**

```python
def register(application):
    application.add_handler(dbconnect_conversation)
    application.add_handler(CommandHandler("dbtables", dbtables_command))
    application.add_handler(CommandHandler("dbpreview", dbpreview_command))
    application.add_handler(dbingest_conversation)
    application.add_handler(CommandHandler("localtables", localtables_command))
    application.add_handler(CommandHandler("localview", localview_command))
    application.add_handler(localedit_conversation)
```

- [ ] **Step 2: Extend `HELP_TEXT` in `server/telegram_bot.py`**

```python
    "/dbconnect, /dbtables, /dbpreview <table>, /dbingest <table> - database ingestion\n"
    "/localtables, /localview <table>, /localedit <table> <row#> - browse/edit the bundled database directly"
```

(Replaces the existing single `/dbconnect...` line, which currently has no trailing `\n` since it's the last line of `HELP_TEXT` - add the `\n` and the new line after it.)

- [ ] **Step 3: Sanity-check the whole bot builds correctly**

Run: `python -c "
import server.telegram_bot as tb
app = tb.build_application('123:fake-token-for-handler-registration-check')
print('handlers registered:', sum(len(v) for v in app.handlers.values()))
"`
Expected: prints a handler count with no exception - should be 24 (the 21 from the original Telegram Admin Parity plan + 3 new: `/localtables`, `/localview`, `localedit_conversation`).

- [ ] **Step 4: Run the full test suite**

Run: `pytest -q`
Expected: PASS, same count as after Task 7 (this task adds no new tests, just wiring)

- [ ] **Step 5: Commit**

```bash
git add server/telegram_admin_db.py server/telegram_bot.py
git commit -m "feat: wire /localtables, /localview, /localedit into the bot"
```

---

### Task 9: Full verification and close-out

**Files:** none (verification only)

- [x] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: PASS, count increased by 43 from this plan's start
**Result:** 639/639 passed. (One real mid-plan regression was caught and fixed as its own commit during Task 6 - see below - the final count reflects the corrected state.)

- [x] **Step 2: Live-verify against the real admin panel and real bot**

**Done (2026-08-17).** Both surfaces exercised live with explicit permission (admin password temporarily reset, restored byte-for-byte afterward - same established pattern):
- Admin panel: viewed and edited a row in all three table kinds - `bot_facilities` (fixed overlay store), a real Custom Data Table's own dynamic table, and `custom_tables` (a registry table, editing only the safe `label` field, not `table_name`). All three confirmed real and correct via direct `local_db.fetch_all()` checks, not just UI appearance.
- Telegram: `/localtables` correctly listed all 6 real tables including both registry tables; `/localview` correctly showed real row data matching the admin-panel edits (proving both surfaces read the same live data); `/localedit` verified end-to-end including the unknown-column rejection path ("Unknown column(s): bogus... Try again, or /cancel.") and a deliberate value containing a comma (`item=Gauze, Bandages, and Tape`) - confirmed the newline-delimited parser correctly kept the whole comma-containing string as one value (not split at the comma), then confirmed the update landed for real via direct DB check.

**Zero code bugs found during live verification itself.** All test data cleaned up (test custom table + registry entry, test `bot_facilities` row), report rebuilt back to byte-identical match with the committed baseline (only the report's own "generated <date>" stamp differed, due to real wall-clock time passing past midnight - reverted, not committed), `git status` confirmed clean.

**Real process mistake found and fixed during Task 6 (not Task 9, but worth recording here since it's part of this plan's overall verification story):** `tests/server/test_telegram_admin_db.py` already existed with 13 tests from the earlier Telegram Admin Parity plan - this plan's Task 6 wrongly assumed it was a new file (both the plan text and my own first execution attempt made the same wrong assumption) and a `Write` call overwrote it, losing the original `/dbconnect`/`/dbtables`/`/dbpreview`/`/dbingest` coverage. Caught immediately via a suspicious `129 deletions` in what should have been a pure-addition commit diff, confirmed via an unexpectedly low full-suite count, fixed by restoring the original 13 tests from git history and merging in the 5 new ones - committed as its own explicit fix commit (`ad38362`) rather than silently amended away. **Lesson for any future task that names a test file matching an existing feature's naming convention: check whether the file already exists before assuming "new file" - a plan can get this wrong even after real research, since a later, unrelated feature can create a same-named file after the plan's own spec/research phase.**

- [x] **Step 3: `finishing-a-development-branch`**

Confirmed: normal repo (`GIT_DIR == GIT_COMMON`), `master` branch directly, no remote - matches every prior phase this session. Nothing to merge or push; work already lands as direct commits.

- [x] **Step 4: Report findings**

See the session summary delivered to the user after this plan's completion.
