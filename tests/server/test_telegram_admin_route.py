"""End-to-end /admin/api/telegram/config tests via FastAPI's TestClient.
telegram_bot.start_bot_task/stop_bot_task are mocked - no real bot
started in any test here. keyring is mocked too, same pattern as
tests/server/test_supplemental_data_route.py.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from server import keystore
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


def test_get_telegram_config_requires_authentication(client):
    response = client.get("/admin/api/telegram/config")
    assert response.status_code == 401


def test_get_telegram_config_reports_not_configured(client):
    _login(client)
    response = client.get("/admin/api/telegram/config")
    assert response.status_code == 200
    assert response.json() == {"configured": False}


def test_save_telegram_config_starts_the_bot(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(admin_route.telegram_bot, "stop_bot_task", AsyncMock())
    monkeypatch.setattr(admin_route.telegram_bot, "start_bot_task", AsyncMock(return_value=True))
    response = client.post(
        "/admin/api/telegram/config",
        json={"token": "123:ABC", "allowed_user_id": "987654321"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert keystore.get_telegram_config() == {"token": "123:ABC", "allowed_user_id": "987654321"}


def test_save_telegram_config_reports_bot_start_failure(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(admin_route.telegram_bot, "stop_bot_task", AsyncMock())
    monkeypatch.setattr(admin_route.telegram_bot, "start_bot_task", AsyncMock(return_value=False))
    response = client.post(
        "/admin/api/telegram/config",
        json={"token": "bad-token", "allowed_user_id": "987654321"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "bot_warning" in body


def test_delete_telegram_config_clears_it_and_stops_the_bot(client, monkeypatch):
    _login(client)
    keystore.set_telegram_config({"token": "123:ABC", "allowed_user_id": "987654321"})
    stop_mock = AsyncMock()
    monkeypatch.setattr(admin_route.telegram_bot, "stop_bot_task", stop_mock)
    response = client.delete("/admin/api/telegram/config")
    assert response.status_code == 200
    assert keystore.get_telegram_config() is None
    stop_mock.assert_awaited_once()
