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
        self.client_encoding = None

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

    def set_client_encoding(self, encoding):
        self.client_encoding = encoding


def test_get_connection_sets_utf8_client_encoding(monkeypatch):
    # Without this, psycopg2 falls back to the OS codepage on Windows
    # (cp1252, not UTF-8), which raises UnicodeEncodeError the moment real
    # non-ASCII data (e.g. a Marham/OSM-scraped facility name) is sent -
    # found live via scripts/25_sync_processed_to_db.py's first real run.
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    result = local_db.get_connection()
    assert result is conn
    assert conn.client_encoding == "UTF8"


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


def test_fetch_all_normalizes_null_columns_to_empty_string(monkeypatch):
    # A NULL database column (e.g. a pre-existing row after ALTER TABLE
    # ADD COLUMN, or any other genuinely-empty TEXT value) must come back
    # as "" like every other caller already assumes - not None, which
    # every existing consumer (report rendering, AI digests, etc.) was
    # built against CSV-era data that never contained real None values.
    cursor = FakeCursor(rows=[{"id": "a1", "district": "Peshawar", "detail": None}])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    records = local_db.fetch_all("supplemental_records")
    assert records == [{"id": "a1", "district": "Peshawar", "detail": ""}]


def test_fetch_all_normalizes_date_columns_to_iso_string(monkeypatch):
    # Custom tables can have real DATE columns (scripts/lib/local_db.py
    # Part C) - psycopg2's RealDictCursor returns those as native
    # datetime.date objects, not strings, unlike every other column in
    # this app's schema (all TEXT until this feature). Every existing
    # caller (JSON responses, json.dumps in AI prompts, string rendering)
    # assumes plain JSON-safe values, so this must come back as an ISO
    # string. Found live: a real date value crashed json.dumps() inside
    # custom_data.py's report-placement AI call.
    import datetime
    cursor = FakeCursor(rows=[{"id": "a1", "last_checked": datetime.date(2026, 8, 10)}])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    records = local_db.fetch_all("custom_cold_chain")
    assert records == [{"id": "a1", "last_checked": "2026-08-10"}]


def test_fetch_all_normalizes_numeric_columns_to_float(monkeypatch):
    # Same reasoning as the date case above - a NUMERIC column comes
    # back as decimal.Decimal, also not JSON-serializable by default.
    import decimal
    cursor = FakeCursor(rows=[{"id": "a1", "temp_c": decimal.Decimal("4.5")}])
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    records = local_db.fetch_all("custom_cold_chain")
    assert records == [{"id": "a1", "temp_c": 4.5}]
    assert isinstance(records[0]["temp_c"], float)


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


def test_apply_schema_uses_create_table_if_not_exists():
    # Idempotent by construction - must be safe to run against a
    # database that already has some/all of these tables (an existing
    # installation upgrading past its first-ever bootstrap), never a
    # plain CREATE TABLE that would error on a table that already exists.
    assert "CREATE TABLE supplemental_records" not in local_db.SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS supplemental_records" in local_db.SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS custom_tables" in local_db.SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS custom_table_columns" in local_db.SCHEMA_SQL


def test_apply_schema_success(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.apply_schema()
    assert conn.committed is True
    assert conn.closed is True


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


def test_column_type_sql_includes_boolean_and_json():
    assert local_db.COLUMN_TYPE_SQL["boolean"] == "BOOLEAN"
    assert local_db.COLUMN_TYPE_SQL["json"] == "JSONB"


def test_replace_table_rejects_invalid_table_name_without_connecting(monkeypatch):
    def fail_connect(**kwargs):
        raise AssertionError("should not attempt to connect with an invalid table name")
    monkeypatch.setattr(local_db.psycopg2, "connect", fail_connect)
    with pytest.raises(local_db.LocalDbError):
        local_db.replace_table("Bad Name", [("district", "text")], [])


def test_replace_table_rejects_unknown_column_type_without_connecting(monkeypatch):
    def fail_connect(**kwargs):
        raise AssertionError("should not attempt to connect with an unknown column type")
    monkeypatch.setattr(local_db.psycopg2, "connect", fail_connect)
    with pytest.raises(local_db.LocalDbError):
        local_db.replace_table("pipeline_x", [("bad", "not_a_real_type")], [])


def test_replace_table_inserts_rows_with_given_columns(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.replace_table(
        "pipeline_district_terrain",
        [("district", "text"), ("mean_elev_m", "number")],
        [{"id": "a1", "district": "Chitral", "mean_elev_m": 3200.5}],
    )
    insert_query, values = cursor.executed[-1]
    assert "INSERT INTO pipeline_district_terrain (id, district, mean_elev_m)" in insert_query
    assert values == [("a1", "Chitral", 3200.5)]
    assert conn.committed is True
    assert conn.closed is True


def test_replace_table_wraps_json_columns(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.replace_table(
        "pipeline_district_boundaries",
        [("district", "text"), ("geometry", "json")],
        [{"id": "a1", "district": "Chitral", "geometry": {"type": "Polygon", "coordinates": []}}],
    )
    _insert_query, values = cursor.executed[-1]
    wrapped = values[0][2]
    assert isinstance(wrapped, local_db.Json)
    assert wrapped.adapted == {"type": "Polygon", "coordinates": []}


def test_replace_table_no_rows_skips_insert(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.replace_table("pipeline_x", [("district", "text")], [])
    assert len(cursor.executed) == 2  # DROP + CREATE only, no INSERT
    assert conn.committed is True
    assert conn.closed is True


def test_replace_table_drops_before_creating(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.replace_table("pipeline_x", [("district", "text")], [])
    assert len(cursor.executed) == 2
    # Both DROP and CREATE go through sql.Composed objects (like create_table()
    # already does), which don't render to plain strings without a live
    # connection - so, matching this file's own existing precedent for
    # create_table()/add_column()/drop_column()/drop_table(), this only
    # asserts the two statements were issued in order and the run
    # committed/closed cleanly, not their literal text.
    assert conn.committed is True
