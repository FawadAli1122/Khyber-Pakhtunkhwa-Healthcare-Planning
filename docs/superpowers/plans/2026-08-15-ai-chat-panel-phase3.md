# "Ask AI" Chat Panel (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single-turn "Ask AI" chat panel on the dashboard where the user picks one of the five phase-2-configured providers, asks a question about the healthcare plan, and gets an answer grounded in the plan's actual data — the first place any of the five keys is used to generate a real answer, not just validate itself.

**Architecture:** Three new server modules — `report_context.py` (a compact text digest of `district_metrics.csv`, the AI's grounding context), `ai_client.py` (one `ask(provider, key, question, context)` dispatching to all five providers' real chat/message-generation APIs), `chat_ui.py` (the widget's HTML/CSS/JS, same string-constant pattern as `admin_ui.py`) — plus a `POST /api/ask` route and a serve-time injection of the widget into the dashboard HTML. The phase-1 pipeline's output (`scripts/14_build_html_report.py`) is never modified; `server/routes/dashboard.py` injects the widget only when serving the file, so the static file itself is unchanged.

**Tech Stack:** Python 3.12, official `anthropic` SDK (Claude), `requests` (the other four providers) — both already project dependencies, no new installs this phase — FastAPI, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-15-ai-chat-panel-phase3-design.md`

## Global Constraints

- No new dependencies. `anthropic` and `requests` are already present from phases 1-2.
- Every provider call in every test is mocked — no test may require a real API key or network access, same posture as phases 1 and 2.
- `ai_client.ask(...)` never lets a provider-specific exception escape uncaught — every failure path raises `ai_client.AIProviderError` with a message safe to show the user directly, never a raw traceback.
- `scripts/14_build_html_report.py` and its output are not touched by this plan. The chat widget is injected only at serve-time in `server/routes/dashboard.py`.
- Reuse `keystore.PROVIDERS` as the single source of truth for the five provider ids everywhere (route validation, `chat_ui.PROVIDER_OPTIONS` values, `ai_client.MODELS` keys) — never redeclare the list of five providers a second time.

---

### Task 1: `server/report_context.py` — data digest builder

**Files:**
- Create: `server/report_context.py`
- Test: `tests/server/test_report_context.py`

**Interfaces:**
- Produces: `report_context.load_metrics(path=METRICS_PATH) -> list[dict]` (thin `csv.DictReader` wrapper, matches the shape used elsewhere in this project, e.g. `scripts/14_build_html_report.py`'s `load_data()`); `report_context.build_context(metrics=None) -> str` (loads via `load_metrics()` if `metrics` is omitted — a plain text digest: totals, tier counts, then a per-district table sorted by gap score descending).
- Consumes: `data/processed/district_metrics.csv` (already the source of truth for phase 1's dashboard).

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_report_context.py`:

```python
from server import report_context


def _fixture_metrics():
    return [
        {
            "district": "Alpha", "need_tier": "Critical", "gap_score": "90.0",
            "population_2023": "100000", "beds_per_1000": "0.50",
            "doctors_per_1000": "0.10", "terrain": "plains",
        },
        {
            "district": "Beta", "need_tier": "Low", "gap_score": "10.0",
            "population_2023": "200000", "beds_per_1000": "5.00",
            "doctors_per_1000": "1.00", "terrain": "mountainous",
        },
    ]


def test_build_context_includes_totals():
    context = report_context.build_context(_fixture_metrics())
    assert "Total districts: 2" in context
    assert "300,000" in context  # total population


def test_build_context_includes_tier_counts():
    context = report_context.build_context(_fixture_metrics())
    assert "Critical=1" in context
    assert "Low=1" in context
    assert "High=0" in context
    assert "Moderate=0" in context


def test_build_context_ranks_by_gap_score_descending():
    context = report_context.build_context(_fixture_metrics())
    assert context.index("Alpha") < context.index("Beta")


def test_build_context_includes_district_fields():
    context = report_context.build_context(_fixture_metrics())
    assert "Alpha" in context
    assert "Critical" in context
    assert "100,000" in context


def test_build_context_loads_real_metrics_by_default():
    context = report_context.build_context()
    assert "Total districts: 35" in context
    assert "Peshawar" in context
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_report_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.report_context'`

- [ ] **Step 3: Implement `server/report_context.py`**

```python
"""Builds a compact text digest of the healthcare plan's data - the AI's
grounding context for the "Ask AI" chat panel. Deliberately not the raw
report HTML: markup is wasteful token-wise and invites the model to
comment on styling instead of data. See docs/superpowers/specs/
2026-08-15-ai-chat-panel-phase3-design.md section 3.
"""
import csv
from pathlib import Path

METRICS_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "district_metrics.csv"

TIER_ORDER = ("Critical", "High", "Moderate", "Low")


def load_metrics(path=METRICS_PATH):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_context(metrics=None):
    if metrics is None:
        metrics = load_metrics()

    total_population = sum(int(float(m["population_2023"])) for m in metrics)
    tier_counts = {tier: 0 for tier in TIER_ORDER}
    for m in metrics:
        tier = m["need_tier"]
        if tier in tier_counts:
            tier_counts[tier] += 1

    lines = [
        "Khyber Pakhtunkhwa Healthcare System Planning Report - data digest",
        f"Total districts: {len(metrics)}",
        f"Total population (2023 census): {total_population:,}",
        "Need-tier counts: " + ", ".join(f"{tier}={count}" for tier, count in tier_counts.items()),
        "",
        "Per-district data (sorted by gap score, most underserved first):",
        "district | need_tier | gap_score | population_2023 | beds_per_1000 | doctors_per_1000 | terrain",
    ]
    ranked = sorted(metrics, key=lambda m: float(m["gap_score"]), reverse=True)
    for m in ranked:
        lines.append(
            f"{m['district']} | {m['need_tier']} | {float(m['gap_score']):.1f} | "
            f"{int(float(m['population_2023'])):,} | {float(m['beds_per_1000']):.2f} | "
            f"{float(m['doctors_per_1000']):.2f} | {m['terrain']}"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_report_context.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add server/report_context.py tests/server/test_report_context.py
git commit -m "feat: add report_context module for AI chat grounding data

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `server/ai_client.py` — real per-provider chat calls

**Files:**
- Create: `server/ai_client.py`
- Test: `tests/server/test_ai_client.py`

**Interfaces:**
- Produces: `ai_client.AIProviderError(Exception)`; `ai_client.MODELS` (dict, keys match `keystore.PROVIDERS`); `ai_client.ask(provider, key, question, context) -> str` — raises `AIProviderError` for an unknown provider, an empty key, an empty question, or any provider-call failure (network error, non-2xx response, unexpected response shape, SDK exception).
- Consumes: `anthropic.Anthropic` and `requests.post`, referenced as `ai_client.anthropic.Anthropic` / `ai_client.requests.post` so tests can monkeypatch them directly (same pattern as `providers.py`'s `test_key`).

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_ai_client.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_ai_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.ai_client'`

- [ ] **Step 3: Implement `server/ai_client.py`**

```python
"""Real chat/message-generation calls to all five AI providers, used by the
"Ask AI" chat panel. Unlike phase 2's providers.test_key() (which only
lists models), this makes an actual answer-generating call. See
docs/superpowers/specs/2026-08-15-ai-chat-panel-phase3-design.md
section 4.
"""
import anthropic
import requests

REQUEST_TIMEOUT_SECONDS = 30
MAX_ANSWER_TOKENS = 1024

SYSTEM_PROMPT = (
    "You are a healthcare planning assistant. Answer using only the data "
    "digest provided; if the digest doesn't contain the answer, say so "
    "plainly rather than guessing."
)

# One named constant per provider - trivially updated later as provider
# lineups change. Anthropic's is the current default per this project's
# established Claude tooling conventions.
MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "grok": "grok-2-latest",
    "groq": "llama-3.3-70b-versatile",
}


class AIProviderError(Exception):
    """Raised when a provider call fails, carrying a message safe to show
    the user directly - never a raw traceback."""


def _ask_anthropic(key, question, context):
    try:
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=MODELS["anthropic"],
            max_tokens=MAX_ANSWER_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"{context}\n\nQuestion: {question}"}],
        )
        text_blocks = [block.text for block in response.content if block.type == "text"]
        return "".join(text_blocks).strip() or "(no answer returned)"
    except Exception as exc:
        raise AIProviderError(f"Claude request failed: {exc}") from exc


def _ask_openai_style(url, model, headers_fn):
    def _ask(key, question, context):
        try:
            response = requests.post(
                url,
                headers=headers_fn(key),
                json={
                    "model": model,
                    "max_tokens": MAX_ANSWER_TOKENS,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
                    ],
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise AIProviderError(f"Request failed: {exc}") from exc
        if response.status_code != 200:
            raise AIProviderError(f"Provider returned HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise AIProviderError(f"Unexpected response shape: {exc}") from exc
    return _ask


def _ask_gemini(key, question, context):
    url = f"https://generativelanguage.googleapis.com/v1/models/{MODELS['gemini']}:generateContent"
    try:
        response = requests.post(
            url,
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": f"{SYSTEM_PROMPT}\n\n{context}\n\nQuestion: {question}"}]}],
                "generationConfig": {"maxOutputTokens": MAX_ANSWER_TOKENS},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise AIProviderError(f"Request failed: {exc}") from exc
    if response.status_code != 200:
        raise AIProviderError(f"Provider returned HTTP {response.status_code}: {response.text[:200]}")
    data = response.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts).strip()
    except (KeyError, IndexError) as exc:
        raise AIProviderError(f"Unexpected response shape: {exc}") from exc


_ASKERS = {
    "anthropic": _ask_anthropic,
    "openai": _ask_openai_style(
        "https://api.openai.com/v1/chat/completions",
        MODELS["openai"],
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
    "gemini": _ask_gemini,
    "grok": _ask_openai_style(
        "https://api.x.ai/v1/chat/completions",
        MODELS["grok"],
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
    "groq": _ask_openai_style(
        "https://api.groq.com/openai/v1/chat/completions",
        MODELS["groq"],
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
}


def ask(provider, key, question, context):
    asker = _ASKERS.get(provider)
    if asker is None:
        raise AIProviderError(f"Unknown provider: {provider}")
    if not key:
        raise AIProviderError("No API key configured for this provider")
    if not question or not question.strip():
        raise AIProviderError("Question must not be empty")
    return asker(key, question, context)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_ai_client.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add server/ai_client.py tests/server/test_ai_client.py
git commit -m "feat: add ai_client module for real per-provider chat calls

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `server/chat_ui.py` — the chat widget

**Files:**
- Create: `server/chat_ui.py`
- Test: `tests/server/test_chat_ui.py`

**Interfaces:**
- Produces: `chat_ui.PROVIDER_OPTIONS` (tuple of `(provider_id, display_name)` pairs, values matching `keystore.PROVIDERS` exactly); `chat_ui.CHAT_CSS`, `chat_ui.CHAT_JS` (plain strings); `chat_ui.render_widget() -> str` (the full widget markup: toggle button, panel, form, `<style>`/`<script>` tags).
- Consumes: `keystore.PROVIDERS` (only in the test, to cross-check `PROVIDER_OPTIONS` doesn't drift from it).

**DOM contract `CHAT_JS` depends on** (consumed by `render_widget()`'s markup): `#ai-chat-toggle` opens/closes `#ai-chat-panel`; `#ai-chat-close` closes it; `#ai-chat-form` submit reads `#ai-chat-provider` and `#ai-chat-input`, appends a question/answer pair to `#ai-chat-log`, and `POST`s `{"provider": ..., "question": ...}` to `/api/ask`.

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_chat_ui.py`:

```python
from server import chat_ui, keystore


def test_provider_options_match_keystore_providers():
    option_values = [value for value, _label in chat_ui.PROVIDER_OPTIONS]
    assert set(option_values) == set(keystore.PROVIDERS)


def test_render_widget_contains_key_hooks():
    widget = chat_ui.render_widget()
    for hook in (
        'id="ai-chat-toggle"',
        'id="ai-chat-panel"',
        'id="ai-chat-form"',
        'id="ai-chat-provider"',
        'id="ai-chat-input"',
        'id="ai-chat-log"',
        "/api/ask",
    ):
        assert hook in widget, f"missing hook: {hook}"


def test_render_widget_lists_all_providers():
    widget = chat_ui.render_widget()
    for value, _label in chat_ui.PROVIDER_OPTIONS:
        assert f'value="{value}"' in widget


def test_chat_js_braces_and_parens_balance():
    js = chat_ui.CHAT_JS
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")
    assert js.count("[") == js.count("]")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_chat_ui.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.chat_ui'`

- [ ] **Step 3: Implement `server/chat_ui.py`**

```python
"""HTML/CSS/JS for the "Ask AI" chat panel, injected into the served
dashboard HTML at serve-time (scripts/14_build_html_report.py's static
output is never modified - see docs/superpowers/specs/
2026-08-15-ai-chat-panel-phase3-design.md section 3). Same string-constant
pattern as admin_ui.py.
"""

PROVIDER_OPTIONS = (
    ("anthropic", "Claude (Anthropic)"),
    ("openai", "OpenAI"),
    ("gemini", "Gemini"),
    ("grok", "Grok (xAI)"),
    ("groq", "Groq"),
)

CHAT_CSS = r"""
#ai-chat-toggle {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  z-index: 1000;
  background: var(--accent, #a85a17);
  color: #fff;
  border: none;
  border-radius: 999px;
  padding: 0.75rem 1.25rem;
  font-size: 0.9rem;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(0,0,0,0.2);
}
#ai-chat-panel {
  position: fixed;
  bottom: 5rem;
  right: 1.5rem;
  z-index: 1000;
  width: min(380px, calc(100vw - 3rem));
  max-height: min(560px, calc(100vh - 8rem));
  display: none;
  flex-direction: column;
  background: var(--panel, #fff);
  border: 1px solid var(--line, rgba(22,33,31,0.13));
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.25);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  color: var(--ink, #16211f);
  overflow: hidden;
}
#ai-chat-panel.open { display: flex; }
#ai-chat-header {
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--line, rgba(22,33,31,0.13));
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
#ai-chat-close { background: none; border: none; cursor: pointer; font-size: 1rem; color: var(--muted, #7c8580); }
#ai-chat-log {
  flex: 1;
  overflow-y: auto;
  padding: 0.75rem 1rem;
  font-size: 0.85rem;
}
.ai-chat-entry { margin-bottom: 0.9rem; }
.ai-chat-question { font-weight: 600; margin-bottom: 0.25rem; }
.ai-chat-answer { white-space: pre-wrap; color: var(--ink-soft, #48534f); }
.ai-chat-answer.error { color: var(--danger, #b3392b); }
.ai-chat-answer.pending { color: var(--muted, #7c8580); font-style: italic; }
#ai-chat-form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid var(--line, rgba(22,33,31,0.13));
}
#ai-chat-provider, #ai-chat-input {
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--line, rgba(22,33,31,0.13));
  border-radius: 6px;
  background: var(--paper, #f3f6f4);
  color: var(--ink, #16211f);
  font-size: 0.85rem;
  font-family: inherit;
}
#ai-chat-input { resize: vertical; min-height: 2.5rem; }
#ai-chat-submit {
  background: var(--accent, #a85a17);
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 0.5rem;
  font-size: 0.85rem;
  cursor: pointer;
}
"""

CHAT_JS = r"""
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("ai-chat-toggle");
    var panel = document.getElementById("ai-chat-panel");
    var closeBtn = document.getElementById("ai-chat-close");
    var form = document.getElementById("ai-chat-form");
    var providerSelect = document.getElementById("ai-chat-provider");
    var input = document.getElementById("ai-chat-input");
    var log = document.getElementById("ai-chat-log");

    if (!toggle || !panel || !form) return;

    toggle.addEventListener("click", function () {
      panel.classList.toggle("open");
    });
    closeBtn.addEventListener("click", function () {
      panel.classList.remove("open");
    });

    form.addEventListener("submit", function (evt) {
      evt.preventDefault();
      var question = input.value.trim();
      if (!question) return;

      var entry = document.createElement("div");
      entry.className = "ai-chat-entry";
      var questionEl = document.createElement("div");
      questionEl.className = "ai-chat-question";
      questionEl.textContent = question;
      var answerEl = document.createElement("div");
      answerEl.className = "ai-chat-answer pending";
      answerEl.textContent = "Thinking...";
      entry.appendChild(questionEl);
      entry.appendChild(answerEl);
      log.appendChild(entry);
      log.scrollTop = log.scrollHeight;
      input.value = "";

      fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: providerSelect.value, question: question }),
      })
        .then(function (res) {
          return res.json().then(function (data) { return { ok: res.ok, data: data }; });
        })
        .then(function (result) {
          answerEl.classList.remove("pending");
          if (result.ok) {
            answerEl.textContent = result.data.answer;
          } else {
            answerEl.classList.add("error");
            answerEl.textContent = (result.data && result.data.detail) || "Request failed";
          }
          log.scrollTop = log.scrollHeight;
        })
        .catch(function (err) {
          answerEl.classList.remove("pending");
          answerEl.classList.add("error");
          answerEl.textContent = "Request failed: " + err;
        });
    });
  });
})();
"""


def render_widget():
    options = "\n".join(f'    <option value="{value}">{label}</option>' for value, label in PROVIDER_OPTIONS)
    return f"""<style>{CHAT_CSS}</style>
<button type="button" id="ai-chat-toggle">Ask AI</button>
<div id="ai-chat-panel">
  <div id="ai-chat-header">
    <span>Ask AI about this plan</span>
    <button type="button" id="ai-chat-close" aria-label="Close">&times;</button>
  </div>
  <div id="ai-chat-log"></div>
  <form id="ai-chat-form">
    <select id="ai-chat-provider">
{options}
    </select>
    <textarea id="ai-chat-input" placeholder="Ask a question about the plan..." required></textarea>
    <button type="submit" id="ai-chat-submit">Ask</button>
  </form>
</div>
<script>{CHAT_JS}</script>
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_chat_ui.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add server/chat_ui.py tests/server/test_chat_ui.py
git commit -m "feat: add chat_ui module for the Ask AI widget

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Wire it in — `/api/ask` route, widget injection, end-to-end tests, manual verification

**Files:**
- Modify: `server/routes/dashboard.py`
- Modify: `tests/server/test_dashboard_route.py`
- Create: `tests/server/test_ask_route.py`

**Interfaces:**
- Consumes: everything from Tasks 1-3, plus `keystore` (phase 2).
- Produces: the final, verified phase-3 chat panel.

- [ ] **Step 1: Replace `server/routes/dashboard.py`**

Find (the complete current file):

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

Replace with:

```python
"""GET / - serves report/KP_Healthcare_Plan.html with the "Ask AI" chat
panel injected before </body>, re-read from disk on every request so a
pipeline rebuild is picked up without a server restart. The pipeline's own
output (scripts/14_build_html_report.py) is never modified - the widget is
injected here, at serve-time only. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md section 4 and
2026-08-15-ai-chat-panel-phase3-design.md section 3.
"""
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse, JSONResponse

from server import ai_client, chat_ui, keystore, report_context

REPORT_PATH = Path(__file__).resolve().parent.parent.parent / "report" / "KP_Healthcare_Plan.html"

router = APIRouter()


def _inject_chat_widget(html_text):
    widget = chat_ui.render_widget()
    if "</body>" in html_text:
        return html_text.replace("</body>", widget + "</body>", 1)
    return html_text + widget


@router.get("/", response_class=HTMLResponse)
def get_dashboard():
    if not REPORT_PATH.exists():
        return HTMLResponse(
            "<h1>Report not built yet</h1>"
            "<p>Run <code>python scripts/14_build_html_report.py</code> first.</p>",
            status_code=503,
        )
    html_text = REPORT_PATH.read_text(encoding="utf-8")
    return HTMLResponse(_inject_chat_widget(html_text))


@router.post("/api/ask")
def ask_ai(provider: str = Body(...), question: str = Body(...)):
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    key = keystore.get_key(provider)
    if not key:
        display_name = dict(chat_ui.PROVIDER_OPTIONS).get(provider, provider)
        return JSONResponse(
            {"detail": f"No API key configured for {display_name} - add one in the admin panel first."},
            status_code=400,
        )
    context = report_context.build_context()
    try:
        answer = ai_client.ask(provider, key, question, context)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)
    return JSONResponse({"answer": answer})
```

- [ ] **Step 2: Add a widget-injection assertion to `tests/server/test_dashboard_route.py`**

Find (the complete current file):

```python
from fastapi.testclient import TestClient

from server.app import create_app


def test_dashboard_route_returns_html():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "Khyber Pakhtunkhwa" in response.text
```

Replace with:

```python
from fastapi.testclient import TestClient

from server.app import create_app


def test_dashboard_route_returns_html():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "Khyber Pakhtunkhwa" in response.text


def test_dashboard_route_includes_chat_widget():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="ai-chat-toggle"' in response.text
    assert "/api/ask" in response.text
```

- [ ] **Step 3: Write `tests/server/test_ask_route.py`**

```python
"""End-to-end /api/ask tests via FastAPI's TestClient. keyring and
ai_client.ask are both mocked - no real OS keyring entries, network calls,
or API keys.
"""
import pytest
from fastapi.testclient import TestClient

from server import ai_client, keystore
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
    response = client.post("/api/ask", json={"provider": "anthropic", "question": "Which district is largest?"})
    assert response.status_code == 200
    assert response.json()["answer"] == "Peshawar is largest."


def test_ask_provider_failure_returns_502(client, monkeypatch):
    keystore.set_key("openai", "sk-real")

    def failing_ask(provider, key, question, context):
        raise ai_client.AIProviderError("Provider returned HTTP 401: unauthorized")

    monkeypatch.setattr(ai_client, "ask", failing_ask)
    response = client.post("/api/ask", json={"provider": "openai", "question": "Hi?"})
    assert response.status_code == 502
    assert "401" in response.json()["detail"]
```

- [ ] **Step 4: Run the new and modified tests**

Run: `pytest tests/server/test_dashboard_route.py tests/server/test_ask_route.py -v`
Expected: 6 passed (2 in `test_dashboard_route.py`, 4 in `test_ask_route.py`)

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass — phase 1's 47, phase 2's 47, plus this phase's 5 (report_context) + 13 (ai_client) + 4 (chat_ui) + 1 new (dashboard widget) + 4 (ask route) = roughly 121; exact count isn't load-bearing, "all pass" is.

- [ ] **Step 6: Manual browser verification**

Start the server: `python -m server`

In a browser at `http://127.0.0.1:8420`:
- The dashboard loads exactly as before, plus a small "Ask AI" button fixed at the bottom-right corner.
- Clicking it opens the chat panel with a provider dropdown listing all 5 providers, a question textarea, and an Ask button.
- With no keys configured yet, asking a question shows a clear "No API key configured for ... — add one in the admin panel first" message, not an error page or a blank response.
- Go to `/admin`, save a real key for at least one provider (only if you have one available and want to spend the small cost of one real call — otherwise save an obviously-fake key to confirm the failure path instead).
- Back on `/`, ask a question with that provider selected. With a real key: confirm an actual, on-topic answer appears (referencing real district names/numbers from the digest). With a fake key: confirm a clear provider-error message appears in the chat log, not a crash.
- Confirm the "Thinking..." pending state shows immediately after submitting and is replaced by the real result.

If any of these fail, this is a real bug to fix before committing — do not report success without having actually driven the browser through each step. Clean up afterward: delete any test keys you saved via the admin panel's Delete button, matching the same cleanup discipline used in phases 1 and 2's manual verification.

- [ ] **Step 7: Final commit**

```bash
git add server/routes/dashboard.py tests/server/test_dashboard_route.py tests/server/test_ask_route.py
git commit -m "feat: wire the Ask AI chat panel into the dashboard route

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
