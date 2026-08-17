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
