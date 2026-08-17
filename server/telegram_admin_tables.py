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


MAX_LISTED_ROWS = 20


def _table_rows_message(table):
    """table: a dict from custom_data.list_tables()/get_table() (has 'id',
    'label', 'columns'). Returns (text, keyboard) the same shape as
    _tables_message() - one row per record, with an inline "Delete row #N"
    button per shown row (callback_data="del:row:<table_id>:<record_id>",
    a 4-segment scheme distinct from the 3-segment "del:table:<id>" and
    "del:<store>:<id>" schemes already used elsewhere)."""
    rows = custom_data.list_records(table["id"])
    if not rows:
        return f"{table['label']}: no rows yet.", None
    shown = rows[:MAX_LISTED_ROWS]
    lines = [f"{table['label']}:"]
    buttons = []
    for i, r in enumerate(shown, start=1):
        cells = ", ".join(f"{c['label']}={r.get(c['column_name'])}" for c in table["columns"])
        lines.append(f"{i}. {cells}")
        buttons.append([InlineKeyboardButton(f"Delete row #{i}", callback_data=f"del:row:{table['id']}:{r['id']}")])
    if len(rows) > MAX_LISTED_ROWS:
        lines.append(f"+{len(rows) - MAX_LISTED_ROWS} more - use the admin panel to see the rest.")
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def tables_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if context.args:
        label = " ".join(context.args)
        table = _find_table_by_label(label)
        if table is None:
            names = ", ".join(t["label"] for t in custom_data.list_tables()) or "(no tables yet - use /newtable)"
            await update.message.reply_text(f"No table named {label!r}. Existing tables: {names}")
            return
        text, keyboard = _table_rows_message(table)
        await update.message.reply_text(text, reply_markup=keyboard)
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


async def delete_row_callback(update, context):
    query = update.callback_query
    if not _authorized(update):
        await query.answer("Not authorized.")
        return
    await query.answer()
    _, _, table_id, record_id = query.data.split(":", 3)
    found = custom_data.delete_row(table_id, record_id)
    if not found:
        await query.edit_message_text("Already deleted.")
        return
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_report)
    table = custom_data.get_table(table_id)
    if table is None:
        await query.edit_message_text("Row deleted.")
        return
    text, keyboard = _table_rows_message(table)
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await query.edit_message_text(text, reply_markup=keyboard)


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


def register(application):
    application.add_handler(CommandHandler("tables", tables_command))
    application.add_handler(CallbackQueryHandler(delete_table_callback, pattern=r"^del:table:"))
    application.add_handler(CallbackQueryHandler(delete_row_callback, pattern=r"^del:row:"))
    application.add_handler(newtable_conversation)
    application.add_handler(addrow_conversation)
