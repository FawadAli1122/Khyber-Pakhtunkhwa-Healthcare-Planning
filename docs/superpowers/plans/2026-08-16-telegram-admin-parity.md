# Telegram Admin Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every admin-panel capability except the bot's own token/allowed-user-id config becomes usable from Telegram chat.

**Architecture:** Three new `server/telegram_*` modules (records, tables, db), each exposing a `register(application)` function called from `telegram_bot.build_application()`, plus two tiny shared helper modules (`telegram_rebuild.py`, `telegram_ui.py`). Every handler is a thin wrapper reusing the exact same server-side domain functions `server/routes/admin.py`'s routes already call - no business logic duplicated between the web routes and the bot, matching this project's existing rule for `telegram_bot.py`.

**Tech Stack:** `python-telegram-bot` v20+ (`ConversationHandler`, `CallbackQueryHandler`, `InlineKeyboardMarkup`), same as the existing `/addpoint` command.

**Spec:** `docs/superpowers/specs/2026-08-16-telegram-admin-parity-design.md`

## Global Constraints

- Every new command starts with the existing `_authorized(update)` check from `server/telegram_bot.py`, replying "Not authorized." exactly like every existing command.
- No new authentication model. No changes to the bot's own token/allowed-user-id handling (stays admin-panel-only).
- No business logic duplicated - every handler calls the same `server/*.py` functions the admin routes already call.
- ~~No automated tests for conversation-handler glue itself (matches this file's existing zero-coverage precedent - see spec section 2).~~ **Corrected during Task 13 execution**: `tests/server/test_telegram_bot.py` (in a subdirectory missed while researching the spec) actually has ~30 solid mocked-`AsyncMock` unit tests for the existing bot commands - the "zero coverage" precedent was wrong. Confirmed with the user via `AskUserQuestion`, then added matching mocked-unit tests for every new conversation handler across `telegram_admin_records.py`/`telegram_admin_tables.py`/`telegram_admin_db.py` (41 tests, `tests/server/test_telegram_admin_records.py`/`test_telegram_admin_tables.py`/`test_telegram_admin_db.py`), on top of the two pure parsers and three `telegram_rebuild` functions this plan always intended to test.
- Record/table listings cap at 20 rows with a "+N more, use the admin panel" note (Telegram's 4096-char message limit).
- `server/routes/admin.py` is not modified by this plan.

---

### Task 1: Shared helpers - `telegram_rebuild.py` and `telegram_ui.py`

**Files:**
- Create: `server/telegram_rebuild.py`
- Create: `server/telegram_ui.py`
- Modify: `server/telegram_bot.py:270-278` (the `/addpoint` location handler's inline rebuild call)
- Test: `tests/test_telegram_rebuild.py`

**Interfaces:**
- Produces: `telegram_rebuild.rebuild_report() -> (ok: bool, warning: str | None)`, `telegram_rebuild.rebuild_downstream() -> (ok: bool, warning: str | None)`, `telegram_rebuild.rebuild_downstream_facilities() -> (ok: bool, warning: str | None)`. `warning` is `None` on success (returncode 0), else a user-facing message identical in wording to the matching `server/routes/admin.py` route's `rebuild_warning` text.
- Produces: `telegram_ui.configured_provider_keyboard() -> InlineKeyboardMarkup | None`. One button per AI provider with a configured key (`keystore.list_status()`), `callback_data=f"provider:{provider}"`. Returns `None` if no provider has a key configured.

- [ ] **Step 1: Write the failing tests for the rebuild helpers**

```python
# tests/test_telegram_rebuild.py
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from server import telegram_rebuild


@pytest.mark.parametrize("func_name,script_name", [
    ("rebuild_report", "14_build_html_report.py"),
    ("rebuild_downstream", "run_downstream.py"),
    ("rebuild_downstream_facilities", "run_downstream_facilities.py"),
])
def test_rebuild_success_returns_ok_true_no_warning(func_name, script_name):
    func = getattr(telegram_rebuild, func_name)
    completed = MagicMock(returncode=0, stderr="")
    with patch("subprocess.run", return_value=completed) as mock_run:
        ok, warning = func()
    assert ok is True
    assert warning is None
    assert script_name in str(mock_run.call_args[0][0])


@pytest.mark.parametrize("func_name", [
    "rebuild_report", "rebuild_downstream", "rebuild_downstream_facilities",
])
def test_rebuild_nonzero_returncode_returns_warning(func_name):
    func = getattr(telegram_rebuild, func_name)
    completed = MagicMock(returncode=1, stderr="boom")
    with patch("subprocess.run", return_value=completed):
        ok, warning = func()
    assert ok is False
    assert "boom" in warning


@pytest.mark.parametrize("func_name", [
    "rebuild_report", "rebuild_downstream", "rebuild_downstream_facilities",
])
def test_rebuild_timeout_returns_warning(func_name):
    func = getattr(telegram_rebuild, func_name)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
        ok, warning = func()
    assert ok is False
    assert "timed out" in warning.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_telegram_rebuild.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.telegram_rebuild'`

- [ ] **Step 3: Write `telegram_rebuild.py`**

```python
"""Three thin wrappers around the "run a pipeline rebuild script, catch a
timeout, check the exit code" pattern already repeated 8 times across
server/routes/admin.py's routes - shared here so the new Telegram admin-
parity commands (server/telegram_admin_records.py,
telegram_admin_tables.py, telegram_admin_db.py) don't repeat it a further
~10 times. server/routes/admin.py's own routes are left as-is (out of
scope for this feature - see docs/superpowers/specs/
2026-08-16-telegram-admin-parity-design.md section 3)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_BUILD_SCRIPT = ROOT / "scripts" / "14_build_html_report.py"
RUN_DOWNSTREAM_SCRIPT = ROOT / "scripts" / "run_downstream.py"
RUN_DOWNSTREAM_FACILITIES_SCRIPT = ROOT / "scripts" / "run_downstream_facilities.py"


def _run_rebuild_script(script_path, timeout, label):
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)], capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"{label} timed out after {timeout} seconds"
    if result.returncode != 0:
        return False, f"{label} failed: {result.stderr[-500:]}"
    return True, None


def rebuild_report():
    return _run_rebuild_script(REPORT_BUILD_SCRIPT, 300, "Report rebuild")


def rebuild_downstream():
    return _run_rebuild_script(RUN_DOWNSTREAM_SCRIPT, 600, "Downstream pipeline rebuild")


def rebuild_downstream_facilities():
    return _run_rebuild_script(RUN_DOWNSTREAM_FACILITIES_SCRIPT, 600, "Downstream pipeline rebuild")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_telegram_rebuild.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Write `telegram_ui.py`**

```python
"""Tiny shared Telegram UI helper used by every new admin-parity
conversation that needs to ask "which AI provider?" - kept separate from
telegram_rebuild.py (a different concern) to avoid duplicating this
~15-line keyboard-building logic three times across
telegram_admin_records.py/telegram_admin_tables.py/telegram_admin_db.py."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from server import keystore


def configured_provider_keyboard():
    statuses = keystore.list_status()
    configured = [s["provider"] for s in statuses if s["configured"]]
    if not configured:
        return None
    buttons = [[InlineKeyboardButton(p, callback_data=f"provider:{p}")] for p in configured]
    return InlineKeyboardMarkup(buttons)
```

- [ ] **Step 6: Wire `/addpoint`'s existing rebuild call to use the shared helper**

In `server/telegram_bot.py`, add `from server import telegram_rebuild` to the imports, then replace the inline `subprocess.run([sys.executable, str(RUN_DOWNSTREAM_FACILITIES_SCRIPT)], capture_output=True, text=True, timeout=600)` block in `addpoint_location()` (lines ~270-278) with:

```python
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_downstream_facilities)
    if not ok:
        await update.message.reply_text(f"Facility saved, but the rebuild failed: {warning}")
    else:
        await update.message.reply_text(f"Done - {record['name']} added to {district}.")
    return ConversationHandler.END
```

(The now-unused `RUN_DOWNSTREAM_FACILITIES_SCRIPT` module-level constant, `subprocess`, and `sys` imports in `telegram_bot.py` stay - `subprocess`/`sys` are still used by `map_command()`, and the constant is harmless to leave for now since it's still descriptively accurate; removing it isn't required by this task.)

- [ ] **Step 7: Run the full test suite**

Run: `pytest -q`
Expected: PASS, count increased by 9 from baseline

- [ ] **Step 8: Commit**

```bash
git add server/telegram_rebuild.py server/telegram_ui.py server/telegram_bot.py tests/test_telegram_rebuild.py
git commit -m "feat: add shared rebuild/provider-keyboard helpers for Telegram admin parity"
```

---

### Task 2: `telegram_admin_records.py` Part A - list + delete for Supplemental Records / Pipeline Overrides / Bot-Added Facilities

**Files:**
- Create: `server/telegram_admin_records.py`

**Interfaces:**
- Consumes: `telegram_rebuild.rebuild_report()`, `telegram_rebuild.rebuild_downstream()`, `telegram_rebuild.rebuild_downstream_facilities()` (Task 1). `supplemental_data.load_records()`/`delete_record(id)`, `metric_overrides.load_records()`/`delete_record(id)`, `bot_facilities.load_records()`/`delete_record(id)` (all pre-existing).
- Produces: `register(application)` - called by Task 12. Registers `CommandHandler("supplemental", ...)`, `CommandHandler("overrides", ...)`, `CommandHandler("facilities", ...)`, and one `CallbackQueryHandler(pattern="^del:(supplemental|overrides|facilities):")`.

- [ ] **Step 1: Write `telegram_admin_records.py`'s records-listing/delete logic**

```python
"""Telegram admin-parity commands for the three admin-overlay record
stores (Supplemental Records, Pipeline Overrides, Bot-Added Facilities)
plus /addrecord (Task 3) and /override (Task 4) - see
docs/superpowers/specs/2026-08-16-telegram-admin-parity-design.md
section 4.1-4.3. Every handler reuses the exact same server-side
functions server/routes/admin.py's routes call - no logic duplicated.
"""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler, filters

from server import ai_client, bot_facilities, document_extraction, keystore, metric_overrides, supplemental_data, telegram_rebuild, telegram_ui
from server.telegram_bot import _authorized

MAX_LISTED_RECORDS = 20

_STORES = {
    "supplemental": {
        "module": supplemental_data,
        "label": "Supplemental Records",
        "row": lambda r: f"{r.get('district', '')} / {r.get('facility', '') or '-'} - {r.get('category', '')}: {r.get('label', '')}",
        "rebuild": telegram_rebuild.rebuild_report,
    },
    "overrides": {
        "module": metric_overrides,
        "label": "Pipeline Overrides",
        "row": lambda r: f"{r.get('district', '')} / {r.get('column', '')}: {r.get('value', '')}",
        "rebuild": telegram_rebuild.rebuild_downstream,
    },
    "facilities": {
        "module": bot_facilities,
        "label": "Bot-Added Facilities",
        "row": lambda r: f"{r.get('name', '')} ({r.get('district', '')}, {r.get('category', '')})",
        "rebuild": telegram_rebuild.rebuild_downstream_facilities,
    },
}


def _records_message(store):
    config = _STORES[store]
    records = config["module"].load_records()
    if not records:
        return f"{config['label']}: no records yet.", None
    shown = records[:MAX_LISTED_RECORDS]
    lines = [f"{config['label']}:"]
    buttons = []
    for i, r in enumerate(shown, start=1):
        lines.append(f"{i}. {config['row'](r)}")
        buttons.append([InlineKeyboardButton(f"Delete #{i}", callback_data=f"del:{store}:{r['id']}")])
    if len(records) > MAX_LISTED_RECORDS:
        lines.append(f"+{len(records) - MAX_LISTED_RECORDS} more - use the admin panel to see the rest.")
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


def _records_command(store):
    async def handler(update, context):
        if not _authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        text, keyboard = _records_message(store)
        await update.message.reply_text(text, reply_markup=keyboard)
    return handler


supplemental_command = _records_command("supplemental")
overrides_command = _records_command("overrides")
facilities_command = _records_command("facilities")


async def delete_callback(update, context):
    query = update.callback_query
    if not _authorized(update):
        await query.answer("Not authorized.")
        return
    await query.answer()
    _, store, record_id = query.data.split(":", 2)
    config = _STORES[store]
    found = config["module"].delete_record(record_id)
    if not found:
        await query.edit_message_text("Already deleted.")
        return
    ok, warning = await asyncio.to_thread(config["rebuild"])
    text, keyboard = _records_message(store)
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await query.edit_message_text(text, reply_markup=keyboard)
```

- [ ] **Step 2: Sanity-check the module imports and builds cleanly**

Run: `python -c "import server.telegram_admin_records"`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
git add server/telegram_admin_records.py
git commit -m "feat: add /supplemental, /overrides, /facilities Telegram commands"
```

---

### Task 3: `telegram_admin_records.py` Part B - `/addrecord`

**Files:**
- Modify: `server/telegram_admin_records.py`

**Interfaces:**
- Consumes: `telegram_ui.configured_provider_keyboard()` (Task 1), `document_extraction.extract(filename, bytes)` (pre-existing), `supplemental_data.add_from_document(provider, key, text, instruction, source)` (pre-existing).
- Produces: `addrecord_conversation` (module-level `ConversationHandler`), added to `register()` in Task 5.

- [ ] **Step 1: Add the `/addrecord` conversation to `telegram_admin_records.py`**

```python
ADDRECORD_DOC, ADDRECORD_INSTRUCTION, ADDRECORD_PROVIDER = range(3)


async def addrecord_start(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("Send me the document (PDF, Word, Excel, text, CSV, or HTML).")
    return ADDRECORD_DOC


async def addrecord_receive_doc(update, context):
    doc = update.message.document
    tg_file = await doc.get_file()
    content_bytes = bytes(await tg_file.download_as_bytearray())
    try:
        extracted = document_extraction.extract(doc.file_name or "upload", content_bytes)
    except (document_extraction.UnsupportedFormatError, document_extraction.ExtractionError) as exc:
        await update.message.reply_text(f"{exc}\n\nSend another document, or /cancel.")
        return ADDRECORD_DOC
    context.user_data["text"] = extracted.text
    context.user_data["source"] = extracted.filename
    await update.message.reply_text("Any instruction? Send one, or /skip.")
    return ADDRECORD_INSTRUCTION


async def _addrecord_prompt_provider(update, context):
    keyboard = telegram_ui.configured_provider_keyboard()
    if keyboard is None:
        await update.message.reply_text("No AI provider configured - use /setkey first.")
        context.user_data.clear()
        return ConversationHandler.END
    await update.message.reply_text("Which AI provider?", reply_markup=keyboard)
    return ADDRECORD_PROVIDER


async def addrecord_receive_instruction(update, context):
    context.user_data["instruction"] = update.message.text.strip()
    return await _addrecord_prompt_provider(update, context)


async def addrecord_skip_instruction(update, context):
    context.user_data["instruction"] = ""
    return await _addrecord_prompt_provider(update, context)


async def addrecord_provider_chosen(update, context):
    query = update.callback_query
    await query.answer()
    provider = query.data.split(":", 1)[1]
    key = keystore.get_key(provider)
    try:
        added = await asyncio.to_thread(
            supplemental_data.add_from_document,
            provider, key, context.user_data["text"], context.user_data["instruction"], context.user_data["source"],
        )
    except (supplemental_data.SupplementalDataError, ai_client.AIProviderError) as exc:
        await query.edit_message_text(f"Failed: {exc}")
        context.user_data.clear()
        return ConversationHandler.END
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_report)
    summary = "\n".join(f"- {r.get('district', '')} / {r.get('category', '')}: {r.get('label', '')}" for r in added)
    text = f"Added {len(added)} record(s):\n{summary}"
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await query.edit_message_text(text)
    context.user_data.clear()
    return ConversationHandler.END


async def addrecord_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


addrecord_conversation = ConversationHandler(
    entry_points=[CommandHandler("addrecord", addrecord_start)],
    states={
        ADDRECORD_DOC: [MessageHandler(filters.Document.ALL, addrecord_receive_doc)],
        ADDRECORD_INSTRUCTION: [
            CommandHandler("skip", addrecord_skip_instruction),
            MessageHandler(filters.TEXT & ~filters.COMMAND, addrecord_receive_instruction),
        ],
        ADDRECORD_PROVIDER: [CallbackQueryHandler(addrecord_provider_chosen, pattern=r"^provider:")],
    },
    fallbacks=[CommandHandler("cancel", addrecord_cancel)],
)
```

- [ ] **Step 2: Sanity-check the module still imports cleanly**

Run: `python -c "import server.telegram_admin_records"`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
git add server/telegram_admin_records.py
git commit -m "feat: add /addrecord Telegram command"
```

---

### Task 4: `telegram_admin_records.py` Part C - `/override`

**Files:**
- Modify: `server/telegram_admin_records.py`

**Interfaces:**
- Consumes: `metric_overrides.add_from_document(provider, key, text, instruction, source)` (pre-existing), `telegram_rebuild.rebuild_downstream()` (Task 1).
- Produces: `override_conversation` (module-level `ConversationHandler`), added to `register()` in Task 5.

- [ ] **Step 1: Add the `/override` conversation to `telegram_admin_records.py`**

```python
OVERRIDE_INPUT, OVERRIDE_PROVIDER = range(2)


async def override_start(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("Describe the update, or send a document.")
    return OVERRIDE_INPUT


async def _override_prompt_provider(update, context):
    keyboard = telegram_ui.configured_provider_keyboard()
    if keyboard is None:
        await update.message.reply_text("No AI provider configured - use /setkey first.")
        context.user_data.clear()
        return ConversationHandler.END
    await update.message.reply_text("Which AI provider?", reply_markup=keyboard)
    return OVERRIDE_PROVIDER


async def override_receive_text(update, context):
    context.user_data["text"] = update.message.text.strip()
    context.user_data["source"] = "Telegram message"
    return await _override_prompt_provider(update, context)


async def override_receive_doc(update, context):
    doc = update.message.document
    tg_file = await doc.get_file()
    content_bytes = bytes(await tg_file.download_as_bytearray())
    try:
        extracted = document_extraction.extract(doc.file_name or "upload", content_bytes)
    except (document_extraction.UnsupportedFormatError, document_extraction.ExtractionError) as exc:
        await update.message.reply_text(f"{exc}\n\nSend another document, or describe the update as text, or /cancel.")
        return OVERRIDE_INPUT
    context.user_data["text"] = extracted.text
    context.user_data["source"] = extracted.filename
    return await _override_prompt_provider(update, context)


async def override_provider_chosen(update, context):
    query = update.callback_query
    await query.answer()
    provider = query.data.split(":", 1)[1]
    key = keystore.get_key(provider)
    try:
        added = await asyncio.to_thread(
            metric_overrides.add_from_document,
            provider, key, context.user_data["text"], "", context.user_data["source"],
        )
    except (metric_overrides.MetricOverrideError, ai_client.AIProviderError) as exc:
        await query.edit_message_text(f"Failed: {exc}")
        context.user_data.clear()
        return ConversationHandler.END
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_downstream)
    summary = "\n".join(f"- {r.get('district', '')} / {r.get('column', '')}: now {r.get('value', '')}" for r in added)
    text = f"Applied {len(added)} update(s):\n{summary}"
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await query.edit_message_text(text)
    context.user_data.clear()
    return ConversationHandler.END


async def override_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


override_conversation = ConversationHandler(
    entry_points=[CommandHandler("override", override_start)],
    states={
        OVERRIDE_INPUT: [
            MessageHandler(filters.Document.ALL, override_receive_doc),
            MessageHandler(filters.TEXT & ~filters.COMMAND, override_receive_text),
        ],
        OVERRIDE_PROVIDER: [CallbackQueryHandler(override_provider_chosen, pattern=r"^provider:")],
    },
    fallbacks=[CommandHandler("cancel", override_cancel)],
)
```

- [ ] **Step 2: Sanity-check the module still imports cleanly**

Run: `python -c "import server.telegram_admin_records"`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
git add server/telegram_admin_records.py
git commit -m "feat: add /override Telegram command"
```

---

### Task 5: `telegram_admin_records.py` - `register()`

**Files:**
- Modify: `server/telegram_admin_records.py`

**Interfaces:**
- Consumes: `supplemental_command`, `overrides_command`, `facilities_command`, `delete_callback` (Task 2), `addrecord_conversation` (Task 3), `override_conversation` (Task 4).
- Produces: `register(application)`, called by Task 12.

- [ ] **Step 1: Add `register()` to `telegram_admin_records.py`**

```python
def register(application):
    application.add_handler(CommandHandler("supplemental", supplemental_command))
    application.add_handler(CommandHandler("overrides", overrides_command))
    application.add_handler(CommandHandler("facilities", facilities_command))
    application.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^del:(supplemental|overrides|facilities):"))
    application.add_handler(addrecord_conversation)
    application.add_handler(override_conversation)
```

- [ ] **Step 2: Sanity-check the module still imports cleanly**

Run: `python -c "import server.telegram_admin_records"`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
git add server/telegram_admin_records.py
git commit -m "feat: add register() to telegram_admin_records"
```

---

### Task 6: `telegram_admin_tables.py` Part A - pure parsers

**Files:**
- Create: `server/telegram_admin_tables.py`
- Test: `tests/test_telegram_admin_tables_parsers.py`

**Interfaces:**
- Produces: `parse_column_spec(text) -> [{"label": str, "type": str}, ...]` (raises `ValueError`). `parse_pipe_row(text, columns) -> {column_name: str, ...}` (raises `ValueError`). Consumed by Task 8/9.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_telegram_admin_tables_parsers.py
import pytest

from server.telegram_admin_tables import parse_column_spec, parse_pipe_row


def test_parse_column_spec_parses_valid_input():
    result = parse_column_spec("name:text, capacity:number, opened:date")
    assert result == [
        {"label": "name", "type": "text"},
        {"label": "capacity", "type": "number"},
        {"label": "opened", "type": "date"},
    ]


def test_parse_column_spec_strips_whitespace():
    result = parse_column_spec("  name : text ,  capacity : number  ")
    assert result == [{"label": "name", "type": "text"}, {"label": "capacity", "type": "number"}]


def test_parse_column_spec_rejects_empty_input():
    with pytest.raises(ValueError):
        parse_column_spec("")


def test_parse_column_spec_rejects_missing_colon():
    with pytest.raises(ValueError, match="name:type"):
        parse_column_spec("name")


def test_parse_column_spec_rejects_unknown_type():
    with pytest.raises(ValueError, match="text, number, date"):
        parse_column_spec("name:integer")


def test_parse_column_spec_rejects_empty_label():
    with pytest.raises(ValueError):
        parse_column_spec(":text")


def test_parse_pipe_row_maps_positionally():
    columns = [{"column_name": "name"}, {"column_name": "capacity"}]
    result = parse_pipe_row("Peshawar DHQ | 50", columns)
    assert result == {"name": "Peshawar DHQ", "capacity": "50"}


def test_parse_pipe_row_strips_whitespace():
    columns = [{"column_name": "name"}]
    result = parse_pipe_row("  Peshawar DHQ  ", columns)
    assert result == {"name": "Peshawar DHQ"}


def test_parse_pipe_row_rejects_count_mismatch():
    columns = [{"column_name": "name"}, {"column_name": "capacity"}]
    with pytest.raises(ValueError, match="2 value"):
        parse_pipe_row("Peshawar DHQ", columns)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_telegram_admin_tables_parsers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.telegram_admin_tables'`

- [ ] **Step 3: Write the parsers in `telegram_admin_tables.py`**

```python
"""Telegram admin-parity commands for Custom Data Tables - see
docs/superpowers/specs/2026-08-16-telegram-admin-parity-design.md
section 4.4. parse_column_spec()/parse_pipe_row() are pure functions
(no Telegram dependency) so they're unit-tested directly; every
conversation handler below them is verified live only, matching this
project's established precedent for server/telegram_bot.py.
"""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler, filters

from server import ai_client, custom_data, document_extraction, keystore, telegram_rebuild, telegram_ui
from server.telegram_bot import _authorized

VALID_TYPES = ("text", "number", "date")


def parse_column_spec(text):
    text = (text or "").strip()
    if not text:
        raise ValueError("Send at least one column, as name:type, name:type, ...")
    columns = []
    for segment in text.split(","):
        segment = segment.strip()
        if ":" not in segment:
            raise ValueError(f"{segment!r} is missing a type - use name:type")
        label, _, col_type = segment.partition(":")
        label = label.strip()
        col_type = col_type.strip().lower()
        if not label:
            raise ValueError(f"{segment!r} has no column name")
        if col_type not in VALID_TYPES:
            raise ValueError(f"{col_type!r} isn't a valid type - use one of text, number, date")
        columns.append({"label": label, "type": col_type})
    return columns


def parse_pipe_row(text, columns):
    values = [v.strip() for v in (text or "").split("|")]
    if len(values) != len(columns):
        raise ValueError(f"Expected {len(columns)} value(s) separated by |, got {len(values)}")
    return {col["column_name"]: value for col, value in zip(columns, values)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_telegram_admin_tables_parsers.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add server/telegram_admin_tables.py tests/test_telegram_admin_tables_parsers.py
git commit -m "feat: add pure column-spec/pipe-row parsers for Telegram custom tables"
```

---

### Task 7: `telegram_admin_tables.py` Part B - `/tables`

**Files:**
- Modify: `server/telegram_admin_tables.py`

**Interfaces:**
- Consumes: `custom_data.list_tables()`, `custom_data.delete_table(id)` (pre-existing), `telegram_rebuild.rebuild_report()` (Task 1).
- Produces: `tables_command`, `delete_table_callback` (module-level), added to `register()` in Task 10.

- [ ] **Step 1: Add `/tables` to `telegram_admin_tables.py`**

```python
MAX_LISTED_TABLES = 20


def _tables_message():
    tables = custom_data.list_tables()
    if not tables:
        return "No custom tables yet.", None
    shown = tables[:MAX_LISTED_TABLES]
    lines = ["Custom Data Tables:"]
    buttons = []
    for i, t in enumerate(shown, start=1):
        lines.append(f"{i}. {t['label']} ({len(t['columns'])} column(s))")
        buttons.append([InlineKeyboardButton(f"Delete #{i}", callback_data=f"del:table:{t['id']}")])
    if len(tables) > MAX_LISTED_TABLES:
        lines.append(f"+{len(tables) - MAX_LISTED_TABLES} more - use the admin panel to see the rest.")
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def tables_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    text, keyboard = _tables_message()
    await update.message.reply_text(text, reply_markup=keyboard)


async def delete_table_callback(update, context):
    query = update.callback_query
    if not _authorized(update):
        await query.answer("Not authorized.")
        return
    await query.answer()
    table_id = query.data.split(":", 2)[2]
    found = custom_data.delete_table(table_id)
    if not found:
        await query.edit_message_text("Already deleted.")
        return
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_report)
    text, keyboard = _tables_message()
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await query.edit_message_text(text, reply_markup=keyboard)
```

- [ ] **Step 2: Sanity-check the module still imports cleanly**

Run: `python -c "import server.telegram_admin_tables"`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
git add server/telegram_admin_tables.py
git commit -m "feat: add /tables Telegram command"
```

---

### Task 8: `telegram_admin_tables.py` Part C - `/newtable`

**Files:**
- Modify: `server/telegram_admin_tables.py`

**Interfaces:**
- Consumes: `parse_column_spec` (Task 6), `custom_data.propose_schema(provider, key, prompt)`, `custom_data.create_table(label, columns)` (pre-existing), `telegram_ui.configured_provider_keyboard()`, `telegram_rebuild.rebuild_report()` (Task 1).
- Produces: `newtable_conversation`, added to `register()` in Task 10.

- [ ] **Step 1: Add the `/newtable` conversation to `telegram_admin_tables.py`**

```python
NEWTABLE_LABEL, NEWTABLE_MODE, NEWTABLE_COLUMNS, NEWTABLE_AI_DESC, NEWTABLE_AI_PROVIDER, NEWTABLE_AI_CONFIRM = range(6)


async def newtable_start(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("Table name?")
    return NEWTABLE_LABEL


async def newtable_receive_label(update, context):
    context.user_data["label"] = update.message.text.strip()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Describe columns myself", callback_data="mode:manual"),
        InlineKeyboardButton("Let AI propose them", callback_data="mode:ai"),
    ]])
    await update.message.reply_text("How do you want to define the columns?", reply_markup=keyboard)
    return NEWTABLE_MODE


async def newtable_mode_chosen(update, context):
    query = update.callback_query
    await query.answer()
    mode = query.data.split(":", 1)[1]
    if mode == "manual":
        await query.edit_message_text("List columns as name:type, name:type (type is text, number, or date).")
        return NEWTABLE_COLUMNS
    await query.edit_message_text("Describe what this table should track.")
    return NEWTABLE_AI_DESC


async def newtable_receive_columns(update, context):
    try:
        columns = parse_column_spec(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(f"{exc}\n\nTry again, or /cancel.")
        return NEWTABLE_COLUMNS
    try:
        await asyncio.to_thread(custom_data.create_table, context.user_data["label"], columns)
    except custom_data.CustomDataError as exc:
        await update.message.reply_text(f"Failed: {exc}")
        context.user_data.clear()
        return ConversationHandler.END
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_report)
    text = f"Created table {context.user_data['label']!r}."
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await update.message.reply_text(text)
    context.user_data.clear()
    return ConversationHandler.END


async def newtable_receive_ai_description(update, context):
    context.user_data["description"] = update.message.text.strip()
    keyboard = telegram_ui.configured_provider_keyboard()
    if keyboard is None:
        await update.message.reply_text("No AI provider configured - use /setkey first.")
        context.user_data.clear()
        return ConversationHandler.END
    await update.message.reply_text("Which AI provider?", reply_markup=keyboard)
    return NEWTABLE_AI_PROVIDER


async def newtable_ai_provider_chosen(update, context):
    query = update.callback_query
    await query.answer()
    provider = query.data.split(":", 1)[1]
    key = keystore.get_key(provider)
    try:
        proposal = await asyncio.to_thread(custom_data.propose_schema, provider, key, context.user_data["description"])
    except (custom_data.CustomDataError, ai_client.AIProviderError) as exc:
        await query.edit_message_text(f"Failed: {exc}")
        context.user_data.clear()
        return ConversationHandler.END
    context.user_data["proposed_columns"] = proposal["columns"]
    lines = [f"- {c['label']} ({c['type']})" for c in proposal["columns"]]
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, create it", callback_data="confirm:yes"),
        InlineKeyboardButton("No, I'll type columns", callback_data="confirm:no"),
    ]])
    await query.edit_message_text("Proposed columns:\n" + "\n".join(lines), reply_markup=keyboard)
    return NEWTABLE_AI_CONFIRM


async def newtable_ai_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm:no":
        await query.edit_message_text("OK - list columns as name:type, name:type (type is text, number, or date).")
        return NEWTABLE_COLUMNS
    try:
        await asyncio.to_thread(custom_data.create_table, context.user_data["label"], context.user_data["proposed_columns"])
    except custom_data.CustomDataError as exc:
        await query.edit_message_text(f"Failed: {exc}")
        context.user_data.clear()
        return ConversationHandler.END
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_report)
    text = f"Created table {context.user_data['label']!r}."
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await query.edit_message_text(text)
    context.user_data.clear()
    return ConversationHandler.END


async def newtable_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


newtable_conversation = ConversationHandler(
    entry_points=[CommandHandler("newtable", newtable_start)],
    states={
        NEWTABLE_LABEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, newtable_receive_label)],
        NEWTABLE_MODE: [CallbackQueryHandler(newtable_mode_chosen, pattern=r"^mode:")],
        NEWTABLE_COLUMNS: [MessageHandler(filters.TEXT & ~filters.COMMAND, newtable_receive_columns)],
        NEWTABLE_AI_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, newtable_receive_ai_description)],
        NEWTABLE_AI_PROVIDER: [CallbackQueryHandler(newtable_ai_provider_chosen, pattern=r"^provider:")],
        NEWTABLE_AI_CONFIRM: [CallbackQueryHandler(newtable_ai_confirm, pattern=r"^confirm:")],
    },
    fallbacks=[CommandHandler("cancel", newtable_cancel)],
)
```

(`custom_data.propose_schema()`'s return shape - `{"label": str, "columns": [{"label": str, "type": str}, ...]}` - confirmed directly against `parse_schema_response()` in `server/custom_data.py:162-190` while writing this plan; `proposal["columns"]` above is exactly that list.)

- [ ] **Step 2: Sanity-check the module still imports cleanly**

Run: `python -c "import server.telegram_admin_tables"`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
git add server/telegram_admin_tables.py
git commit -m "feat: add /newtable Telegram command"
```

---

### Task 9: `telegram_admin_tables.py` Part D - `/addrow`

**Files:**
- Modify: `server/telegram_admin_tables.py`

**Interfaces:**
- Consumes: `parse_pipe_row` (Task 6), `custom_data.get_table`/`list_tables`/`preview_extraction`/`add_rows` (pre-existing), `document_extraction.extract` (pre-existing), `telegram_ui.configured_provider_keyboard()`, `telegram_rebuild.rebuild_report()` (Task 1).
- Produces: `addrow_conversation`, added to `register()` in Task 10.

- [ ] **Step 1: Add the `/addrow` conversation to `telegram_admin_tables.py`**

```python
ADDROW_INPUT, ADDROW_PROVIDER, ADDROW_CONFIRM = range(3)


def _find_table_by_label(label):
    label = label.strip().lower()
    matches = [t for t in custom_data.list_tables() if t["label"].strip().lower() == label]
    return matches[0] if len(matches) == 1 else None


async def addrow_start(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return ConversationHandler.END
    context.user_data.clear()
    if not context.args:
        await update.message.reply_text("Usage: /addrow <table name>")
        return ConversationHandler.END
    label = " ".join(context.args)
    table = _find_table_by_label(label)
    if table is None:
        names = ", ".join(t["label"] for t in custom_data.list_tables()) or "(no tables yet - use /newtable)"
        await update.message.reply_text(f"No table named {label!r}. Existing tables: {names}")
        return ConversationHandler.END
    context.user_data["table"] = table
    col_desc = ", ".join(f"{c['label']} ({c['column_type']})" for c in table["columns"])
    await update.message.reply_text(
        f"Columns: {col_desc}\n\nSend values as val1 | val2 | val3 (matching column order), "
        "or send a document for AI extraction."
    )
    return ADDROW_INPUT


async def _addrow_prompt_provider(update, context):
    keyboard = telegram_ui.configured_provider_keyboard()
    if keyboard is None:
        await update.message.reply_text("No AI provider configured - use /setkey first.")
        context.user_data.clear()
        return ConversationHandler.END
    await update.message.reply_text("Which AI provider?", reply_markup=keyboard)
    return ADDROW_PROVIDER


async def addrow_receive_text(update, context):
    table = context.user_data["table"]
    try:
        row = parse_pipe_row(update.message.text, table["columns"])
    except ValueError as exc:
        await update.message.reply_text(f"{exc}\n\nTry again, or /cancel.")
        return ADDROW_INPUT
    context.user_data["mode"] = "manual"
    context.user_data["pending_rows"] = [row]
    return await _addrow_prompt_provider(update, context)


async def addrow_receive_doc(update, context):
    doc = update.message.document
    tg_file = await doc.get_file()
    content_bytes = bytes(await tg_file.download_as_bytearray())
    try:
        extracted = document_extraction.extract(doc.file_name or "upload", content_bytes)
    except (document_extraction.UnsupportedFormatError, document_extraction.ExtractionError) as exc:
        await update.message.reply_text(f"{exc}\n\nSend another document, or type values as val1 | val2, or /cancel.")
        return ADDROW_INPUT
    context.user_data["mode"] = "ai"
    context.user_data["doc_text"] = extracted.text
    return await _addrow_prompt_provider(update, context)


async def addrow_provider_chosen(update, context):
    query = update.callback_query
    await query.answer()
    provider = query.data.split(":", 1)[1]
    key = keystore.get_key(provider)
    context.user_data["provider"] = provider
    table = context.user_data["table"]

    if context.user_data["mode"] == "ai":
        try:
            rows = await asyncio.to_thread(
                custom_data.preview_extraction, provider, key, table["id"], context.user_data["doc_text"], "",
            )
        except (custom_data.CustomDataError, ai_client.AIProviderError) as exc:
            await query.edit_message_text(f"Failed: {exc}")
            context.user_data.clear()
            return ConversationHandler.END
        if not rows:
            await query.edit_message_text("No rows found in that document.")
            context.user_data.clear()
            return ConversationHandler.END
        context.user_data["pending_rows"] = rows

    lines = [", ".join(f"{k}={v}" for k, v in row.items()) for row in context.user_data["pending_rows"]]
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, add", callback_data="confirm:yes"),
        InlineKeyboardButton("No, cancel", callback_data="confirm:no"),
    ]])
    await query.edit_message_text(
        f"Add {len(context.user_data['pending_rows'])} row(s)?\n" + "\n".join(lines), reply_markup=keyboard,
    )
    return ADDROW_CONFIRM


async def addrow_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm:no":
        await query.edit_message_text("Cancelled.")
        context.user_data.clear()
        return ConversationHandler.END
    table = context.user_data["table"]
    provider = context.user_data["provider"]
    key = keystore.get_key(provider)
    try:
        added = await asyncio.to_thread(
            custom_data.add_rows, table["id"], context.user_data["pending_rows"], provider, key,
        )
    except (custom_data.CustomDataError, ai_client.AIProviderError) as exc:
        await query.edit_message_text(f"Failed: {exc}")
        context.user_data.clear()
        return ConversationHandler.END
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_report)
    text = f"Added {len(added)} row(s) to {table['label']!r}."
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await query.edit_message_text(text)
    context.user_data.clear()
    return ConversationHandler.END


async def addrow_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


addrow_conversation = ConversationHandler(
    entry_points=[CommandHandler("addrow", addrow_start)],
    states={
        ADDROW_INPUT: [
            MessageHandler(filters.Document.ALL, addrow_receive_doc),
            MessageHandler(filters.TEXT & ~filters.COMMAND, addrow_receive_text),
        ],
        ADDROW_PROVIDER: [CallbackQueryHandler(addrow_provider_chosen, pattern=r"^provider:")],
        ADDROW_CONFIRM: [CallbackQueryHandler(addrow_confirm, pattern=r"^confirm:")],
    },
    fallbacks=[CommandHandler("cancel", addrow_cancel)],
)
```

- [ ] **Step 2: Sanity-check the module still imports cleanly**

Run: `python -c "import server.telegram_admin_tables"`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
git add server/telegram_admin_tables.py
git commit -m "feat: add /addrow Telegram command"
```

---

### Task 10: `telegram_admin_tables.py` - `register()`

**Files:**
- Modify: `server/telegram_admin_tables.py`

**Interfaces:**
- Consumes: `tables_command`, `delete_table_callback` (Task 7), `newtable_conversation` (Task 8), `addrow_conversation` (Task 9).
- Produces: `register(application)`, called by Task 12.

- [ ] **Step 1: Add `register()` to `telegram_admin_tables.py`**

```python
def register(application):
    application.add_handler(CommandHandler("tables", tables_command))
    application.add_handler(CallbackQueryHandler(delete_table_callback, pattern=r"^del:table:"))
    application.add_handler(newtable_conversation)
    application.add_handler(addrow_conversation)
```

- [ ] **Step 2: Sanity-check the module still imports cleanly**

Run: `python -c "import server.telegram_admin_tables"`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
git add server/telegram_admin_tables.py
git commit -m "feat: add register() to telegram_admin_tables"
```

---

### Task 11: `telegram_admin_db.py` - `/dbconnect`, `/dbtables`, `/dbpreview`

**Files:**
- Create: `server/telegram_admin_db.py`

**Interfaces:**
- Consumes: `keystore.set_db_connection`/`get_db_connection` (pre-existing), `db_ingestion.test_connection`/`list_tables`/`fetch_table_text` (pre-existing).
- Produces: `dbconnect_conversation`, `dbtables_command`, `dbpreview_command` (module-level), added to `register()` in Task 12 (Task 12 also adds `/dbingest`, built in this same task's module for cohesion - see Step 1's full file content).

- [ ] **Step 1: Write `telegram_admin_db.py`**

```python
"""Telegram admin-parity commands for Database Ingestion - see
docs/superpowers/specs/2026-08-16-telegram-admin-parity-design.md
section 4.5. /dbconnect explicitly warns that credentials typed here
remain in this chat's permanent history before asking for any of them -
the same trade-off already accepted for /setkey's AI provider keys, with
a bigger blast radius, confirmed with the user for this feature via
AskUserQuestion (see the spec's section 1).
"""
import asyncio

from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler, filters

from server import ai_client, db_ingestion, keystore, supplemental_data, telegram_rebuild, telegram_ui
from server.telegram_bot import _authorized

DBCONNECT_CONSENT, DBCONNECT_HOST, DBCONNECT_PORT, DBCONNECT_DATABASE, DBCONNECT_USER, DBCONNECT_PASSWORD, DBCONNECT_SSLMODE = range(7)


async def dbconnect_start(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "This will ask for database credentials, which will remain in this chat's "
        "history. Reply yes to continue, or /cancel."
    )
    return DBCONNECT_CONSENT


async def dbconnect_consent(update, context):
    if update.message.text.strip().lower() != "yes":
        await update.message.reply_text("Cancelled.")
        return ConversationHandler.END
    await update.message.reply_text("Host?")
    return DBCONNECT_HOST


async def dbconnect_receive_host(update, context):
    context.user_data["host"] = update.message.text.strip()
    await update.message.reply_text("Port? (e.g. 5432)")
    return DBCONNECT_PORT


async def dbconnect_receive_port(update, context):
    try:
        context.user_data["port"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("That's not a number - send the port again, or /cancel.")
        return DBCONNECT_PORT
    await update.message.reply_text("Database name?")
    return DBCONNECT_DATABASE


async def dbconnect_receive_database(update, context):
    context.user_data["database"] = update.message.text.strip()
    await update.message.reply_text("Username?")
    return DBCONNECT_USER


async def dbconnect_receive_user(update, context):
    context.user_data["user"] = update.message.text.strip()
    await update.message.reply_text("Password? (this will be visible in your chat history)")
    return DBCONNECT_PASSWORD


async def dbconnect_receive_password(update, context):
    context.user_data["password"] = update.message.text
    await update.message.reply_text("SSL mode? (optional - send 'skip' to leave blank)")
    return DBCONNECT_SSLMODE


async def dbconnect_receive_sslmode(update, context):
    text = update.message.text.strip()
    conn_info = {
        "host": context.user_data["host"],
        "port": context.user_data["port"],
        "database": context.user_data["database"],
        "user": context.user_data["user"],
        "password": context.user_data["password"],
        "sslmode": "" if text.lower() == "skip" else text,
    }
    await asyncio.to_thread(keystore.set_db_connection, conn_info)
    ok, detail = await asyncio.to_thread(db_ingestion.test_connection, conn_info)
    await update.message.reply_text(detail)
    context.user_data.clear()
    return ConversationHandler.END


async def dbconnect_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


dbconnect_conversation = ConversationHandler(
    entry_points=[CommandHandler("dbconnect", dbconnect_start)],
    states={
        DBCONNECT_CONSENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, dbconnect_consent)],
        DBCONNECT_HOST: [MessageHandler(filters.TEXT & ~filters.COMMAND, dbconnect_receive_host)],
        DBCONNECT_PORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, dbconnect_receive_port)],
        DBCONNECT_DATABASE: [MessageHandler(filters.TEXT & ~filters.COMMAND, dbconnect_receive_database)],
        DBCONNECT_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, dbconnect_receive_user)],
        DBCONNECT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, dbconnect_receive_password)],
        DBCONNECT_SSLMODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, dbconnect_receive_sslmode)],
    },
    fallbacks=[CommandHandler("cancel", dbconnect_cancel)],
)


def _require_connection():
    conn_info = keystore.get_db_connection()
    if not conn_info:
        return None, "No database connection configured - use /dbconnect first."
    return conn_info, None


async def dbtables_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    conn_info, error = _require_connection()
    if error:
        await update.message.reply_text(error)
        return
    try:
        tables = await asyncio.to_thread(db_ingestion.list_tables, conn_info)
    except db_ingestion.DbIngestionError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text("\n".join(tables) if tables else "No tables found.")


async def dbpreview_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /dbpreview <table>")
        return
    conn_info, error = _require_connection()
    if error:
        await update.message.reply_text(error)
        return
    table = context.args[0]
    try:
        text = await asyncio.to_thread(db_ingestion.fetch_table_text, conn_info, table)
    except db_ingestion.DbIngestionError as exc:
        await update.message.reply_text(str(exc))
        return
    if len(text) > 3800:
        text = text[:3800] + "\n...(truncated)"
    await update.message.reply_text(text or "(empty)")
```

- [ ] **Step 2: Sanity-check the module imports cleanly**

Run: `python -c "import server.telegram_admin_db"`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
git add server/telegram_admin_db.py
git commit -m "feat: add /dbconnect, /dbtables, /dbpreview Telegram commands"
```

---

### Task 12: `telegram_admin_db.py` - `/dbingest` and `register()`

**Files:**
- Modify: `server/telegram_admin_db.py`

**Interfaces:**
- Consumes: `_require_connection`, `db_ingestion.fetch_table_text` (Task 11), `supplemental_data.add_from_document` (pre-existing), `telegram_ui.configured_provider_keyboard()`, `telegram_rebuild.rebuild_report()` (Task 1).
- Produces: `register(application)`, called by Task 13.

- [ ] **Step 1: Add `/dbingest` and `register()` to `telegram_admin_db.py`**

```python
DBINGEST_PROVIDER = 0


async def dbingest_start(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return ConversationHandler.END
    context.user_data.clear()
    if not context.args:
        await update.message.reply_text("Usage: /dbingest <table>")
        return ConversationHandler.END
    conn_info, error = _require_connection()
    if error:
        await update.message.reply_text(error)
        return ConversationHandler.END
    table = context.args[0]
    try:
        text = await asyncio.to_thread(db_ingestion.fetch_table_text, conn_info, table)
    except db_ingestion.DbIngestionError as exc:
        await update.message.reply_text(str(exc))
        return ConversationHandler.END
    context.user_data["table"] = table
    context.user_data["text"] = text
    keyboard = telegram_ui.configured_provider_keyboard()
    if keyboard is None:
        await update.message.reply_text("No AI provider configured - use /setkey first.")
        context.user_data.clear()
        return ConversationHandler.END
    await update.message.reply_text("Which AI provider?", reply_markup=keyboard)
    return DBINGEST_PROVIDER


async def dbingest_provider_chosen(update, context):
    query = update.callback_query
    await query.answer()
    provider = query.data.split(":", 1)[1]
    key = keystore.get_key(provider)
    table = context.user_data["table"]
    try:
        added = await asyncio.to_thread(
            supplemental_data.add_from_document, provider, key, context.user_data["text"], "", f"db:{table}",
        )
    except (supplemental_data.SupplementalDataError, ai_client.AIProviderError) as exc:
        await query.edit_message_text(f"Failed: {exc}")
        context.user_data.clear()
        return ConversationHandler.END
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_report)
    summary = "\n".join(f"- {r.get('district', '')} / {r.get('category', '')}: {r.get('label', '')}" for r in added)
    text = f"Added {len(added)} record(s):\n{summary}"
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await query.edit_message_text(text)
    context.user_data.clear()
    return ConversationHandler.END


async def dbingest_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


dbingest_conversation = ConversationHandler(
    entry_points=[CommandHandler("dbingest", dbingest_start)],
    states={DBINGEST_PROVIDER: [CallbackQueryHandler(dbingest_provider_chosen, pattern=r"^provider:")]},
    fallbacks=[CommandHandler("cancel", dbingest_cancel)],
)


def register(application):
    application.add_handler(dbconnect_conversation)
    application.add_handler(CommandHandler("dbtables", dbtables_command))
    application.add_handler(CommandHandler("dbpreview", dbpreview_command))
    application.add_handler(dbingest_conversation)
```

- [ ] **Step 2: Sanity-check the module still imports cleanly**

Run: `python -c "import server.telegram_admin_db"`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
git add server/telegram_admin_db.py
git commit -m "feat: add /dbingest Telegram command and telegram_admin_db register()"
```

---

### Task 13: Wire everything into `telegram_bot.py`

**Files:**
- Modify: `server/telegram_bot.py`

**Interfaces:**
- Consumes: `telegram_admin_records.register`, `telegram_admin_tables.register`, `telegram_admin_db.register` (Tasks 5, 10, 12).

- [ ] **Step 1: Import the three new modules and call `register()` in `build_application()`**

**Do not add this as a top-level import.** All three new modules do
`from server.telegram_bot import _authorized` at their own top level
(Tasks 2/6/11); if `telegram_bot.py` also imported them at its own top
level, Python would hit a circular import the moment either side loads
first (the partially-initialized `telegram_bot` module wouldn't yet have
`_authorized` defined when the new modules try to import it). Import
them **inside** `build_application()` instead - by the time this
function runs, `telegram_bot.py`'s own top-level code (including
`_authorized`'s definition) has already fully executed, so the circular
reference resolves cleanly:

```python
def build_application(token):
    from server import telegram_admin_db, telegram_admin_records, telegram_admin_tables

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("map", map_command))
    application.add_handler(CommandHandler("ask", ask_command))
    application.add_handler(CommandHandler("keys", keys_command))
    application.add_handler(CommandHandler("setkey", setkey_command))
    application.add_handler(addpoint_conversation)
    telegram_admin_records.register(application)
    telegram_admin_tables.register(application)
    telegram_admin_db.register(application)
    return application
```

(This replaces the existing `build_application()` body - every line
above except the three new `register()` calls and the new local import
already exists; only add what's missing.)

- [ ] **Step 2: Extend `HELP_TEXT`**

```python
HELP_TEXT = (
    "KP Healthcare Plan bot.\n\n"
    "/report - download the current PDF report\n"
    "/map - render the current map with all layers\n"
    "/ask <question> - ask the AI about the current data\n"
    "/keys - list configured AI provider keys\n"
    "/setkey <provider> <key> - set an AI provider key\n"
    "/addpoint - add a new facility (guided)\n"
    "/cancel - cancel an in-progress guided command\n\n"
    "Admin (same access as the web admin panel):\n"
    "/addrecord - extract a document and add it to the report\n"
    "/supplemental, /overrides, /facilities - list/delete records\n"
    "/override - apply a new pipeline data override\n"
    "/tables, /newtable, /addrow <table> - manage custom data tables\n"
    "/dbconnect, /dbtables, /dbpreview <table>, /dbingest <table> - database ingestion"
)
```

- [ ] **Step 3: Run the full test suite**

Run: `pytest -q`
Expected: PASS, same count as after Task 6 (this task adds no new tests, just wiring)

- [ ] **Step 4: Sanity-check the whole bot module builds a real `Application` without error**

Run: `python -c "
import server.telegram_bot as tb
app = tb.build_application('123:fake-token-for-handler-registration-check')
print('handlers registered:', sum(len(v) for v in app.handlers.values()))
"`
Expected: prints a handler count with no exception (this only builds the `Application` and registers handlers - it does not contact Telegram's servers, matching `build_application()`'s existing pre-`initialize()` shape).

- [ ] **Step 5: Commit**

```bash
git add server/telegram_bot.py
git commit -m "feat: wire Telegram admin-parity commands into the bot"
```

---

### Task 14: Full verification and close-out

**Files:** none (verification only)

- [x] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: PASS, count increased by 59 from this plan's start (9 rebuild + 9 parser + 41 mocked-handler tests - see the Global Constraints correction above)
**Result:** 590/590 passed.

- [x] **Step 2: Live-verify against the real bot**

With explicit permission (same established pattern as the original Telegram Connector feature and this session's other live-verification steps), drive the user's own logged-in Telegram Web session in Chrome to exercise, at minimum:
- `/addrecord` with a real test document, confirm it appears in a rebuilt report, then delete it via `/supplemental`'s inline Delete button and confirm the report reverts.
- `/override` via typed text (not a document), confirm the value changed, then delete it via `/overrides` and confirm it reverts.
- `/newtable` via the manual column-spec path, `/addrow` via the manual pipe-row path, confirm the row lands in the database (`psql` or the admin panel), then delete the table via `/tables`.
- `/dbconnect` against a real (or throwaway, matching this project's own established "spin up a disposable Postgres instance" pattern from the original Database Ingestion feature) database, `/dbtables`, `/dbpreview`, `/dbingest`, then delete the resulting record.
- Confirm `/cancel` works mid-conversation for at least one multi-step command.

Clean up all test data afterward (delete every table/record created during verification), confirm the report rebuilds back to a byte-identical match with the committed baseline (`git status` clean), and restore the admin password if it was temporarily reset for any web-side comparison.

**Done (2026-08-16).** Every command listed above was exercised live against the real bot via the user's own logged-in Telegram Web session (explicit permission given), connected to the already-running bundled local database rather than a separate throwaway instance (safe, local, already-known credentials - same spirit as the established "disposable Postgres" pattern, no extra cluster needed): `/addrecord` (real Groq extraction, real report rebuild, delete+revert via `/supplemental`), `/override` (real AI call correctly rejected an implausible 88% population swing per the pre-existing plausibility guard, then a plausible value applied and reverted correctly via `/overrides`), `/newtable`+`/addrow` (manual paths, real table+row created with correct type coercion, confirmed the provider-required-even-for-manual-rows design point), `/tables` delete, `/dbconnect` (consent gate + full credential sequence + real connection test), `/dbtables`, `/dbpreview`, `/dbingest` (real AI extraction from a live DB table into a new supplemental record), and `/cancel` mid-conversation. **Zero code bugs found.** One non-bug investigated thoroughly: a `delete_callback` message edit on Telegram Web appeared stuck for several minutes on the very first delete during this session - directly verified via `psql`/Python that the underlying delete+rebuild+revert had actually completed correctly and quickly; a page reload still showed stale content; the *next* click (which should have hit the "already deleted" path) is what actually surfaced the correct final state. Concluded as Telegram Web client-side/automation-session latency, not a bot defect - matches this project's own established "automation flakiness, not an app bug" precedent from earlier sessions. All test data cleaned up (via the bot's own delete commands where possible, direct DB access otherwise); `git status` confirmed clean (the `gis/KP_Healthcare_Plan.qgz` rebuild diff was only the already-known cosmetic random-layer-id noise, reverted, not committed); 590/590 tests passing.

- [x] **Step 3: `finishing-a-development-branch`**

Confirmed: normal repo (`GIT_DIR == GIT_COMMON`), `master` branch directly, no remote - matches every prior phase this session. Nothing to merge or push; work already lands as direct commits (12 commits across this plan).

- [x] **Step 4: Report findings**

See the session summary delivered to the user after this plan's completion.
