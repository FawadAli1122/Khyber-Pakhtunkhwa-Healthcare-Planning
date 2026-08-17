"""Unit tests for server/metric_overrides.py. ai_client.ask is mocked in
every test that calls add_from_document - no real provider calls. Every
local_db call is mocked, matching tests/lib/test_local_db.py's own
established pattern - no test here touches a real database.
OVERRIDABLE_FIELDS' underlying CSVs still use tmp_path (unaffected by
the storage migration - only the overrides log itself moved to the
database). See docs/superpowers/specs/
2026-08-15-pipeline-data-overrides-phase4d-design.md sections 3-4 and
2026-08-16-bundled-local-database-design.md section 5.
"""
import json

import pytest

from server import ai_client, metric_overrides

KNOWN_DISTRICTS = ["Peshawar", "Chitral", "Abbottabad"]


@pytest.fixture
def fake_fields(tmp_path, monkeypatch):
    population_csv = tmp_path / "population.csv"
    population_csv.write_text(
        "district,population_2023,population_prior,growth_rate_pct\n"
        "Peshawar,4750388,4269079,1.10\n"
        "Chitral,318234,300000,0.60\n",
        encoding="utf-8",
    )
    health_csv = tmp_path / "health.csv"
    health_csv.write_text(
        "district,govt_institutions,govt_beds,pvt_hospitals,pvt_beds,medical_staff,paramedical_staff,pvt_practitioners\n"
        "Peshawar,129,5310,147,14532,2941,5285,1035\n"
        "Chitral,0,0,0,0,0,0,0\n",
        encoding="utf-8",
    )
    fields = {
        "population": (population_csv, {"population_2023", "population_prior", "growth_rate_pct"}, 0.5),
        "health": (health_csv, {"govt_institutions", "govt_beds", "pvt_hospitals", "pvt_beds",
                                 "medical_staff", "paramedical_staff", "pvt_practitioners"}, 1.0),
    }
    monkeypatch.setattr(metric_overrides, "OVERRIDABLE_FIELDS", fields)
    return fields


def test_parse_override_response_valid_json(fake_fields):
    raw = json.dumps([
        {"district": "Peshawar", "file": "population", "column": "population_2023",
         "value": 5000000, "reason": "New provincial estimate"},
    ])
    records = metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)
    assert records == [
        {"district": "Peshawar", "file": "population", "column": "population_2023",
         "value": 5000000.0, "reason": "New provincial estimate"},
    ]


def test_parse_override_response_strips_code_fence(fake_fields):
    raw = "```json\n" + json.dumps([
        {"district": "Peshawar", "file": "health", "column": "govt_beds", "value": 6000, "reason": ""},
    ]) + "\n```"
    records = metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)
    assert records[0]["column"] == "govt_beds"


def test_parse_override_response_invalid_json_raises(fake_fields):
    with pytest.raises(metric_overrides.MetricOverrideError):
        metric_overrides.parse_override_response("not json at all", KNOWN_DISTRICTS)


def test_parse_override_response_unknown_district_raises(fake_fields):
    raw = json.dumps([{"district": "Atlantis", "file": "population", "column": "population_2023",
                        "value": 100, "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError, match="Atlantis"):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_unknown_file_raises(fake_fields):
    raw = json.dumps([{"district": "Peshawar", "file": "roads", "column": "road_length_km",
                        "value": 100, "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError, match="roads"):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_unknown_column_raises(fake_fields):
    raw = json.dumps([{"district": "Peshawar", "file": "population", "column": "pop_per_bed",
                        "value": 100, "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError, match="pop_per_bed"):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_non_numeric_value_raises(fake_fields):
    raw = json.dumps([{"district": "Peshawar", "file": "population", "column": "population_2023",
                        "value": "a lot", "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_negative_value_raises(fake_fields):
    raw = json.dumps([{"district": "Peshawar", "file": "population", "column": "population_2023",
                        "value": -5, "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_excessive_swing_raises(fake_fields):
    # Peshawar's current population_2023 is 4750388; +/-50% threshold allows up to 7125582.
    raw = json.dumps([{"district": "Peshawar", "file": "population", "column": "population_2023",
                        "value": 20000000, "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError, match="swing"):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_within_swing_threshold_accepted(fake_fields):
    # +30% is within the +/-50% population threshold.
    raw = json.dumps([{"district": "Peshawar", "file": "population", "column": "population_2023",
                        "value": 6175504, "reason": "growth"}])
    records = metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)
    assert records[0]["value"] == 6175504.0


def test_parse_override_response_zero_current_value_skips_swing_check(fake_fields):
    # Chitral's govt_institutions is 0 - any positive value is accepted since a
    # percentage swing from zero isn't meaningful.
    raw = json.dumps([{"district": "Chitral", "file": "health", "column": "govt_institutions",
                        "value": 5, "reason": "new registrations"}])
    records = metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)
    assert records[0]["value"] == 5.0


def test_parse_override_response_empty_array_raises(fake_fields):
    with pytest.raises(metric_overrides.MetricOverrideError, match="no updates"):
        metric_overrides.parse_override_response("[]", KNOWN_DISTRICTS)


def test_parse_override_response_not_a_list_raises(fake_fields):
    with pytest.raises(metric_overrides.MetricOverrideError):
        metric_overrides.parse_override_response(json.dumps({"district": "Peshawar"}), KNOWN_DISTRICTS)


def test_load_records_calls_fetch_all_with_column_map(monkeypatch):
    fake_records = [{"id": "aaa111", "district": "Peshawar", "file": "population",
                      "column": "population_2023", "value": "5000000", "reason": "estimate",
                      "source": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(
        metric_overrides.local_db, "fetch_all",
        lambda table, order_by=None, column_map=None: fake_records if table == "metric_overrides" else [],
    )
    assert metric_overrides.load_records() == fake_records


def test_append_records_calls_insert_many_with_column_map(monkeypatch):
    calls = []
    monkeypatch.setattr(
        metric_overrides.local_db, "insert_many",
        lambda table, fieldnames, records, column_map=None: calls.append((table, fieldnames, records, column_map)),
    )
    records = [{"id": "aaa111", "district": "Peshawar", "file": "population", "column": "population_2023",
                "value": "5000000", "reason": "estimate", "source": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    metric_overrides.append_records(records)
    assert calls == [("metric_overrides", metric_overrides.FIELDNAMES, records, {"column": "column_name"})]


def test_add_from_document_success(fake_fields, monkeypatch):
    monkeypatch.setattr(metric_overrides, "load_known_districts", lambda: KNOWN_DISTRICTS)
    inserted = []
    monkeypatch.setattr(metric_overrides.local_db, "insert_many",
                         lambda table, fieldnames, records, column_map=None: inserted.append(records))

    raw_response = json.dumps([
        {"district": "Peshawar", "file": "population", "column": "population_2023",
         "value": 5000000, "reason": "New estimate"},
    ])
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw_response)

    added = metric_overrides.add_from_document(
        "anthropic", "sk-ant-real", "some document text", "update Peshawar's population", "census.pdf",
    )
    assert len(added) == 1
    assert added[0]["district"] == "Peshawar"
    assert added[0]["source"] == "census.pdf"
    assert "added_at" in added[0]
    assert inserted == [added]


def test_add_from_document_validation_failure_raises(fake_fields, monkeypatch):
    monkeypatch.setattr(metric_overrides, "load_known_districts", lambda: KNOWN_DISTRICTS)
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: "not valid json")
    with pytest.raises(metric_overrides.MetricOverrideError):
        metric_overrides.add_from_document("anthropic", "sk-ant-real", "text", "", "doc.pdf")


def test_delete_record_calls_delete_by_id(monkeypatch):
    monkeypatch.setattr(metric_overrides.local_db, "delete_by_id",
                         lambda table, record_id: table == "metric_overrides" and record_id == "aaa111")
    assert metric_overrides.delete_record("aaa111") is True
    assert metric_overrides.delete_record("does-not-exist") is False


def test_add_from_document_stamps_distinct_id_per_record(fake_fields, monkeypatch):
    monkeypatch.setattr(metric_overrides, "load_known_districts", lambda: KNOWN_DISTRICTS)
    monkeypatch.setattr(metric_overrides.local_db, "insert_many",
                         lambda table, fieldnames, records, column_map=None: None)

    raw_response = json.dumps([
        {"district": "Peshawar", "file": "population", "column": "population_2023",
         "value": 5000000, "reason": "estimate"},
        {"district": "Chitral", "file": "health", "column": "govt_beds",
         "value": 10, "reason": "new count"},
    ])
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw_response)

    added = metric_overrides.add_from_document(
        "anthropic", "sk-ant-real", "some document text", "", "census.pdf",
    )
    assert len(added) == 2
    assert added[0]["id"] != added[1]["id"]
    assert added[0]["added_at"] == added[1]["added_at"]
