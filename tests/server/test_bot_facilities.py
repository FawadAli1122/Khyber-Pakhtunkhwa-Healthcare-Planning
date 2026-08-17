"""Unit tests for server/bot_facilities.py. Same id/backfill/delete_record
shape as server/supplemental_data.py and server/metric_overrides.py. Every
local_db call is mocked, matching tests/lib/test_local_db.py's own
established pattern - no test here touches a real database. See
docs/superpowers/specs/2026-08-16-manage-records-design.md,
docs/superpowers/specs/2026-08-16-telegram-connector-design.md section 8,
and 2026-08-16-bundled-local-database-design.md section 5.
"""
from server import bot_facilities


def test_load_records_calls_fetch_all(monkeypatch):
    fake_records = [{"id": "aaa111", "name": "Field Clinic", "district": "Peshawar", "lat": "34.01",
                      "lon": "71.58", "category": "Clinic", "added_at": "2026-08-16T00:00:00+00:00",
                      "added_by": "555"}]
    monkeypatch.setattr(bot_facilities.local_db, "fetch_all",
                         lambda table, order_by=None: fake_records if table == "bot_facilities" else [])
    assert bot_facilities.load_records() == fake_records


def test_append_records_calls_insert_many(monkeypatch):
    calls = []
    monkeypatch.setattr(bot_facilities.local_db, "insert_many",
                         lambda table, fieldnames, records: calls.append((table, fieldnames, records)))
    records = [{"id": "aaa111", "name": "Field Clinic", "district": "Peshawar", "lat": "34.01",
                "lon": "71.58", "category": "Clinic", "added_at": "2026-08-16T00:00:00+00:00", "added_by": "555"}]
    bot_facilities.append_records(records)
    assert calls == [("bot_facilities", bot_facilities.FIELDNAMES, records)]


def test_delete_record_calls_delete_by_id(monkeypatch):
    monkeypatch.setattr(bot_facilities.local_db, "delete_by_id",
                         lambda table, record_id: table == "bot_facilities" and record_id == "aaa111")
    assert bot_facilities.delete_record("aaa111") is True
    assert bot_facilities.delete_record("does-not-exist") is False


def test_add_facility_writes_a_record(monkeypatch):
    inserted = []
    monkeypatch.setattr(bot_facilities.local_db, "insert_many",
                         lambda table, fieldnames, records: inserted.append(records))
    record = bot_facilities.add_facility(
        name="Field Clinic", district="Peshawar", lat=34.01, lon=71.58,
        category="Clinic", added_by="555",
    )
    assert record["name"] == "Field Clinic"
    assert record["id"]
    assert "added_at" in record
    assert inserted == [[record]]
