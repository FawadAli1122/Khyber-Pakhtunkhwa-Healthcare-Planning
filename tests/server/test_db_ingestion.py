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
