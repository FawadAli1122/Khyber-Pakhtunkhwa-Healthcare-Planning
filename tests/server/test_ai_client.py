"""No test here makes a real network call or needs a real API key - every
provider call is mocked.
"""
import pytest
import requests

from server import ai_client


class FakeResponse:
    def __init__(self, status_code, json_data, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text or str(json_data)

    def json(self):
        return self._json_data


def test_unknown_provider():
    with pytest.raises(ai_client.AIProviderError, match="Unknown provider"):
        ai_client.ask("bogus", "key", "question?", "context")


def test_empty_key_rejected():
    with pytest.raises(ai_client.AIProviderError, match="No API key"):
        ai_client.ask("anthropic", "", "question?", "context")


def test_empty_question_rejected():
    with pytest.raises(ai_client.AIProviderError, match="Question must not be empty"):
        ai_client.ask("anthropic", "sk-ant-real", "  ", "context")


def test_anthropic_success(monkeypatch):
    class FakeBlock:
        type = "text"
        text = "Peshawar has the highest population."

    class FakeMessage:
        content = [FakeBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            assert kwargs["model"] == ai_client.MODELS["anthropic"]
            assert "context digest" in kwargs["messages"][0]["content"]
            return FakeMessage()

    class FakeClient:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setattr(ai_client.anthropic, "Anthropic", FakeClient)
    answer = ai_client.ask("anthropic", "sk-ant-real", "Which district?", "context digest")
    assert answer == "Peshawar has the highest population."


def test_anthropic_failure_raises_ai_provider_error(monkeypatch):
    class FakeClient:
        def __init__(self, api_key):
            raise RuntimeError("boom")

    monkeypatch.setattr(ai_client.anthropic, "Anthropic", FakeClient)
    with pytest.raises(ai_client.AIProviderError, match="Claude request failed"):
        ai_client.ask("anthropic", "sk-ant-bad", "Which district?", "context")


def test_openai_success(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        assert headers["Authorization"] == "Bearer sk-real"
        assert json["model"] == ai_client.MODELS["openai"]
        return FakeResponse(200, {"choices": [{"message": {"content": "An answer."}}]})

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    answer = ai_client.ask("openai", "sk-real", "Question?", "context")
    assert answer == "An answer."


def test_openai_http_error_raises(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(401, {}, text="unauthorized")

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    with pytest.raises(ai_client.AIProviderError, match="HTTP 401"):
        ai_client.ask("openai", "sk-bad", "Question?", "context")


def test_groq_and_grok_use_openai_shape(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        assert "Bearer" in headers["Authorization"]
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    for provider in ("groq", "grok"):
        answer = ai_client.ask(provider, "key-123", "Question?", "context")
        assert answer == "ok"


def test_gemini_success(monkeypatch):
    def fake_post(url, params=None, json=None, timeout=None):
        assert params == {"key": "goog-real"}
        return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "Gemini answer."}]}}]})

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    answer = ai_client.ask("gemini", "goog-real", "Question?", "context")
    assert answer == "Gemini answer."


def test_gemini_http_error_raises(monkeypatch):
    def fake_post(url, params=None, json=None, timeout=None):
        return FakeResponse(400, {}, text="bad request")

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    with pytest.raises(ai_client.AIProviderError, match="HTTP 400"):
        ai_client.ask("gemini", "goog-bad", "Question?", "context")


def test_network_error_raises(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        raise requests.ConnectionError("no network")

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    with pytest.raises(ai_client.AIProviderError, match="Request failed"):
        ai_client.ask("openai", "sk-real", "Question?", "context")


def test_unexpected_response_shape_raises(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(200, {"unexpected": "shape"})

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    with pytest.raises(ai_client.AIProviderError, match="Unexpected response shape"):
        ai_client.ask("openai", "sk-real", "Question?", "context")
