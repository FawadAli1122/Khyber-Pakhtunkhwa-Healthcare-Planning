"""Telegram bot connector for the KP Healthcare Plan dashboard - long
polling, single-allowlisted-user auth, running as a background component
of the same asyncio event loop server/app.py's FastAPI app runs on. Every
handler is a thin wrapper: authorize, then call existing server-side
logic (pdf_export, report_context, ai_client, keystore) or narrowly new
logic (bot_facilities, qgis_render, run_downstream_facilities) - no
business logic duplicated between the web routes and the bot. See
docs/superpowers/specs/2026-08-16-telegram-connector-design.md.
"""
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler, filters

from scripts.lib.geo_utils import find_containing_district
from server import ai_client, bot_facilities, keystore, pdf_export, report_context, telegram_rebuild

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "report" / "KP_Healthcare_Plan.html"
QGIS_PYTHON = r"C:\Program Files\QGIS 4.0.0\bin\python-qgis.bat"
RENDER_SCRIPT = ROOT / "scripts" / "lib" / "qgis_render.py"
QGZ_PATH = ROOT / "gis" / "KP_Healthcare_Plan.qgz"
KP_BBOX = (31.0, 69.2, 36.9, 74.1)  # lat_min, lon_min, lat_max, lon_max - same constant every geo-fetch script in this project uses
BOUNDARIES_PATH = ROOT / "data" / "processed" / "boundaries.json"
RUN_DOWNSTREAM_FACILITIES_SCRIPT = ROOT / "scripts" / "run_downstream_facilities.py"

NAME, CATEGORY, LOCATION = range(3)

_districts_cache = None

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
    "/tables [name] - list tables, or show a table's rows; /newtable, /addrow <table> - manage custom data tables\n"
    "/dbconnect, /dbtables, /dbpreview <table>, /dbingest <table> - database ingestion\n"
    "/localtables, /localview <table>, /localedit <table> <row#> - browse/edit the bundled database directly\n\n"
    "Built by Engr. Fawad Ali - fawadali1234567@gmail.com"
)

_application = None


def _authorized(update):
    config = keystore.get_telegram_config()
    if not config:
        return False
    user = update.effective_user
    if user is None:
        return False
    return str(user.id) == str(config["allowed_user_id"])


def _load_districts():
    global _districts_cache
    if _districts_cache is None:
        boundaries = json.loads(BOUNDARIES_PATH.read_text(encoding="utf-8"))
        _districts_cache = [
            {"district": d["district"], "geometry": shape(d["geometry"])}
            for d in boundaries["districts"]
        ]
    return _districts_cache


def _is_within_kp(lon, lat):
    lat_min, lon_min, lat_max, lon_max = KP_BBOX
    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        return False
    districts = _load_districts()
    province_geom = unary_union([d["geometry"] for d in districts])
    return province_geom.contains(Point(lon, lat))


def _resolve_district(lon, lat):
    return find_containing_district(lon, lat, _load_districts())


async def start_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    await update.message.reply_text(HELP_TEXT)


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


async def start_bot_task():
    global _application
    config = keystore.get_telegram_config()
    if not config:
        return False
    try:
        application = build_application(config["token"])
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
    except Exception as exc:
        print(f"Telegram bot failed to start: {exc}", file=sys.stderr)
        _application = None
        return False
    _application = application
    return True


async def stop_bot_task():
    global _application
    if _application is None:
        return
    await _application.updater.stop()
    await _application.stop()
    await _application.shutdown()
    _application = None


# --- Temporary stubs, replaced in later tasks ---


async def report_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if not REPORT_PATH.exists():
        await update.message.reply_text("Report not built yet - run the pipeline first.")
        return
    html_text = REPORT_PATH.read_text(encoding="utf-8")
    pdf_bytes = await asyncio.to_thread(pdf_export.render_report_pdf, html_text)
    await update.message.reply_document(document=pdf_bytes, filename="KP_Healthcare_Plan.pdf")


async def map_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if not QGZ_PATH.exists():
        await update.message.reply_text("Map not built yet - run the pipeline first.")
        return
    await update.message.reply_text("Rendering map...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = Path(tmp_dir) / "map.png"
        result = await asyncio.to_thread(
            subprocess.run,
            [QGIS_PYTHON, str(RENDER_SCRIPT), str(QGZ_PATH), str(output_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0 or not output_path.exists():
            await update.message.reply_text(f"Map rendering failed: {result.stderr[-500:]}")
            return
        await update.message.reply_photo(photo=output_path.read_bytes())


async def ask_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask <question>")
        return
    provider = next((p for p in keystore.PROVIDERS if keystore.get_key(p)), None)
    if provider is None:
        await update.message.reply_text("No AI provider configured - add one in the admin panel first.")
        return
    key = keystore.get_key(provider)
    context_text = report_context.build_context()
    try:
        answer = await asyncio.to_thread(ai_client.ask, provider, key, question, context_text)
    except ai_client.AIProviderError as exc:
        await update.message.reply_text(f"AI request failed: {exc}")
        return
    await update.message.reply_text(answer)


async def keys_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    statuses = keystore.list_status()
    lines = [f"{s['provider']}: {'configured' if s['configured'] else 'not configured'}" for s in statuses]
    await update.message.reply_text("\n".join(lines))


async def setkey_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setkey <provider> <key>")
        return
    provider, key = context.args[0], " ".join(context.args[1:])
    if provider not in keystore.PROVIDERS:
        await update.message.reply_text(f"Unknown provider: {provider}. Choose from: {', '.join(keystore.PROVIDERS)}")
        return
    keystore.set_key(provider, key)
    await update.message.reply_text(f"{provider} key saved.")


async def addpoint_start(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("What's the facility's name?")
    return NAME


async def addpoint_name(update, context):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("What category is it? (e.g. Hospital, Clinic, Pharmacy)")
    return CATEGORY


async def addpoint_category(update, context):
    context.user_data["category"] = update.message.text.strip()
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Share location", request_location=True)]],
        one_time_keyboard=True, resize_keyboard=True,
    )
    await update.message.reply_text("Now share the facility's location.", reply_markup=keyboard)
    return LOCATION


async def addpoint_location(update, context):
    location = update.message.location
    if location is None:
        await update.message.reply_text("Please share a location using the button, or /cancel.")
        return LOCATION
    lon, lat = location.longitude, location.latitude

    if not _is_within_kp(lon, lat):
        await update.message.reply_text(
            "That location is outside Khyber Pakhtunkhwa - not added.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    district = _resolve_district(lon, lat)
    record = bot_facilities.add_facility(
        name=context.user_data["name"],
        district=district,
        lat=lat,
        lon=lon,
        category=context.user_data["category"],
        added_by=str(update.effective_user.id),
    )
    context.user_data.clear()

    await update.message.reply_text(
        f"Adding {record['name']} to {district}... this may take a few minutes.",
        reply_markup=ReplyKeyboardRemove(),
    )
    ok, warning = await asyncio.to_thread(telegram_rebuild.rebuild_downstream_facilities)
    if not ok:
        await update.message.reply_text(f"Facility saved, but the rebuild failed: {warning}")
    else:
        await update.message.reply_text(f"Done - {record['name']} added to {district}.")
    return ConversationHandler.END


async def addpoint_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


addpoint_conversation = ConversationHandler(
    entry_points=[CommandHandler("addpoint", addpoint_start)],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addpoint_name)],
        CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, addpoint_category)],
        LOCATION: [MessageHandler(filters.LOCATION, addpoint_location)],
    },
    fallbacks=[CommandHandler("cancel", addpoint_cancel)],
)
