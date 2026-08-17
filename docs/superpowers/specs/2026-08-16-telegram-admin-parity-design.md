# Telegram Admin Parity — Design

## 1. Goal

Every admin-panel capability except the bot's own configuration (token,
allowed user id — kept admin-panel-only by deliberate design, since the
bot editing the credential that authenticates it is self-referentially
risky) becomes usable from Telegram chat: document extraction and
add-to-report, managing Supplemental Records / Pipeline Overrides /
Bot-Added Facilities (list + delete), applying a new pipeline override,
Custom Data Tables (create / add rows / delete), and Database Ingestion
(connect / list tables / preview / add to report).

This scope, including Database Ingestion, was explicitly confirmed by the
user via `AskUserQuestion` after being shown the trade-off that DB
credentials typed into Telegram chat remain in that chat's permanent
history (the same trade-off already accepted for `/setkey`'s AI provider
keys, but with a bigger blast radius).

## 2. Non-goals

- The bot's own token/allowed-user-id stays admin-panel-only (unchanged).
- No pagination beyond a hard row cap with a "+N more, see admin panel"
  note — Telegram caps a single message at 4096 characters.
- No inline editing of an AI-proposed table schema over chat — accept the
  proposal as-is, or redo it by describing the columns manually. Full
  per-column editing (the admin panel's editable form) doesn't map
  cleanly onto a chat conversation without a much heavier UI (Telegram
  inline forms don't exist as a primitive); this is a deliberate scope
  cut, not an oversight.
- No new authentication/authorization model. Every new command reuses the
  bot's existing single-allowlisted-Telegram-user check (`_authorized()`
  in `server/telegram_bot.py`) exactly as every existing command does.
- No automated tests for the Telegram-specific glue itself (matches this
  file's own established precedent — `server/telegram_bot.py` has zero
  test coverage today; every handler, including the existing `/addpoint`
  conversation, was verified live only, since testing a real
  `python-telegram-bot` conversation flow requires either a live bot
  account or heavy mocking of the library's internals that would test the
  mock more than the code). New **pure** parsing helpers introduced by
  this feature (the column mini-DSL parser, the pipe-delimited row
  parser) are the exception — same "pure functions get unit tests,
  network/UI glue gets live verification" split this project uses
  everywhere else.

## 3. Architecture

`server/telegram_bot.py` (296 lines today) stays as the application
bootstrap (`build_application`, `start_bot_task`/`stop_bot_task`,
`_authorized`, `HELP_TEXT`) plus every command it already has
(`/start /report /map /ask /keys /setkey /addpoint /cancel`), and adds
each new module's handlers to the same `Application` in
`build_application()`. Splitting into new modules — rather than growing
this one file to ~1200 lines — follows this project's own
"split when a file grows unwieldy" convention:

- **`server/telegram_admin_records.py`** (new) — `/addrecord`,
  `/supplemental`, `/overrides`, `/facilities`, `/override`. Reuses
  `document_extraction`, `supplemental_data`, `metric_overrides`,
  `bot_facilities` exactly as `server/routes/admin.py`'s routes do.
- **`server/telegram_admin_tables.py`** (new) — `/tables`, `/newtable`,
  `/addrow`. Reuses `custom_data` exactly as the admin routes do.
- **`server/telegram_admin_db.py`** (new) — `/dbconnect`, `/dbtables`,
  `/dbpreview`, `/dbingest`. Reuses `keystore.set_db_connection`/
  `get_db_connection` and `db_ingestion` exactly as the admin routes do.
- **`server/telegram_rebuild.py`** (new) — three tiny helpers,
  `rebuild_report()`, `rebuild_downstream()`, and
  `rebuild_downstream_facilities()`, each wrapping the "`subprocess.run`
  the rebuild script, catch `TimeoutExpired`, check `returncode`, return
  `(ok, warning_text_or_None)`" pattern that's already repeated 8 times
  across `server/routes/admin.py`'s routes — one helper per rebuild
  script the admin routes actually use (`14_build_html_report.py`,
  `run_downstream.py`, `run_downstream_facilities.py` respectively; the
  third is also what `/addpoint`'s existing rebuild step in
  `telegram_bot.py` already runs inline today, and can switch to the
  shared helper as part of this work). Used by all three new modules
  above so this feature doesn't introduce that same repetition a further
  ~10 times. **`server/routes/admin.py`'s routes are not touched** —
  redirecting its 8 existing call sites to use this helper too would be
  a legitimate follow-up cleanup, but is out of scope here (this feature
  only needs the helper to exist for its own new code, and touching
  already-shipped, already-tested routes isn't part of what was asked).

Every new module exposes a `register(application)` function
(`application.add_handler(...)` for each of its commands/conversations),
called once from `telegram_bot.build_application()`. `HELP_TEXT` is
extended with the new commands, grouped under a new admin-only heading
inside the same text.

## 4. Conversation design

`/addpoint` already establishes the pattern this feature follows
throughout: a `ConversationHandler` with small integer states, one state
per prompt, a `/cancel` fallback that clears `context.user_data` and
ends the conversation. Every new multi-step command gets its own
`ConversationHandler` with its own state constants (python-telegram-bot
tracks conversation state per-handler-instance, keyed by chat, so state
integers can freely repeat across different `ConversationHandler`s
without collision — this project's existing single-conversation
`/addpoint` doesn't need to change for that reason) **and its own
`/cancel` fallback** — `/addpoint`'s existing `CommandHandler("cancel",
addpoint_cancel)` is scoped to its own `ConversationHandler` and isn't
shared automatically; each new conversation registers an equally trivial
`CommandHandler("cancel", <name>_cancel)` of its own (clear
`user_data`, reply "Cancelled.", return `ConversationHandler.END` — the
same three lines every time). Multiple `ConversationHandler`s each
listening for `/cancel` coexist fine in one `Application`: only the
conversation currently active for that chat consumes it.

### 4.1 `/addrecord` — Extract + Add to Report

1. Bot: "Send me the document (PDF, Word, Excel, text, CSV, or HTML)."
2. User sends a `Document` message. Bot downloads it
   (`await message.document.get_file()` → `await file.download_as_bytearray()`),
   runs `document_extraction.extract(filename, bytes)` — on
   `UnsupportedFormatError`/`ExtractionError`, reply with the error and
   re-prompt in the same state (don't advance/cancel — mirrors the web
   form staying on the page after a failed extraction).
3. Bot: "Any instruction? Send one, or /skip."
4. Bot: shows an inline keyboard of AI providers that currently have a
   key configured (`keystore.list_status()`, same set `/setkey`/`/keys`
   already expose) — tapping one calls
   `supplemental_data.add_from_document(provider, key, text, instruction, filename)`,
   then `telegram_rebuild.rebuild_report()`, then replies with the added
   record(s) summary + any rebuild warning. If no provider has a key
   configured, skip the keyboard and reply "No AI provider configured -
   use /setkey first," ending the conversation (matches the admin
   route's own 400 for this case).

### 4.2 `/supplemental`, `/overrides`, `/facilities` — list + delete

Not conversations — single commands. Each calls its module's
`load_records()`, formats up to 20 rows as numbered lines (district /
key fields / added_at, mirroring the admin table's own columns), and
attaches one inline button per row: `InlineKeyboardButton("Delete #3",
callback_data="del:<store>:<record_id>")`, where `<store>` is one of
`supplemental`, `overrides`, `facilities` (and, from 4.4 below, `table`
for Custom Data Tables). A shared `CallbackQueryHandler` (registered
once, pattern `^del:`) splits `callback_data` on `:` to get `<store>`
and `<record_id>`, calls
the matching module's `delete_record(record_id)`, then runs whichever
rebuild the equivalent admin route triggers for that store —
`rebuild_report()` for Supplemental Records, `rebuild_downstream()` for
Pipeline Overrides, `rebuild_downstream_facilities()` for Bot-Added
Facilities (three distinct scripts, matching `server/routes/admin.py`'s
own three distinct delete routes exactly) — and edits the original
message to strike the deleted row and report any rebuild warning. If
there are more than 20 records, the message ends with "+N more — use
the admin panel to see the rest."

### 4.3 `/override` — apply a new pipeline override

1. Bot: "Describe the update, or send a document." (accepts either a
   text message or a `Document` in the same state)
2. If text: used directly as `document_text` (source_document="Telegram
   message"). If a document: extracted the same way as `/addrecord`
   step 2.
3. Bot: shows the AI-provider inline keyboard (same as 4.1) → calls
   `metric_overrides.add_from_document(provider, key, text, "", source)`,
   then `telegram_rebuild.rebuild_downstream()` (matches the admin
   route's own downstream — not just report — rebuild for overrides),
   replies with the applied update(s) + any warning.

### 4.4 `/tables`, `/newtable`, `/addrow` — Custom Data Tables

- **`/tables`**: lists every table (label, column count, row count) with
  an inline "Delete Table" button per row
  (`callback_data="del:table:<table_id>"`, same shared handler pattern
  as 4.2) → `custom_data.delete_table(table_id)` +
  `telegram_rebuild.rebuild_report()`.
- **`/newtable`**: conversation. Bot: "Table name?" → text. Bot: inline
  choice "Describe columns myself" / "Let AI propose them." Manual path:
  "List columns as `name:type, name:type` (type is text, number, or
  date)," parsed by a new pure function `parse_column_spec(text)` in
  `telegram_admin_tables.py` (raises a `ValueError` with a clear message
  on a bad type/malformed entry, caught and re-prompted in the same
  state — never silently drops a column). AI path: "Describe what this
  table should track" → `custom_data.propose_schema(provider, key,
  description)` (provider chosen via the same inline keyboard) → bot
  shows the proposed columns as text and asks "Create with these
  columns? yes / no" — "no" drops back to the manual-columns prompt
  rather than ending the conversation. Either path ends with
  `custom_data.create_table(label, columns)`.
- **`/addrow <table>`**: conversation, table resolved by label (case-
  insensitive match against `custom_data.list_tables()`; ambiguous/
  missing name replies with the real options and ends). Bot shows the
  table's columns, then: "Send values as `val1 | val2 | val3` (matching
  column order), or send a document for AI extraction." Manual path:
  parsed by a new pure function `parse_pipe_row(text, columns)`
  (splits on `|`, strips whitespace, maps positionally, raises
  `ValueError` on a count mismatch). Document path: extracted the same
  way as 4.1, then the AI-provider inline keyboard (same as 4.1),
  then `custom_data.preview_extraction(...)`. **Both paths then show the
  AI-provider inline keyboard if it hasn't been shown yet** —
  `add_rows()` always needs a provider even for a manually-typed row, to
  run the report-placement AI call (the same "manual entries still need
  *some* provider selected, purely for placement" behavior the admin
  panel's editable grid already has) — then show the row back to the
  user as "Add this row? yes / no" before calling
  `custom_data.add_rows(table_id, [row], provider, key)` — the same
  preview-before-commit guarantee the admin panel's editable grid
  provides, just condensed into a single yes/no instead of an editable
  grid (chat has no in-place grid editing primitive; retyping is the
  correction path, same as `/newtable`'s AI-schema retry). Either path
  ends with `telegram_rebuild.rebuild_report()`.

### 4.5 `/dbconnect`, `/dbtables`, `/dbpreview`, `/dbingest` — Database Ingestion

- **`/dbconnect`**: conversation. First message: "This will ask for
  database credentials, which will remain in this chat's history -
  reply yes to continue, or /cancel." Only on "yes" does it proceed to
  ask host → port → database name → username → password → sslmode
  (optional, "skip" allowed) in sequence, one message each (mirrors
  `/addpoint`'s one-prompt-per-state style). Calls
  `keystore.set_db_connection(conn_info)` then
  `db_ingestion.test_connection(conn_info)`, replies with the result.
- **`/dbtables`**: single command, `db_ingestion.list_tables(...)`,
  replies with the list (or the admin route's own "no connection
  configured" message if none is saved).
- **`/dbpreview <table>`**: single command,
  `db_ingestion.fetch_table_text(conn_info, table)`, replies with the
  text truncated to ~3800 characters with a "(truncated)" note if longer.
- **`/dbingest <table>`**: conversation. Same AI-provider inline keyboard
  as 4.1 → `db_ingestion.fetch_table_text(...)` →
  `supplemental_data.add_from_document(provider, key, text, "",
  f"db:{table}")` → `telegram_rebuild.rebuild_report()`.

## 5. Error handling conventions (apply to every new command)

- Every handler starts with the existing `_authorized(update)` check,
  replying "Not authorized." and returning `ConversationHandler.END`
  (or just returning, for non-conversation commands) exactly like every
  existing command.
- Every domain-layer `*Error` exception (`SupplementalDataError`,
  `MetricOverrideError`, `CustomDataError`, `DbIngestionError`) is caught
  at the point it's raised and its message sent back to the user
  directly — these are already written to be safe/user-facing text (the
  same messages the admin panel shows), never a raw traceback.
  `ai_client.AIProviderError` is caught the same way `/ask` already
  catches it.
- Any unexpected exception inside a conversation step is **not** swallowed
  silently: it propagates (python-telegram-bot logs it and the
  conversation simply doesn't advance), matching this project's existing
  `/addpoint`/`/map` behavior — no new global catch-all is introduced.

## 6. `HELP_TEXT`

Extended with a new block under the existing command list:

```
Admin (same access as the web admin panel):
/addrecord - extract a document and add it to the report
/supplemental, /overrides, /facilities - list/delete records
/override - apply a new pipeline data override
/tables, /newtable, /addrow <table> - manage custom data tables
/dbconnect, /dbtables, /dbpreview <table>, /dbingest <table> - database ingestion
```

## 7. Testing plan

- `parse_column_spec()` and `parse_pipe_row()` (both pure functions, no
  Telegram/network dependency) get real unit tests: valid input, bad
  type name, wrong value count, extra/missing whitespace.
- `telegram_rebuild.rebuild_report()`/`rebuild_downstream()`/
  `rebuild_downstream_facilities()` get unit tests with `subprocess.run`
  mocked (success, non-zero returncode, `TimeoutExpired`) — same shape
  as this project's existing subprocess-wrapping tests elsewhere.
- Everything else (the conversation handlers themselves) is verified
  live against the real bot, driving the user's own logged-in Telegram
  Web session in Chrome with explicit permission (the established
  pattern from the original Telegram Connector feature) — covering at
  least one full run of each new command, plus the inline-delete flow
  for `/supplemental`/`/tables`.
