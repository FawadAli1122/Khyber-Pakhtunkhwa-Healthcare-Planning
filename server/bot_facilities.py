"""Facilities added via the Telegram bot's /addpoint command - a fourth
facility source alongside KPHCC/OSM/Marham, merged in by
scripts/07_merge_facilities.py (which reads the bot_facilities table
directly via scripts.lib.local_db, not through this module - see that
script for why). See docs/superpowers/specs/2026-08-16-manage-records-design.md,
docs/superpowers/specs/2026-08-16-telegram-connector-design.md section 8,
and docs/superpowers/specs/2026-08-16-bundled-local-database-design.md.
"""
import uuid
from datetime import datetime, timezone

from scripts.lib import local_db

FIELDNAMES = ("id", "name", "district", "lat", "lon", "category", "added_at", "added_by")


def load_records():
    return local_db.fetch_all("bot_facilities", order_by="added_at")


def append_records(records):
    local_db.insert_many("bot_facilities", FIELDNAMES, records)


def delete_record(record_id):
    return local_db.delete_by_id("bot_facilities", record_id)


def add_facility(name, district, lat, lon, category, added_by):
    record = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "district": district,
        "lat": lat,
        "lon": lon,
        "category": category,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "added_by": added_by,
    }
    append_records([record])
    return record
