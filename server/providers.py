"""Validates a stored/candidate API key against each AI provider by making
one lightweight "list models" call - confirms the key authenticates,
nothing more. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md section 7.

Every provider-specific function catches broadly and returns (False, "...")
rather than raising - a bad key or a network hiccup is a normal UI state
that test_key()'s caller must always get a tuple back for, never an
exception.
"""
import anthropic
import requests

REQUEST_TIMEOUT_SECONDS = 10


def _test_anthropic(key):
    try:
        client = anthropic.Anthropic(api_key=key)
        models = list(client.models.list())
        return True, f"Authenticated, {len(models)} model(s) available"
    except Exception as exc:
        return False, f"Request failed: {exc}"


def _test_openai_style(url, headers_fn):
    def _test(key):
        try:
            response = requests.get(url, headers=headers_fn(key), timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            return False, f"Request failed: {exc}"
        if response.status_code == 200:
            count = len(response.json().get("data", []))
            return True, f"Authenticated, {count} model(s) available"
        if response.status_code in (401, 403):
            return False, "Authentication failed - check the key"
        return False, f"Unexpected response: HTTP {response.status_code}"
    return _test


def _test_gemini(key):
    url = "https://generativelanguage.googleapis.com/v1/models"
    try:
        response = requests.get(url, params={"key": key}, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return False, f"Request failed: {exc}"
    if response.status_code == 200:
        count = len(response.json().get("models", []))
        return True, f"Authenticated, {count} model(s) available"
    if response.status_code in (400, 401, 403):
        return False, "Authentication failed - check the key"
    return False, f"Unexpected response: HTTP {response.status_code}"


_TESTERS = {
    "anthropic": _test_anthropic,
    "openai": _test_openai_style(
        "https://api.openai.com/v1/models",
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
    "gemini": _test_gemini,
    "grok": _test_openai_style(
        "https://api.x.ai/v1/models",
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
    "groq": _test_openai_style(
        "https://api.groq.com/openai/v1/models",
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
}


def test_key(provider, key):
    tester = _TESTERS.get(provider)
    if tester is None:
        return False, f"Unknown provider: {provider}"
    if not key:
        return False, "No API key provided"
    return tester(key)
