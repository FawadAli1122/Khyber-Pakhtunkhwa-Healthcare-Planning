"""End-to-end /admin/api/extract tests via FastAPI's TestClient.
document_extraction.extract is mocked - this file exercises the route's
auth/status-code plumbing only; the real extraction logic is covered by
tests/server/test_document_extraction.py. keyring is mocked too, same
pattern as tests/server/test_routes.py.
"""
import io

import pytest
from fastapi.testclient import TestClient

from server import document_extraction, keystore
from server.app import create_app


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


def test_extract_requires_authentication(client):
    response = client.post(
        "/admin/api/extract",
        files={"file": ("data.xlsx", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert response.status_code == 401


def test_extract_success(client, monkeypatch):
    _login(client)
    fake_result = document_extraction.ExtractionResult(
        filename="data.xlsx", format="xlsx", text="Peshawar | 4750388"
    )
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)
    response = client.post(
        "/admin/api/extract",
        files={"file": ("data.xlsx", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json() == {"filename": "data.xlsx", "format": "xlsx", "text": "Peshawar | 4750388"}


def test_extract_unsupported_format_returns_415(client, monkeypatch):
    _login(client)

    def failing_extract(filename, content_bytes):
        raise document_extraction.UnsupportedFormatError("Unsupported file type: .csv")

    monkeypatch.setattr(document_extraction, "extract", failing_extract)
    response = client.post(
        "/admin/api/extract",
        files={"file": ("data.csv", io.BytesIO(b"x"), "text/csv")},
    )
    assert response.status_code == 415
    assert "csv" in response.json()["detail"]


def test_extract_parse_failure_returns_422(client, monkeypatch):
    _login(client)

    def failing_extract(filename, content_bytes):
        raise document_extraction.ExtractionError("Could not read Excel file: corrupt")

    monkeypatch.setattr(document_extraction, "extract", failing_extract)
    response = client.post(
        "/admin/api/extract",
        files={"file": ("data.xlsx", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert response.status_code == 422
    assert "corrupt" in response.json()["detail"]
