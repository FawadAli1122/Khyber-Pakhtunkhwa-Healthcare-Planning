"""End-to-end /admin/api/supplemental-data tests via FastAPI's TestClient.
document_extraction.extract, supplemental_data.add_from_document, and the
report-rebuild subprocess call are all mocked - no real file parsing, AI
provider call, or report-build script run. keyring is mocked too, same
pattern as tests/server/test_routes.py.
"""
import io
import subprocess

import pytest
from fastapi.testclient import TestClient

from server import document_extraction, keystore, supplemental_data
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


def _upload(client, filename="data.xlsx"):
    return client.post(
        "/admin/api/supplemental-data",
        files={"file": (filename, io.BytesIO(b"x"), "application/octet-stream")},
        data={"provider": "anthropic", "instruction": "test instruction"},
    )


def test_supplemental_data_requires_authentication(client):
    response = _upload(client)
    assert response.status_code == 401


def test_supplemental_data_success(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="data.xlsx", format="xlsx", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)
    fake_added = [{"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
                   "label": "MRI Machine", "detail": "1 unit", "source_document": "data.xlsx",
                   "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data, "add_from_document", lambda *args, **kwargs: fake_added)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())

    response = _upload(client)
    assert response.status_code == 200
    assert response.json() == {"added": fake_added}


def test_supplemental_data_without_configured_key_returns_400(client):
    _login(client)
    response = _upload(client)
    assert response.status_code == 400


def test_supplemental_data_unsupported_format_returns_415(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")

    def failing_extract(filename, content_bytes):
        raise document_extraction.UnsupportedFormatError("Unsupported file type: .zip")

    monkeypatch.setattr(document_extraction, "extract", failing_extract)
    response = _upload(client, filename="data.zip")
    assert response.status_code == 415


def test_supplemental_data_validation_failure_returns_400(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="data.xlsx", format="xlsx", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)

    def failing_add(*args, **kwargs):
        raise supplemental_data.SupplementalDataError("AI response was not valid JSON")

    monkeypatch.setattr(supplemental_data, "add_from_document", failing_add)
    response = _upload(client)
    assert response.status_code == 400


def test_supplemental_data_rebuild_failure_still_returns_added_records(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="data.xlsx", format="xlsx", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)
    fake_added = [{"district": "Peshawar", "facility": "", "category": "equipment", "label": "X-ray",
                   "detail": "", "source_document": "data.xlsx", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data, "add_from_document", lambda *args, **kwargs: fake_added)

    class FailedProcess:
        returncode = 1
        stderr = "Traceback: something broke"

    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FailedProcess())

    response = _upload(client)
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == fake_added
    assert "rebuild_warning" in body


def test_list_supplemental_records_requires_authentication(client):
    response = client.get("/admin/api/supplemental-data/records")
    assert response.status_code == 401


def test_list_supplemental_records_returns_records(client, monkeypatch):
    _login(client)
    fake_records = [{"id": "aaa111", "district": "Peshawar", "facility": "", "category": "equipment",
                      "label": "X-ray", "detail": "", "source_document": "a.pdf",
                      "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data, "load_records", lambda: fake_records)
    response = client.get("/admin/api/supplemental-data/records")
    assert response.status_code == 200
    assert response.json() == {"records": fake_records}


def test_delete_supplemental_record_requires_authentication(client):
    response = client.delete("/admin/api/supplemental-data/records/aaa111")
    assert response.status_code == 401


def test_delete_supplemental_record_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(supplemental_data, "delete_record", lambda record_id, path=None: True)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.delete("/admin/api/supplemental-data/records/aaa111")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_delete_supplemental_record_unknown_id_returns_404(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(supplemental_data, "delete_record", lambda record_id, path=None: False)
    response = client.delete("/admin/api/supplemental-data/records/does-not-exist")
    assert response.status_code == 404


def test_delete_supplemental_record_rebuild_failure_still_returns_deleted(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(supplemental_data, "delete_record", lambda record_id, path=None: True)

    class FailedProcess:
        returncode = 1
        stderr = "Traceback: something broke"

    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FailedProcess())
    response = client.delete("/admin/api/supplemental-data/records/aaa111")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert "rebuild_warning" in body


def test_supplemental_data_rebuild_timeout_still_returns_added_records(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="data.xlsx", format="xlsx", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)
    fake_added = [{"district": "Peshawar", "facility": "", "category": "equipment", "label": "X-ray",
                   "detail": "", "source_document": "data.xlsx", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data, "add_from_document", lambda *args, **kwargs: fake_added)

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["python", "14_build_html_report.py"], timeout=300)

    monkeypatch.setattr(admin_route.subprocess, "run", raise_timeout)

    response = _upload(client)
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == fake_added
    assert "rebuild_warning" in body
    assert "timed out" in body["rebuild_warning"].lower()
