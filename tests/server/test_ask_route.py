"""End-to-end /api/ask tests via FastAPI's TestClient. keyring and
ai_client.ask are both mocked - no real OS keyring entries, network calls,
or API keys.
"""
import pytest
from fastapi.testclient import TestClient

from server import ai_client, keystore, report_context
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


def test_ask_unknown_provider_404s(client):
    response = client.post("/api/ask", json={"provider": "bogus", "question": "Hi?"})
    assert response.status_code == 404


def test_ask_without_configured_key_returns_400(client):
    response = client.post("/api/ask", json={"provider": "anthropic", "question": "Hi?"})
    assert response.status_code == 400
    assert "admin panel" in response.json()["detail"]


def test_ask_success(client, monkeypatch):
    keystore.set_key("anthropic", "sk-ant-real")
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: "Peshawar is largest.")
    # build_context()'s supplemental_records/custom_tables defaults now
    # read the bundled local database - both mocked here so this route
    # test never needs a real running database, matching this project's
    # established discipline.
    monkeypatch.setattr(report_context.supplemental_data, "load_records", lambda: [])
    monkeypatch.setattr(report_context.custom_tables_lib, "list_tables_with_data", lambda: [])
    response = client.post("/api/ask", json={"provider": "anthropic", "question": "Which district is largest?"})
    assert response.status_code == 200
    assert response.json()["answer"] == "Peshawar is largest."


def test_ask_provider_failure_returns_502(client, monkeypatch):
    keystore.set_key("openai", "sk-real")

    def failing_ask(provider, key, question, context):
        raise ai_client.AIProviderError("Provider returned HTTP 401: unauthorized")

    monkeypatch.setattr(ai_client, "ask", failing_ask)
    monkeypatch.setattr(report_context.supplemental_data, "load_records", lambda: [])
    monkeypatch.setattr(report_context.custom_tables_lib, "list_tables_with_data", lambda: [])
    response = client.post("/api/ask", json={"provider": "openai", "question": "Hi?"})
    assert response.status_code == 502
    assert "401" in response.json()["detail"]
