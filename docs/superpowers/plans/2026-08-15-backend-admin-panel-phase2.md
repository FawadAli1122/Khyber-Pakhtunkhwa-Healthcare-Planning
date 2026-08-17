# Backend + Admin Panel (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local FastAPI server that serves the phase-1 dashboard as-is at `/`, and a password-gated `/admin` panel where the user stores and validates API keys for five AI providers (Claude, OpenAI, Gemini, Grok, Groq) in OS-level secret storage — never in a file or client-visible HTML.

**Architecture:** New top-level `server/` package (FastAPI + `uvicorn`, binds `127.0.0.1` only), independent of `scripts/` (the pipeline is untouched). `keystore.py` wraps the `keyring` package for both provider keys and admin auth secrets. `auth.py` handles password hashing (stdlib PBKDF2) and signed session cookies (stdlib HMAC) — no new crypto dependency beyond `keyring` itself. `providers.py` validates each stored/candidate key with one lightweight "list models" call per provider. `admin_ui.py` renders the setup/login/panel pages as server-side HTML + a small vanilla-JS layer, styled with the same teal-ink/burnt-ochre palette as the phase-1 dashboard.

**Tech Stack:** Python 3.12, FastAPI, `uvicorn`, `keyring`, official `anthropic` SDK (Claude calls only, per this project's tooling conventions), `requests` (already a dependency, used for the other four providers), `httpx` (test-only, required by FastAPI's `TestClient`), `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md`

## Global Constraints

- New dependencies: `fastapi`, `uvicorn`, `keyring`, `anthropic`, `httpx` (test-only). All installed in Task 1 before any other task.
- Server binds `127.0.0.1` only — never `0.0.0.0`. Set this explicitly wherever the host is configured; never rely on a library default.
- No provider API call may be made without a Claude call going through the official `anthropic` SDK — never raw HTTP for Anthropic, per this project's established tooling conventions. The other four providers use `requests` (already a project dependency).
- Every provider/keyring/HTTP call in every test is mocked. No test may require a real API key, a real OS keyring entry, or network access.
- No CSRF protection in this phase — explicit, documented deferral (see spec §6): a same-origin, cookie-authenticated, localhost-only single-user tool doesn't meet the CSRF threat model. Do not add it speculatively; do not treat its absence as a bug to fix.
- Full API keys must never appear in any HTTP response after being saved — only `keystore.mask()`'s last-4-characters hint. Enforce this by construction (routes read `configured`/`hint` from `keystore.list_status()`, never the raw key) rather than by redacting at the last moment.
- Follow the existing `scripts/lib/` string-constant convention (see `scripts/lib/dashboard_assets.py`) for `admin_ui.py`'s CSS/JS: plain (non-f) strings, spliced into f-strings via `{ADMIN_CSS}`/`{ADMIN_JS}` placeholders, never typed directly inside an f-string literal.

---

### Task 1: Project setup — dependencies and package skeleton

**Files:**
- Create: `server/__init__.py`
- Create: `server/routes/__init__.py`
- Create: `tests/server/__init__.py`

**Interfaces:**
- Produces: an importable `server` package and `server.routes` subpackage (both empty at this point — later tasks fill them in), and confirmation that `fastapi`, `uvicorn`, `keyring`, `anthropic`, `httpx` are installed and importable.
- Consumes: nothing.

- [ ] **Step 1: Install the new dependencies**

Run:

```bash
pip install fastapi uvicorn keyring anthropic httpx
```

Expected: all five install without error.

- [ ] **Step 2: Create the package skeleton**

Create `server/__init__.py` (empty file):

```python
```

Create `server/routes/__init__.py` (empty file):

```python
```

Create `tests/server/__init__.py` (empty file):

```python
```

- [ ] **Step 3: Verify every new dependency imports**

Run:

```bash
python -c "import fastapi, uvicorn, keyring, anthropic, httpx; print('OK')"
```

Expected: prints `OK` with no `ImportError`.

- [ ] **Step 4: Commit**

```bash
git add server/__init__.py server/routes/__init__.py tests/server/__init__.py
git commit -m "chore: scaffold server package, install phase-2 dependencies

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `server/keystore.py` — OS-keyring-backed key storage

**Files:**
- Create: `server/keystore.py`
- Test: `tests/server/test_keystore.py`

**Interfaces:**
- Produces: `keystore.PROVIDERS` (tuple of 5 provider ids: `"anthropic"`, `"openai"`, `"gemini"`, `"grok"`, `"groq"`); `keystore.get_key(provider) -> str | None`; `keystore.set_key(provider, value)`; `keystore.delete_key(provider)` (no-op if absent); `keystore.mask(value) -> str | None`; `keystore.list_status() -> list[dict]` (each `{"provider": str, "configured": bool, "hint": str | None}`); `keystore.get_admin_password_hash() -> str | None`; `keystore.set_admin_password_hash(value)`; `keystore.get_session_secret() -> str | None`; `keystore.set_session_secret(value)`. All provider-facing functions (`get_key`/`set_key`/`delete_key`) raise `ValueError` for a provider not in `PROVIDERS`.
- Consumes: the `keyring` package's `get_password`/`set_password`/`delete_password` module-level functions, referenced as `keystore.keyring.*` so tests can monkeypatch them directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_keystore.py`:

```python
import keyring.errors
import pytest

from server import keystore


class FakeStore:
    def __init__(self):
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        key = (service, username)
        if key not in self.data:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.data[key]


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(keystore.keyring, "get_password", store.get_password)
    monkeypatch.setattr(keystore.keyring, "set_password", store.set_password)
    monkeypatch.setattr(keystore.keyring, "delete_password", store.delete_password)
    return store


def test_set_and_get_key(fake_store):
    keystore.set_key("anthropic", "sk-ant-abc123")
    assert keystore.get_key("anthropic") == "sk-ant-abc123"


def test_get_key_missing_returns_none(fake_store):
    assert keystore.get_key("openai") is None


def test_delete_key_removes_it(fake_store):
    keystore.set_key("groq", "gsk-xyz")
    keystore.delete_key("groq")
    assert keystore.get_key("groq") is None


def test_delete_key_missing_is_a_noop(fake_store):
    keystore.delete_key("gemini")  # must not raise


def test_unknown_provider_raises(fake_store):
    with pytest.raises(ValueError):
        keystore.get_key("bogus")
    with pytest.raises(ValueError):
        keystore.set_key("bogus", "x")
    with pytest.raises(ValueError):
        keystore.delete_key("bogus")


def test_mask_short_value():
    assert keystore.mask("abcd") == "****"


def test_mask_long_value():
    assert keystore.mask("sk-ant-api03-abcdefgh1234") == "*" * 22 + "1234"


def test_mask_none():
    assert keystore.mask(None) is None


def test_list_status_reports_configured_and_hint(fake_store):
    keystore.set_key("anthropic", "sk-ant-abcd1234")
    statuses = keystore.list_status()
    by_provider = {s["provider"]: s for s in statuses}
    assert by_provider["anthropic"]["configured"] is True
    assert by_provider["anthropic"]["hint"] == "*" * (len("sk-ant-abcd1234") - 4) + "1234"
    assert by_provider["openai"]["configured"] is False
    assert by_provider["openai"]["hint"] is None
    assert {s["provider"] for s in statuses} == set(keystore.PROVIDERS)


def test_admin_password_hash_roundtrip(fake_store):
    assert keystore.get_admin_password_hash() is None
    keystore.set_admin_password_hash("salt$digest")
    assert keystore.get_admin_password_hash() == "salt$digest"


def test_session_secret_roundtrip(fake_store):
    assert keystore.get_session_secret() is None
    keystore.set_session_secret("deadbeef")
    assert keystore.get_session_secret() == "deadbeef"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_keystore.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.keystore'`

- [ ] **Step 3: Implement `server/keystore.py`**

```python
"""Wraps the `keyring` package for storing AI-provider API keys and admin
auth secrets in OS-level secret storage (Windows Credential Manager on this
platform) - never in a plaintext file. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md section 5.
"""
import keyring

SERVICE_NAME = "kp-healthcare-plan"

PROVIDERS = ("anthropic", "openai", "gemini", "grok", "groq")

# Reserved keyring usernames for admin auth secrets - never listed as AI
# providers, and never accepted by get_key/set_key/delete_key.
ADMIN_PASSWORD_KEY = "admin_password_hash"
SESSION_SECRET_KEY = "session_secret"


def _check_provider(provider):
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")


def get_key(provider):
    _check_provider(provider)
    return keyring.get_password(SERVICE_NAME, provider)


def set_key(provider, value):
    _check_provider(provider)
    keyring.set_password(SERVICE_NAME, provider, value)


def delete_key(provider):
    _check_provider(provider)
    try:
        keyring.delete_password(SERVICE_NAME, provider)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent - deleting a non-existent key is a no-op


def mask(value):
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def list_status():
    statuses = []
    for provider in PROVIDERS:
        value = get_key(provider)
        statuses.append({"provider": provider, "configured": bool(value), "hint": mask(value)})
    return statuses


def get_admin_password_hash():
    return keyring.get_password(SERVICE_NAME, ADMIN_PASSWORD_KEY)


def set_admin_password_hash(value):
    keyring.set_password(SERVICE_NAME, ADMIN_PASSWORD_KEY, value)


def get_session_secret():
    return keyring.get_password(SERVICE_NAME, SESSION_SECRET_KEY)


def set_session_secret(value):
    keyring.set_password(SERVICE_NAME, SESSION_SECRET_KEY, value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_keystore.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add server/keystore.py tests/server/test_keystore.py
git commit -m "feat: add keystore module for OS-keyring-backed key storage

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `server/auth.py` — password hashing and session cookies

**Files:**
- Create: `server/auth.py`
- Test: `tests/server/test_auth.py`

**Interfaces:**
- Consumes: `keystore.get_admin_password_hash`/`set_admin_password_hash`/`get_session_secret`/`set_session_secret` (Task 2).
- Produces: `auth.hash_password(password) -> str`; `auth.verify_password(password, stored) -> bool`; `auth.is_admin_password_set() -> bool`; `auth.set_admin_password(password)`; `auth.verify_admin_password(password) -> bool`; `auth.get_session_secret() -> bytes` (generates and persists once via `keystore` if absent); `auth.create_session_cookie(secret, now=None) -> str`; `auth.verify_session_cookie(cookie, secret, now=None) -> bool`; `auth.SESSION_TTL_SECONDS` (int, used by Task 6's route tests).

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_auth.py`:

```python
import pytest

from server import auth, keystore


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


def test_hash_and_verify_password_roundtrip():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_wrong_password():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("wrong password", hashed) is False


def test_hash_password_is_salted_differently_each_time():
    a = auth.hash_password("same password")
    b = auth.hash_password("same password")
    assert a != b


def test_is_admin_password_set_false_initially(fake_store):
    assert auth.is_admin_password_set() is False


def test_set_admin_password_then_is_set(fake_store):
    auth.set_admin_password("hunter2hunter2")
    assert auth.is_admin_password_set() is True


def test_verify_admin_password_correct(fake_store):
    auth.set_admin_password("hunter2hunter2")
    assert auth.verify_admin_password("hunter2hunter2") is True


def test_verify_admin_password_wrong(fake_store):
    auth.set_admin_password("hunter2hunter2")
    assert auth.verify_admin_password("nope") is False


def test_verify_admin_password_when_unset(fake_store):
    assert auth.verify_admin_password("anything") is False


def test_get_session_secret_generated_once_and_stable(fake_store):
    first = auth.get_session_secret()
    second = auth.get_session_secret()
    assert first == second
    assert len(first) == 32
    assert isinstance(first, bytes)


def test_session_cookie_roundtrip_valid():
    secret = b"x" * 32
    cookie = auth.create_session_cookie(secret, now=1000.0)
    assert auth.verify_session_cookie(cookie, secret, now=1000.0 + 60) is True


def test_session_cookie_expired():
    secret = b"x" * 32
    cookie = auth.create_session_cookie(secret, now=1000.0)
    assert auth.verify_session_cookie(cookie, secret, now=1000.0 + auth.SESSION_TTL_SECONDS + 1) is False


def test_session_cookie_wrong_secret_rejected():
    secret = b"x" * 32
    other_secret = b"y" * 32
    cookie = auth.create_session_cookie(secret, now=1000.0)
    assert auth.verify_session_cookie(cookie, other_secret, now=1000.0) is False


def test_session_cookie_tampered_payload_rejected():
    secret = b"x" * 32
    cookie = auth.create_session_cookie(secret, now=1000.0)
    _, _, signature = cookie.partition(".")
    tampered = f"9999999999.{signature}"
    assert auth.verify_session_cookie(tampered, secret, now=1000.0) is False


def test_verify_session_cookie_malformed_input():
    secret = b"x" * 32
    assert auth.verify_session_cookie("", secret) is False
    assert auth.verify_session_cookie("no-dot-here", secret) is False
    assert auth.verify_session_cookie(None, secret) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.auth'`

- [ ] **Step 3: Implement `server/auth.py`**

```python
"""Admin password hashing (stdlib PBKDF2) and signed session cookies (stdlib
HMAC) - see docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md
section 6. No new dependency for either: the only new dependency is the
`keyring`-backed storage in keystore.py.
"""
import hashlib
import hmac
import secrets
import time

from server import keystore

PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16
SESSION_TTL_SECONDS = 60 * 60 * 12  # 12 hours


def hash_password(password):
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    salt_hex, _, digest_hex = stored.partition("$")
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(actual, expected)


def is_admin_password_set():
    return keystore.get_admin_password_hash() is not None


def set_admin_password(password):
    keystore.set_admin_password_hash(hash_password(password))


def verify_admin_password(password):
    stored = keystore.get_admin_password_hash()
    if stored is None:
        return False
    return verify_password(password, stored)


def get_session_secret():
    secret_hex = keystore.get_session_secret()
    if secret_hex is None:
        secret_hex = secrets.token_bytes(32).hex()
        keystore.set_session_secret(secret_hex)
    return bytes.fromhex(secret_hex)


def create_session_cookie(secret, now=None):
    expires_at = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    payload = str(expires_at)
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_session_cookie(cookie, secret, now=None):
    if not cookie or "." not in cookie:
        return False
    payload, _, signature = cookie.partition(".")
    expected_signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return False
    try:
        expires_at = int(payload)
    except ValueError:
        return False
    current_time = now if now is not None else time.time()
    return current_time < expires_at
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_auth.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add server/auth.py tests/server/test_auth.py
git commit -m "feat: add auth module for admin password hashing and session cookies

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `server/providers.py` — per-provider key validation

**Files:**
- Create: `server/providers.py`
- Test: `tests/server/test_providers.py`

**Interfaces:**
- Produces: `providers.test_key(provider, key) -> tuple[bool, str]` — `(ok, detail)`. Never raises: network errors, non-2xx responses, and SDK exceptions are all caught and reported as `(False, "...")`.
- Consumes: `anthropic.Anthropic` (official SDK, Claude only) and `requests.get` (the other four providers) — referenced as `providers.anthropic.Anthropic` / `providers.requests.get` so tests can monkeypatch them directly, same pattern as Task 2/3's `keystore.keyring.*`.

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_providers.py`:

```python
"""No test here makes a real network call or needs a real API key - every
provider call is mocked."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_providers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.providers'`

- [ ] **Step 3: Implement `server/providers.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_providers.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add server/providers.py tests/server/test_providers.py
git commit -m "feat: add providers module for per-provider API key validation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Dashboard route, app factory, entry point

**Files:**
- Create: `server/routes/dashboard.py`
- Create: `server/app.py`
- Create: `server/__main__.py`
- Test: `tests/server/test_dashboard_route.py`

**Interfaces:**
- Produces: `server.app.create_app() -> FastAPI` and `server.app.app` (the created instance); `server.routes.dashboard.router` (an `APIRouter` with a single `GET /` route). `python -m server` starts the server on `127.0.0.1:8420`.
- Consumes: `report/KP_Healthcare_Plan.html` (read fresh on every request, per spec §4).

- [ ] **Step 1: Write the failing test**

Create `tests/server/test_dashboard_route.py`:

```python
from fastapi.testclient import TestClient

from server.app import create_app


def test_dashboard_route_returns_html():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "Khyber Pakhtunkhwa" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_dashboard_route.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.app'` (or `server.routes.dashboard`)

- [ ] **Step 3: Implement `server/routes/dashboard.py`**

```python
"""GET / - serves report/KP_Healthcare_Plan.html as-is, re-read from disk on
every request so a pipeline rebuild is picked up without a server restart.
See docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md
section 4.
"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

REPORT_PATH = Path(__file__).resolve().parent.parent.parent / "report" / "KP_Healthcare_Plan.html"

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def get_dashboard():
    if not REPORT_PATH.exists():
        return HTMLResponse(
            "<h1>Report not built yet</h1>"
            "<p>Run <code>python scripts/14_build_html_report.py</code> first.</p>",
            status_code=503,
        )
    return HTMLResponse(REPORT_PATH.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Implement `server/app.py`**

```python
"""FastAPI application factory. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md.
"""
from fastapi import FastAPI

from server.routes import dashboard


def create_app():
    app = FastAPI(title="KP Healthcare Plan")
    app.include_router(dashboard.router)
    return app


app = create_app()
```

(Note: `admin.router` is added to `create_app()` in Task 6 — keep this
function's shape stable so that addition is a one-line change.)

- [ ] **Step 5: Implement `server/__main__.py`**

```python
"""Run with: python -m server
Starts the local dashboard + admin server on 127.0.0.1 only - never
0.0.0.0 - per docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md section 3.
"""
import uvicorn

HOST = "127.0.0.1"
PORT = 8420


def main():
    uvicorn.run("server.app:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/server/test_dashboard_route.py -v`
Expected: 1 passed

- [ ] **Step 7: Commit**

```bash
git add server/routes/dashboard.py server/app.py server/__main__.py tests/server/test_dashboard_route.py
git commit -m "feat: add dashboard route, app factory, and server entry point

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `server/admin_ui.py` + `server/routes/admin.py` — the admin panel

**Files:**
- Create: `server/admin_ui.py`
- Create: `server/routes/admin.py`
- Modify: `server/app.py`

**Interfaces:**
- Consumes: `keystore` (Task 2), `auth` (Task 3), `providers` (Task 4).
- Produces: `admin_ui.render_setup_page(error=None) -> str`; `admin_ui.render_login_page(error=None) -> str`; `admin_ui.render_admin_panel(statuses) -> str` (`statuses` is `keystore.list_status()`'s return shape); `admin.router` (an `APIRouter` mounted in `server/app.py`); `admin.SESSION_COOKIE_NAME = "kp_admin_session"` (consumed by Task 7's route tests).
- DOM contract for `admin_ui.ADMIN_JS` (consumed by the routes it calls): `.provider-row[data-provider]` wraps one `<input>`, a `.provider-status` span, and `.save-btn`/`.test-btn`/`.delete-btn` buttons per provider; `#logout-btn` triggers `POST /admin/logout`.

- [ ] **Step 1: Implement `server/admin_ui.py`**

```python
"""HTML/CSS/JS for the admin panel - setup, login, and the key-management
panel itself. Same string-constant pattern as scripts/lib/dashboard_assets.py:
plain (non-f) strings for CSS/JS so callers can splice them into an f-string
without escaping braces. Palette matches report/KP_Healthcare_Plan.html's
:root tokens (deep teal-ink ground, burnt-ochre accent) for visual
continuity between the dashboard and the admin panel.
"""
import html

from server.keystore import PROVIDERS

DISPLAY_NAMES = {
    "anthropic": "Claude (Anthropic)",
    "openai": "OpenAI",
    "gemini": "Gemini",
    "grok": "Grok (xAI)",
    "groq": "Groq",
}

ADMIN_CSS = r"""
:root {
  color-scheme: light;
  --ink: #16211f;
  --ink-soft: #48534f;
  --muted: #7c8580;
  --paper: #f3f6f4;
  --panel: #ffffff;
  --line: rgba(22,33,31,0.13);
  --accent: #a85a17;
  --accent-ink: #6e3b0e;
  --accent-2: #2d6e64;
  --danger: #b3392b;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --ink: #eaeeec;
    --ink-soft: #b7c0bb;
    --muted: #8a9490;
    --paper: #111815;
    --panel: #182420;
    --line: rgba(234,238,236,0.14);
    --accent: #dd9247;
    --accent-ink: #f2c692;
    --accent-2: #62b0a2;
    --danger: #e2685a;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--paper);
  color: var(--ink);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}
h1 {
  font-family: Georgia, "Iowan Old Style", "Palatino Linotype", "Noto Serif", serif;
  color: var(--ink);
}
.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 2rem;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 8px 24px rgba(22,33,31,0.08);
}
.panel-card { max-width: 640px; }
label { display: block; font-size: 0.85rem; color: var(--ink-soft); margin-top: 0.75rem; }
input[type="password"], input[type="text"] {
  width: 100%;
  padding: 0.55rem 0.7rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-size: 0.95rem;
  margin: 0.35rem 0 1rem;
}
button {
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  cursor: pointer;
}
button.primary { background: var(--accent); color: #fff; }
button.danger { background: var(--danger); color: #fff; }
button.secondary { background: var(--panel); color: var(--ink); border: 1px solid var(--line); }
.error { color: var(--danger); font-size: 0.85rem; margin-bottom: 0.75rem; }
.provider-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}
.provider-row:last-child { border-bottom: none; }
.provider-name { font-weight: 600; min-width: 140px; }
.provider-hint { color: var(--muted); font-size: 0.85rem; flex: 1; min-width: 120px; }
.provider-status { font-size: 0.8rem; min-width: 90px; }
.provider-status.ok { color: var(--accent-2); }
.provider-status.bad { color: var(--danger); }
.provider-actions { display: flex; gap: 0.4rem; align-items: center; }
.provider-actions input { margin: 0; width: 160px; }
"""

ADMIN_JS = r"""
(function () {
  "use strict";

  function byId(id) { return document.getElementById(id); }

  function apiCall(method, url, body) {
    var options = { method: method };
    if (body !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (res) {
      return res.json().then(function (data) { return { status: res.status, data: data }; });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".provider-row").forEach(function (row) {
      var provider = row.getAttribute("data-provider");
      var input = row.querySelector("input");
      var statusEl = row.querySelector(".provider-status");
      var saveBtn = row.querySelector(".save-btn");
      var deleteBtn = row.querySelector(".delete-btn");
      var testBtn = row.querySelector(".test-btn");

      function setStatus(ok, text) {
        statusEl.textContent = text;
        statusEl.className = "provider-status " + (ok ? "ok" : "bad");
      }

      saveBtn.addEventListener("click", function () {
        var value = input.value.trim();
        if (!value) return;
        apiCall("PUT", "/admin/api/keys/" + provider, { api_key: value }).then(function (result) {
          if (result.status === 200) {
            input.value = "";
            window.location.reload();
          } else {
            setStatus(false, (result.data && result.data.detail) || "Save failed");
          }
        });
      });

      deleteBtn.addEventListener("click", function () {
        apiCall("DELETE", "/admin/api/keys/" + provider).then(function () {
          window.location.reload();
        });
      });

      testBtn.addEventListener("click", function () {
        var value = input.value.trim();
        var body = value ? { api_key: value } : undefined;
        setStatus(true, "Testing...");
        apiCall("POST", "/admin/api/keys/" + provider + "/test", body).then(function (result) {
          setStatus(!!(result.data && result.data.ok), (result.data && result.data.detail) || "Unknown error");
        });
      });
    });

    var logoutBtn = byId("logout-btn");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", function () {
        apiCall("POST", "/admin/logout").then(function () {
          window.location.href = "/admin";
        });
      });
    }
  });
})();
"""


def render_setup_page(error=None):
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Set Up Admin Password</title>
<style>{ADMIN_CSS}</style>
</head>
<body>
<div class="card">
<h1>Set Up Admin Password</h1>
<p>This is a one-time step. This password protects the admin panel where AI provider API keys are stored.</p>
{error_html}
<form method="post" action="/admin/setup">
  <label for="password">Password</label>
  <input type="password" id="password" name="password" required minlength="8" autofocus>
  <label for="confirm">Confirm password</label>
  <input type="password" id="confirm" name="confirm" required minlength="8">
  <button type="submit" class="primary">Set Password</button>
</form>
</div>
</body>
</html>
"""


def render_login_page(error=None):
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Admin Login</title>
<style>{ADMIN_CSS}</style>
</head>
<body>
<div class="card">
<h1>Admin Login</h1>
{error_html}
<form method="post" action="/admin/login">
  <label for="password">Password</label>
  <input type="password" id="password" name="password" required autofocus>
  <button type="submit" class="primary">Log In</button>
</form>
</div>
</body>
</html>
"""


def _provider_row_html(status):
    provider = status["provider"]
    hint = status["hint"] or "not configured"
    display_name = DISPLAY_NAMES.get(provider, provider)
    return f"""<div class="provider-row" data-provider="{html.escape(provider)}">
  <span class="provider-name">{html.escape(display_name)}</span>
  <span class="provider-hint">{html.escape(hint)}</span>
  <span class="provider-status"></span>
  <div class="provider-actions">
    <input type="text" placeholder="Paste API key" autocomplete="off">
    <button type="button" class="primary save-btn">Save</button>
    <button type="button" class="secondary test-btn">Test</button>
    <button type="button" class="danger delete-btn">Delete</button>
  </div>
</div>"""


def render_admin_panel(statuses):
    rows = "\n".join(_provider_row_html(s) for s in statuses)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Provider Keys</title>
<style>{ADMIN_CSS}</style>
</head>
<body>
<div class="card panel-card">
<h1>AI Provider Keys</h1>
<p>Keys are stored in this machine's OS credential store, never in a file or sent to the browser after saving.</p>
{rows}
<p style="margin-top:1.5rem"><button type="button" class="secondary" id="logout-btn">Log Out</button></p>
</div>
<script>{ADMIN_JS}</script>
</body>
</html>
"""
```

- [ ] **Step 2: Implement `server/routes/admin.py`**

```python
"""GET/POST /admin, /admin/setup, /admin/login, /admin/logout, and the
/admin/api/keys* JSON API. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md sections 6 and 8.
"""
from fastapi import APIRouter, Body, Cookie, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from server import admin_ui, auth, keystore, providers

router = APIRouter()

SESSION_COOKIE_NAME = "kp_admin_session"


def _authenticated(session_cookie):
    if not session_cookie:
        return False
    return auth.verify_session_cookie(session_cookie, auth.get_session_secret())


def _require_auth(session_cookie):
    if not _authenticated(session_cookie):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return None


@router.get("/admin", response_class=HTMLResponse)
def admin_home(kp_admin_session: str | None = Cookie(default=None)):
    if not auth.is_admin_password_set():
        return HTMLResponse(admin_ui.render_setup_page())
    if not _authenticated(kp_admin_session):
        return HTMLResponse(admin_ui.render_login_page())
    return HTMLResponse(admin_ui.render_admin_panel(keystore.list_status()))


@router.post("/admin/setup")
def admin_setup(password: str = Form(...), confirm: str = Form(...)):
    if auth.is_admin_password_set():
        return HTMLResponse(admin_ui.render_login_page(), status_code=403)
    if password != confirm:
        return HTMLResponse(admin_ui.render_setup_page(error="Passwords do not match"), status_code=400)
    if len(password) < 8:
        return HTMLResponse(
            admin_ui.render_setup_page(error="Password must be at least 8 characters"), status_code=400
        )
    auth.set_admin_password(password)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/login")
def admin_login(password: str = Form(...)):
    if not auth.verify_admin_password(password):
        return HTMLResponse(admin_ui.render_login_page(error="Incorrect password"), status_code=401)
    cookie_value = auth.create_session_cookie(auth.get_session_secret())
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(SESSION_COOKIE_NAME, cookie_value, httponly=True, samesite="lax")
    return response


@router.post("/admin/logout")
def admin_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/admin/api/keys")
def list_keys(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    return JSONResponse(keystore.list_status())


@router.put("/admin/api/keys/{provider}")
def set_key(
    provider: str,
    kp_admin_session: str | None = Cookie(default=None),
    api_key: str = Body(..., embed=True),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    keystore.set_key(provider, api_key)
    return JSONResponse({"ok": True})


@router.delete("/admin/api/keys/{provider}")
def delete_key(provider: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    keystore.delete_key(provider)
    return JSONResponse({"ok": True})


@router.post("/admin/api/keys/{provider}/test")
async def test_key_route(
    provider: str,
    request: Request,
    kp_admin_session: str | None = Cookie(default=None),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    # Manually inspect the body rather than a FastAPI Body(...) param: the
    # "test the already-saved key" call sends no body at all, and the two
    # cases (candidate key vs. saved key) are simplest handled explicitly.
    body_bytes = await request.body()
    candidate_key = None
    if body_bytes:
        payload = await request.json()
        candidate_key = payload.get("api_key")
    key_to_test = candidate_key or keystore.get_key(provider)
    ok, detail = providers.test_key(provider, key_to_test or "")
    return JSONResponse({"ok": ok, "detail": detail})
```

- [ ] **Step 3: Mount the admin router in `server/app.py`**

Find:

```python
from fastapi import FastAPI

from server.routes import dashboard


def create_app():
    app = FastAPI(title="KP Healthcare Plan")
    app.include_router(dashboard.router)
    return app
```

Replace with:

```python
from fastapi import FastAPI

from server.routes import admin, dashboard


def create_app():
    app = FastAPI(title="KP Healthcare Plan")
    app.include_router(dashboard.router)
    app.include_router(admin.router)
    return app
```

- [ ] **Step 4: Manual smoke check that the app still starts**

Run: `python -c "from server.app import create_app; create_app(); print('OK')"`
Expected: prints `OK` with no import or startup error (full route-behavior testing is Task 7).

- [ ] **Step 5: Commit**

```bash
git add server/admin_ui.py server/routes/admin.py server/app.py
git commit -m "feat: add admin panel UI and routes for key management

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: End-to-end route tests and manual verification

**Files:**
- Create: `tests/server/test_routes.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: the final, verified phase-2 backend.

- [ ] **Step 1: Write `tests/server/test_routes.py`**

```python
"""End-to-end route tests via FastAPI's TestClient. All keyring/provider
calls are mocked - no real OS keyring entries or network calls.
"""
import pytest
from fastapi.testclient import TestClient

from server import keystore, providers
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


def test_dashboard_route_serves_report_or_placeholder(client):
    response = client.get("/")
    assert response.status_code in (200, 503)
    assert "html" in response.text.lower()


def test_admin_shows_setup_when_no_password(client):
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Set Up Admin Password" in response.text


def test_full_setup_login_flow(client):
    setup = client.post("/admin/setup", data=SETUP_FORM)
    assert setup.status_code in (200, 303)

    unauth = client.get("/admin")
    assert "Admin Login" in unauth.text

    login = client.post("/admin/login", data={"password": "hunter2hunter2"})
    assert login.status_code in (200, 303)
    assert "kp_admin_session" in login.cookies

    panel = client.get("/admin")
    assert "AI Provider Keys" in panel.text


def test_setup_rejects_mismatched_passwords(client):
    response = client.post("/admin/setup", data={"password": "aaaaaaaa", "confirm": "bbbbbbbb"})
    assert response.status_code == 400


def test_setup_rejects_short_password(client):
    response = client.post("/admin/setup", data={"password": "short", "confirm": "short"})
    assert response.status_code == 400


def test_second_setup_attempt_rejected(client):
    client.post("/admin/setup", data=SETUP_FORM)
    second = client.post("/admin/setup", data={"password": "different1", "confirm": "different1"})
    assert second.status_code == 403


def test_login_wrong_password_rejected(client):
    client.post("/admin/setup", data=SETUP_FORM)
    response = client.post("/admin/login", data={"password": "wrongwrong"})
    assert response.status_code == 401


def test_api_keys_require_authentication(client):
    response = client.get("/admin/api/keys")
    assert response.status_code == 401


def test_authenticated_key_lifecycle(client, monkeypatch):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})

    listing = client.get("/admin/api/keys")
    assert listing.status_code == 200
    assert {row["provider"] for row in listing.json()} == set(keystore.PROVIDERS)

    saved = client.put("/admin/api/keys/anthropic", json={"api_key": "sk-ant-testtest"})
    assert saved.status_code == 200

    listing2 = client.get("/admin/api/keys")
    anthropic_row = next(r for r in listing2.json() if r["provider"] == "anthropic")
    assert anthropic_row["configured"] is True

    monkeypatch.setattr(providers, "test_key", lambda provider, key: (True, "Authenticated, 1 model(s) available"))
    tested = client.post("/admin/api/keys/anthropic/test")
    assert tested.status_code == 200
    assert tested.json()["ok"] is True

    deleted = client.delete("/admin/api/keys/anthropic")
    assert deleted.status_code == 200
    listing3 = client.get("/admin/api/keys")
    anthropic_row3 = next(r for r in listing3.json() if r["provider"] == "anthropic")
    assert anthropic_row3["configured"] is False


def test_unknown_provider_404s(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    response = client.put("/admin/api/keys/bogus", json={"api_key": "x"})
    assert response.status_code == 404


def test_logout_clears_session(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    client.post("/admin/logout")
    response = client.get("/admin/api/keys")
    assert response.status_code == 401
```

- [ ] **Step 2: Run the new tests**

Run: `pytest tests/server/test_routes.py -v`
Expected: 11 passed. If any assertion fails, fix the corresponding Task 5/6 route or template — do not weaken these assertions to make them pass.

- [ ] **Step 3: Run the full test suite**

Run: `pytest tests/ -v`
Expected: all tests pass, including every pre-existing phase-1 test (47 from phase 1, plus this phase's new tests — expect roughly 47 + 12 + 15 + 11 + 1 + 11 = 97, exact count isn't load-bearing, "all pass" is).

- [ ] **Step 4: Manual browser verification**

Start the server: `python -m server`

Confirm, in a browser pointed at `http://127.0.0.1:8420`:
- `/` renders the same interactive dashboard from phase 1 (choropleth, sortable table, etc. all still work).
- `/admin` shows the one-time setup form on first visit; after setting a password, it shows a login form; after logging in, it shows the panel with all 5 providers listed as "not configured".
- Typing a fake key into any provider's field and clicking **Test** shows a failure status (no real key available) without the page erroring.
- Clicking **Save** with a fake key stores it (page reloads, hint shows masked last-4 characters); **Delete** removes it (reloads, back to "not configured").
- **Log Out** returns to the login form; `/admin` after logout does not show the panel without logging in again.

Stop the server (Ctrl+C) once confirmed. If any of these fail, this is a real bug to fix before committing — do not report success without having actually driven the browser through each step.

- [ ] **Step 5: Final commit**

```bash
git add tests/server/test_routes.py
git commit -m "test: add end-to-end admin panel route tests

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
