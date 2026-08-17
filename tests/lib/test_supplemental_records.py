from scripts.lib import supplemental_records


def test_load_records_calls_fetch_all(monkeypatch):
    fake_records = [{"id": "aaa111", "district": "Peshawar", "facility": "DHQ Hospital",
                      "category": "equipment", "label": "MRI Machine", "detail": "1 unit",
                      "source_document": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(
        supplemental_records.local_db, "fetch_all",
        lambda table, order_by=None: fake_records if table == "supplemental_records" else [],
    )
    assert supplemental_records.load_records() == fake_records


def test_load_records_returns_empty_list_when_table_empty(monkeypatch):
    monkeypatch.setattr(supplemental_records.local_db, "fetch_all", lambda table, order_by=None: [])
    assert supplemental_records.load_records() == []
