"""Unit tests for server/telegram_bot.py. Every Telegram API call is
mocked - no real bot, no real network call, in any test here. See
docs/superpowers/specs/2026-08-16-telegram-connector-design.md.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from server import keystore, telegram_bot

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


def test_authorized_allows_the_allowlisted_user(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=987654321)
    assert telegram_bot._authorized(update) is True


def test_authorized_rejects_any_other_user(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111111111)
    assert telegram_bot._authorized(update) is False


def test_authorized_rejects_everyone_when_not_configured(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: None)
    update = _make_update(user_id=987654321)
    assert telegram_bot._authorized(update) is False


def test_build_application_registers_all_commands():
    application = telegram_bot.build_application("fake-token")
    handlers = application.handlers[0]
    # ConversationHandler (addpoint) has no .commands attribute of its own
    # - its /addpoint entry point lives one level down, inside
    # entry_points - so it's checked separately, by identity, rather than
    # folded into the same .commands scan as the six plain CommandHandlers.
    command_names = set()
    for handler in handlers:
        if hasattr(handler, "commands"):
            command_names |= set(handler.commands)
    assert {"start", "report", "map", "ask", "keys", "setkey"} <= command_names
    assert telegram_bot.addpoint_conversation in handlers


@pytest.mark.asyncio
async def test_start_command_authorized_sends_help(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    await telegram_bot.start_command(update, _make_context())
    update.message.reply_text.assert_awaited_once()
    assert "/report" in update.message.reply_text.call_args[0][0]
    assert "Engr. Fawad Ali" in update.message.reply_text.call_args[0][0]
    assert "fawadali1234567@gmail.com" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_start_command_unauthorized_sends_generic_rejection(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111111111)
    await telegram_bot.start_command(update, _make_context())
    update.message.reply_text.assert_awaited_once_with("Not authorized.")


@pytest.mark.asyncio
async def test_start_bot_task_returns_false_when_not_configured(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: None)
    result = await telegram_bot.start_bot_task()
    assert result is False


@pytest.mark.asyncio
async def test_start_bot_task_returns_false_on_application_failure(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)

    def failing_build(token):
        raise RuntimeError("invalid token")

    monkeypatch.setattr(telegram_bot, "build_application", failing_build)
    result = await telegram_bot.start_bot_task()
    assert result is False


@pytest.mark.asyncio
async def test_start_bot_task_logs_the_real_error_on_failure(monkeypatch, capsys):
    # Regression: the admin panel's "bot failed to start - check the
    # token" message is misleading when the real cause is something else
    # entirely (e.g. api.telegram.org unreachable on this network) -
    # caught via live manual verification, not a unit test on its own,
    # but this locks in that the real exception is at least logged
    # server-side for diagnosis rather than silently discarded.
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)

    def failing_build(token):
        raise RuntimeError("Connection timed out")

    monkeypatch.setattr(telegram_bot, "build_application", failing_build)
    await telegram_bot.start_bot_task()
    captured = capsys.readouterr()
    assert "Connection timed out" in captured.err


@pytest.mark.asyncio
async def test_stop_bot_task_is_a_noop_when_nothing_running():
    telegram_bot._application = None
    await telegram_bot.stop_bot_task()  # must not raise


@pytest.mark.asyncio
async def test_keys_command_lists_provider_status(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "list_status", lambda: [
        {"provider": "anthropic", "configured": True, "hint": "****1234"},
        {"provider": "groq", "configured": False, "hint": None},
    ])
    update = _make_update()
    await telegram_bot.keys_command(update, _make_context())
    reply = update.message.reply_text.call_args[0][0]
    assert "anthropic: configured" in reply
    assert "groq: not configured" in reply


@pytest.mark.asyncio
async def test_setkey_command_sets_the_key(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    set_calls = []
    monkeypatch.setattr(keystore, "set_key", lambda provider, key: set_calls.append((provider, key)))
    update = _make_update()
    await telegram_bot.setkey_command(update, _make_context(args=["groq", "gsk-xyz"]))
    assert set_calls == [("groq", "gsk-xyz")]
    update.message.reply_text.assert_awaited_once_with("groq key saved.")


@pytest.mark.asyncio
async def test_setkey_command_unknown_provider_rejected(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    await telegram_bot.setkey_command(update, _make_context(args=["bogus", "key"]))
    reply = update.message.reply_text.call_args[0][0]
    assert "Unknown provider: bogus" in reply


@pytest.mark.asyncio
async def test_setkey_command_missing_args_shows_usage(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    await telegram_bot.setkey_command(update, _make_context(args=["groq"]))
    update.message.reply_text.assert_awaited_once_with("Usage: /setkey <provider> <key>")


@pytest.mark.asyncio
async def test_keys_command_unauthorized_rejected(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111111111)
    await telegram_bot.keys_command(update, _make_context())
    update.message.reply_text.assert_awaited_once_with("Not authorized.")


from pathlib import Path

from server import ai_client


@pytest.mark.asyncio
async def test_report_command_sends_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    report_path = tmp_path / "report.html"
    report_path.write_text("<html>report</html>", encoding="utf-8")
    monkeypatch.setattr(telegram_bot, "REPORT_PATH", report_path)
    monkeypatch.setattr(telegram_bot.pdf_export, "render_report_pdf", lambda html_text: b"%PDF-fake")
    update = _make_update()
    await telegram_bot.report_command(update, _make_context())
    update.message.reply_document.assert_awaited_once()
    assert update.message.reply_document.call_args.kwargs["document"] == b"%PDF-fake"


@pytest.mark.asyncio
async def test_report_command_not_built_yet(monkeypatch, tmp_path):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(telegram_bot, "REPORT_PATH", tmp_path / "does_not_exist.html")
    update = _make_update()
    await telegram_bot.report_command(update, _make_context())
    reply = update.message.reply_text.call_args[0][0]
    assert "not built yet" in reply.lower()


@pytest.mark.asyncio
async def test_ask_command_answers_using_first_configured_provider(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_key", lambda provider: "sk-real" if provider == "groq" else None)
    monkeypatch.setattr(telegram_bot.report_context, "build_context", lambda: "digest text")
    monkeypatch.setattr(telegram_bot.ai_client, "ask", lambda provider, key, question, context: "the answer")
    update = _make_update()
    await telegram_bot.ask_command(update, _make_context(args=["What", "is", "the", "gap", "score?"]))
    update.message.reply_text.assert_awaited_once_with("the answer")


@pytest.mark.asyncio
async def test_ask_command_no_provider_configured(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_key", lambda provider: None)
    update = _make_update()
    await telegram_bot.ask_command(update, _make_context(args=["hi"]))
    reply = update.message.reply_text.call_args[0][0]
    assert "add one in the admin panel first" in reply


@pytest.mark.asyncio
async def test_ask_command_missing_question_shows_usage(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    await telegram_bot.ask_command(update, _make_context(args=[]))
    update.message.reply_text.assert_awaited_once_with("Usage: /ask <question>")


@pytest.mark.asyncio
async def test_ask_command_provider_error_becomes_plain_reply(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_key", lambda provider: "sk-real" if provider == "groq" else None)
    monkeypatch.setattr(telegram_bot.report_context, "build_context", lambda: "digest text")

    def failing_ask(provider, key, question, context):
        raise ai_client.AIProviderError("rate limited")

    monkeypatch.setattr(telegram_bot.ai_client, "ask", failing_ask)
    update = _make_update()
    await telegram_bot.ask_command(update, _make_context(args=["hi"]))
    reply = update.message.reply_text.call_args[0][0]
    assert "rate limited" in reply


class FakeCompletedProcess:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


@pytest.mark.asyncio
async def test_map_command_renders_and_sends_photo(monkeypatch, tmp_path):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    qgz_path = tmp_path / "project.qgz"
    qgz_path.write_bytes(b"fake qgz")
    monkeypatch.setattr(telegram_bot, "QGZ_PATH", qgz_path)

    def fake_run(args, **kwargs):
        output_path = Path(args[-1])
        output_path.write_bytes(b"\x89PNG fake")
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(telegram_bot.subprocess, "run", fake_run)
    update = _make_update()
    await telegram_bot.map_command(update, _make_context())
    update.message.reply_photo.assert_awaited_once()
    assert update.message.reply_photo.call_args.kwargs["photo"] == b"\x89PNG fake"


@pytest.mark.asyncio
async def test_map_command_project_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(telegram_bot, "QGZ_PATH", tmp_path / "does_not_exist.qgz")
    update = _make_update()
    await telegram_bot.map_command(update, _make_context())
    replies = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("not built yet" in r.lower() for r in replies)


@pytest.mark.asyncio
async def test_map_command_render_failure_becomes_plain_reply(monkeypatch, tmp_path):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    qgz_path = tmp_path / "project.qgz"
    qgz_path.write_bytes(b"fake qgz")
    monkeypatch.setattr(telegram_bot, "QGZ_PATH", qgz_path)
    monkeypatch.setattr(
        telegram_bot.subprocess, "run",
        lambda args, **kwargs: FakeCompletedProcess(returncode=1, stderr="Traceback: PyQGIS failure"),
    )
    update = _make_update()
    await telegram_bot.map_command(update, _make_context())
    replies = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("rendering failed" in r.lower() for r in replies)


from shapely.geometry import Polygon
from telegram.ext import ConversationHandler

from server import bot_facilities

FAKE_DISTRICTS = [{
    "district": "Peshawar",
    "geometry": Polygon([(71.4, 33.9), (71.7, 33.9), (71.7, 34.1), (71.4, 34.1)]),
}]


def _make_location_update(lat, lon, user_id=987654321):
    update = _make_update(user_id=user_id)
    update.message.location = MagicMock()
    update.message.location.latitude = lat
    update.message.location.longitude = lon
    return update


@pytest.mark.asyncio
async def test_addpoint_start_authorized_asks_for_name(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    context = _make_context()
    state = await telegram_bot.addpoint_start(update, context)
    assert state == telegram_bot.NAME
    update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_addpoint_start_unauthorized_ends_conversation(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111111111)
    state = await telegram_bot.addpoint_start(update, _make_context())
    assert state == ConversationHandler.END


@pytest.mark.asyncio
async def test_addpoint_name_then_category_stores_user_data():
    update = _make_update()
    context = _make_context()
    update.message.text = "Field Clinic"
    state = await telegram_bot.addpoint_name(update, context)
    assert state == telegram_bot.CATEGORY
    assert context.user_data["name"] == "Field Clinic"

    update.message.text = "Clinic"
    state = await telegram_bot.addpoint_category(update, context)
    assert state == telegram_bot.LOCATION
    assert context.user_data["category"] == "Clinic"


@pytest.mark.asyncio
async def test_addpoint_location_inside_kp_adds_facility(monkeypatch):
    monkeypatch.setattr(telegram_bot, "_load_districts", lambda: FAKE_DISTRICTS)
    inserted = []
    monkeypatch.setattr(bot_facilities.local_db, "insert_many",
                         lambda table, fieldnames, records: inserted.append(records))
    monkeypatch.setattr(
        telegram_bot.subprocess, "run",
        lambda args, **kwargs: FakeCompletedProcess(returncode=0),
    )
    update = _make_location_update(lat=34.0, lon=71.55)
    context = _make_context()
    context.user_data["name"] = "Field Clinic"
    context.user_data["category"] = "Clinic"

    state = await telegram_bot.addpoint_location(update, context)

    assert state == ConversationHandler.END
    saved = inserted[0]
    assert len(saved) == 1
    assert saved[0]["name"] == "Field Clinic"
    assert saved[0]["district"] == "Peshawar"
    replies = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("Peshawar" in r for r in replies)


@pytest.mark.asyncio
async def test_addpoint_location_outside_kp_rejected_without_writing(monkeypatch):
    monkeypatch.setattr(telegram_bot, "_load_districts", lambda: FAKE_DISTRICTS)
    inserted = []
    monkeypatch.setattr(bot_facilities.local_db, "insert_many",
                         lambda table, fieldnames, records: inserted.append(records))
    update = _make_location_update(lat=30.0, lon=75.0)  # nowhere near KP
    context = _make_context()
    context.user_data["name"] = "Field Clinic"
    context.user_data["category"] = "Clinic"

    state = await telegram_bot.addpoint_location(update, context)

    assert state == ConversationHandler.END
    assert inserted == []
    reply = update.message.reply_text.call_args[0][0]
    assert "outside" in reply.lower()


@pytest.mark.asyncio
async def test_addpoint_location_missing_prompts_again():
    update = _make_update()
    update.message.location = None
    context = _make_context()
    state = await telegram_bot.addpoint_location(update, context)
    assert state == telegram_bot.LOCATION


@pytest.mark.asyncio
async def test_addpoint_cancel_ends_conversation():
    update = _make_update()
    context = _make_context()
    context.user_data["name"] = "Field Clinic"
    state = await telegram_bot.addpoint_cancel(update, context)
    assert state == ConversationHandler.END
    assert context.user_data == {}
