# KP Healthcare Plan — Telegram Connector

Status: Approved design, pre-implementation
Date: 2026-08-16

## 1. Purpose

Let the admin interact with the KP Healthcare Plan dashboard from Telegram
instead of only the local web admin panel: view the current report and a
rendered map, ask the existing AI chat panel's question-answering, add a
new facility point from the field, and manage AI provider API keys — all
from a phone.

The user originally asked for WhatsApp; during brainstorming they switched
to Telegram, which is a strictly better fit for a project that runs
locally with no public URL — Telegram's Bot API requires no business
verification, has no template-message restrictions on outbound messages,
and supports **long-polling** (the bot repeatedly asks Telegram for new
messages) so it needs no public HTTPS endpoint at all, unlike WhatsApp's
Cloud API or a webhook-based Telegram integration.

## 2. Scope Decisions From Brainstorming

- **One connector, six commands, bundled into a single spec** — the user
  explicitly chose to bundle the write-capable `/addpoint` command and
  `/setkey` into this same first sub-project rather than split them into
  a smaller read-only slice first, so this spec covers: `/start`,
  `/report`, `/map`, `/ask`, `/keys`, `/setkey`, `/addpoint`.
- **Long-polling, not a webhook.** The admin server runs locally
  (`python -m server`), with no public URL. Long-polling needs no
  inbound network exposure at all; a webhook would require deploying
  somewhere reachable or tunneling, which is out of scope here.
- **The bot runs in the same process as the admin server**, started as a
  background asyncio task from `python -m server`'s own startup — one
  command still starts everything. If no bot token is configured, the
  task no-ops silently so existing (non-Telegram) usage is unaffected.
- **Two separate credential paths, deliberately different trust levels:**
  - The bot's *own* token and the single allowlisted Telegram user ID are
    configured **only** through the admin panel (new section, stored via
    the existing `keystore.py` OS-credential-store pattern) — there's a
    chicken-and-egg problem otherwise: you can't message a bot to
    configure the bot that doesn't exist yet without a token.
  - **AI provider keys** (Groq, Anthropic, etc.) become settable through
    *either* the admin panel *or* the bot's `/setkey` command — same
    underlying `keystore.set_key()`, two entry points. Authorized purely
    by being the allowlisted Telegram user (the user's explicit choice,
    made after being shown the trade-off below).
  - **Trade-off the user explicitly accepted:** Telegram bots cannot
    delete a message the user sent in a private chat (only messages the
    bot itself sent, or any message in a group where the bot is admin).
    A key sent via `/setkey` therefore stays visible in the user's own
    Telegram chat history — the bot's reply never echoes the key back,
    but the user's own sent message remains. This is a real, accepted
    security downgrade from the admin panel's OS-credential-store-only
    model, scoped narrowly to this one command.
- **Single allowlisted Telegram user**, matching this project's existing
  single-admin-password trust model. Every other sender is rejected with
  a generic "not authorized" reply (no information about what the bot
  does, to avoid leaking capability to an unauthorized sender).
- **`/addpoint` creates a real facility**, not a lightweight annotation —
  stored in a new append-only overlay (`data/processed/bot_facilities.csv`,
  same `id`/backfill/`delete_record` shape as
  [[2026-08-16-manage-records-design]]'s two stores) and merged into the
  report/map as a fourth facility source alongside KPHCC/OSM/Marham, not
  a separate "suggested site" marker.
- **`/map` renders a real PNG via headless PyQGIS**, not a link to the
  web dashboard — the dashboard isn't necessarily reachable from wherever
  the user is viewing Telegram (e.g. on their phone, off the admin
  machine's local network), while a rendered image works in any chat
  immediately.
- **Every Telegram API call is mocked in automated tests**, matching this
  project's established "every AI provider call in every automated test
  is mocked" discipline. Live verification needs a real bot token (via
  Telegram's @BotFather) and the user's real Telegram user ID, configured
  through the admin panel — same pattern as testing AI-provider features
  with a real API key.
- **Out of scope for this pass:** webhook-based delivery, multi-user
  allowlists, in-place editing of a bot-added facility (delete + re-add
  only, matching Manage Records' "view + delete only" decision), and any
  WhatsApp-specific work (superseded by this Telegram decision).

## 3. New Dependency

`python-telegram-bot` (v20+, async, includes `Application.updater.start_polling()`
for non-blocking long-polling and `ConversationHandler` for the multi-step
`/addpoint` flow). This project tracks no dependency manifest file at all
(`requirements.txt`/`pyproject.toml` — neither exists); every dependency
so far (Playwright, scikit-learn, shapely, psycopg2, PyQGIS, etc.) has
been installed ad-hoc into the system Python as each feature needed it.
This dependency follows that same established pattern.

## 4. Credential Storage

Two new `keystore.py` entries, alongside the existing AI-provider-key and
admin-password-hash storage (same OS credential store, same `keyring`
library, no new storage mechanism):

- `telegram_bot_token` — the bot's token from @BotFather.
- `telegram_allowed_user_id` — the single allowlisted Telegram numeric
  user ID (not a username - usernames are mutable and not guaranteed
  unique in Telegram's API the way a user ID is).

New admin panel section, "Telegram Bot" (`server/admin_ui.py`, matching
the existing `upload-section` block style): two fields (bot token,
allowed user ID) with Save/Delete, same interaction pattern as the
existing provider-key rows. Setting or changing the token triggers the
running bot task to restart with the new token (stop the current
`Application`, start a new one) - handled by the same route that saves
the token, not a separate "restart bot" button.

## 5. Bot Process Lifecycle

New `server/telegram_bot.py`:

- `build_application(token) -> telegram.ext.Application` — constructs the
  `Application` with all command/conversation handlers registered.
- `start_bot_task() -> asyncio.Task | None` — reads the token from
  `keystore`; returns `None` (no task) if unset. Otherwise builds the
  application and starts `updater.start_polling()` as a background task,
  returning the task handle so it can be cancelled on restart/shutdown.
- `stop_bot_task(task)` — cancels the task and awaits `Application.stop()`
  / `Application.shutdown()` cleanly.

`server/app.py`'s `create_app()` is currently a plain factory with no
startup/shutdown hooks at all - this spec adds one. A FastAPI `lifespan`
async context manager is added to `create_app()`: on startup, call
`start_bot_task()`; on shutdown, call `stop_bot_task()` if a task is
running. The admin route that saves a new bot token calls `stop_bot_task()`
then `start_bot_task()` again so a token change takes effect immediately
without requiring a full server restart.

Whether FastAPI's `TestClient` triggers this new lifespan for every
existing `TestClient(create_app())` call in this project's test suite
(most of which don't use it as a `with` block) needs checking during
planning - if it does, `start_bot_task()`'s "no token configured → no-op"
behavior must hold even under the `fake_store` keyring mock every existing
admin-route test already uses, so no existing test's behavior changes.

## 6. Authorization

Every handler passes through a single `_authorized(update) -> bool` check
first: compares `update.effective_user.id` against the stored
`telegram_allowed_user_id`. Unauthorized senders get a fixed generic
reply ("Not authorized.") and nothing else — no hint at available
commands, no distinction between "wrong user" and "bot not configured
yet" in the reply text (though logged server-side for the admin's own
diagnosis).

## 7. Commands

All handlers live in `server/telegram_bot.py`, each a thin wrapper that
authorizes, then calls into existing (or narrowly new) server-side logic
— no business logic duplicated between the web routes and the bot.

- **`/start`** — authorization check, then a fixed help message listing
  the six commands.
- **`/report`** — calls the existing `pdf_export.render_report_pdf()`
  (already used by the web dashboard's "Download PDF" link) against the
  current `report/KP_Healthcare_Plan.html`, sends the PDF bytes as a
  Telegram document. Same "not built yet" 503-equivalent message as the
  web route if the report file doesn't exist.
- **`/map`** — runs new `scripts/lib/qgis_render.py` as a **subprocess**
  through QGIS's own bundled Python interpreter (see section 10 - PyQGIS
  cannot be imported into the regular environment `python -m server`
  itself runs in), writing a PNG to a temp path; the handler then reads
  that file and sends it as a Telegram photo. Errors (project file
  missing, PyQGIS failure, non-zero subprocess exit) become a plain text
  reply, never a raw traceback - same `capture_output=True` /
  `result.returncode` pattern the admin routes already use for their
  rebuild subprocess calls.
- **`/ask <question>`** — calls the existing `report_context.build_context()`
  + `ai_client.ask()`, exactly the logic `/api/ask` already uses, with
  the first configured provider (by `keystore.PROVIDERS` order) chosen
  automatically. Replies with a clear message if no provider is
  configured, matching the existing "add one in the admin panel first"
  wording style.
- **`/keys`** — replies with each provider's configured/not-configured
  status (`keystore.list_status()`, same call the admin panel's key list
  uses), never a raw key value.
- **`/setkey <provider> <key>`** — validates `provider` against
  `keystore.PROVIDERS` (same "Unknown provider" wording as the existing
  `PUT /admin/api/keys/{provider}` route), then `keystore.set_key()`.
  Replies with confirmation only, never echoing the key.
- **`/addpoint`** — a `ConversationHandler` with these states, one
  message exchange per state:
  1. Bot asks for the facility name (free text).
  2. Bot asks for a category (free text - matches the free-form category
     convention `supplemental_data.py`'s AI extraction already uses, not
     a fixed enum).
  3. Bot asks the user to share a location (Telegram's native
     location-share UI - a `ReplyKeyboardMarkup` button with
     `request_location=True`, so the user taps to share their current
     GPS position or a dropped pin, never hand-types lat/lon).
  4. On receiving the location: validates it falls within `KP_BBOX`
     (`(31.0, 69.2, 36.9, 74.1)`, the same bounds every other geo-fetch
     script in this project already checks) **and** within the real KP
     province polygon (`data/processed/boundaries.json`'s province
     geometry, via `shapely`'s `.contains()`) - the bbox alone isn't
     enough, per `07_merge_facilities.py`'s own explicit comment that
     the bounding rectangle includes real slivers of neighboring
     Islamabad/Punjab/Afghanistan, and `find_containing_district()`'s
     "nearest district" fallback exists precisely for a genuine
     just-outside-its-own-district-polygon KP point, not for silently
     relabeling an actually-foreign point as KP. A point that fails
     either check gets a clear rejection and the conversation ends
     without writing anything. Only a point inside the real province
     polygon proceeds to district resolution via the *already existing*
     `scripts.lib.geo_utils.find_containing_district()` (loaded once
     against `data/processed/boundaries.json`, matching how
     `07_merge_facilities.py` already loads district polygons), appends
     a record to
     `bot_facilities.csv` (id/name/district/lat/lon/category/added_at/added_by),
     then runs `scripts/run_downstream_facilities.py` as a subprocess
     (same "kick off a rebuild, report a warning if it fails" pattern
     the admin routes already use, adapted to a Telegram reply instead
     of a JSON response) and replies with a confirmation once the
     facility is confirmed added (including the resolved district, so
     the user can catch a wrong pin immediately).
  A `/cancel` command aborts an in-progress `/addpoint` conversation at
  any state without writing anything.

## 8. Data Model: `bot_facilities.csv`

New store, `server/bot_facilities.py`, structurally identical to
`server/supplemental_data.py`/`server/metric_overrides.py` from
[[2026-08-16-manage-records-design]] (`id`/`load_records`/`_write_records`/
`append_records`/`delete_record`, same backfill-on-load behavior):

```
FIELDNAMES = ("id", "name", "district", "lat", "lon", "category", "added_at", "added_by")
```

`added_by` stores the Telegram user id (an integer, stringified) that
added the record - a lightweight provenance trail, not used for access
control (access control is the single-allowlist check at the handler
level, not per-record).

This store gets the same admin-panel "view + delete" table treatment as
supplemental records and overrides (new "Bot-Added Facilities" section in
`server/admin_ui.py`, reusing the exact `initRecordsTable`/`renderRecordRow`/
`showEmptyRow` JS helpers already built for Manage Records - no new JS
patterns, just a third table). Deleting a bot-added facility from the
admin panel triggers `run_downstream_facilities.py` the same way adding
one does, so a bad point can be corrected from the desktop too, not just
via Telegram.

## 9. Pipeline Wiring

`07_merge_facilities.py` gets extended to read `bot_facilities.csv` as a
fourth source (alongside KPHCC/OSM/Marham), converting each row into the
same facility-record shape the merge/dedup pass already produces for the
other three sources, before the existing dedup-by-proximity logic runs
(a bot-added facility that turns out to duplicate an existing KPHCC/OSM/
Marham entry gets caught by the same dedup this feature already relies on
for Marham).

New `scripts/run_downstream_facilities.py`, mirroring
`scripts/run_downstream.py`'s "skip the expensive fetch stages" shape but
starting one stage earlier - at `07_merge_facilities.py` instead of `07b`
- since a new facility changes the merged set itself, not just an
overridden number:

```
STAGES = [
    "07_merge_facilities.py",
    "07b_apply_metric_overrides.py",
    "08_compute_district_metrics.py",
    "09_gap_score_and_clusters.py",
    "10_forecast_demand.py",
    "11_suggest_new_sites.py",
    "20_cross_validate_facility_counts.py",
    "12_write_shapefiles.py",
    "13_build_qgis_project.py",
    "14_build_html_report.py",
]
```

Does **not** re-fetch KPHCC/OSM/Marham/DEM/roads (`03`/`04`/`05`/`06`/`15`/
`16`/`16b`/`21`/`22`), matching `run_downstream.py`'s existing rationale
that those sources don't change from an admin-panel/bot action.

## 10. Map Rendering

**PyQGIS cannot run inside the regular server process.**
`scripts/13b_build_qgis_project_pyqgis.py`'s own docstring already
establishes this: `from qgis.core import ...` only works through QGIS's
own bundled Python interpreter (`C:\Program Files\QGIS 4.0.0\bin\python-qgis.bat`
on this machine), not the plain Python environment `python -m server`
runs in - which is exactly why `13b_...` is a standalone script never
wired into `run_all.py`/`run_downstream.py` (those use the hand-authored-XML
`13_build_qgis_project.py` instead, which imports nothing from `qgis`).

So `scripts/lib/qgis_render.py` is a standalone **script** (not an
importable library function despite the `lib/` location - kept there
because it's shared logic invoked the same way from both the bot handler
and, potentially, a future web route, not because it's imported directly):
run as `python-qgis.bat scripts/lib/qgis_render.py <qgz_path> <output_png_path>`.
It initializes a headless `QgsApplication(argv, False)` (matching
`13b_build_qgis_project_pyqgis.py`'s own initialization), loads the
`.qgz` project via `QgsProject.read()`, builds `QgsMapSettings` from the
project's registered layers and full extent, renders via
`QgsMapRendererParallelJob`, and writes the resulting `QImage` to the
given output path as PNG.

The `/map` bot handler (running in the regular environment) invokes this
via `subprocess.run([QGIS_PYTHON_PATH, str(RENDER_SCRIPT), qgz_path, tmp_png_path], ...)`,
the same `capture_output=True`/`returncode` pattern the admin routes
already use for `run_downstream.py`, then reads `tmp_png_path`'s bytes.

This is genuinely new code (no existing "render to image" capability in
this codebase), but reuses an already-proven toolchain - **not** related
to the unrelated, previously-documented "QGIS desktop screenshot capture
is structurally unreliable in this environment" limitation from past
sessions, which was about the harness's own tool-execution overlay
compositing on top of a screen-captured image of the desktop QGIS
*application window*. `QgsMapRendererParallelJob` renders directly into
an in-memory image buffer with no screen capture involved at all, and
launching QGIS-family processes via subprocess is the part past sessions
already confirmed works fine - only capturing a screenshot of the
running desktop app was ever the unreliable part, and that's not what
this does.

## 11. Error Handling

Every handler catches its own failure modes and replies with a plain
message - never a raw traceback into a Telegram chat. `AIProviderError`,
`SupplementalDataError`-equivalent, and PyQGIS rendering failures all get
the same "safe to show directly" treatment every other error path in this
project already follows.

## 12. Testing

- `server/telegram_bot.py`: every handler tested with a mocked `Update`/
  `Context` (matching `python-telegram-bot`'s own testing conventions),
  no real Telegram API call in any automated test. Authorization check
  tested independently (allowed user id passes, any other id rejected).
- `server/bot_facilities.py`: same test shape as `supplemental_data.py`/
  `metric_overrides.py` (round-trip, backfill, `delete_record`).
- `scripts/lib/qgis_render.py`: **no automated pytest coverage** -
  verified, checked: no test in this project's suite imports `qgis` at
  all, matching `13b_build_qgis_project_pyqgis.py`'s own established
  precedent of zero automated coverage for anything requiring QGIS's
  bundled Python interpreter (`pytest tests/` runs under the regular
  environment, which cannot import PyQGIS at all - see section 10).
  Verified only manually: run it directly via `python-qgis.bat` against
  the real `.qgz` file and confirm a real, non-trivial PNG is produced
  (file exists, non-zero size, correct dimensions).
- `scripts/07_merge_facilities.py`: new test(s) for the fourth
  (`bot_facilities.csv`) source, including a dedup-against-existing-source
  case.
- `scripts/run_downstream_facilities.py`: matching
  `run_downstream.py`'s own (lack of) dedicated test - it's a thin stage
  runner, verified via the manual end-to-end pass instead.
- **Manual verification** (this project's established cadence for
  anything touching a real external API): create a real Telegram bot via
  @BotFather, configure its token + the user's real Telegram user ID
  through the admin panel, and exercise all six commands against the
  real running bot from a real Telegram client - including a real
  `/addpoint` flow (confirm the new facility appears in the report/map
  and the admin panel's new table), a real `/setkey`/`/keys` round trip,
  and confirming an unauthorized second Telegram account gets rejected.
