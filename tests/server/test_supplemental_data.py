"""Unit tests for server/supplemental_data.py. ai_client.ask is mocked in
every test that calls add_from_document - no real provider calls. Every
local_db call is mocked, matching tests/lib/test_local_db.py's own
established pattern - no test here touches a real database. See
docs/superpowers/specs/2026-08-15-supplemental-facility-data-phase4b-design.md
sections 3-4 and 2026-08-16-bundled-local-database-design.md section 5.
"""
import json

import pytest

from server import ai_client, supplemental_data

KNOWN_DISTRICTS = ["Peshawar", "Chitral", "Abbottabad"]


def test_parse_ai_response_valid_json():
    raw = json.dumps([
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit, operational"},
    ])
    records = supplemental_data.parse_ai_response(raw, KNOWN_DISTRICTS)
    assert records == [
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit, operational"},
    ]


def test_parse_ai_response_strips_code_fence():
    raw = "```json\n" + json.dumps([
        {"district": "Chitral", "facility": "", "category": "outbreak",
         "label": "Cholera", "detail": "12 confirmed cases"},
    ]) + "\n```"
    records = supplemental_data.parse_ai_response(raw, KNOWN_DISTRICTS)
    assert records[0]["category"] == "outbreak"


def test_parse_ai_response_invalid_json_raises():
    with pytest.raises(supplemental_data.SupplementalDataError):
        supplemental_data.parse_ai_response("not json at all", KNOWN_DISTRICTS)


def test_parse_ai_response_unknown_district_raises():
    raw = json.dumps([{"district": "Atlantis", "facility": "", "category": "equipment",
                        "label": "X-ray", "detail": ""}])
    with pytest.raises(supplemental_data.SupplementalDataError, match="Atlantis"):
        supplemental_data.parse_ai_response(raw, KNOWN_DISTRICTS)


def test_parse_ai_response_missing_required_field_raises():
    raw = json.dumps([{"district": "Peshawar", "facility": "", "category": "",
                        "label": "X-ray", "detail": ""}])
    with pytest.raises(supplemental_data.SupplementalDataError):
        supplemental_data.parse_ai_response(raw, KNOWN_DISTRICTS)


def test_parse_ai_response_empty_array_raises():
    with pytest.raises(supplemental_data.SupplementalDataError, match="no records"):
        supplemental_data.parse_ai_response("[]", KNOWN_DISTRICTS)


def test_parse_ai_response_not_a_list_raises():
    with pytest.raises(supplemental_data.SupplementalDataError):
        supplemental_data.parse_ai_response(json.dumps({"district": "Peshawar"}), KNOWN_DISTRICTS)


def test_load_records_calls_fetch_all(monkeypatch):
    fake_records = [{"id": "aaa111", "district": "Peshawar", "facility": "DHQ Hospital",
                      "category": "equipment", "label": "MRI Machine", "detail": "1 unit",
                      "source_document": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data.local_db, "fetch_all",
                         lambda table, order_by=None: fake_records if table == "supplemental_records" else [])
    assert supplemental_data.load_records() == fake_records


def test_append_records_calls_insert_many(monkeypatch):
    calls = []
    monkeypatch.setattr(supplemental_data.local_db, "insert_many",
                         lambda table, fieldnames, records: calls.append((table, fieldnames, records)))
    records = [{"id": "aaa111", "district": "Peshawar", "facility": "", "category": "equipment",
                "label": "X-ray", "detail": "", "source_document": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    supplemental_data.append_records(records)
    assert calls == [("supplemental_records", supplemental_data.FIELDNAMES, records)]


def test_delete_record_calls_delete_by_id(monkeypatch):
    monkeypatch.setattr(supplemental_data.local_db, "delete_by_id",
                         lambda table, record_id: table == "supplemental_records" and record_id == "aaa111")
    assert supplemental_data.delete_record("aaa111") is True
    assert supplemental_data.delete_record("does-not-exist") is False


def test_add_from_document_stamps_distinct_id_per_record(monkeypatch):
    monkeypatch.setattr(supplemental_data, "load_known_districts", lambda path=None: KNOWN_DISTRICTS)

    raw_response = json.dumps([
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit"},
        {"district": "Chitral", "facility": "", "category": "outbreak",
         "label": "Cholera", "detail": "12 cases"},
    ])
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw_response)
    monkeypatch.setattr(supplemental_data.local_db, "insert_many", lambda table, fieldnames, records: None)

    added = supplemental_data.add_from_document(
        "anthropic", "sk-ant-real", "some document text", "", "equipment.pdf",
    )
    assert len(added) == 2
    assert added[0]["id"] != added[1]["id"]
    assert added[0]["added_at"] == added[1]["added_at"]


def test_parse_ai_response_case_insensitive_district_resolves_to_canonical():
    raw = json.dumps([
        {"district": "peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit"},
    ])
    records = supplemental_data.parse_ai_response(raw, KNOWN_DISTRICTS)
    assert records[0]["district"] == "Peshawar"


def test_add_from_document_success(monkeypatch):
    monkeypatch.setattr(supplemental_data, "load_known_districts", lambda path=None: KNOWN_DISTRICTS)
    inserted = []
    monkeypatch.setattr(supplemental_data.local_db, "insert_many",
                         lambda table, fieldnames, records: inserted.append(records))

    raw_response = json.dumps([
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit"},
    ])
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw_response)

    added = supplemental_data.add_from_document(
        "anthropic", "sk-ant-real", "some document text",
        "this is for Peshawar's DHQ Hospital", "equipment.pdf",
    )
    assert len(added) == 1
    assert added[0]["district"] == "Peshawar"
    assert added[0]["source_document"] == "equipment.pdf"
    assert "added_at" in added[0]
    assert inserted == [added]


def test_add_from_document_validation_failure_raises(monkeypatch):
    monkeypatch.setattr(supplemental_data, "load_known_districts", lambda path=None: KNOWN_DISTRICTS)
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: "not valid json")
    with pytest.raises(supplemental_data.SupplementalDataError):
        supplemental_data.add_from_document("anthropic", "sk-ant-real", "text", "", "doc.pdf")


def test_build_extraction_question_still_allows_free_form_categories():
    # Regression: the existing phase 4b free-form path must not be
    # removed or narrowed by adding SARA guidance.
    question = supplemental_data.build_extraction_question("", KNOWN_DISTRICTS)
    assert "not a fixed list" in question
    assert "outbreak" in question


def test_build_extraction_question_includes_sara_domains_and_items():
    question = supplemental_data.build_extraction_question("", KNOWN_DISTRICTS)
    assert "Basic Equipment" in question
    assert "Thermometer" in question
    assert "Essential Medicines" in question
    assert "Paracetamol" in question


def test_build_extraction_question_explains_present_absent_convention():
    question = supplemental_data.build_extraction_question("", KNOWN_DISTRICTS)
    assert "present" in question
    assert "absent" in question
