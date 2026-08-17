# KP Healthcare Plan — "Ask AI" Chat Panel (Phase 3)

Status: Approved design, pre-implementation
Date: 2026-08-15

## 1. Purpose

Wire the five AI provider keys validated in phase 2 into something the user
can actually use: a single-turn "Ask AI" chat panel on the dashboard, where
the user asks a question about the healthcare plan and a chosen provider
answers using the report's actual data as context. This is the "used for
real" step promised in the phase-1/phase-2 roadmaps, and the natural
foundation phase 4 (document-driven auto-update) builds on — but phase 3
itself does not update the report or any underlying data; it only answers
questions about what's already there.

## 2. Scope Decisions From Brainstorming

- **Feature: a chat panel, not narrative regeneration or bare plumbing.**
  Considered and rejected: an admin-panel "regenerate this section" button
  (touches actual report content, needs a review/approve step — closer to
  phase 4's territory than phase 3's) and building `ai_client.py` with no
  visible feature (lowest scope, but nothing to actually use before phase
  4). The chat panel is useful immediately and exercises all 5 providers
  through one shared interface without touching report content.
- **Provider choice is per-question, not a persisted default.** The chat UI
  always requires picking which of the 5 configured providers to ask for
  that specific question — no "default provider" setting to add to the
  admin panel.
- **Single-turn, not multi-turn.** Each question is answered independently,
  with no conversation memory. Multi-turn history is real added complexity
  (client-side state, growing token cost) with no clear need yet — deferred
  as a possible future enhancement, not built now.

## 3. Architecture

Three new pieces, all server-side:

1. **`server/report_context.py`** — builds a compact text digest from
   `data/processed/district_metrics.csv` (province totals, tier counts, a
   condensed per-district table) as the AI's grounding context.
   Deliberately **not** the raw report HTML: markup is wasteful token-wise
   and invites the model to comment on styling instead of data. The digest
   is the same underlying data source the phase-1 dashboard and phase-4
   pipeline both already treat as ground truth.
2. **`server/ai_client.py`** — one function `ask(provider, question,
   context) -> str`, dispatching to each of the five providers' real
   chat/message-generation APIs (phase 2's `providers.test_key` only ever
   listed models; this makes an actual answer-generating call). Every
   provider function raises a typed `AIProviderError` on failure — never a
   bare exception the route has to guess about — carrying a message safe to
   show the user (no raw stack traces reaching the chat UI).
3. **`server/chat_ui.py`** — the chat panel's HTML/CSS/JS, same
   string-constant pattern as phase 2's `admin_ui.py`.

**UI injection happens at serve-time, not in the pipeline.** The phase-1
report generator (`scripts/14_build_html_report.py`) is untouched and its
output stays exactly what it always was — `server/routes/dashboard.py`
reads that static file and injects the chat widget's HTML/CSS/JS right
before `</body>` when serving it. This keeps the deterministic pipeline's
output pristine (still openable standalone via `file://`, still what phase
1's own tests verify) and treats AI chat purely as a server-side
augmentation layer, consistent with how phase 2 already separates "what the
pipeline produces" from "what the server adds."

## 4. Per-Provider Implementation

| Provider | Call | Model (default, easily changed later — each is one named constant) |
|---|---|---|
| Claude (Anthropic) | Official `anthropic` SDK, `client.messages.create(...)` | `claude-opus-5` — the current default per this project's established Claude tooling conventions |
| OpenAI | `POST https://api.openai.com/v1/chat/completions` via `requests` | `gpt-4o-mini` |
| Gemini | `POST https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key=...` via `requests` | `gemini-2.0-flash` |
| Grok (xAI) | `POST https://api.x.ai/v1/chat/completions` via `requests` (OpenAI-compatible) | `grok-2-latest` |
| Groq | `POST https://api.groq.com/openai/v1/chat/completions` via `requests` (OpenAI-compatible) | `llama-3.3-70b-versatile` |

Every call includes a short system-role instruction ("You are a healthcare
planning assistant. Answer using only the data digest provided; if the
digest doesn't contain the answer, say so plainly rather than guessing.")
plus the digest from `report_context.py` and the user's question. Answers
are capped at a fixed `max_tokens`/equivalent (1024) to bound cost and
latency — this is a live, per-question API call the user is paying for,
not a cached or free operation.

## 5. Route

`POST /api/ask` on the existing dashboard router. Body `{"provider": "...",
"question": "..."}` (provider must be one of the five phase-2 provider
ids). Response `{"answer": "..."}` on success. On failure — no key
configured for that provider, or the provider call itself fails — returns
a 4xx/5xx with `{"detail": "..."}` carrying a message the chat UI can show
directly (e.g. "No API key configured for Gemini — add one in the admin
panel first," or a provider's own reported error, never a Python
traceback). No authentication on this route, consistent with `/` itself —
this is a single-user localhost tool, and gating a chat feature behind the
admin login would contradict the dashboard being the public-facing surface.

## 6. Testing Strategy

Unchanged posture from phases 1 and 2: no test may require a real API key
or network access.

- `tests/server/test_report_context.py` — the digest builder against a
  small fixture (or the real `district_metrics.csv`, since it's
  deterministic and already read elsewhere in the test suite), asserting
  the digest contains expected totals/tiers without depending on exact
  wording.
- `tests/server/test_ai_client.py` — each provider's `ask()` path mocked
  the same way phase 2's `test_providers.py` mocks `test_key()`: the
  `anthropic` SDK call and the four `requests.post` calls, covering both a
  successful answer and a provider-side failure raising `AIProviderError`.
- `tests/server/test_ask_route.py` — FastAPI `TestClient` against
  `/api/ask`, with `ai_client.ask` mocked, covering success, unconfigured
  key, and provider failure.

## 7. Roadmap (context for later phases — not this spec's scope)

- **Phase 4 — Document ingestion.** Upload Excel/PDF/Word/HTML/database
  inputs; AI extracts data and updates plan content/data with full
  autonomy (per the explicit earlier user decision — no human-in-the-loop
  review gate), then the dashboard is regenerated. This phase's
  `ai_client.py` is the natural call layer phase 4 reuses rather than
  rebuilding.
- **Phase 1b — Methodology upgrade.** 2SFCA-style accessibility and
  p-median/MCLP site suggestion, replacing the current heuristics in the
  deterministic pipeline. Independent of phases 2–4.
