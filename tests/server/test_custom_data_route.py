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
    keystore.set_key("groq", "sk-test")
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


def test_preview_custom_data_success(client, monkeypatch, fake_store):
    _login(client)
    keystore.set_key("groq", "sk-test")
    monkeypatch.setattr(admin_route.document_extraction, "extract", lambda filename, content: type(
        "Result", (), {"text": "extracted text"}
    )())
    monkeypatch.setattr(
        custom_data, "preview_extraction",
        lambda provider, key, table_id, text, instruction: [{"status": "ok"}],
    )
    response = client.post(
        "/admin/api/custom-data/tables/t1/preview",
        data={"provider": "groq", "instruction": ""},
        files={"file": ("notes.txt", b"some notes", "text/plain")},
    )
    assert response.status_code == 200
    assert response.json() == {"rows": [{"status": "ok"}]}


def test_preview_custom_data_unknown_table_returns_404(client, monkeypatch, fake_store):
    _login(client)
    keystore.set_key("groq", "sk-test")
    monkeypatch.setattr(admin_route.document_extraction, "extract", lambda filename, content: type(
        "Result", (), {"text": "extracted text"}
    )())
    monkeypatch.setattr(custom_data, "preview_extraction", lambda provider, key, table_id, text, instruction: None)
    response = client.post(
        "/admin/api/custom-data/tables/does-not-exist/preview",
        data={"provider": "groq", "instruction": ""},
        files={"file": ("notes.txt", b"some notes", "text/plain")},
    )
    assert response.status_code == 404


def test_preview_custom_data_validation_error_returns_400(client, monkeypatch, fake_store):
    _login(client)
    keystore.set_key("groq", "sk-test")
    monkeypatch.setattr(admin_route.document_extraction, "extract", lambda filename, content: type(
        "Result", (), {"text": "extracted text"}
    )())

    def raise_error(provider, key, table_id, text, instruction):
        raise custom_data.CustomDataError("AI did not find any records to add")

    monkeypatch.setattr(custom_data, "preview_extraction", raise_error)
    response = client.post(
        "/admin/api/custom-data/tables/t1/preview",
        data={"provider": "groq", "instruction": ""},
        files={"file": ("notes.txt", b"some notes", "text/plain")},
    )
    assert response.status_code == 400


def test_add_custom_rows_success(client, monkeypatch, fake_store):
    _login(client)
    keystore.set_key("groq", "sk-test")
    monkeypatch.setattr(
        custom_data, "add_rows",
        lambda table_id, rows, provider, key: [{"status": "ok"}],
    )
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.post(
        "/admin/api/custom-data/tables/t1/records",
        json={"provider": "groq", "rows": [{"status": "working"}]},
    )
    assert response.status_code == 200
    assert response.json() == {"added": [{"status": "ok"}]}


def test_add_custom_rows_unknown_table_returns_404(client, monkeypatch, fake_store):
    _login(client)
    keystore.set_key("groq", "sk-test")
    monkeypatch.setattr(custom_data, "add_rows", lambda table_id, rows, provider, key: None)
    response = client.post(
        "/admin/api/custom-data/tables/does-not-exist/records",
        json={"provider": "groq", "rows": [{"status": "working"}]},
    )
    assert response.status_code == 404


def test_add_custom_rows_validation_error_returns_400(client, monkeypatch, fake_store):
    _login(client)
    keystore.set_key("groq", "sk-test")

    def raise_error(table_id, rows, provider, key):
        raise custom_data.CustomDataError("No rows to add")

    monkeypatch.setattr(custom_data, "add_rows", raise_error)
    response = client.post(
        "/admin/api/custom-data/tables/t1/records",
        json={"provider": "groq", "rows": []},
    )
    assert response.status_code == 400
