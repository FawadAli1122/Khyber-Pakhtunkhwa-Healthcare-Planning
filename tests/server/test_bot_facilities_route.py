"""End-to-end /admin/api/bot-facilities/records tests via FastAPI's
TestClient. The downstream-rebuild subprocess call is mocked - no real
pipeline run in any test here. Same keyring-mocking pattern as
tests/server/test_supplemental_data_route.py.
"""
import pytest
from fastapi.testclient import TestClient

from server import bot_facilities, keystore
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


def test_list_bot_facilities_requires_authentication(client):
    response = client.get("/admin/api/bot-facilities/records")
    assert response.status_code == 401


def test_list_bot_facilities_returns_records(client, monkeypatch):
    _login(client)
    fake_records = [{"id": "aaa111", "name": "Field Clinic", "district": "Peshawar",
                      "lat": "34.0", "lon": "71.5", "category": "Clinic",
                      "added_at": "2026-08-16T00:00:00+00:00", "added_by": "555"}]
    monkeypatch.setattr(bot_facilities, "load_records", lambda: fake_records)
    response = client.get("/admin/api/bot-facilities/records")
    assert response.status_code == 200
    assert response.json() == {"records": fake_records}


def test_delete_bot_facility_requires_authentication(client):
    response = client.delete("/admin/api/bot-facilities/records/aaa111")
    assert response.status_code == 401


def test_delete_bot_facility_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(bot_facilities, "delete_record", lambda record_id, path=None: True)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.delete("/admin/api/bot-facilities/records/aaa111")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_delete_bot_facility_unknown_id_returns_404(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(bot_facilities, "delete_record", lambda record_id, path=None: False)
    response = client.delete("/admin/api/bot-facilities/records/does-not-exist")
    assert response.status_code == 404


def test_delete_bot_facility_rebuild_failure_still_returns_deleted(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(bot_facilities, "delete_record", lambda record_id, path=None: True)

    class FailedProcess:
        returncode = 1
        stderr = "Traceback: something broke"

    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FailedProcess())
    response = client.delete("/admin/api/bot-facilities/records/aaa111")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert "rebuild_warning" in body
