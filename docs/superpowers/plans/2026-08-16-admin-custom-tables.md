# Admin-Defined Custom Data Tables Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the admin create real Postgres tables/columns from the admin panel (explicit form or AI-proposed-then-edited), populate them via document upload + AI extraction, and have that data appear in the generated HTML report - with the AI choosing each table's report title/narrative/placement once, at data-add time, deterministically rendered by Python thereafter.

**Architecture:** `scripts/lib/local_db.py` gains a Part C (safe dynamic DDL: `create_table`/`add_column`/`drop_column`/`drop_table`, identifier validation) and an `update_by_id` addition to Part A. `server/custom_data.py` (new) owns all AI orchestration (schema inference, row extraction, report-placement) and non-AI CRUD, mirroring `server/supplemental_data.py`'s established shape. `scripts/lib/custom_tables.py` (new) is the report-build-side read/render counterpart (mirrors `scripts/lib/supplemental_records.py`), since `scripts/14_build_html_report.py` can never import from `server/`. New admin routes + UI reuse every existing pattern (two-step-confirm delete, `initRecordsTable`, the shared `/admin/api/extract` preview, the lightweight report-only rebuild).

**Tech Stack:** Python 3.12, `psycopg2` (incl. `psycopg2.sql` for DDL identifier safety), FastAPI, vanilla JS (no framework, matching `server/admin_ui.py`), pytest + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-08-16-admin-custom-tables-design.md`

## Global Constraints

- Every dynamically-created table/column name is derived via `local_db.slugify()`, validated via `local_db.validate_identifier()`, and (for tables) prefixed `custom_` - never taken from raw admin/AI text unvalidated.
- Column types are always exactly one of `"text"` / `"number"` / `"date"` at the app level, mapped to `TEXT`/`NUMERIC`/`DATE` only inside `local_db.py`'s DDL functions - never arbitrary SQL.
- The new DDL functions (`create_table`/`add_column`/`drop_column`/`drop_table`) build every statement with `psycopg2.sql.Identifier`/`sql.SQL`, never raw string interpolation. The *reused* `fetch_all`/`insert_many`/`delete_by_id`/`update_by_id` keep their existing plain-string-interpolation style (safe here because every `table_name`/`column_name` reaching them was already validated once, at creation time, and is always sourced from the `custom_tables`/`custom_table_columns` registry thereafter - never freshly re-derived from raw input).
- An empty custom table (zero rows) gets no report section and no AI placement call at all - matches the Facility Readiness section's "no data yet" precedent.
- The AI never authors report HTML directly - it only ever returns bounded structured JSON (`{title, narrative, placement}` / `{label, columns}` / row arrays); Python renders all markup, matching every other AI usage in this app.
- Every `psycopg2` call and every AI provider call in every automated test is mocked - no test touches a real database or a real AI provider. Real DDL execution and real AI calls are verified manually against the real running server in the final task.
- Deleting a row/column/table triggers the same lightweight rebuild every other admin-panel delete already uses (`REPORT_BUILD_SCRIPT` subprocess - `14_build_html_report.py` only, not the full pipeline).

---

### Task 1: `scripts/lib/local_db.py` - registry schema, DDL safety, `update_by_id`

**Files:**
- Modify: `scripts/lib/local_db.py`
- Modify: `tests/lib/test_local_db.py`

**Interfaces:**
- Consumes: existing `get_connection()`, `LocalDbError` (already in the module).
- Produces: `local_db.slugify(label) -> str`, `local_db.validate_identifier(name)` (raises `LocalDbError`), `local_db.create_table(table_name, columns)`, `local_db.add_column(table_name, column_name, column_type)`, `local_db.drop_column(table_name, column_name)`, `local_db.drop_table(table_name)`, `local_db.update_by_id(table, record_id, fields, column_map=None) -> bool` - all consumed by Task 2.
- `SCHEMA_SQL` gains `custom_tables`/`custom_table_columns` - consumed by Task 2 via the *existing* `fetch_all`/`insert_many`/`delete_by_id`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/lib/test_local_db.py` (after the existing tests, same file - `FakeCursor`/`FakeConnection`/`fake_password` already defined there):

```python
def test_slugify_lowercases_and_replaces_punctuation():
    assert local_db.slugify("Cold Chain Equipment!") == "cold_chain_equipment"


def test_slugify_collapses_repeated_separators():
    assert local_db.slugify("  Last   Checked -- Date  ") == "last_checked_date"


def test_slugify_raises_on_no_usable_characters():
    with pytest.raises(local_db.LocalDbError):
        local_db.slugify("!!!")


def test_slugify_truncates_long_labels():
    assert len(local_db.slugify("x" * 100)) == 40


def test_validate_identifier_accepts_valid_name():
    local_db.validate_identifier("custom_cold_chain_equipment")  # does not raise


def test_validate_identifier_rejects_leading_digit():
    with pytest.raises(local_db.LocalDbError):
        local_db.validate_identifier("1bad")


def test_validate_identifier_rejects_uppercase():
    with pytest.raises(local_db.LocalDbError):
        local_db.validate_identifier("Bad")


def test_validate_identifier_rejects_special_characters():
    with pytest.raises(local_db.LocalDbError):
        local_db.validate_identifier("bad; DROP TABLE x; --")


def test_create_table_success(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.create_table("custom_cold_chain", [("status", "text"), ("checked_on", "date")])
    assert conn.committed is True
    assert conn.closed is True


def test_create_table_rejects_invalid_name_without_connecting(monkeypatch):
    def fail_connect(**kwargs):
        raise AssertionError("should not attempt to connect with an invalid table name")
    monkeypatch.setattr(local_db.psycopg2, "connect", fail_connect)
    with pytest.raises(local_db.LocalDbError):
        local_db.create_table("Bad Name", [("status", "text")])


def test_create_table_rejects_unknown_column_type(monkeypatch):
    def fail_connect(**kwargs):
        raise AssertionError("should not attempt to connect with an unknown column type")
    monkeypatch.setattr(local_db.psycopg2, "connect", fail_connect)
    with pytest.raises(local_db.LocalDbError):
        local_db.create_table("custom_cold_chain", [("status", "not_a_real_type")])


def test_add_column_success(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.add_column("custom_cold_chain", "notes", "text")
    assert conn.committed is True
    assert conn.closed is True


def test_drop_column_success(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.drop_column("custom_cold_chain", "notes")
    assert conn.committed is True
    assert conn.closed is True


def test_drop_table_success(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.drop_table("custom_cold_chain")
    assert conn.committed is True
    assert conn.closed is True


def test_update_by_id_returns_true_when_updated(monkeypatch):
    cursor = FakeCursor(rowcount=1)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    assert local_db.update_by_id("custom_tables", "t1", {"report_title": "X"}) is True
    assert conn.committed is True
    assert conn.closed is True


def test_update_by_id_returns_false_when_not_found(monkeypatch):
    cursor = FakeCursor(rowcount=0)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    assert local_db.update_by_id("custom_tables", "does-not-exist", {"report_title": "X"}) is False


def test_update_by_id_builds_correct_update(monkeypatch):
    cursor = FakeCursor(rowcount=1)
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.update_by_id("custom_tables", "t1", {"report_title": "X", "report_narrative": "Y"})
    query, values = cursor.executed[0]
    assert "UPDATE custom_tables SET" in query
    assert "report_title = %s" in query
    assert "report_narrative = %s" in query
    assert values == ["X", "Y", "t1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_local_db.py -v`
Expected: FAIL (`AttributeError: module 'scripts.lib.local_db' has no attribute 'slugify'`, etc.)

- [ ] **Step 3: Implement**

Add near the top of `scripts/lib/local_db.py`, alongside the existing `import psycopg2` (Task 1 of the original bundled-database plan):

```python
import re

from psycopg2 import sql
```

Add after the existing `SCHEMA_SQL` string (still inside the triple-quoted block, before the closing `"""`), i.e. extend `SCHEMA_SQL` to also create the two new registry tables:

```python
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
CREATE TABLE custom_tables (
    id TEXT PRIMARY KEY,
    label TEXT, table_name TEXT, created_at TEXT,
    report_title TEXT, report_narrative TEXT, report_placement TEXT
);
CREATE TABLE custom_table_columns (
    id TEXT PRIMARY KEY,
    custom_table_id TEXT, label TEXT, column_name TEXT, column_type TEXT
);
"""
```

(This replaces the existing `SCHEMA_SQL` definition in place - the first three `CREATE TABLE` statements are unchanged, only the two new ones are appended.)

Add near the end of `scripts/lib/local_db.py` (after the existing `ensure_running()`):

```python
COLUMN_TYPE_SQL = {"text": "TEXT", "number": "NUMERIC", "date": "DATE"}


def slugify(label):
    slug = re.sub(r"[^a-z0-9]+", "_", (label or "").strip().lower()).strip("_")
    if not slug:
        raise LocalDbError(f"{label!r} does not contain any usable characters for a name")
    return slug[:40]


def validate_identifier(name):
    if not re.match(r"^[a-z][a-z0-9_]*$", name or ""):
        raise LocalDbError(f"Invalid internal name: {name!r}")


def _sql_type_for(column_type):
    sql_type = COLUMN_TYPE_SQL.get(column_type)
    if sql_type is None:
        raise LocalDbError(f"Unknown column type: {column_type!r} (must be one of {sorted(COLUMN_TYPE_SQL)})")
    return sql_type


def create_table(table_name, columns):
    """columns: list of (column_name, column_type) tuples - column_type
    one of "text"/"number"/"date". Every admin-defined table also gets a
    fixed id/added_at pair, matching every other table in this schema."""
    validate_identifier(table_name)
    col_defs = [sql.SQL("id TEXT PRIMARY KEY"), sql.SQL("added_at TEXT")]
    for column_name, column_type in columns:
        validate_identifier(column_name)
        col_defs.append(sql.SQL("{} {}").format(sql.Identifier(column_name), sql.SQL(_sql_type_for(column_type))))
    statement = sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(table_name), sql.SQL(", ").join(col_defs))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def add_column(table_name, column_name, column_type):
    validate_identifier(table_name)
    validate_identifier(column_name)
    statement = sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
        sql.Identifier(table_name), sql.Identifier(column_name), sql.SQL(_sql_type_for(column_type))
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def drop_column(table_name, column_name):
    validate_identifier(table_name)
    validate_identifier(column_name)
    statement = sql.SQL("ALTER TABLE {} DROP COLUMN {}").format(
        sql.Identifier(table_name), sql.Identifier(column_name)
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def drop_table(table_name):
    validate_identifier(table_name)
    statement = sql.SQL("DROP TABLE {}").format(sql.Identifier(table_name))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def update_by_id(table, record_id, fields, column_map=None):
    column_map = column_map or {}
    set_columns = [column_map.get(k, k) for k in fields]
    set_clause = ", ".join(f"{col} = %s" for col in set_columns)
    values = list(fields.values()) + [record_id]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {table} SET {set_clause} WHERE id = %s", values)
            updated = cur.rowcount == 1
        conn.commit()
    finally:
        conn.close()
    return updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_local_db.py -v`
Expected: PASS (all tests, existing + new).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/local_db.py tests/lib/test_local_db.py
git commit -m "feat: add custom-table DDL safety, registry schema, and update_by_id to local_db.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `server/custom_data.py` Part A - table/column/row CRUD (non-AI)

**Files:**
- Create: `server/custom_data.py`
- Test: `tests/server/test_custom_data.py`

**Interfaces:**
- Consumes: `local_db.slugify`, `local_db.validate_identifier`, `local_db.create_table`, `local_db.add_column`, `local_db.drop_column`, `local_db.drop_table`, `local_db.fetch_all`, `local_db.insert_many`, `local_db.delete_by_id`, `local_db.update_by_id` (Task 1).
- Produces: `custom_data.CustomDataError`, `custom_data.list_tables()`, `custom_data.get_table(table_id)`, `custom_data.create_table(label, columns)`, `custom_data.add_column(table_id, label, column_type)`, `custom_data.delete_column(table_id, column_id)`, `custom_data.delete_table(table_id)`, `custom_data.delete_row(table_id, record_id)`, `custom_data.list_records(table_id)` - consumed by Task 4 (via `get_table`) and Task 8 (routes).

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_custom_data.py`:

```python
"""Unit tests for server/custom_data.py's non-AI table/column/row CRUD
orchestration. Every local_db call is mocked - no real database touched,
matching tests/server/test_supplemental_data.py's established pattern.
See docs/superpowers/specs/2026-08-16-admin-custom-tables-design.md.
"""
import pytest

from server import custom_data


def _table_row(table_id="t1", label="Cold Chain", table_name="custom_cold_chain"):
    return {"id": table_id, "label": label, "table_name": table_name, "created_at": "2026-08-16T00:00:00+00:00",
            "report_title": "", "report_narrative": "", "report_placement": ""}


def _column_row(col_id="c1", table_id="t1", label="Status", column_name="status", column_type="text"):
    return {"id": col_id, "custom_table_id": table_id, "label": label,
            "column_name": column_name, "column_type": column_type}


def test_list_tables_attaches_columns(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else [_column_row()]
    ))
    tables = custom_data.list_tables()
    assert len(tables) == 1
    assert tables[0]["columns"] == [_column_row()]


def test_get_table_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    assert custom_data.get_table("does-not-exist") is None


def test_create_table_success(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    created_ddl = []
    monkeypatch.setattr(custom_data.local_db, "create_table", lambda name, cols: created_ddl.append((name, cols)))
    inserted = []
    monkeypatch.setattr(
        custom_data.local_db, "insert_many",
        lambda table, fieldnames, records: inserted.append((table, records)),
    )

    table = custom_data.create_table("Cold Chain Equipment", [{"label": "Status", "type": "text"}])

    assert created_ddl == [("custom_cold_chain_equipment", [("status", "text")])]
    assert inserted[0][0] == "custom_tables"
    assert inserted[0][1][0]["label"] == "Cold Chain Equipment"
    assert inserted[0][1][0]["table_name"] == "custom_cold_chain_equipment"
    assert inserted[1][0] == "custom_table_columns"
    assert inserted[1][1][0]["column_name"] == "status"
    assert table["label"] == "Cold Chain Equipment"


def test_create_table_rejects_empty_label():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.create_table("  ", [{"label": "Status", "type": "text"}])


def test_create_table_rejects_no_columns():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.create_table("Cold Chain", [])


def test_create_table_rejects_unknown_column_type():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.create_table("Cold Chain", [{"label": "Status", "type": "boolean"}])


def test_create_table_rejects_duplicate_column_labels():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.create_table("Cold Chain", [
            {"label": "Status", "type": "text"}, {"label": "status", "type": "number"},
        ])


def test_create_table_rejects_name_collision(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else []
    ))
    with pytest.raises(custom_data.CustomDataError):
        custom_data.create_table("Cold Chain", [{"label": "Status", "type": "text"}])


def test_add_column_success(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else [_column_row()]
    ))
    ddl_calls = []
    monkeypatch.setattr(
        custom_data.local_db, "add_column",
        lambda table_name, col, ctype: ddl_calls.append((table_name, col, ctype)),
    )
    monkeypatch.setattr(custom_data.local_db, "insert_many", lambda *a, **k: None)

    table = custom_data.add_column("t1", "Last Checked", "date")
    assert ddl_calls == [("custom_cold_chain", "last_checked", "date")]
    assert table is not None


def test_add_column_returns_none_for_missing_table(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    assert custom_data.add_column("does-not-exist", "Notes", "text") is None


def test_add_column_rejects_duplicate_name(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else [_column_row()]
    ))
    with pytest.raises(custom_data.CustomDataError):
        custom_data.add_column("t1", "Status", "text")


def test_delete_column_success(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else [_column_row()]
    ))
    dropped = []
    monkeypatch.setattr(custom_data.local_db, "drop_column", lambda table_name, col: dropped.append((table_name, col)))
    monkeypatch.setattr(custom_data.local_db, "delete_by_id", lambda table, record_id: True)

    assert custom_data.delete_column("t1", "c1") is True
    assert dropped == [("custom_cold_chain", "status")]


def test_delete_column_returns_false_for_unknown_column(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else []
    ))
    assert custom_data.delete_column("t1", "does-not-exist") is False


def test_delete_table_success(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else [_column_row()]
    ))
    dropped = []
    monkeypatch.setattr(custom_data.local_db, "drop_table", lambda table_name: dropped.append(table_name))
    deleted = []
    monkeypatch.setattr(
        custom_data.local_db, "delete_by_id",
        lambda table, record_id: deleted.append((table, record_id)) or True,
    )

    assert custom_data.delete_table("t1") is True
    assert dropped == ["custom_cold_chain"]
    assert ("custom_table_columns", "c1") in deleted
    assert ("custom_tables", "t1") in deleted


def test_delete_table_returns_false_for_unknown_table(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    assert custom_data.delete_table("does-not-exist") is False


def test_delete_row_success(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else []
    ))
    monkeypatch.setattr(
        custom_data.local_db, "delete_by_id",
        lambda table, record_id: table == "custom_cold_chain" and record_id == "r1",
    )
    assert custom_data.delete_row("t1", "r1") is True


def test_delete_row_returns_false_for_unknown_table(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    assert custom_data.delete_row("does-not-exist", "r1") is False


def test_list_records_success(monkeypatch):
    fake_rows = [{"id": "r1", "status": "ok"}]
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else (fake_rows if table == "custom_cold_chain" else [])
    ))
    assert custom_data.list_records("t1") == fake_rows


def test_list_records_returns_none_for_unknown_table(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    assert custom_data.list_records("does-not-exist") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_custom_data.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'server.custom_data'`)

- [ ] **Step 3: Implement**

Create `server/custom_data.py`:

```python
"""Admin-defined custom data tables - real, dynamically created Postgres
tables (scripts/lib/local_db.py Part C) with their own admin-chosen
columns, alongside the three fixed admin-overlay tables
(supplemental_data.py/metric_overrides.py/bot_facilities.py). This module
owns non-AI CRUD (Part A, below); AI schema inference/row extraction/
report placement live in the same module (Parts B and C, added by later
tasks in this feature's plan). See docs/superpowers/specs/
2026-08-16-admin-custom-tables-design.md.
"""
import uuid
from datetime import datetime, timezone

from scripts.lib import local_db

TABLE_FIELDNAMES = ("id", "label", "table_name", "created_at", "report_title", "report_narrative", "report_placement")
COLUMN_FIELDNAMES = ("id", "custom_table_id", "label", "column_name", "column_type")
VALID_COLUMN_TYPES = ("text", "number", "date")


class CustomDataError(Exception):
    """Raised when a custom-table operation fails validation - message
    safe to show the admin directly, never a raw traceback."""


def _validate_columns(columns):
    if not columns:
        raise CustomDataError("At least one column is required")
    seen = set()
    for col in columns:
        label = (col.get("label") or "").strip()
        column_type = col.get("type")
        if not label:
            raise CustomDataError("Every column needs a label")
        if column_type not in VALID_COLUMN_TYPES:
            raise CustomDataError(f"Unknown column type: {column_type!r} (must be one of {VALID_COLUMN_TYPES})")
        key = local_db.slugify(label)
        if key in seen:
            raise CustomDataError(f"Duplicate column name: {label!r}")
        seen.add(key)


def list_tables():
    tables = local_db.fetch_all("custom_tables", order_by="created_at")
    columns = local_db.fetch_all("custom_table_columns")
    by_table = {}
    for col in columns:
        by_table.setdefault(col["custom_table_id"], []).append(col)
    for table in tables:
        table["columns"] = by_table.get(table["id"], [])
    return tables


def get_table(table_id):
    for table in list_tables():
        if table["id"] == table_id:
            return table
    return None


def create_table(label, columns):
    """columns: list of {"label": str, "type": "text"|"number"|"date"}."""
    label = (label or "").strip()
    if not label:
        raise CustomDataError("Table label is required")
    _validate_columns(columns)

    table_name = f"custom_{local_db.slugify(label)}"
    existing = local_db.fetch_all("custom_tables")
    if any(t["table_name"] == table_name for t in existing):
        raise CustomDataError(f"A table named {label!r} (or one that maps to the same internal name) already exists")

    ddl_columns = [(local_db.slugify(c["label"]), c["type"]) for c in columns]
    local_db.create_table(table_name, ddl_columns)

    table_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    local_db.insert_many("custom_tables", TABLE_FIELDNAMES, [{
        "id": table_id, "label": label, "table_name": table_name, "created_at": now,
        "report_title": "", "report_narrative": "", "report_placement": "",
    }])
    for col in columns:
        local_db.insert_many("custom_table_columns", COLUMN_FIELDNAMES, [{
            "id": uuid.uuid4().hex[:12], "custom_table_id": table_id,
            "label": col["label"].strip(), "column_name": local_db.slugify(col["label"]),
            "column_type": col["type"],
        }])
    return get_table(table_id)


def add_column(table_id, label, column_type):
    table = get_table(table_id)
    if table is None:
        return None
    _validate_columns([{"label": label, "type": column_type}])
    column_name = local_db.slugify(label)
    if any(c["column_name"] == column_name for c in table["columns"]):
        raise CustomDataError(f"A column named {label!r} already exists on this table")
    local_db.add_column(table["table_name"], column_name, column_type)
    local_db.insert_many("custom_table_columns", COLUMN_FIELDNAMES, [{
        "id": uuid.uuid4().hex[:12], "custom_table_id": table_id,
        "label": label.strip(), "column_name": column_name, "column_type": column_type,
    }])
    return get_table(table_id)


def delete_column(table_id, column_id):
    table = get_table(table_id)
    if table is None:
        return False
    column = next((c for c in table["columns"] if c["id"] == column_id), None)
    if column is None:
        return False
    local_db.drop_column(table["table_name"], column["column_name"])
    local_db.delete_by_id("custom_table_columns", column_id)
    return True


def delete_table(table_id):
    table = get_table(table_id)
    if table is None:
        return False
    local_db.drop_table(table["table_name"])
    for column in table["columns"]:
        local_db.delete_by_id("custom_table_columns", column["id"])
    local_db.delete_by_id("custom_tables", table_id)
    return True


def delete_row(table_id, record_id):
    table = get_table(table_id)
    if table is None:
        return False
    return local_db.delete_by_id(table["table_name"], record_id)


def list_records(table_id):
    table = get_table(table_id)
    if table is None:
        return None
    return local_db.fetch_all(table["table_name"], order_by="added_at")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_custom_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/custom_data.py tests/server/test_custom_data.py
git commit -m "feat: add server/custom_data.py table/column/row CRUD orchestration

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `server/custom_data.py` Part B - AI schema inference

**Files:**
- Modify: `server/custom_data.py`
- Modify: `tests/server/test_custom_data.py`

**Interfaces:**
- Consumes: `server.ai_client.ask(provider, key, question, context)` (existing).
- Produces: `custom_data.build_schema_prompt(prompt)`, `custom_data.parse_schema_response(raw_text)`, `custom_data.propose_schema(provider, key, prompt)` - consumed by Task 8 (routes).

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_custom_data.py`:

```python
import json

from server import ai_client


def test_build_schema_prompt_includes_user_prompt():
    question = custom_data.build_schema_prompt("track cold-chain equipment status per facility")
    assert "cold-chain equipment status per facility" in question


def test_parse_schema_response_valid():
    raw = json.dumps({"label": "Cold Chain Equipment", "columns": [
        {"label": "Facility", "type": "text"}, {"label": "Last Checked", "type": "date"},
    ]})
    result = custom_data.parse_schema_response(raw)
    assert result["label"] == "Cold Chain Equipment"
    assert result["columns"] == [{"label": "Facility", "type": "text"}, {"label": "Last Checked", "type": "date"}]


def test_parse_schema_response_strips_code_fence():
    raw = "```json\n" + json.dumps({"label": "X", "columns": [{"label": "A", "type": "text"}]}) + "\n```"
    result = custom_data.parse_schema_response(raw)
    assert result["label"] == "X"


def test_parse_schema_response_rejects_invalid_json():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_schema_response("not json")


def test_parse_schema_response_rejects_missing_label():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_schema_response(json.dumps({"columns": [{"label": "A", "type": "text"}]}))


def test_parse_schema_response_rejects_empty_columns():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_schema_response(json.dumps({"label": "X", "columns": []}))


def test_parse_schema_response_rejects_unknown_column_type():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_schema_response(json.dumps({"label": "X", "columns": [{"label": "A", "type": "boolean"}]}))


def test_propose_schema_calls_ai_client(monkeypatch):
    raw = json.dumps({"label": "X", "columns": [{"label": "A", "type": "text"}]})
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw)
    result = custom_data.propose_schema("groq", "key123", "track something")
    assert result["label"] == "X"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_custom_data.py -v`
Expected: FAIL (`AttributeError: module 'server.custom_data' has no attribute 'build_schema_prompt'`)

- [ ] **Step 3: Implement**

Add to `server/custom_data.py`, near the top alongside the existing imports:

```python
import json
import re

from server import ai_client

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
```

Add at the end of `server/custom_data.py`:

```python
def build_schema_prompt(prompt):
    return (
        "Propose a database table structure for a Khyber Pakhtunkhwa healthcare "
        f"planning admin who wants to track: {prompt}. Respond with ONLY a JSON "
        'object (no prose, no markdown code fence) shaped exactly like: '
        '{"label": "...", "columns": [{"label": "...", "type": "text"}]}. '
        '"label" is a short, human-readable table name (2-5 words). "columns" is '
        'a list of 2-8 sensible fields for this data - each with a short "label" '
        'and a "type" that MUST be exactly one of "text", "number", or "date". '
        'Always include a column for whichever facility/district/entity the data '
        "is about, if applicable."
    )


def parse_schema_response(raw_text):
    text = raw_text.strip()
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CustomDataError(f"AI response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CustomDataError("AI response must be a JSON object")
    label = str(parsed.get("label", "")).strip()
    columns = parsed.get("columns")
    if not label:
        raise CustomDataError("AI did not propose a table label")
    if not isinstance(columns, list) or not columns:
        raise CustomDataError("AI did not propose any columns")
    cleaned_columns = []
    for index, col in enumerate(columns):
        if not isinstance(col, dict):
            raise CustomDataError(f"Column {index} is not a JSON object")
        col_label = str(col.get("label", "")).strip()
        col_type = col.get("type")
        if not col_label:
            raise CustomDataError(f"Column {index} is missing a label")
        if col_type not in VALID_COLUMN_TYPES:
            raise CustomDataError(f"Column {index} has an unknown type: {col_type!r}")
        cleaned_columns.append({"label": col_label, "type": col_type})
    return {"label": label, "columns": cleaned_columns}


def propose_schema(provider, key, prompt):
    question = build_schema_prompt(prompt)
    raw_response = ai_client.ask(provider, key, question, "")
    return parse_schema_response(raw_response)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_custom_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/custom_data.py tests/server/test_custom_data.py
git commit -m "feat: add AI schema-inference-from-prompt to custom_data.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `server/custom_data.py` Part C - AI row extraction, report placement, `add_data`

**Files:**
- Modify: `server/custom_data.py`
- Modify: `tests/server/test_custom_data.py`

**Interfaces:**
- Consumes: `ai_client.ask` (existing), `get_table` (Task 2), `local_db.insert_many`, `local_db.fetch_all`, `local_db.update_by_id` (Task 1).
- Produces: `custom_data.REPORT_ANCHORS`, `custom_data.build_extraction_question(table, instruction)`, `custom_data.parse_extraction_response(raw_text, table)`, `custom_data.build_placement_question(table, rows)`, `custom_data.parse_placement_response(raw_text)`, `custom_data.propose_placement(provider, key, table, rows)`, `custom_data.add_data(provider, key, table_id, document_text, instruction)` - consumed by Task 8 (routes).

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_custom_data.py`:

```python
from datetime import date


def test_build_extraction_question_includes_columns():
    table = {"label": "Cold Chain", "columns": [
        {"column_name": "facility", "column_type": "text", "label": "Facility"},
        {"column_name": "checked_on", "column_type": "date", "label": "Checked On"},
    ]}
    question = custom_data.build_extraction_question(table, "")
    assert "facility" in question
    assert "checked_on" in question


def test_parse_extraction_response_valid():
    table = {"columns": [
        {"column_name": "facility", "column_type": "text", "label": "Facility"},
        {"column_name": "temp_c", "column_type": "number", "label": "Temp C"},
        {"column_name": "checked_on", "column_type": "date", "label": "Checked On"},
    ]}
    raw = json.dumps([{"facility": "DHQ Hospital", "temp_c": 4.5, "checked_on": "2026-08-16"}])
    rows = custom_data.parse_extraction_response(raw, table)
    assert rows == [{"facility": "DHQ Hospital", "temp_c": 4.5, "checked_on": "2026-08-16"}]


def test_parse_extraction_response_rejects_bad_number():
    table = {"columns": [{"column_name": "temp_c", "column_type": "number", "label": "Temp C"}]}
    raw = json.dumps([{"temp_c": "not a number"}])
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_extraction_response(raw, table)


def test_parse_extraction_response_rejects_bad_date():
    table = {"columns": [{"column_name": "checked_on", "column_type": "date", "label": "Checked On"}]}
    raw = json.dumps([{"checked_on": "not a date"}])
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_extraction_response(raw, table)


def test_parse_extraction_response_defaults_missing_text_to_empty_string():
    table = {"columns": [{"column_name": "notes", "column_type": "text", "label": "Notes"}]}
    rows = custom_data.parse_extraction_response(json.dumps([{}]), table)
    assert rows == [{"notes": ""}]


def test_parse_extraction_response_rejects_empty_array():
    table = {"columns": [{"column_name": "notes", "column_type": "text", "label": "Notes"}]}
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_extraction_response("[]", table)


def test_parse_placement_response_valid():
    raw = json.dumps({
        "title": "Cold Chain Status", "narrative": "Most facilities report working refrigeration.",
        "placement": "after:facility-readiness",
    })
    result = custom_data.parse_placement_response(raw)
    assert result["title"] == "Cold Chain Status"
    assert result["placement"] == "after:facility-readiness"


def test_parse_placement_response_falls_back_on_hallucinated_anchor():
    raw = json.dumps({"title": "X", "narrative": "Y", "placement": "after:not-a-real-anchor"})
    result = custom_data.parse_placement_response(raw)
    assert result["placement"] == "new_section"


def test_parse_placement_response_falls_back_on_malformed_json():
    result = custom_data.parse_placement_response("not json at all")
    assert result["placement"] == "new_section"


def test_parse_placement_response_falls_back_on_wrong_shape():
    result = custom_data.parse_placement_response(json.dumps(["not", "a", "dict"]))
    assert result["placement"] == "new_section"


def test_add_data_extracts_inserts_and_stores_placement(monkeypatch):
    table = {
        "id": "t1", "label": "Cold Chain", "table_name": "custom_cold_chain",
        "columns": [{"column_name": "status", "column_type": "text", "label": "Status"}],
    }
    monkeypatch.setattr(custom_data, "get_table", lambda table_id: table if table_id == "t1" else None)

    extraction_raw = json.dumps([{"status": "working"}])
    placement_raw = json.dumps({"title": "Cold Chain Status", "narrative": "All good.", "placement": "new_section"})
    calls = {"n": 0}

    def fake_ask(provider, key, question, context):
        calls["n"] += 1
        return extraction_raw if calls["n"] == 1 else placement_raw

    monkeypatch.setattr(custom_data.ai_client, "ask", fake_ask)
    inserted = []
    monkeypatch.setattr(
        custom_data.local_db, "insert_many",
        lambda table_name, fieldnames, records: inserted.append((table_name, records)),
    )
    monkeypatch.setattr(
        custom_data.local_db, "fetch_all",
        lambda table_name, order_by=None: [{"id": "r1", "status": "working"}],
    )
    updates = []
    monkeypatch.setattr(
        custom_data.local_db, "update_by_id",
        lambda table_name, record_id, fields: updates.append((table_name, record_id, fields)),
    )

    rows = custom_data.add_data("groq", "key123", "t1", "document text", "")

    assert inserted[0][0] == "custom_cold_chain"
    assert inserted[0][1][0]["status"] == "working"
    assert updates == [("custom_tables", "t1", {
        "report_title": "Cold Chain Status", "report_narrative": "All good.", "report_placement": "new_section",
    })]
    assert len(rows) == 1


def test_add_data_returns_none_for_unknown_table(monkeypatch):
    monkeypatch.setattr(custom_data, "get_table", lambda table_id: None)
    assert custom_data.add_data("groq", "key123", "does-not-exist", "doc", "") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_custom_data.py -v`
Expected: FAIL (`AttributeError: module 'server.custom_data' has no attribute 'build_extraction_question'`)

- [ ] **Step 3: Implement**

Add `import uuid` is already present (Task 2); add `from datetime import date` alongside the existing `from datetime import datetime, timezone` import in `server/custom_data.py`.

Add at the end of `server/custom_data.py`:

```python
REPORT_ANCHORS = (
    "current-state", "infrastructure-context", "terrain-elevation", "district-data",
    "findings", "future-planning", "supplemental-data", "facility-readiness",
)


def build_extraction_question(table, instruction):
    columns_desc = "; ".join(f'"{c["column_name"]}" ({c["column_type"]})' for c in table["columns"])
    instruction_line = f"Admin's instruction: {instruction}. " if instruction else ""
    return (
        "Extract structured records from the document content above, for a table "
        f'called "{table["label"]}" with these exact fields: {columns_desc}. Respond '
        "with ONLY a JSON array (no prose, no markdown code fence) of objects using "
        'exactly those field names as keys - every value for a "number" field must be '
        'a JSON number (not a string), every value for a "date" field must be an ISO '
        '"YYYY-MM-DD" string, and every value for a "text" field must be a string. If '
        'a field is not mentioned for a given record, use "" for text, null for '
        "number/date. If there is nothing extractable, respond with an empty JSON "
        f"array: []. {instruction_line}"
    )


def _validate_row_value(value, column_type, column_label):
    if column_type == "text":
        return "" if value is None else str(value)
    if column_type == "number":
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            raise CustomDataError(f"{column_label!r} must be a number, got {value!r}")
    if column_type == "date":
        if value is None or value == "":
            return None
        try:
            date.fromisoformat(str(value))
        except ValueError:
            raise CustomDataError(f"{column_label!r} must be an ISO date (YYYY-MM-DD), got {value!r}")
        return str(value)
    raise CustomDataError(f"Unknown column type: {column_type!r}")


def parse_extraction_response(raw_text, table):
    text = raw_text.strip()
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CustomDataError(f"AI response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise CustomDataError("AI response must be a JSON array of records")
    if not parsed:
        raise CustomDataError("AI did not find any records to add")

    rows = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise CustomDataError(f"Record {index} is not a JSON object")
        row = {}
        for col in table["columns"]:
            row[col["column_name"]] = _validate_row_value(
                item.get(col["column_name"]), col["column_type"], col["label"]
            )
        rows.append(row)
    return rows


def build_placement_question(table, rows):
    columns_desc = ", ".join(c["label"] for c in table["columns"])
    sample = json.dumps(rows[:5])
    anchors_list = ", ".join(REPORT_ANCHORS)
    return (
        f'A new admin-defined data table called "{table["label"]}" (columns: '
        f"{columns_desc}) has {len(rows)} record(s) that will appear in a Khyber "
        f"Pakhtunkhwa healthcare planning report. Sample records: {sample}. "
        'Respond with ONLY a JSON object (no prose, no markdown code fence) shaped '
        'exactly like: {"title": "...", "narrative": "...", "placement": "..."}. '
        '"title" is a short section heading for this data. "narrative" is 1-3 '
        'sentences interpreting what this data shows. "placement" MUST be exactly '
        'the string "new_section", or exactly "after:<anchor>" where <anchor> is '
        f"one of these existing report section ids: {anchors_list}."
    )


def parse_placement_response(raw_text):
    text = raw_text.strip()
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"title": "", "narrative": "", "placement": "new_section"}
    if not isinstance(parsed, dict):
        return {"title": "", "narrative": "", "placement": "new_section"}
    title = str(parsed.get("title", "")).strip()
    narrative = str(parsed.get("narrative", "")).strip()
    placement = parsed.get("placement")
    valid_after = isinstance(placement, str) and placement.startswith("after:") \
        and placement[len("after:"):] in REPORT_ANCHORS
    if placement != "new_section" and not valid_after:
        placement = "new_section"
    return {"title": title, "narrative": narrative, "placement": placement}


def propose_placement(provider, key, table, rows):
    question = build_placement_question(table, rows)
    raw_response = ai_client.ask(provider, key, question, "")
    return parse_placement_response(raw_response)


def add_data(provider, key, table_id, document_text, instruction):
    table = get_table(table_id)
    if table is None:
        return None
    question = build_extraction_question(table, instruction)
    raw_response = ai_client.ask(provider, key, question, document_text)
    rows = parse_extraction_response(raw_response, table)

    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        row["id"] = uuid.uuid4().hex[:12]
        row["added_at"] = now
    fieldnames = ("id", "added_at") + tuple(c["column_name"] for c in table["columns"])
    local_db.insert_many(table["table_name"], fieldnames, rows)

    all_rows = local_db.fetch_all(table["table_name"], order_by="added_at")
    placement = propose_placement(provider, key, table, all_rows)
    local_db.update_by_id("custom_tables", table_id, {
        "report_title": placement["title"] or table["label"],
        "report_narrative": placement["narrative"],
        "report_placement": placement["placement"],
    })
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_custom_data.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add server/custom_data.py tests/server/test_custom_data.py
git commit -m "feat: add AI row extraction, report placement, and add_data orchestration to custom_data.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `scripts/lib/custom_tables.py` - report read/render side

**Files:**
- Create: `scripts/lib/custom_tables.py`
- Test: `tests/lib/test_custom_tables.py`

**Interfaces:**
- Consumes: `local_db.fetch_all` (Task 1).
- Produces: `custom_tables.list_tables_with_data()`, `custom_tables.render_section_html(table)` - consumed by Task 6 (`14_build_html_report.py`) and Task 7 (`report_context.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/test_custom_tables.py`:

```python
"""Unit tests for scripts/lib/custom_tables.py - the report-build-side
read/render counterpart to server/custom_data.py's admin-panel
orchestration. Every local_db call is mocked, matching
tests/lib/test_supplemental_records.py's established pattern. See
docs/superpowers/specs/2026-08-16-admin-custom-tables-design.md.
"""
from scripts.lib import custom_tables


def _table_row(table_id="t1"):
    return {"id": table_id, "label": "Cold Chain", "table_name": "custom_cold_chain",
            "created_at": "2026-08-16T00:00:00+00:00", "report_title": "Cold Chain Status",
            "report_narrative": "All good.", "report_placement": "new_section"}


def _column_row(table_id="t1"):
    return {"id": "c1", "custom_table_id": table_id, "label": "Status",
            "column_name": "status", "column_type": "text"}


def test_list_tables_with_data_omits_empty_tables(monkeypatch):
    monkeypatch.setattr(custom_tables.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables"
        else [_column_row()] if table == "custom_table_columns"
        else []
    ))
    assert custom_tables.list_tables_with_data() == []


def test_list_tables_with_data_includes_populated_tables(monkeypatch):
    rows = [{"id": "r1", "added_at": "2026-08-16T00:00:00+00:00", "status": "working"}]
    monkeypatch.setattr(custom_tables.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables"
        else [_column_row()] if table == "custom_table_columns"
        else rows
    ))
    result = custom_tables.list_tables_with_data()
    assert len(result) == 1
    assert result[0]["rows"] == rows
    assert result[0]["columns"] == [_column_row()]


def test_render_section_html_includes_title_narrative_and_rows():
    table = {
        "id": "t1", "label": "Cold Chain", "report_title": "Cold Chain Status",
        "report_narrative": "All good.", "columns": [_column_row()],
        "rows": [{"id": "r1", "status": "working"}],
    }
    html = custom_tables.render_section_html(table)
    assert 'id="custom-t1"' in html
    assert "Cold Chain Status" in html
    assert "All good." in html
    assert "working" in html


def test_render_section_html_falls_back_to_label_when_no_title():
    table = {
        "id": "t1", "label": "Cold Chain", "report_title": "", "report_narrative": "",
        "columns": [_column_row()], "rows": [{"id": "r1", "status": "working"}],
    }
    html = custom_tables.render_section_html(table)
    assert "Cold Chain" in html


def test_render_section_html_escapes_untrusted_content():
    table = {
        "id": "t1", "label": "X", "report_title": "<script>alert(1)</script>",
        "report_narrative": "", "columns": [_column_row()],
        "rows": [{"id": "r1", "status": "<b>working</b>"}],
    }
    html = custom_tables.render_section_html(table)
    assert "<script>" not in html
    assert "<b>working</b>" not in html


def test_render_section_html_handles_none_cell_value():
    table = {
        "id": "t1", "label": "X", "report_title": "X", "report_narrative": "",
        "columns": [_column_row()], "rows": [{"id": "r1", "status": None}],
    }
    html = custom_tables.render_section_html(table)  # must not raise
    assert 'id="custom-t1"' in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_custom_tables.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'scripts.lib.custom_tables'`)

- [ ] **Step 3: Implement**

Create `scripts/lib/custom_tables.py`:

```python
"""Reads admin-defined custom data tables (scripts/lib/local_db.py) for
scripts/14_build_html_report.py's report sections - a report-build-side
counterpart to server/custom_data.py's admin-panel orchestration, living
here since the report-build script can't import from server/ (it runs
standalone, never inside the FastAPI app - same reason
scripts/lib/supplemental_records.py exists). See docs/superpowers/specs/
2026-08-16-admin-custom-tables-design.md sections 6-7.
"""
import html

from scripts.lib import local_db


def list_tables_with_data():
    """Every custom table that has at least one row - report-ready:
    registry metadata (label/report_title/report_narrative/
    report_placement), its columns, and its current rows. Empty tables
    are omitted entirely (see spec section 6)."""
    tables = local_db.fetch_all("custom_tables", order_by="created_at")
    columns = local_db.fetch_all("custom_table_columns")
    by_table = {}
    for col in columns:
        by_table.setdefault(col["custom_table_id"], []).append(col)

    result = []
    for table in tables:
        table["columns"] = by_table.get(table["id"], [])
        rows = local_db.fetch_all(table["table_name"], order_by="added_at")
        if not rows:
            continue
        table["rows"] = rows
        result.append(table)
    return result


def render_section_html(table):
    title = html.escape(table["report_title"] or table["label"])
    narrative = html.escape(table["report_narrative"]) if table["report_narrative"] else ""
    narrative_html = f"<p>{narrative}</p>" if narrative else ""
    header_cells = "".join(f"<th>{html.escape(c['label'])}</th>" for c in table["columns"])
    body_rows = []
    for row in table["rows"]:
        cells = "".join(
            f"<td>{html.escape(str(row.get(c['column_name']) or ''))}</td>" for c in table["columns"]
        )
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        f'<section id="custom-{table["id"]}">\n'
        f"<h2>{title}</h2>\n"
        f"{narrative_html}\n"
        '<div class="table-wrap"><table>\n'
        f"<thead><tr>{header_cells}</tr></thead>\n"
        f'<tbody>{"".join(body_rows)}</tbody>\n'
        "</table></div>\n"
        "</section>"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_custom_tables.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/custom_tables.py tests/lib/test_custom_tables.py
git commit -m "feat: add scripts/lib/custom_tables.py report read/render side

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Wire custom-table sections into `scripts/14_build_html_report.py`

**Files:**
- Modify: `scripts/14_build_html_report.py`
- Test: `tests/test_custom_tables_section.py`

**Interfaces:**
- Consumes: `custom_tables.list_tables_with_data()`, `custom_tables.render_section_html(table)` (Task 5).
- Produces: `_insert_custom_sections(html_text, tables)` (module-private, tested directly via `importlib`) - wired into the report's own `build()` function, no new public interface consumed elsewhere.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_custom_tables_section.py`:

```python
"""Tests for scripts/14_build_html_report.py's _insert_custom_sections()
- the pure string-manipulation function that places each custom table's
rendered section either after a named existing anchor or as a new
section before <footer>, operating on the already-fully-assembled report
HTML string. See docs/superpowers/specs/
2026-08-16-admin-custom-tables-design.md section 6.
"""
import importlib

report_mod = importlib.import_module("scripts.14_build_html_report")


def _table(table_id="t1", placement="new_section"):
    return {
        "id": table_id, "label": "Cold Chain", "report_title": "Cold Chain Status",
        "report_narrative": "", "report_placement": placement, "columns": [], "rows": [],
    }


def test_insert_custom_sections_after_named_anchor():
    html_text = '<section id="facility-readiness"><p>x</p></section><footer>foo</footer>'
    result = report_mod._insert_custom_sections(html_text, [_table(placement="after:facility-readiness")])
    assert result.index("facility-readiness") < result.index("custom-t1")
    assert result.index("custom-t1") < result.index("<footer>")


def test_insert_custom_sections_new_section_before_footer():
    html_text = '<section id="facility-readiness"></section><footer>foo</footer>'
    result = report_mod._insert_custom_sections(html_text, [_table(placement="new_section")])
    assert result.index("custom-t1") < result.index("<footer>")


def test_insert_custom_sections_no_tables_leaves_html_unchanged():
    html_text = '<section id="facility-readiness"></section><footer>foo</footer>'
    assert report_mod._insert_custom_sections(html_text, []) == html_text


def test_insert_custom_sections_falls_back_to_new_section_for_missing_anchor():
    html_text = '<section id="facility-readiness"></section><footer>foo</footer>'
    result = report_mod._insert_custom_sections(html_text, [_table(placement="after:does-not-exist")])
    assert result.index("custom-t1") < result.index("<footer>")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_custom_tables_section.py -v`
Expected: FAIL (`AttributeError: module 'scripts.14_build_html_report' has no attribute '_insert_custom_sections'`)

- [ ] **Step 3: Implement**

Add near the top of `scripts/14_build_html_report.py`, alongside the existing `scripts.lib` imports:

```python
from scripts.lib import custom_tables as custom_tables_lib
```

Add this function anywhere at module level in `scripts/14_build_html_report.py` (e.g. directly above `def build(`):

```python
def _insert_custom_sections(html_text, tables):
    new_sections = []
    by_anchor = {}
    for table in tables:
        section_html = custom_tables_lib.render_section_html(table)
        placement = table.get("report_placement") or "new_section"
        if placement.startswith("after:"):
            by_anchor.setdefault(placement[len("after:"):], []).append(section_html)
        else:
            new_sections.append(section_html)

    for anchor, sections in by_anchor.items():
        marker = f'<section id="{anchor}">'
        idx = html_text.find(marker)
        if idx == -1:
            new_sections.extend(sections)
            continue
        close_idx = html_text.find("</section>", idx)
        insertion_point = close_idx + len("</section>")
        html_text = html_text[:insertion_point] + "\n" + "\n".join(sections) + html_text[insertion_point:]

    if new_sections:
        html_text = html_text.replace("<footer>", "\n".join(new_sections) + "\n<footer>", 1)
    return html_text
```

In `build()`, find the existing lines:

```python
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "KP_Healthcare_Plan.html").write_text(html, encoding="utf-8")
```

Replace with:

```python
    html = _insert_custom_sections(html, custom_tables_lib.list_tables_with_data())

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "KP_Healthcare_Plan.html").write_text(html, encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_custom_tables_section.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass (no existing report-build test calls `build()` end-to-end against a real database, so this change is additive-only for them).

- [ ] **Step 6: Commit**

```bash
git add scripts/14_build_html_report.py tests/test_custom_tables_section.py
git commit -m "feat: render admin-defined custom table sections into the HTML report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: `server/report_context.py` - Ask AI / Telegram bot digest

**Files:**
- Modify: `server/report_context.py`
- Modify: `tests/server/test_report_context.py`

**Interfaces:**
- Consumes: `custom_tables.list_tables_with_data()` (Task 5).
- Produces: `build_context(..., custom_tables=None)` - existing name, new optional keyword parameter. No change to any existing caller's required arguments.

- [ ] **Step 1: Write the failing tests**

Replace `tests/server/test_report_context.py` in full:

```python
from server import report_context

_FACILITIES = [
    {"name": "Alpha General Hospital", "category": "Hospital", "district": "Alpha", "is_duplicate_of": ""},
    {"name": "Alpha Clinic", "category": "Clinic", "district": "Alpha", "is_duplicate_of": ""},
    {"name": "Beta Clinic", "category": "Clinic", "district": "Beta", "is_duplicate_of": ""},
]

_CUSTOM_TABLES = [
    {"label": "Cold Chain Equipment", "columns": [{"label": "Facility"}, {"label": "Status"}],
     "rows": [{"id": "r1"}, {"id": "r2"}]},
]


def _fixture_metrics():
    return [
        {
            "district": "Alpha", "need_tier": "Critical", "gap_score": "90.0",
            "population_2023": "100000", "beds_per_1000": "0.50",
            "doctors_per_1000": "0.10", "terrain": "plains",
        },
        {
            "district": "Beta", "need_tier": "Low", "gap_score": "10.0",
            "population_2023": "200000", "beds_per_1000": "5.00",
            "doctors_per_1000": "1.00", "terrain": "mountainous",
        },
    ]


def test_build_context_includes_totals():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert "Total districts: 2" in context
    assert "300,000" in context  # total population


def test_build_context_includes_tier_counts():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert "Critical=1" in context
    assert "Low=1" in context
    assert "High=0" in context
    assert "Moderate=0" in context


def test_build_context_ranks_by_gap_score_descending():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert context.index("Alpha") < context.index("Beta")


def test_build_context_includes_district_fields():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert "Alpha" in context
    assert "Critical" in context
    assert "100,000" in context


def test_build_context_loads_real_metrics_by_default(monkeypatch):
    # supplemental_records/custom_tables are mocked here (not left to
    # their own None defaults) so this test - about metrics loading, not
    # the other two - doesn't require a real running local database.
    monkeypatch.setattr(report_context.supplemental_data, "load_records", lambda: [])
    monkeypatch.setattr(report_context.custom_tables_lib, "list_tables_with_data", lambda: [])
    context = report_context.build_context()
    assert "Total districts: 35" in context
    assert "Peshawar" in context


def test_build_context_includes_supplemental_records():
    supplemental = [
        {"district": "Alpha", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit, operational"},
    ]
    context = report_context.build_context(_fixture_metrics(), supplemental, custom_tables=[])
    assert "MRI Machine" in context
    assert "DHQ Hospital" in context


def test_build_context_omits_supplemental_section_when_empty():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert "Additional facility/district information" not in context


def test_build_context_handles_none_facility_without_crashing():
    # build_context's own contract, independent of where its
    # supplemental_records argument comes from: a record with a None
    # facility value must not crash the sort-by-tuple comparison (None
    # vs str) and must not leak the literal string "None" into the
    # digest. (Previously constructed via a ragged CSV row read through
    # supplemental_data.load_records() - no longer possible now that
    # load_records() is backed by a real database table, which can't
    # produce a ragged row; the None-handling contract this test checks
    # belongs to build_context() itself, so it's exercised directly.)
    supplemental = [
        {"district": "Alpha", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit", "source_document": "a.pdf",
         "added_at": "2026-08-15T00:00:00+00:00"},
        {"district": "Alpha", "facility": None, "category": "equipment",
         "label": "X-ray", "detail": "", "source_document": "a.pdf",
         "added_at": "2026-08-15T00:00:01+00:00"},
    ]

    context = report_context.build_context(_fixture_metrics(), supplemental, custom_tables=[])
    assert "None" not in context
    assert "DHQ Hospital" in context


def test_build_context_includes_facility_totals_and_breakdowns():
    context = report_context.build_context(_fixture_metrics(), [], _FACILITIES, custom_tables=[])
    assert "Total known facilities: 3" in context
    assert "Hospital: 1" in context
    assert "Clinic: 2" in context
    assert "Alpha: 2" in context
    assert "Beta: 1" in context


def test_build_context_facility_totals_include_flagged_duplicates():
    # Matches scripts/14_build_html_report.py's own "Known Facilities"
    # stat tile: is_duplicate_of records are flagged, not dropped, from
    # the merged table - the AI's count must match what the report
    # itself already displays, not a different "distinct" number.
    facilities = _FACILITIES + [
        {"name": "Alpha Clinic (dup)", "category": "Clinic", "district": "Alpha", "is_duplicate_of": "Alpha Clinic"},
    ]
    context = report_context.build_context(_fixture_metrics(), [], facilities, custom_tables=[])
    assert "Total known facilities: 4" in context


def test_build_context_omits_facilities_section_when_empty():
    context = report_context.build_context(_fixture_metrics(), [], [], custom_tables=[])
    assert "Total known facilities" not in context


def test_build_context_loads_real_facilities_by_default(monkeypatch):
    # Same reasoning as test_build_context_loads_real_metrics_by_default:
    # mock supplemental_records/custom_tables so this facilities-loading
    # test doesn't require a real running local database.
    monkeypatch.setattr(report_context.supplemental_data, "load_records", lambda: [])
    monkeypatch.setattr(report_context.custom_tables_lib, "list_tables_with_data", lambda: [])
    context = report_context.build_context()
    assert "Total known facilities:" in context
    assert "Peshawar:" in context


def test_build_context_includes_custom_tables_summary():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=_CUSTOM_TABLES)
    assert "Cold Chain Equipment" in context
    assert "2 records" in context
    assert "Facility" in context


def test_build_context_omits_custom_tables_section_when_empty():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert "Admin-Defined Custom Data Tables" not in context
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_report_context.py -v`
Expected: FAIL (`TypeError: build_context() got an unexpected keyword argument 'custom_tables'`)

- [ ] **Step 3: Implement**

In `server/report_context.py`, add to the existing imports:

```python
from scripts.lib import custom_tables as custom_tables_lib
```

Change the function signature from:

```python
def build_context(metrics=None, supplemental_records=None, facilities=None):
```

to:

```python
def build_context(metrics=None, supplemental_records=None, facilities=None, custom_tables=None):
```

Add right after the existing `if facilities is None: facilities = load_facilities()` block:

```python
    if custom_tables is None:
        custom_tables = custom_tables_lib.list_tables_with_data()
```

Add right before the final `return "\n".join(lines)`:

```python
    if custom_tables:
        lines.append("")
        lines.append("Admin-Defined Custom Data Tables:")
        for table in custom_tables:
            columns = ", ".join(c["label"] for c in table["columns"])
            lines.append(f"- {table['label']} ({len(table['rows'])} records) - columns: {columns}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_report_context.py -v`
Expected: PASS.

- [ ] **Step 5: Check for any other test file relying on `build_context`'s old signature**

Run: `grep -rn "build_context(" tests/ server/ | grep -v test_report_context.py`
Expected: any other call sites (e.g. the "Ask AI" route, the Telegram bot's `/ask`) call `build_context()` with no arguments or only positional metrics/supplemental/facilities - all still valid since `custom_tables` is a new optional keyword-only-in-practice parameter with its own safe default. No changes needed there; this step only confirms it.

- [ ] **Step 6: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add server/report_context.py tests/server/test_report_context.py
git commit -m "feat: include admin-defined custom tables in the Ask AI / Telegram bot digest

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Admin routes (`server/routes/admin.py`)

**Files:**
- Modify: `server/routes/admin.py`
- Test: `tests/server/test_custom_data_route.py`

**Interfaces:**
- Consumes: `custom_data.*` (Tasks 2-4), `document_extraction.extract` (existing), `keystore.PROVIDERS`/`keystore.get_key` (existing), `REPORT_BUILD_SCRIPT` (existing module constant).
- Produces: 9 new `/admin/api/custom-data/...` routes - consumed by Task 9 (admin UI JS).

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_custom_data_route.py`:

```python
"""End-to-end /admin/api/custom-data/* tests via FastAPI's TestClient.
The downstream-rebuild subprocess call and every custom_data/ai_client
call are mocked - no real database or AI provider touched in any test
here. Same keyring-mocking pattern as
tests/server/test_bot_facilities_route.py.
"""
import pytest
from fastapi.testclient import TestClient

from server import custom_data, keystore
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


_TABLE = {
    "id": "t1", "label": "Cold Chain", "table_name": "custom_cold_chain",
    "created_at": "2026-08-16T00:00:00+00:00", "report_title": "", "report_narrative": "",
    "report_placement": "", "columns": [{"id": "c1", "custom_table_id": "t1", "label": "Status",
                                          "column_name": "status", "column_type": "text"}],
}


def test_list_custom_tables_requires_authentication(client):
    response = client.get("/admin/api/custom-data/tables")
    assert response.status_code == 401


def test_list_custom_tables_returns_tables(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "list_tables", lambda: [_TABLE])
    response = client.get("/admin/api/custom-data/tables")
    assert response.status_code == 200
    assert response.json() == {"tables": [_TABLE]}


def test_create_custom_table_requires_authentication(client):
    response = client.post("/admin/api/custom-data/tables", json={"label": "X", "columns": []})
    assert response.status_code == 401


def test_create_custom_table_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "create_table", lambda label, columns: _TABLE)
    response = client.post(
        "/admin/api/custom-data/tables",
        json={"label": "Cold Chain", "columns": [{"label": "Status", "type": "text"}]},
    )
    assert response.status_code == 200
    assert response.json() == {"table": _TABLE}


def test_create_custom_table_validation_error_returns_400(client, monkeypatch):
    _login(client)

    def raise_error(label, columns):
        raise custom_data.CustomDataError("At least one column is required")

    monkeypatch.setattr(custom_data, "create_table", raise_error)
    response = client.post("/admin/api/custom-data/tables", json={"label": "Cold Chain", "columns": []})
    assert response.status_code == 400


def test_propose_schema_requires_authentication(client):
    response = client.post("/admin/api/custom-data/propose-schema", json={"provider": "groq", "prompt": "x"})
    assert response.status_code == 401


def test_propose_schema_success(client, monkeypatch, fake_store):
    _login(client)
    fake_store.set_password(keystore.SERVICE_NAME, "groq_key", "sk-test")
    monkeypatch.setattr(custom_data, "propose_schema", lambda provider, key, prompt: {"label": "X", "columns": []})
    response = client.post("/admin/api/custom-data/propose-schema", json={"provider": "groq", "prompt": "track x"})
    assert response.status_code == 200
    assert response.json() == {"proposal": {"label": "X", "columns": []}}


def test_propose_schema_missing_key_returns_400(client, monkeypatch):
    _login(client)
    response = client.post("/admin/api/custom-data/propose-schema", json={"provider": "groq", "prompt": "track x"})
    assert response.status_code == 400


def test_add_column_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "add_column", lambda table_id, label, column_type: _TABLE)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.post("/admin/api/custom-data/tables/t1/columns", json={"label": "Notes", "type": "text"})
    assert response.status_code == 200
    assert response.json() == {"table": _TABLE}


def test_add_column_unknown_table_returns_404(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "add_column", lambda table_id, label, column_type: None)
    response = client.post("/admin/api/custom-data/tables/does-not-exist/columns", json={"label": "Notes", "type": "text"})
    assert response.status_code == 404


def test_delete_column_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "delete_column", lambda table_id, column_id: True)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.delete("/admin/api/custom-data/tables/t1/columns/c1")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_delete_column_unknown_returns_404(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "delete_column", lambda table_id, column_id: False)
    response = client.delete("/admin/api/custom-data/tables/t1/columns/does-not-exist")
    assert response.status_code == 404


def test_list_custom_records_success(client, monkeypatch):
    _login(client)
    fake_rows = [{"id": "r1", "status": "ok"}]
    monkeypatch.setattr(custom_data, "list_records", lambda table_id: fake_rows if table_id == "t1" else None)
    response = client.get("/admin/api/custom-data/tables/t1/records")
    assert response.status_code == 200
    assert response.json() == {"records": fake_rows}


def test_list_custom_records_unknown_table_returns_404(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "list_records", lambda table_id: None)
    response = client.get("/admin/api/custom-data/tables/does-not-exist/records")
    assert response.status_code == 404


def test_delete_custom_record_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "delete_row", lambda table_id, record_id: True)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.delete("/admin/api/custom-data/tables/t1/records/r1")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_delete_custom_record_unknown_returns_404(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "delete_row", lambda table_id, record_id: False)
    response = client.delete("/admin/api/custom-data/tables/t1/records/does-not-exist")
    assert response.status_code == 404


def test_delete_custom_table_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "delete_table", lambda table_id: True)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.delete("/admin/api/custom-data/tables/t1")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_delete_custom_table_unknown_returns_404(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(custom_data, "delete_table", lambda table_id: False)
    response = client.delete("/admin/api/custom-data/tables/does-not-exist")
    assert response.status_code == 404


def test_add_custom_data_success(client, monkeypatch, fake_store):
    _login(client)
    fake_store.set_password(keystore.SERVICE_NAME, "groq_key", "sk-test")
    monkeypatch.setattr(admin_route.document_extraction, "extract", lambda filename, content: type(
        "Result", (), {"text": "extracted text"}
    )())
    monkeypatch.setattr(custom_data, "add_data", lambda provider, key, table_id, text, instruction: [{"status": "ok"}])
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.post(
        "/admin/api/custom-data/tables/t1/data",
        data={"provider": "groq", "instruction": ""},
        files={"file": ("notes.txt", b"some notes", "text/plain")},
    )
    assert response.status_code == 200
    assert response.json() == {"added": [{"status": "ok"}]}


def test_add_custom_data_unknown_table_returns_404(client, monkeypatch, fake_store):
    _login(client)
    fake_store.set_password(keystore.SERVICE_NAME, "groq_key", "sk-test")
    monkeypatch.setattr(admin_route.document_extraction, "extract", lambda filename, content: type(
        "Result", (), {"text": "extracted text"}
    )())
    monkeypatch.setattr(custom_data, "add_data", lambda provider, key, table_id, text, instruction: None)
    response = client.post(
        "/admin/api/custom-data/tables/does-not-exist/data",
        data={"provider": "groq", "instruction": ""},
        files={"file": ("notes.txt", b"some notes", "text/plain")},
    )
    assert response.status_code == 404


def test_add_custom_data_validation_error_returns_400(client, monkeypatch, fake_store):
    _login(client)
    fake_store.set_password(keystore.SERVICE_NAME, "groq_key", "sk-test")
    monkeypatch.setattr(admin_route.document_extraction, "extract", lambda filename, content: type(
        "Result", (), {"text": "extracted text"}
    )())

    def raise_error(provider, key, table_id, text, instruction):
        raise custom_data.CustomDataError("AI did not find any records to add")

    monkeypatch.setattr(custom_data, "add_data", raise_error)
    response = client.post(
        "/admin/api/custom-data/tables/t1/data",
        data={"provider": "groq", "instruction": ""},
        files={"file": ("notes.txt", b"some notes", "text/plain")},
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_custom_data_route.py -v`
Expected: FAIL (`404 Not Found` for every route - none exist yet).

- [ ] **Step 3: Implement**

Add `custom_data` to the existing `from server import (...)` block in `server/routes/admin.py` (alphabetically, between `bot_facilities` and `db_ingestion`).

Add at the end of `server/routes/admin.py`:

```python
@router.get("/admin/api/custom-data/tables")
def list_custom_tables(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    return JSONResponse({"tables": custom_data.list_tables()})


@router.post("/admin/api/custom-data/tables")
def create_custom_table(
    kp_admin_session: str | None = Cookie(default=None),
    label: str = Body(...),
    columns: list = Body(...),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    try:
        table = custom_data.create_table(label, columns)
    except custom_data.CustomDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse({"table": table})


@router.post("/admin/api/custom-data/propose-schema")
def propose_custom_schema(
    kp_admin_session: str | None = Cookie(default=None),
    provider: str = Body(...),
    prompt: str = Body(...),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    key = keystore.get_key(provider)
    if not key:
        return JSONResponse(
            {"detail": f"No API key configured for {provider} - add one in the admin panel first."},
            status_code=400,
        )
    try:
        proposal = custom_data.propose_schema(provider, key, prompt)
    except custom_data.CustomDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)
    return JSONResponse({"proposal": proposal})


@router.post("/admin/api/custom-data/tables/{table_id}/columns")
def add_custom_column(
    table_id: str,
    kp_admin_session: str | None = Cookie(default=None),
    label: str = Body(...),
    type: str = Body(...),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    try:
        table = custom_data.add_column(table_id, label, type)
    except custom_data.CustomDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    if table is None:
        return JSONResponse({"detail": "No custom table with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"table": table, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"table": table, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"table": table})


@router.delete("/admin/api/custom-data/tables/{table_id}/columns/{column_id}")
def delete_custom_column(table_id: str, column_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    found = custom_data.delete_column(table_id, column_id)
    if not found:
        return JSONResponse({"detail": "No custom table/column with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"deleted": True, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"deleted": True, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"deleted": True})


@router.get("/admin/api/custom-data/tables/{table_id}/records")
def list_custom_records(table_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    records = custom_data.list_records(table_id)
    if records is None:
        return JSONResponse({"detail": "No custom table with that id"}, status_code=404)
    return JSONResponse({"records": records})


@router.post("/admin/api/custom-data/tables/{table_id}/data")
async def add_custom_data(
    table_id: str,
    file: UploadFile = File(...),
    provider: str = Form(...),
    instruction: str = Form(""),
    kp_admin_session: str | None = Cookie(default=None),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    key = keystore.get_key(provider)
    if not key:
        return JSONResponse(
            {"detail": f"No API key configured for {provider} - add one in the admin panel first."},
            status_code=400,
        )

    content_bytes = await file.read()
    try:
        extracted = document_extraction.extract(file.filename or "upload", content_bytes)
    except document_extraction.UnsupportedFormatError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=415)
    except document_extraction.ExtractionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    try:
        added = custom_data.add_data(provider, key, table_id, extracted.text, instruction)
    except custom_data.CustomDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)
    if added is None:
        return JSONResponse({"detail": "No custom table with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"added": added, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"added": added, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"added": added})


@router.delete("/admin/api/custom-data/tables/{table_id}/records/{record_id}")
def delete_custom_record(table_id: str, record_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    found = custom_data.delete_row(table_id, record_id)
    if not found:
        return JSONResponse({"detail": "No custom table/record with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"deleted": True, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"deleted": True, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"deleted": True})


@router.delete("/admin/api/custom-data/tables/{table_id}")
def delete_custom_table(table_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    found = custom_data.delete_table(table_id)
    if not found:
        return JSONResponse({"detail": "No custom table with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"deleted": True, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"deleted": True, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"deleted": True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_custom_data_route.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add server/routes/admin.py tests/server/test_custom_data_route.py
git commit -m "feat: add /admin/api/custom-data/* routes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Admin UI (`server/admin_ui.py`) - Custom Data section

**Files:**
- Modify: `server/admin_ui.py`

**Interfaces:**
- Consumes: the 9 routes from Task 8.
- Produces: nothing consumed elsewhere - this is the final UI-facing task.

No automated tests (matches this project's established precedent - `server/admin_ui.py`'s existing sections, e.g. Bot-Added Facilities/Telegram Bot/Database Ingestion, have no dedicated test file either; their correctness is covered by the route tests they call plus manual verification). Verified manually in Task 10.

- [ ] **Step 1: Add the CSS**

In `server/admin_ui.py`, inside the `ADMIN_CSS` string (near the existing `#bot-facilities-status { display: none; margin-top: 0.5rem; }` line), add:

```css
.custom-table-block { border: 1px solid #ddd; border-radius: 6px; padding: 1rem; margin-top: 1rem; }
.custom-table-block h3 { margin-top: 0; }
.column-chip { display: inline-flex; align-items: center; gap: 0.4rem; border: 1px solid #ccc; border-radius: 999px; padding: 0.15rem 0.6rem; margin: 0.2rem 0.3rem 0.2rem 0; font-size: 0.85rem; }
.column-chip button { border: none; background: none; color: #b00; cursor: pointer; font-weight: bold; padding: 0; }
.new-column-row { display: flex; gap: 0.5rem; margin-bottom: 0.5rem; align-items: center; }
#custom-tables-status { display: none; margin-top: 0.5rem; }
```

- [ ] **Step 2: Add the HTML**

In `server/admin_ui.py`'s `render_admin_panel()`, insert a new `<div class="upload-section">` block right before the existing `<p style="margin-top:1.5rem"><button type="button" class="secondary" id="logout-btn">Log Out</button></p>` line:

```html
<div class="upload-section">
  <h2>Custom Data Tables</h2>
  <p class="hint">Create your own tables for data that doesn't fit anywhere else (equipment tracking, staff records, anything). Populate them the same way as document upload elsewhere on this page - the AI decides how to title, summarize, and place each table's section in the report.</p>
  <h3>Create a new table</h3>
  <label for="new-table-label">Table name</label>
  <input type="text" id="new-table-label" placeholder="e.g. Cold Chain Equipment">
  <div id="new-table-columns"></div>
  <button type="button" class="secondary" id="add-column-row-btn">+ Add Column</button>
  <label for="schema-prompt">Or describe it and let AI propose the columns (optional)</label>
  <textarea id="schema-prompt" rows="2" placeholder="e.g. track cold-chain equipment status per facility"></textarea>
  <label for="schema-provider">AI provider</label>
  <select id="schema-provider">
{provider_options}
  </select>
  <button type="button" class="secondary" id="propose-schema-btn">Propose Schema</button>
  <button type="button" class="primary" id="create-table-btn">Create Table</button>
  <p id="custom-tables-status" class="error"></p>
  <div id="custom-tables-container"></div>
</div>
```

- [ ] **Step 3: Add the JS**

In `server/admin_ui.py`'s `ADMIN_JS` string, add near the existing `refreshBotFacilities()` function:

```javascript
  var COLUMN_TYPES = ["text", "number", "date"];

  function newColumnRow(label, type) {
    var row = document.createElement("div");
    row.className = "new-column-row";
    var labelInput = document.createElement("input");
    labelInput.type = "text";
    labelInput.placeholder = "Column name";
    labelInput.className = "new-column-label";
    labelInput.value = label || "";
    var typeSelect = document.createElement("select");
    typeSelect.className = "new-column-type";
    COLUMN_TYPES.forEach(function (t) {
      var opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      if (t === type) opt.selected = true;
      typeSelect.appendChild(opt);
    });
    var removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "secondary";
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", function () { row.remove(); });
    row.appendChild(labelInput);
    row.appendChild(typeSelect);
    row.appendChild(removeBtn);
    return row;
  }

  function collectNewTableColumns() {
    var rows = document.querySelectorAll("#new-table-columns .new-column-row");
    var columns = [];
    rows.forEach(function (row) {
      var label = row.querySelector(".new-column-label").value.trim();
      var type = row.querySelector(".new-column-type").value;
      if (label) columns.push({ label: label, type: type });
    });
    return columns;
  }

  function renderCustomTableBlock(table) {
    var block = document.createElement("div");
    block.className = "custom-table-block";

    var heading = document.createElement("h3");
    heading.textContent = table.label;
    block.appendChild(heading);

    var deleteTableBtn = document.createElement("button");
    deleteTableBtn.type = "button";
    deleteTableBtn.className = "danger";
    deleteTableBtn.textContent = "Delete Table";
    deleteTableBtn.addEventListener("click", function () {
      if (deleteTableBtn.getAttribute("data-confirming") !== "true") {
        deleteTableBtn.setAttribute("data-confirming", "true");
        deleteTableBtn.textContent = "Confirm delete table?";
        return;
      }
      apiCall("DELETE", "/admin/api/custom-data/tables/" + encodeURIComponent(table.id)).then(function () {
        refreshCustomTables();
      });
    });
    block.appendChild(deleteTableBtn);

    var columnsWrap = document.createElement("div");
    table.columns.forEach(function (col) {
      var chip = document.createElement("span");
      chip.className = "column-chip";
      chip.textContent = col.label + " (" + col.column_type + ") ";
      var removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.textContent = "×";
      removeBtn.addEventListener("click", function () {
        if (removeBtn.getAttribute("data-confirming") !== "true") {
          removeBtn.setAttribute("data-confirming", "true");
          removeBtn.textContent = "confirm?";
          return;
        }
        apiCall(
          "DELETE",
          "/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/columns/" + encodeURIComponent(col.id)
        ).then(function () { refreshCustomTables(); });
      });
      chip.appendChild(removeBtn);
      columnsWrap.appendChild(chip);
    });
    block.appendChild(columnsWrap);

    var addColLabel = document.createElement("input");
    addColLabel.type = "text";
    addColLabel.placeholder = "New column name";
    var addColType = document.createElement("select");
    COLUMN_TYPES.forEach(function (t) {
      var opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      addColType.appendChild(opt);
    });
    var addColBtn = document.createElement("button");
    addColBtn.type = "button";
    addColBtn.className = "secondary";
    addColBtn.textContent = "Add Column";
    addColBtn.addEventListener("click", function () {
      var label = addColLabel.value.trim();
      if (!label) return;
      apiCall("POST", "/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/columns", {
        label: label, type: addColType.value,
      }).then(function () { refreshCustomTables(); });
    });
    block.appendChild(addColLabel);
    block.appendChild(addColType);
    block.appendChild(addColBtn);

    var fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = ".xlsx,.xls,.pdf,.docx,.html,.htm,.txt,.csv";
    var instructionInput = document.createElement("textarea");
    instructionInput.rows = 2;
    instructionInput.placeholder = "Instruction (optional)";
    var providerSelect = document.createElement("select");
    providerSelect.innerHTML = document.getElementById("supplemental-provider").innerHTML;
    var addDataBtn = document.createElement("button");
    addDataBtn.type = "button";
    addDataBtn.className = "primary";
    addDataBtn.textContent = "Add Data";
    var addDataStatus = document.createElement("p");
    addDataStatus.className = "error";
    addDataBtn.addEventListener("click", function () {
      if (!fileInput.files.length) {
        addDataStatus.textContent = "Choose a file first";
        addDataStatus.style.display = "block";
        return;
      }
      var formData = new FormData();
      formData.append("file", fileInput.files[0]);
      formData.append("provider", providerSelect.value);
      formData.append("instruction", instructionInput.value);
      addDataBtn.disabled = true;
      addDataBtn.textContent = "Adding...";
      fetch("/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/data", {
        method: "POST", body: formData,
      })
        .then(function (res) { return res.json().then(function (data) { return { status: res.status, data: data }; }); })
        .then(function (result) {
          addDataBtn.disabled = false;
          addDataBtn.textContent = "Add Data";
          if (result.status === 200) {
            refreshCustomTables();
          } else {
            addDataStatus.textContent = (result.data && result.data.detail) || "Add failed";
            addDataStatus.style.display = "block";
          }
        });
    });
    block.appendChild(fileInput);
    block.appendChild(instructionInput);
    block.appendChild(providerSelect);
    block.appendChild(addDataBtn);
    block.appendChild(addDataStatus);

    var recordsWrap = document.createElement("div");
    recordsWrap.className = "records-table-wrap";
    var recordsTable = document.createElement("table");
    recordsTable.className = "records-table";
    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    table.columns.forEach(function (col) {
      var th = document.createElement("th");
      th.textContent = col.label;
      headRow.appendChild(th);
    });
    headRow.appendChild(document.createElement("th"));
    thead.appendChild(headRow);
    var tbody = document.createElement("tbody");
    tbody.id = "custom-table-tbody-" + table.id;
    recordsTable.appendChild(thead);
    recordsTable.appendChild(tbody);
    recordsWrap.appendChild(recordsTable);
    block.appendChild(recordsWrap);
    var recordsStatus = document.createElement("p");
    recordsStatus.id = "custom-table-status-" + table.id;
    recordsStatus.className = "error";
    block.appendChild(recordsStatus);

    initRecordsTable({
      listUrl: "/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/records",
      deleteUrlPrefix: "/admin/api/custom-data/tables/" + encodeURIComponent(table.id) + "/records/",
      tbodyId: tbody.id,
      statusId: recordsStatus.id,
      columns: table.columns.map(function (col) { return col.column_name; }),
    });

    return block;
  }

  function refreshCustomTables() {
    var container = byId("custom-tables-container");
    if (!container) return;
    apiCall("GET", "/admin/api/custom-data/tables").then(function (result) {
      container.innerHTML = "";
      var tables = (result.data && result.data.tables) || [];
      tables.forEach(function (table) {
        container.appendChild(renderCustomTableBlock(table));
      });
    });
  }
```

(That line copies the already-rendered provider `<option>` list from the existing "Extract Document" section's dropdown, avoiding duplicating the server-side `provider_options` string a second time inside the JS itself.)

Add near the existing `document.addEventListener("click", ...)` block that resets `.delete-record-btn[data-confirming]` (extend it, don't duplicate it), so table-delete and column-delete buttons also reset on an outside click:

```javascript
  document.addEventListener("click", function (evt) {
    document.querySelectorAll('.delete-record-btn[data-confirming="true"]').forEach(function (btn) {
      if (btn !== evt.target) {
        btn.removeAttribute("data-confirming");
        btn.textContent = "Delete";
      }
    });
    document.querySelectorAll('button[data-confirming="true"]').forEach(function (btn) {
      if (btn !== evt.target && btn.textContent.indexOf("Confirm") === 0) {
        btn.removeAttribute("data-confirming");
        btn.textContent = btn.textContent.indexOf("column") !== -1 ? "×" : "Delete Table";
      }
    });
  });
```

Wire up the "Create Table"/"Propose Schema"/"+ Add Column" buttons and initial load, inside the existing `document.addEventListener("DOMContentLoaded", function () { ... })` block (add these lines alongside the existing `refreshBotFacilities();` call):

```javascript
    var addColumnRowBtn = byId("add-column-row-btn");
    if (addColumnRowBtn) {
      addColumnRowBtn.addEventListener("click", function () {
        byId("new-table-columns").appendChild(newColumnRow());
      });
    }

    var proposeSchemaBtn = byId("propose-schema-btn");
    if (proposeSchemaBtn) {
      proposeSchemaBtn.addEventListener("click", function () {
        var prompt = byId("schema-prompt").value.trim();
        var statusEl = byId("custom-tables-status");
        if (!prompt) return;
        proposeSchemaBtn.disabled = true;
        proposeSchemaBtn.textContent = "Proposing...";
        apiCall("POST", "/admin/api/custom-data/propose-schema", {
          provider: byId("schema-provider").value, prompt: prompt,
        }).then(function (result) {
          proposeSchemaBtn.disabled = false;
          proposeSchemaBtn.textContent = "Propose Schema";
          if (result.status !== 200) {
            statusEl.textContent = (result.data && result.data.detail) || "Propose failed";
            statusEl.style.display = "block";
            return;
          }
          var proposal = result.data.proposal;
          byId("new-table-label").value = proposal.label;
          byId("new-table-columns").innerHTML = "";
          proposal.columns.forEach(function (col) {
            byId("new-table-columns").appendChild(newColumnRow(col.label, col.type));
          });
        });
      });
    }

    var createTableBtn = byId("create-table-btn");
    if (createTableBtn) {
      createTableBtn.addEventListener("click", function () {
        var label = byId("new-table-label").value.trim();
        var columns = collectNewTableColumns();
        var statusEl = byId("custom-tables-status");
        if (!label || !columns.length) {
          statusEl.textContent = "A table name and at least one column are required";
          statusEl.style.display = "block";
          return;
        }
        apiCall("POST", "/admin/api/custom-data/tables", { label: label, columns: columns }).then(function (result) {
          if (result.status !== 200) {
            statusEl.textContent = (result.data && result.data.detail) || "Create failed";
            statusEl.style.display = "block";
            return;
          }
          statusEl.style.display = "none";
          byId("new-table-label").value = "";
          byId("new-table-columns").innerHTML = "";
          byId("schema-prompt").value = "";
          refreshCustomTables();
        });
      });
    }

    refreshCustomTables();
```

- [ ] **Step 4: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass (this task has no automated tests of its own - confirms nothing else broke).

- [ ] **Step 5: Commit**

```bash
git add server/admin_ui.py
git commit -m "feat: add Custom Data Tables section to the admin panel UI

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: Full test suite and live manual verification

**Files:** none (verification only).

This feature executes real DDL (`CREATE TABLE`/`ALTER TABLE`/`DROP TABLE`) against the bundled database and makes real AI calls for the first time in this codebase, so per this project's established cadence it needs careful manual verification against the real running server - not just mocks.

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 2: Start the server**

Run: `python -m server` (or double-click `Start Dashboard.bat`). Confirm it starts cleanly (same pattern as every prior manual-verification session - reset the admin password with permission first if it isn't known, restore it byte-for-byte afterward).

- [ ] **Step 3: Create a table via the explicit form**

In the admin panel's new "Custom Data Tables" section, create a table (e.g. "Cold Chain Equipment" with columns Facility/text, Status/text, Last Checked/date). Confirm via `psql` (`\dt`) that a real `custom_cold_chain_equipment` table now exists with the right columns (`\d custom_cold_chain_equipment`), and that a `custom_tables`/`custom_table_columns` registry row was written.

- [ ] **Step 4: Create a table via AI schema proposal**

Type a prompt (e.g. "track staff training completion per facility") into "Or describe it and let AI propose the columns," click "Propose Schema" with a real provider/key, confirm the form fields populate with a sensible proposal, edit one field, then click "Create Table." Confirm the edited version (not the raw AI proposal) is what was actually created.

- [ ] **Step 5: Add data and confirm report placement**

Upload a small real test document to one of the two tables via "Add Data." Confirm the row appears in that table's records list in the admin panel. Confirm via `psql` that the row is really in the dynamic table. Rebuild and open `report/KP_Healthcare_Plan.html` - confirm a new section appears with an AI-chosen title/narrative, placed either as a new section or after a real named anchor (not literally the string "new_section" or an unresolved placeholder). Confirm "Ask AI" (or the Telegram bot's `/ask`, if reachable) can correctly answer a question referencing this new data.

- [ ] **Step 6: Exercise delete at all three levels**

Delete the row (two-step confirm) - confirm the report rebuilds without that data and the DB table is really empty (`SELECT COUNT(*)`). Delete a column - confirm via `psql` (`\d`) that the column is really gone. Delete a whole table - confirm via `psql` (`\dt`) that the table is really gone and its registry rows are cleaned up.

- [ ] **Step 7: Clean up**

Confirm both test tables created in Steps 3-4 are fully deleted (Step 6 covers this - re-verify via `psql \dt` that no stray `custom_*` tables remain, and `SELECT COUNT(*) FROM custom_tables` / `custom_table_columns` both return 0). Confirm `git status`/`git diff` shows no unexpected changes to committed pipeline output (`report/KP_Healthcare_Plan.html` should rebuild back to its exact pre-verification committed state once all test tables are gone - if it doesn't, that's a real bug). Restore the admin password if it was reset. Stop the server.

- [ ] **Step 8: Report findings**

If everything above checks out clean, this task (and the whole plan) is done. If anything looks wrong (a DDL statement fails, a placement anchor doesn't resolve, deleted data doesn't actually revert), that's a real bug to fix with its own test (where automatable) before considering this complete.
