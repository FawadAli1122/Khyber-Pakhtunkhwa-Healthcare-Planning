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
