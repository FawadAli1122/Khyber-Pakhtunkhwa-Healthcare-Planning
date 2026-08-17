# KP Healthcare Plan — Backend + Admin Panel (Phase 2)

Status: Approved design, pre-implementation
Date: 2026-08-15

## 1. Purpose

Add a local web server that serves the interactive dashboard built in phase 1
and gives the user an admin panel to store and validate API keys for five AI
providers — Claude, OpenAI, Gemini, Grok, and Groq — as the foundation for
phase 3 (actually calling those providers) and phase 4 (document-upload-driven
updates). Phase 2 does **not** call any provider to do real work; it only
proves each stored key authenticates.

This is phase 2 of the roadmap recorded in
`docs/superpowers/specs/2026-08-15-interactive-dashboard-phase1-design.md`
§7. Phase 1 (the interactive dashboard) is complete and merged to `master`.

## 2. Why a Backend At All

Phase 1's dashboard is a single static HTML file — deliberately, since it had
no secrets to protect. API keys are different: embedding them in client-side
HTML/JS (the literal ask in the original request) would expose them to
anyone who opens the file or its page source. This was decided explicitly
during brainstorming: keys live server-side, in OS-level secret storage
(Windows Credential Manager via the `keyring` package), never in a file the
browser can read. The admin panel is a UI *against* this server, not an
embedded secret.

## 3. Approach

**FastAPI + `uvicorn`**, a new top-level `server/` package, separate from
`scripts/` (the existing pipeline stays a pure batch process — this doesn't
touch it). Rejected alternative: Flask — simpler, but synchronous by default,
which would need extra work (threads/async wrappers) once phase 3 calls five
different AI providers concurrently; FastAPI's native async fits that need
without a later rewrite.

The server binds to `127.0.0.1` only — never `0.0.0.0` — per the earlier
decision that this is a single-user local tool, not a network service.

## 4. Serving the Dashboard

`GET /` reads `report/KP_Healthcare_Plan.html` fresh on every request (no
in-memory caching, no template engine) and returns it as-is. Regenerating the
report via `python scripts/14_build_html_report.py` and refreshing the
browser picks up changes immediately — no server restart needed. This keeps
phase 1's file as the single source of truth for dashboard content; phase 2
adds nothing to how it's built, only how it's served.

## 5. Key Storage

`keystore.py` wraps the `keyring` package (new dependency) with three
functions: `get_key(provider) -> str | None`, `set_key(provider, value)`,
`delete_key(provider)`. On Windows this resolves to Windows Credential
Manager — real OS-level secret storage, never a plaintext file on disk. A
`list_status()` helper returns, per provider, whether a key is configured and
a masked hint (last 4 characters only) — never the full key — for the admin
UI to render without re-exposing secrets it already stored.

Providers are a fixed list: `["anthropic", "openai", "gemini", "grok", "groq"]`.

## 6. Admin Authentication

A single admin password, not per-provider accounts — this is a personal
single-user tool. Decided explicitly during brainstorming: worth the small
extra build even though the server only listens on localhost, as cheap
insurance if the machine is ever shared or remoted into.

- **First visit to `/admin`** with no password set (checked via
  `keystore`-adjacent storage, see below) shows a one-time setup form.
- **Password hashing:** stdlib `hashlib.pbkdf2_hmac("sha256", password,
  salt, 200_000)` — no new dependency for this. Salt + hash stored via the
  same `keyring` mechanism as provider keys, under a reserved `admin`
  service entry that is never listed alongside the five AI providers.
- **Session cookie:** on successful login, an HMAC-signed cookie
  (`hmac`/`hashlib`, stdlib) carrying `{"admin": true, "exp": <timestamp>}`.
  The signing secret is generated once via `secrets.token_bytes(32)` and
  stored in `keyring` (reserved `session_secret` entry), so it survives
  server restarts but never touches disk in plaintext.
- Every `/admin` and `/admin/api/*` route requires a valid, unexpired
  cookie; `/` (the dashboard itself) requires none.

**Explicitly out of scope:** CSRF protection. This is a same-origin,
cookie-authenticated, localhost-only single-user tool — the standard CSRF
threat model (a malicious third-party site tricking a logged-in browser into
issuing state-changing requests) doesn't meaningfully apply until this ever
listens beyond `127.0.0.1`, which it explicitly does not in this phase.
Flagged here so it's a conscious deferral, not an oversight, if the server's
binding ever changes in a later phase.

## 7. Testing Each Provider's Key

One lightweight, free-or-near-free "list models" call per provider — enough
to confirm the key authenticates, nothing that does real work or costs
meaningfully:

| Provider | Call | Library |
|---|---|---|
| Claude (Anthropic) | `client.models.list()` | Official `anthropic` SDK (new dependency) — required for any Anthropic API call per this project's tooling conventions; never raw HTTP where an official SDK exists. |
| OpenAI | `GET https://api.openai.com/v1/models` | `requests` (already a project dependency) |
| Gemini | `GET https://generativelanguage.googleapis.com/v1/models?key=<key>` | `requests` |
| Grok (xAI) | `GET https://api.x.ai/v1/models` | `requests` |
| Groq | `GET https://api.groq.com/openai/v1/models` | `requests` |

`providers.py` exposes one dispatch function, `test_key(provider: str, key:
str) -> tuple[bool, str]` — `(ok, detail)`, where `detail` is a short
human-readable status ("Authenticated, 42 models available" /
"401 Unauthorized" / "Request timed out") shown in the admin UI. Network
errors and non-2xx responses are caught and reported as `(False, ...)`,
never raised past this function — a bad key is a normal UI state, not a
server error.

## 8. Routes

| Route | Method | Auth | Purpose |
|---|---|---|---|
| `/` | GET | none | Serves `report/KP_Healthcare_Plan.html` as-is |
| `/admin` | GET | none* | Setup form (no password yet) → login form (not authenticated) → admin panel (authenticated) |
| `/admin/setup` | POST | none, once | Sets the admin password; 403 if one already exists |
| `/admin/login` | POST | none | Verifies password, sets the session cookie |
| `/admin/logout` | POST | cookie | Clears the session cookie |
| `/admin/api/keys` | GET | cookie | JSON: `[{provider, configured: bool, hint: str|null}]` for all 5 providers |
| `/admin/api/keys/{provider}` | PUT | cookie | Body `{"api_key": "..."}` — stores the key via `keystore.set_key` |
| `/admin/api/keys/{provider}` | DELETE | cookie | Removes the key via `keystore.delete_key` |
| `/admin/api/keys/{provider}/test` | POST | cookie | Body optional `{"api_key": "..."}` to test an unsaved value, else tests the currently stored key. Returns `{"ok": bool, "detail": str}` |

*`/admin` itself has no cookie requirement because it must render the
setup/login forms before any session exists; it internally branches on
whether a password is set and whether the request's cookie is valid.

## 9. Admin Panel UI

Five rows — Claude, OpenAI, Gemini, Grok, Groq — each showing
configured-or-not, the masked hint, and Save / Delete / Test buttons. Styled
with the report's existing teal-ink/burnt-ochre design system
(`admin_ui.py` holds `ADMIN_CSS`/`ADMIN_JS`/HTML constants, same pattern as
phase 1's `dashboard_assets.py`) — not a generic bolted-on admin theme.

## 10. Testing Strategy

No test depends on a real network call or a real API key:

- `tests/server/test_keystore.py` — monkeypatches `keyring.get_password` /
  `set_password` / `delete_password` directly (not a real OS keyring
  backend) to verify `keystore.py`'s logic.
- `tests/server/test_auth.py` — password hashing round-trip, cookie
  sign/verify round-trip, and expiry behavior with a fake/injected clock.
- `tests/server/test_providers.py` — monkeypatches the `anthropic` SDK call
  and the four `requests.get` calls to verify `test_key()`'s dispatch and
  its `(ok, detail)` handling of both success and failure responses.
- `tests/server/test_routes.py` — FastAPI `TestClient` exercises the full
  route set (setup → login → list/set/delete/test keys → logout) with the
  above layers mocked, confirming auth gating actually blocks unauthenticated
  requests to every `/admin/api/*` route.

## 11. Roadmap (context for later phases — not this spec's scope)

Unchanged from the phase-1 spec's §7, restated here for continuity:

- **Phase 3 — Multi-provider AI integration.** The five keys validated here
  get used for real: Claude/OpenAI/Gemini/Grok/Groq wired in behind this
  backend, selectable from the admin panel.
- **Phase 4 — Document ingestion.** Upload Excel/PDF/Word/HTML/database
  inputs; AI extracts data and updates plan content/data with full autonomy
  (per explicit earlier user decision — no human-in-the-loop review gate),
  then the dashboard is regenerated.
- **Phase 1b — Methodology upgrade.** 2SFCA-style accessibility and
  p-median/MCLP site suggestion, replacing the current heuristics in the
  deterministic pipeline. Independent of phases 2–4.
