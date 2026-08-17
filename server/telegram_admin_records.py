"""Telegram admin-parity commands for the three admin-overlay record
stores (Supplemental Records, Pipeline Overrides, Bot-Added Facilities)
plus /addrecord and /override - see docs/superpowers/specs/
2026-08-16-telegram-admin-parity-design.md section 4.1-4.3. Every
handler reuses the exact same server-side functions
server/routes/admin.py's routes call - no logic duplicated.
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


def register(application):
    application.add_handler(CommandHandler("supplemental", supplemental_command))
    application.add_handler(CommandHandler("overrides", overrides_command))
    application.add_handler(CommandHandler("facilities", facilities_command))
    application.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^del:(supplemental|overrides|facilities):"))
    application.add_handler(addrecord_conversation)
    application.add_handler(override_conversation)
