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
