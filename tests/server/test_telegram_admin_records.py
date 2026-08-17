"""Unit tests for server/telegram_admin_records.py. Every Telegram API
call is mocked - no real bot, no real network call - matching the
pattern already established in tests/server/test_telegram_bot.py.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from server import ai_client, bot_facilities, keystore, metric_overrides, supplemental_data, telegram_admin_records, telegram_rebuild, telegram_ui

TELEGRAM_CONFIG = {"token": "123456:ABC-DEF", "allowed_user_id": "987654321"}


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
async def test_supplemental_command_lists_records(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(supplemental_data, "load_records", lambda: [
        {"id": "abc123", "district": "Peshawar", "facility": "DHQ", "category": "Equipment", "label": "Ventilator"},
    ])
    update = _make_update()
    await telegram_admin_records.supplemental_command(update, _make_context())
    text = update.message.reply_text.call_args.args[0]
    assert "Peshawar" in text and "Ventilator" in text
    keyboard = update.message.reply_text.call_args.kwargs["reply_markup"]
    assert keyboard is not None


@pytest.mark.asyncio
async def test_supplemental_command_no_records(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(supplemental_data, "load_records", lambda: [])
    update = _make_update()
    await telegram_admin_records.supplemental_command(update, _make_context())
    text = update.message.reply_text.call_args.args[0]
    assert "no records yet" in text.lower()
    assert update.message.reply_text.call_args.kwargs["reply_markup"] is None


@pytest.mark.asyncio
async def test_supplemental_command_unauthorized(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111)
    await telegram_admin_records.supplemental_command(update, _make_context())
    update.message.reply_text.assert_awaited_once_with("Not authorized.")


@pytest.mark.asyncio
async def test_delete_callback_deletes_and_rebuilds(monkeypatch):
    # _STORES captures each store's rebuild function by direct reference
    # at module-import time, not by fresh telegram_rebuild.<name> attribute
    # lookup - so the fake must replace the dict entry itself, not
    # monkeypatch telegram_rebuild's own attribute (which _STORES would
    # never see, and which would otherwise let a real subprocess rebuild
    # run during this test).
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(supplemental_data, "delete_record", lambda rid: True)
    monkeypatch.setattr(supplemental_data, "load_records", lambda: [])
    rebuild_calls = []
    monkeypatch.setitem(
        telegram_admin_records._STORES["supplemental"], "rebuild",
        lambda: rebuild_calls.append(1) or (True, None),
    )
    update = _make_callback_update("del:supplemental:abc123")
    await telegram_admin_records.delete_callback(update, _make_context())
    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_text.assert_awaited_once()
    assert rebuild_calls == [1]


@pytest.mark.asyncio
async def test_delete_callback_uses_correct_rebuild_per_store(monkeypatch):
    # Real bug class this guards against: Bot-Added Facilities must use
    # rebuild_downstream_facilities(), not rebuild_report()/rebuild_downstream()
    # - three genuinely distinct scripts, easy to mix up.
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(bot_facilities, "delete_record", lambda rid: True)
    monkeypatch.setattr(bot_facilities, "load_records", lambda: [])
    called = []
    monkeypatch.setitem(telegram_admin_records._STORES["facilities"], "rebuild", lambda: called.append("facilities") or (True, None))
    monkeypatch.setitem(telegram_admin_records._STORES["supplemental"], "rebuild", lambda: called.append("report") or (True, None))
    monkeypatch.setitem(telegram_admin_records._STORES["overrides"], "rebuild", lambda: called.append("downstream") or (True, None))
    update = _make_callback_update("del:facilities:xyz789")
    await telegram_admin_records.delete_callback(update, _make_context())
    assert called == ["facilities"]


@pytest.mark.asyncio
async def test_delete_callback_record_not_found(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(metric_overrides, "delete_record", lambda rid: False)
    update = _make_callback_update("del:overrides:missing")
    await telegram_admin_records.delete_callback(update, _make_context())
    update.callback_query.edit_message_text.assert_awaited_once_with("Already deleted.")


@pytest.mark.asyncio
async def test_addrecord_start_authorized_asks_for_document(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    context = _make_context()
    state = await telegram_admin_records.addrecord_start(update, context)
    assert state == telegram_admin_records.ADDRECORD_DOC
    update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_addrecord_start_unauthorized_ends(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    from telegram.ext import ConversationHandler
    update = _make_update(user_id=111)
    state = await telegram_admin_records.addrecord_start(update, _make_context())
    assert state == ConversationHandler.END


@pytest.mark.asyncio
async def test_addrecord_skip_instruction_no_provider_configured(monkeypatch):
    monkeypatch.setattr(telegram_ui, "configured_provider_keyboard", lambda: None)
    from telegram.ext import ConversationHandler
    update = _make_update()
    context = _make_context()
    state = await telegram_admin_records.addrecord_skip_instruction(update, context)
    assert state == ConversationHandler.END
    text = update.message.reply_text.call_args.args[0]
    assert "No AI provider configured" in text


@pytest.mark.asyncio
async def test_addrecord_provider_chosen_success(monkeypatch):
    monkeypatch.setattr(keystore, "get_key", lambda provider: "sk-real")
    monkeypatch.setattr(
        supplemental_data, "add_from_document",
        lambda provider, key, text, instruction, source: [
            {"district": "Peshawar", "category": "Equipment", "label": "Ventilator"},
        ],
    )
    monkeypatch.setattr(telegram_rebuild, "rebuild_report", lambda: (True, None))
    update = _make_callback_update("provider:groq")
    context = _make_context()
    context.user_data = {"text": "doc text", "instruction": "", "source": "test.txt"}
    from telegram.ext import ConversationHandler
    state = await telegram_admin_records.addrecord_provider_chosen(update, context)
    assert state == ConversationHandler.END
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Added 1 record" in text
    assert context.user_data == {}


@pytest.mark.asyncio
async def test_addrecord_provider_chosen_ai_error_reported(monkeypatch):
    monkeypatch.setattr(keystore, "get_key", lambda provider: "sk-real")

    def failing_add(*args, **kwargs):
        raise ai_client.AIProviderError("rate limited")

    monkeypatch.setattr(supplemental_data, "add_from_document", failing_add)
    update = _make_callback_update("provider:groq")
    context = _make_context()
    context.user_data = {"text": "doc text", "instruction": "", "source": "test.txt"}
    from telegram.ext import ConversationHandler
    state = await telegram_admin_records.addrecord_provider_chosen(update, context)
    assert state == ConversationHandler.END
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "rate limited" in text


@pytest.mark.asyncio
async def test_override_receive_text_sets_source_and_prompts_provider(monkeypatch):
    monkeypatch.setattr(telegram_ui, "configured_provider_keyboard", lambda: MagicMock())
    update = _make_update()
    update.message.text = "Peshawar's population is now 5.1 million"
    context = _make_context()
    state = await telegram_admin_records.override_receive_text(update, context)
    assert state == telegram_admin_records.OVERRIDE_PROVIDER
    assert context.user_data["text"] == "Peshawar's population is now 5.1 million"
    assert context.user_data["source"] == "Telegram message"


@pytest.mark.asyncio
async def test_override_provider_chosen_success_rebuilds_downstream(monkeypatch):
    monkeypatch.setattr(keystore, "get_key", lambda provider: "sk-real")
    monkeypatch.setattr(
        metric_overrides, "add_from_document",
        lambda provider, key, text, instruction, source: [
            {"district": "Peshawar", "column": "population_2023", "value": 5100000},
        ],
    )
    called = []
    monkeypatch.setattr(telegram_rebuild, "rebuild_downstream", lambda: called.append(1) or (True, None))
    update = _make_callback_update("provider:groq")
    context = _make_context()
    context.user_data = {"text": "text", "source": "Telegram message"}
    await telegram_admin_records.override_provider_chosen(update, context)
    assert called == [1]
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Applied 1 update" in text
