"""Unit tests for server/telegram_admin_tables.py's Telegram-glue handlers
(parse_column_spec()/parse_pipe_row() are covered separately in
tests/test_telegram_admin_tables_parsers.py, as pure functions). Every
Telegram API call is mocked, matching tests/server/test_telegram_bot.py's
established pattern.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from server import ai_client, custom_data, keystore, telegram_admin_tables, telegram_rebuild, telegram_ui

TELEGRAM_CONFIG = {"token": "123456:ABC-DEF", "allowed_user_id": "987654321"}

FAKE_TABLE = {
    "id": "tbl1", "label": "Cold Chain Equipment",
    "columns": [
        {"column_name": "name", "label": "Name", "column_type": "text"},
        {"column_name": "capacity", "label": "Capacity", "column_type": "number"},
    ],
}


def _make_update(user_id=987654321):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message = AsyncMock()
    return update


def _make_context(args=None):
    context = MagicMock()
    context.args = args or []
    context.user_data = {}
    return context


def _make_callback_update(data, user_id=987654321):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query = AsyncMock()
    update.callback_query.data = data
    return update


@pytest.mark.asyncio
async def test_tables_command_lists_tables(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(custom_data, "list_tables", lambda: [FAKE_TABLE])
    update = _make_update()
    await telegram_admin_tables.tables_command(update, _make_context())
    text = update.message.reply_text.call_args.args[0]
    assert "Cold Chain Equipment" in text
    assert "2 column" in text


@pytest.mark.asyncio
async def test_tables_command_no_tables(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(custom_data, "list_tables", lambda: [])
    update = _make_update()
    await telegram_admin_tables.tables_command(update, _make_context())
    text = update.message.reply_text.call_args.args[0]
    assert "no custom tables" in text.lower()


@pytest.mark.asyncio
async def test_delete_table_callback_deletes_and_rebuilds(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(custom_data, "delete_table", lambda tid: True)
    monkeypatch.setattr(custom_data, "list_tables", lambda: [])
    called = []
    monkeypatch.setattr(telegram_rebuild, "rebuild_report", lambda: called.append(1) or (True, None))
    update = _make_callback_update("del:table:tbl1")
    await telegram_admin_tables.delete_table_callback(update, _make_context())
    assert called == [1]
    update.callback_query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_tables_command_with_name_shows_rows(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(custom_data, "list_tables", lambda: [FAKE_TABLE])
    monkeypatch.setattr(
        custom_data, "list_records",
        lambda tid: [{"id": "row1", "name": "Fridge A", "capacity": 50}],
    )
    update = _make_update()
    await telegram_admin_tables.tables_command(update, _make_context(args=["Cold", "Chain", "Equipment"]))
    text = update.message.reply_text.call_args.args[0]
    assert "Fridge A" in text and "Capacity=50" in text
    keyboard = update.message.reply_text.call_args.kwargs["reply_markup"]
    assert keyboard is not None


@pytest.mark.asyncio
async def test_tables_command_with_unknown_name_lists_existing(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(custom_data, "list_tables", lambda: [FAKE_TABLE])
    update = _make_update()
    await telegram_admin_tables.tables_command(update, _make_context(args=["Nonexistent"]))
    text = update.message.reply_text.call_args.args[0]
    assert "No table named" in text
    assert "Cold Chain Equipment" in text


def test_table_rows_message_empty_table(monkeypatch):
    monkeypatch.setattr(custom_data, "list_records", lambda tid: [])
    text, keyboard = telegram_admin_tables._table_rows_message(FAKE_TABLE)
    assert "no rows yet" in text.lower()
    assert keyboard is None


@pytest.mark.asyncio
async def test_delete_row_callback_deletes_and_rebuilds(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(custom_data, "delete_row", lambda tid, rid: True)
    monkeypatch.setattr(custom_data, "get_table", lambda tid: FAKE_TABLE)
    monkeypatch.setattr(custom_data, "list_records", lambda tid: [])
    called = []
    monkeypatch.setattr(telegram_rebuild, "rebuild_report", lambda: called.append(1) or (True, None))
    update = _make_callback_update("del:row:tbl1:row1")
    await telegram_admin_tables.delete_row_callback(update, _make_context())
    assert called == [1]
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "no rows yet" in text.lower()


@pytest.mark.asyncio
async def test_delete_row_callback_row_not_found(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(custom_data, "delete_row", lambda tid, rid: False)
    update = _make_callback_update("del:row:tbl1:missing")
    await telegram_admin_tables.delete_row_callback(update, _make_context())
    update.callback_query.edit_message_text.assert_awaited_once_with("Already deleted.")


@pytest.mark.asyncio
async def test_newtable_mode_chosen_manual_prompts_columns():
    update = _make_callback_update("mode:manual")
    state = await telegram_admin_tables.newtable_mode_chosen(update, _make_context())
    assert state == telegram_admin_tables.NEWTABLE_COLUMNS


@pytest.mark.asyncio
async def test_newtable_receive_columns_invalid_reprompts():
    update = _make_update()
    update.message.text = "not-a-valid-spec"
    context = _make_context()
    context.user_data["label"] = "Test Table"
    state = await telegram_admin_tables.newtable_receive_columns(update, context)
    assert state == telegram_admin_tables.NEWTABLE_COLUMNS
    text = update.message.reply_text.call_args.args[0]
    assert "name:type" in text


@pytest.mark.asyncio
async def test_newtable_receive_columns_valid_creates_table(monkeypatch):
    created = []
    monkeypatch.setattr(custom_data, "create_table", lambda label, columns: created.append((label, columns)))
    monkeypatch.setattr(telegram_rebuild, "rebuild_report", lambda: (True, None))
    update = _make_update()
    update.message.text = "name:text, capacity:number"
    context = _make_context()
    context.user_data["label"] = "Test Table"
    from telegram.ext import ConversationHandler
    state = await telegram_admin_tables.newtable_receive_columns(update, context)
    assert state == ConversationHandler.END
    assert created == [("Test Table", [{"label": "name", "type": "text"}, {"label": "capacity", "type": "number"}])]
    assert context.user_data == {}


@pytest.mark.asyncio
async def test_newtable_ai_confirm_no_drops_to_manual_columns():
    update = _make_callback_update("confirm:no")
    state = await telegram_admin_tables.newtable_ai_confirm(update, _make_context())
    assert state == telegram_admin_tables.NEWTABLE_COLUMNS


@pytest.mark.asyncio
async def test_addrow_start_unknown_table_lists_existing(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(custom_data, "list_tables", lambda: [FAKE_TABLE])
    from telegram.ext import ConversationHandler
    update = _make_update()
    context = _make_context(args=["Nonexistent", "Table"])
    state = await telegram_admin_tables.addrow_start(update, context)
    assert state == ConversationHandler.END
    text = update.message.reply_text.call_args.args[0]
    assert "Cold Chain Equipment" in text


@pytest.mark.asyncio
async def test_addrow_start_found_table_prompts_values(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(custom_data, "list_tables", lambda: [FAKE_TABLE])
    update = _make_update()
    context = _make_context(args=["Cold", "Chain", "Equipment"])
    state = await telegram_admin_tables.addrow_start(update, context)
    assert state == telegram_admin_tables.ADDROW_INPUT
    assert context.user_data["table"] == FAKE_TABLE


@pytest.mark.asyncio
async def test_addrow_receive_text_invalid_count_reprompts():
    update = _make_update()
    update.message.text = "only one value"
    context = _make_context()
    context.user_data["table"] = FAKE_TABLE
    state = await telegram_admin_tables.addrow_receive_text(update, context)
    assert state == telegram_admin_tables.ADDROW_INPUT


@pytest.mark.asyncio
async def test_addrow_receive_text_valid_stores_pending_row(monkeypatch):
    monkeypatch.setattr(telegram_ui, "configured_provider_keyboard", lambda: MagicMock())
    update = _make_update()
    update.message.text = "Fridge A | 50"
    context = _make_context()
    context.user_data["table"] = FAKE_TABLE
    state = await telegram_admin_tables.addrow_receive_text(update, context)
    assert state == telegram_admin_tables.ADDROW_PROVIDER
    assert context.user_data["mode"] == "manual"
    assert context.user_data["pending_rows"] == [{"name": "Fridge A", "capacity": "50"}]


@pytest.mark.asyncio
async def test_addrow_provider_chosen_manual_mode_skips_extraction(monkeypatch):
    # Real design point this guards: add_rows() always needs a provider
    # even for a manually-typed row (used for the report-placement call) -
    # the manual path must still reach the confirm step, not call AI
    # extraction on the already-parsed row.
    update = _make_callback_update("provider:groq")
    context = _make_context()
    context.user_data = {"table": FAKE_TABLE, "mode": "manual", "pending_rows": [{"name": "Fridge A", "capacity": "50"}]}
    state = await telegram_admin_tables.addrow_provider_chosen(update, context)
    assert state == telegram_admin_tables.ADDROW_CONFIRM
    assert context.user_data["provider"] == "groq"
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Fridge A" in text


@pytest.mark.asyncio
async def test_addrow_provider_chosen_ai_mode_calls_preview_extraction(monkeypatch):
    monkeypatch.setattr(keystore, "get_key", lambda provider: "sk-real")
    monkeypatch.setattr(
        custom_data, "preview_extraction",
        lambda provider, key, table_id, text, instruction: [{"name": "Fridge B", "capacity": "30"}],
    )
    update = _make_callback_update("provider:groq")
    context = _make_context()
    context.user_data = {"table": FAKE_TABLE, "mode": "ai", "doc_text": "some document text"}
    state = await telegram_admin_tables.addrow_provider_chosen(update, context)
    assert state == telegram_admin_tables.ADDROW_CONFIRM
    assert context.user_data["pending_rows"] == [{"name": "Fridge B", "capacity": "30"}]


@pytest.mark.asyncio
async def test_addrow_confirm_no_cancels_without_adding(monkeypatch):
    added = []
    monkeypatch.setattr(custom_data, "add_rows", lambda *a, **k: added.append(1))
    from telegram.ext import ConversationHandler
    update = _make_callback_update("confirm:no")
    context = _make_context()
    context.user_data = {"table": FAKE_TABLE, "provider": "groq", "pending_rows": [{}]}
    state = await telegram_admin_tables.addrow_confirm(update, context)
    assert state == ConversationHandler.END
    assert added == []
    update.callback_query.edit_message_text.assert_awaited_once_with("Cancelled.")


@pytest.mark.asyncio
async def test_addrow_confirm_yes_adds_rows_and_rebuilds(monkeypatch):
    monkeypatch.setattr(keystore, "get_key", lambda provider: "sk-real")
    monkeypatch.setattr(custom_data, "add_rows", lambda table_id, rows, provider, key: rows)
    monkeypatch.setattr(telegram_rebuild, "rebuild_report", lambda: (True, None))
    from telegram.ext import ConversationHandler
    update = _make_callback_update("confirm:yes")
    context = _make_context()
    context.user_data = {"table": FAKE_TABLE, "provider": "groq", "pending_rows": [{"name": "Fridge A", "capacity": "50"}]}
    state = await telegram_admin_tables.addrow_confirm(update, context)
    assert state == ConversationHandler.END
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Added 1 row" in text
