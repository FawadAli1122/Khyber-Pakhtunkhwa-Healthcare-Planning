"""Telegram admin-parity commands for Database Ingestion - see
docs/superpowers/specs/2026-08-16-telegram-admin-parity-design.md
section 4.5. /dbconnect explicitly warns that credentials typed here
remain in this chat's permanent history before asking for any of them -
the same trade-off already accepted for /setkey's AI provider keys, with
a bigger blast radius, confirmed with the user for this feature via
AskUserQuestion (see the spec's section 1).
"""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler, filters

from server import ai_client, db_browser, db_ingestion, keystore, supplemental_data, telegram_rebuild, telegram_ui
from server.telegram_bot import _authorized

def parse_field_updates(text):
    """text: one "column=value" pair per line (not comma-separated - a
    value may itself contain a comma, e.g. editing a narrative field;
    each line splits on only the *first* "=", so a value may safely
    contain "=" too). Returns {column: value}. Raises ValueError on a
    line with no "=", an empty column name, or no usable lines at all."""
    fields = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"{line!r} is missing '=' - use column=value")
        column, _, value = line.partition("=")
        column = column.strip()
        if not column:
            raise ValueError(f"{line!r} has no column name")
        fields[column] = value.strip()
    if not fields:
        raise ValueError("Send at least one column=value line")
    return fields


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


MAX_LISTED_LOCAL_TABLES = 20
MAX_LISTED_LOCAL_ROWS = 20


async def localtables_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    tables = db_browser.list_tables()
    if not tables:
        await update.message.reply_text("No tables found.")
        return
    shown = tables[:MAX_LISTED_LOCAL_TABLES]
    lines = ["Database tables:"] + [f"{i}. {t}" for i, t in enumerate(shown, start=1)]
    if len(tables) > MAX_LISTED_LOCAL_TABLES:
        lines.append(f"+{len(tables) - MAX_LISTED_LOCAL_TABLES} more - use the admin panel to see the rest.")
    await update.message.reply_text("\n".join(lines))


def _truncate_cell(value, limit=200):
    """Any single cell value longer than `limit` characters is cut short
    with a note - guards against a single large-value column (e.g. the
    processed-data sync's geometry columns, or any long free-text column
    a Custom Data Table might have) pushing /localview's whole message
    past Telegram's 4096-character limit and failing to send. Generic:
    applied to every cell, not special-cased to any one table."""
    if value is None:
        return value
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… ({len(text) - limit} more chars, see admin panel)"


TELEGRAM_MESSAGE_LIMIT = 4096


def _truncate_message(text, limit=4000):
    """Per-cell truncation alone doesn't guarantee the *whole* message
    stays under Telegram's 4096-character hard limit - enough rows times
    enough columns, each individually under _truncate_cell()'s own cap,
    can still add up past it (found live: 20 rows of
    pipeline_district_boundaries, each with a truncated-but-still-~230-
    character geometry cell alongside its other columns, summed to well
    over 4096 and the real send failed with "Message is too long"). This
    is the actual guarantee: cut the fully-assembled message itself,
    with headroom below Telegram's real limit for the appended note.
    Generic - applies to any /localview reply regardless of column/row
    count, not just the boundary tables that surfaced this."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n… truncated ({len(text) - limit} more chars) - use the admin panel to see the rest."


async def localview_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /localview <table>")
        return
    table = context.args[0]
    columns = db_browser.get_table_columns(table)
    if columns is None:
        names = ", ".join(db_browser.list_tables())
        await update.message.reply_text(f"No table named {table!r}. Existing tables: {names}")
        return
    rows = db_browser.get_table_rows(table)
    if not rows:
        await update.message.reply_text(f"{table}: no rows yet.")
        return
    shown = rows[:MAX_LISTED_LOCAL_ROWS]
    lines = [f"{table}:"]
    for i, r in enumerate(shown, start=1):
        cells = ", ".join(f"{c['name']}={_truncate_cell(r.get(c['name']))}" for c in columns)
        lines.append(f"{i}. {cells}")
    if len(rows) > MAX_LISTED_LOCAL_ROWS:
        lines.append(f"+{len(rows) - MAX_LISTED_LOCAL_ROWS} more - use the admin panel to see the rest.")
    lines.append(f"Use /localedit {table} <row#> to change a value.")
    await update.message.reply_text(_truncate_message("\n".join(lines)))


LOCALEDIT_FIELDS, LOCALEDIT_CONFIRM = range(2)


async def localedit_start(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return ConversationHandler.END
    context.user_data.clear()
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /localedit <table> <row#>")
        return ConversationHandler.END
    table = context.args[0]
    columns = db_browser.get_table_columns(table)
    if columns is None:
        names = ", ".join(db_browser.list_tables())
        await update.message.reply_text(f"No table named {table!r}. Existing tables: {names}")
        return ConversationHandler.END
    try:
        row_number = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Row number must be a number - use /localview <table> to see them.")
        return ConversationHandler.END
    rows = db_browser.get_table_rows(table)
    if row_number < 1 or row_number > len(rows):
        await update.message.reply_text(f"No row #{row_number} - {table} has {len(rows)} row(s). Use /localview {table} to see them.")
        return ConversationHandler.END
    row = rows[row_number - 1]
    context.user_data["table"] = table
    context.user_data["columns"] = columns
    context.user_data["row"] = row
    current = "\n".join(f"{c['name']}={row.get(c['name'])}" for c in columns)
    await update.message.reply_text(
        f"Current values:\n{current}\n\nSend the columns to change, one per line as column=value "
        "(only include what's different)."
    )
    return LOCALEDIT_FIELDS


async def localedit_receive_fields(update, context):
    try:
        fields = parse_field_updates(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(f"{exc}\n\nTry again, or /cancel.")
        return LOCALEDIT_FIELDS
    row = context.user_data["row"]
    known_columns = {c["name"] for c in context.user_data["columns"]}
    unknown = [name for name in fields if name not in known_columns]
    if unknown:
        await update.message.reply_text(f"Unknown column(s): {', '.join(unknown)}\n\nTry again, or /cancel.")
        return LOCALEDIT_FIELDS
    context.user_data["fields"] = fields
    diff = "\n".join(f"{name}: {row.get(name)} -> {value}" for name, value in fields.items())
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, update", callback_data="confirm:yes"),
        InlineKeyboardButton("No, cancel", callback_data="confirm:no"),
    ]])
    await update.message.reply_text(f"Update these fields?\n{diff}", reply_markup=keyboard)
    return LOCALEDIT_CONFIRM


async def localedit_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm:no":
        await query.edit_message_text("Cancelled.")
        context.user_data.clear()
        return ConversationHandler.END
    table = context.user_data["table"]
    row = context.user_data["row"]
    try:
        updated = await asyncio.to_thread(db_browser.update_row, table, row["id"], context.user_data["fields"])
    except ValueError as exc:
        await query.edit_message_text(f"Failed: {exc}")
        context.user_data.clear()
        return ConversationHandler.END
    if not updated:
        await query.edit_message_text("That row no longer exists.")
        context.user_data.clear()
        return ConversationHandler.END
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_report)
    text = "Updated."
    if not ok:
        text += f"\n\nRebuild warning: {warning}"
    await query.edit_message_text(text)
    context.user_data.clear()
    return ConversationHandler.END


async def localedit_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


localedit_conversation = ConversationHandler(
    entry_points=[CommandHandler("localedit", localedit_start)],
    states={
        LOCALEDIT_FIELDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, localedit_receive_fields)],
        LOCALEDIT_CONFIRM: [CallbackQueryHandler(localedit_confirm, pattern=r"^confirm:")],
    },
    fallbacks=[CommandHandler("cancel", localedit_cancel)],
)


def register(application):
    application.add_handler(dbconnect_conversation)
    application.add_handler(CommandHandler("dbtables", dbtables_command))
    application.add_handler(CommandHandler("dbpreview", dbpreview_command))
    application.add_handler(dbingest_conversation)
    application.add_handler(CommandHandler("localtables", localtables_command))
    application.add_handler(CommandHandler("localview", localview_command))
    application.add_handler(localedit_conversation)
