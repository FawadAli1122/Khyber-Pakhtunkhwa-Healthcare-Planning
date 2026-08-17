"""No test here makes a real network call or needs a real API key - every
provider call is mocked.
"""
import requests

from server import providers


class FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data


def test_unknown_provider():
    ok, detail = providers.test_key("bogus", "some-key")
    assert ok is False
    assert "Unknown provider" in detail


def test_empty_key_rejected():
    ok, detail = providers.test_key("anthropic", "")
    assert ok is False
    assert "No API key" in detail


def test_anthropic_success(monkeypatch):
    class FakeModels:
        def list(self):
            return [object(), object(), object()]

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(providers.anthropic, "Anthropic", FakeClient)
    ok, detail = providers.test_key("anthropic", "sk-ant-real")
    assert ok is True
    assert "3 model" in detail


def test_anthropic_error_reported_not_raised(monkeypatch):
    class FakeClient:
        def __init__(self, api_key):
            raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(providers.anthropic, "Anthropic", FakeClient)
    ok, detail = providers.test_key("anthropic", "sk-ant-bad")
    assert ok is False
    assert "Request failed" in detail


def test_openai_success(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer sk-real"
        return FakeResponse(200, {"data": [{"id": "gpt-x"}, {"id": "gpt-y"}]})

    monkeypatch.setattr(providers.requests, "get", fake_get)
    ok, detail = providers.test_key("openai", "sk-real")
    assert ok is True
    assert "2 model" in detail


def test_openai_unauthorized(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse(401, {})

    monkeypatch.setattr(providers.requests, "get", fake_get)
    ok, detail = providers.test_key("openai", "sk-bad")
    assert ok is False
    assert "Authentication failed" in detail


def test_groq_and_grok_use_openai_shape(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert "Bearer" in headers["Authorization"]
        return FakeResponse(200, {"data": []})

    monkeypatch.setattr(providers.requests, "get", fake_get)
    for provider in ("groq", "grok"):
        ok, _detail = providers.test_key(provider, "key-123")
        assert ok is True


def test_gemini_success(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        assert params == {"key": "goog-real"}
        return FakeResponse(200, {"models": [{"name": "gemini-x"}]})

    monkeypatch.setattr(providers.requests, "get", fake_get)
    ok, detail = providers.test_key("gemini", "goog-real")
    assert ok is True
    assert "1 model" in detail


def test_gemini_bad_key(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse(400, {})

    monkeypatch.setattr(providers.requests, "get", fake_get)
    ok, _detail = providers.test_key("gemini", "goog-bad")
    assert ok is False


def test_network_error_reported_not_raised(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise requests.ConnectionError("no network")

    monkeypatch.setattr(providers.requests, "get", fake_get)
    ok, detail = providers.test_key("openai", "sk-real")
    assert ok is False
    assert "Request failed" in detail
