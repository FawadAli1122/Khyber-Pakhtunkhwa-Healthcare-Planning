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
