"""Unit tests for server/telegram_admin_db.py. Every Telegram API call
and every real database call is mocked - matching
tests/server/test_telegram_bot.py's established pattern.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from server import db_browser, db_ingestion, keystore, supplemental_data, telegram_admin_db, telegram_rebuild, telegram_ui

TELEGRAM_CONFIG = {"token": "123456:ABC-DEF", "allowed_user_id": "987654321"}
FAKE_CONN = {"host": "localhost", "port": 5432, "database": "kp_health", "user": "u", "password": "p", "sslmode": ""}


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
async def test_dbconnect_start_authorized_warns_about_chat_history(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    context = _make_context()
    state = await telegram_admin_db.dbconnect_start(update, context)
    assert state == telegram_admin_db.DBCONNECT_CONSENT
    text = update.message.reply_text.call_args.args[0]
    assert "remain in this chat" in text


@pytest.mark.asyncio
async def test_dbconnect_consent_no_cancels():
    update = _make_update()
    update.message.text = "no"
    state = await telegram_admin_db.dbconnect_consent(update, _make_context())
    assert state == ConversationHandler.END


@pytest.mark.asyncio
async def test_dbconnect_consent_yes_asks_host():
    update = _make_update()
    update.message.text = "yes"
    state = await telegram_admin_db.dbconnect_consent(update, _make_context())
    assert state == telegram_admin_db.DBCONNECT_HOST


@pytest.mark.asyncio
async def test_dbconnect_receive_port_invalid_reprompts():
    update = _make_update()
    update.message.text = "not-a-number"
    context = _make_context()
    state = await telegram_admin_db.dbconnect_receive_port(update, context)
    assert state == telegram_admin_db.DBCONNECT_PORT
    assert "port" not in context.user_data


@pytest.mark.asyncio
async def test_dbconnect_receive_port_valid_stores_int():
    update = _make_update()
    update.message.text = "5544"
    context = _make_context()
    state = await telegram_admin_db.dbconnect_receive_port(update, context)
    assert state == telegram_admin_db.DBCONNECT_DATABASE
    assert context.user_data["port"] == 5544


@pytest.mark.asyncio
async def test_dbconnect_receive_sslmode_skip_saves_and_tests(monkeypatch):
    saved = []
    monkeypatch.setattr(keystore, "set_db_connection", lambda conn_info: saved.append(conn_info))
    monkeypatch.setattr(db_ingestion, "test_connection", lambda conn_info: (True, "Connected"))
    update = _make_update()
    update.message.text = "skip"
    context = _make_context()
    context.user_data = {"host": "localhost", "port": 5432, "database": "kp_health", "user": "u", "password": "p"}
    state = await telegram_admin_db.dbconnect_receive_sslmode(update, context)
    assert state == ConversationHandler.END
    assert saved == [{"host": "localhost", "port": 5432, "database": "kp_health", "user": "u", "password": "p", "sslmode": ""}]
    update.message.reply_text.assert_awaited_once_with("Connected")
    assert context.user_data == {}


@pytest.mark.asyncio
async def test_dbtables_command_no_connection_configured(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_db_connection", lambda: None)
    update = _make_update()
    await telegram_admin_db.dbtables_command(update, _make_context())
    text = update.message.reply_text.call_args.args[0]
    assert "/dbconnect" in text


@pytest.mark.asyncio
async def test_dbtables_command_lists_tables(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_db_connection", lambda: FAKE_CONN)
    monkeypatch.setattr(db_ingestion, "list_tables", lambda conn_info: ["facilities", "equipment"])
    update = _make_update()
    await telegram_admin_db.dbtables_command(update, _make_context())
    text = update.message.reply_text.call_args.args[0]
    assert "facilities" in text and "equipment" in text


@pytest.mark.asyncio
async def test_dbtables_command_ingestion_error_reported(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_db_connection", lambda: FAKE_CONN)

    def failing_list(conn_info):
        raise db_ingestion.DbIngestionError("connection refused")

    monkeypatch.setattr(db_ingestion, "list_tables", failing_list)
    update = _make_update()
    await telegram_admin_db.dbtables_command(update, _make_context())
    update.message.reply_text.assert_awaited_once_with("connection refused")


@pytest.mark.asyncio
async def test_dbpreview_command_truncates_long_text(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_db_connection", lambda: FAKE_CONN)
    monkeypatch.setattr(db_ingestion, "fetch_table_text", lambda conn_info, table: "x" * 5000)
    update = _make_update()
    await telegram_admin_db.dbpreview_command(update, _make_context(args=["facilities"]))
    text = update.message.reply_text.call_args.args[0]
    assert len(text) < 5000
    assert text.endswith("(truncated)")


@pytest.mark.asyncio
async def test_dbingest_start_no_args_shows_usage(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    state = await telegram_admin_db.dbingest_start(update, _make_context(args=[]))
    assert state == ConversationHandler.END
    update.message.reply_text.assert_awaited_once_with("Usage: /dbingest <table>")


@pytest.mark.asyncio
async def test_dbingest_start_success_prompts_provider(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_db_connection", lambda: FAKE_CONN)
    monkeypatch.setattr(db_ingestion, "fetch_table_text", lambda conn_info, table: "col1|col2\nval1|val2")
    monkeypatch.setattr(telegram_ui, "configured_provider_keyboard", lambda: MagicMock())
    update = _make_update()
    context = _make_context(args=["facilities"])
    state = await telegram_admin_db.dbingest_start(update, context)
    assert state == telegram_admin_db.DBINGEST_PROVIDER
    assert context.user_data["table"] == "facilities"


@pytest.mark.asyncio
async def test_dbingest_provider_chosen_success(monkeypatch):
    monkeypatch.setattr(keystore, "get_key", lambda provider: "sk-real")
    monkeypatch.setattr(
        supplemental_data, "add_from_document",
        lambda provider, key, text, instruction, source: [{"district": "Peshawar", "category": "Equipment", "label": "Ventilator"}],
    )
    monkeypatch.setattr(telegram_rebuild, "rebuild_report", lambda: (True, None))
    update = _make_callback_update("provider:groq")
    context = _make_context()
    context.user_data = {"table": "facilities", "text": "some table text"}
    state = await telegram_admin_db.dbingest_provider_chosen(update, context)
    assert state == ConversationHandler.END
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Added 1 record" in text


@pytest.mark.asyncio
async def test_localtables_command_lists_tables(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "list_tables", lambda: ["bot_facilities", "custom_tables"])
    update = _make_update()
    await telegram_admin_db.localtables_command(update, _make_context())
    text = update.message.reply_text.call_args.args[0]
    assert "bot_facilities" in text and "custom_tables" in text


@pytest.mark.asyncio
async def test_localtables_command_unauthorized(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111)
    await telegram_admin_db.localtables_command(update, _make_context())
    update.message.reply_text.assert_awaited_once_with("Not authorized.")


@pytest.mark.asyncio
async def test_localview_command_shows_rows(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: [{"name": "id", "type": "text"}, {"name": "name", "type": "text"}])
    monkeypatch.setattr(db_browser, "get_table_rows", lambda t: [{"id": "r1", "name": "Fridge A"}])
    update = _make_update()
    await telegram_admin_db.localview_command(update, _make_context(args=["bot_facilities"]))
    text = update.message.reply_text.call_args.args[0]
    assert "Fridge A" in text


@pytest.mark.asyncio
async def test_localview_command_unknown_table(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: None)
    monkeypatch.setattr(db_browser, "list_tables", lambda: ["bot_facilities"])
    update = _make_update()
    await telegram_admin_db.localview_command(update, _make_context(args=["nonexistent"]))
    text = update.message.reply_text.call_args.args[0]
    assert "No table named" in text


@pytest.mark.asyncio
async def test_localview_command_no_args_shows_usage(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    await telegram_admin_db.localview_command(update, _make_context(args=[]))
    update.message.reply_text.assert_awaited_once_with("Usage: /localview <table>")


def test_truncate_cell_leaves_short_values_unchanged():
    assert telegram_admin_db._truncate_cell("Fridge A") == "Fridge A"


def test_truncate_cell_leaves_none_unchanged():
    assert telegram_admin_db._truncate_cell(None) is None


def test_truncate_cell_cuts_long_values_with_a_note():
    long_value = "x" * 500
    result = telegram_admin_db._truncate_cell(long_value, limit=200)
    assert result.startswith("x" * 200)
    assert "more chars" in result
    assert len(result) < len(long_value)


def test_truncate_cell_exact_limit_length_unchanged():
    exact_value = "x" * 200
    assert telegram_admin_db._truncate_cell(exact_value, limit=200) == exact_value


@pytest.mark.asyncio
async def test_localview_command_truncates_long_cell_values(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: [{"name": "id", "type": "text"}, {"name": "geometry", "type": "jsonb"}])
    monkeypatch.setattr(db_browser, "get_table_rows", lambda t: [{"id": "r1", "geometry": "{" + ("x" * 5000) + "}"}])
    update = _make_update()
    await telegram_admin_db.localview_command(update, _make_context(args=["pipeline_district_boundaries"]))
    text = update.message.reply_text.call_args.args[0]
    assert len(text) < 4096
    assert "more chars" in text


def test_truncate_message_leaves_short_messages_unchanged():
    assert telegram_admin_db._truncate_message("short") == "short"


def test_truncate_message_cuts_long_messages_with_a_note():
    long_message = "x" * 5000
    result = telegram_admin_db._truncate_message(long_message, limit=4000)
    assert result.startswith("x" * 4000)
    assert "truncated" in result
    assert len(result) < telegram_admin_db.TELEGRAM_MESSAGE_LIMIT


@pytest.mark.asyncio
async def test_localview_command_stays_under_telegram_limit_with_many_truncated_rows(monkeypatch):
    # Reproduces the real bug found live: 20 rows, each with a cell that's
    # individually under _truncate_cell()'s own 200-char cap, still summed
    # to well over Telegram's 4096-character message limit - the real
    # send failed with telegram.error.BadRequest("Message is too long").
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: [
        {"name": "district", "type": "text"}, {"name": "division", "type": "text"},
        {"name": "area_km2", "type": "numeric"}, {"name": "geometry", "type": "jsonb"},
    ])
    many_rows = [
        {"district": f"District {i}", "division": "Some Division", "area_km2": 1234.5,
         "geometry": "{" + ("x" * 3000) + "}"}
        for i in range(20)
    ]
    monkeypatch.setattr(db_browser, "get_table_rows", lambda t: many_rows)
    update = _make_update()
    await telegram_admin_db.localview_command(update, _make_context(args=["pipeline_district_boundaries"]))
    text = update.message.reply_text.call_args.args[0]
    assert len(text) < telegram_admin_db.TELEGRAM_MESSAGE_LIMIT


FAKE_TABLE_COLUMNS = [{"name": "id", "type": "text"}, {"name": "name", "type": "text"}]
FAKE_ROWS = [{"id": "r1", "name": "Fridge A"}, {"id": "r2", "name": "Fridge B"}]


@pytest.mark.asyncio
async def test_localedit_start_missing_args_shows_usage(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    state = await telegram_admin_db.localedit_start(update, _make_context(args=["bot_facilities"]))
    assert state == ConversationHandler.END
    update.message.reply_text.assert_awaited_once_with("Usage: /localedit <table> <row#>")


@pytest.mark.asyncio
async def test_localedit_start_row_out_of_range(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: FAKE_TABLE_COLUMNS)
    monkeypatch.setattr(db_browser, "get_table_rows", lambda t: FAKE_ROWS)
    update = _make_update()
    state = await telegram_admin_db.localedit_start(update, _make_context(args=["bot_facilities", "5"]))
    assert state == ConversationHandler.END
    text = update.message.reply_text.call_args.args[0]
    assert "No row #5" in text


@pytest.mark.asyncio
async def test_localedit_start_valid_row_shows_current_values(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: FAKE_TABLE_COLUMNS)
    monkeypatch.setattr(db_browser, "get_table_rows", lambda t: FAKE_ROWS)
    update = _make_update()
    context = _make_context(args=["bot_facilities", "2"])
    state = await telegram_admin_db.localedit_start(update, context)
    assert state == telegram_admin_db.LOCALEDIT_FIELDS
    assert context.user_data["row"] == {"id": "r2", "name": "Fridge B"}
    text = update.message.reply_text.call_args.args[0]
    assert "Fridge B" in text


@pytest.mark.asyncio
async def test_localedit_receive_fields_unknown_column_reprompts():
    update = _make_update()
    update.message.text = "bogus=X"
    context = _make_context()
    context.user_data = {"row": FAKE_ROWS[0], "columns": FAKE_TABLE_COLUMNS}
    state = await telegram_admin_db.localedit_receive_fields(update, context)
    assert state == telegram_admin_db.LOCALEDIT_FIELDS
    text = update.message.reply_text.call_args.args[0]
    assert "Unknown column" in text


@pytest.mark.asyncio
async def test_localedit_receive_fields_valid_shows_diff():
    update = _make_update()
    update.message.text = "name=Fridge C"
    context = _make_context()
    context.user_data = {"row": FAKE_ROWS[0], "columns": FAKE_TABLE_COLUMNS}
    state = await telegram_admin_db.localedit_receive_fields(update, context)
    assert state == telegram_admin_db.LOCALEDIT_CONFIRM
    text = update.message.reply_text.call_args.args[0]
    assert "Fridge A -> Fridge C" in text


@pytest.mark.asyncio
async def test_localedit_confirm_no_cancels_without_updating(monkeypatch):
    called = []
    monkeypatch.setattr(db_browser, "update_row", lambda *a, **k: called.append(1))
    update = _make_callback_update("confirm:no")
    context = _make_context()
    context.user_data = {"table": "bot_facilities", "row": FAKE_ROWS[0], "fields": {"name": "X"}}
    state = await telegram_admin_db.localedit_confirm(update, context)
    assert state == ConversationHandler.END
    assert called == []
    update.callback_query.edit_message_text.assert_awaited_once_with("Cancelled.")


@pytest.mark.asyncio
async def test_localedit_confirm_yes_updates_and_rebuilds(monkeypatch):
    monkeypatch.setattr(db_browser, "update_row", lambda table, rid, fields: True)
    called = []
    monkeypatch.setattr(telegram_rebuild, "rebuild_report", lambda: called.append(1) or (True, None))
    update = _make_callback_update("confirm:yes")
    context = _make_context()
    context.user_data = {"table": "bot_facilities", "row": FAKE_ROWS[0], "fields": {"name": "Fridge C"}}
    state = await telegram_admin_db.localedit_confirm(update, context)
    assert state == ConversationHandler.END
    assert called == [1]
    update.callback_query.edit_message_text.assert_awaited_once_with("Updated.")


@pytest.mark.asyncio
async def test_localedit_confirm_row_gone(monkeypatch):
    monkeypatch.setattr(db_browser, "update_row", lambda table, rid, fields: False)
    update = _make_callback_update("confirm:yes")
    context = _make_context()
    context.user_data = {"table": "bot_facilities", "row": FAKE_ROWS[0], "fields": {"name": "X"}}
    state = await telegram_admin_db.localedit_confirm(update, context)
    assert state == ConversationHandler.END
    update.callback_query.edit_message_text.assert_awaited_once_with("That row no longer exists.")
