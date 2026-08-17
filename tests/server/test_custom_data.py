"""Unit tests for server/custom_data.py's non-AI table/column/row CRUD
orchestration. Every local_db call is mocked - no real database touched,
matching tests/server/test_supplemental_data.py's established pattern.
See docs/superpowers/specs/2026-08-16-admin-custom-tables-design.md.
"""
import json

import pytest

from server import ai_client, custom_data


def _table_row(table_id="t1", label="Cold Chain", table_name="custom_cold_chain"):
    return {"id": table_id, "label": label, "table_name": table_name, "created_at": "2026-08-16T00:00:00+00:00",
            "report_title": "", "report_narrative": "", "report_placement": ""}


def _column_row(col_id="c1", table_id="t1", label="Status", column_name="status", column_type="text"):
    return {"id": col_id, "custom_table_id": table_id, "label": label,
            "column_name": column_name, "column_type": column_type}


def test_list_tables_attaches_columns(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else [_column_row()]
    ))
    tables = custom_data.list_tables()
    assert len(tables) == 1
    assert tables[0]["columns"] == [_column_row()]


def test_get_table_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    assert custom_data.get_table("does-not-exist") is None


def test_create_table_success(monkeypatch):
    inserted = []
    monkeypatch.setattr(
        custom_data.local_db, "fetch_all",
        lambda table, order_by=None: [r for t, recs in inserted for r in recs if t == table],
    )
    created_ddl = []
    monkeypatch.setattr(custom_data.local_db, "create_table", lambda name, cols: created_ddl.append((name, cols)))
    monkeypatch.setattr(
        custom_data.local_db, "insert_many",
        lambda table, fieldnames, records: inserted.append((table, records)),
    )

    table = custom_data.create_table("Cold Chain Equipment", [{"label": "Status", "type": "text"}])

    assert created_ddl == [("custom_cold_chain_equipment", [("status", "text")])]
    assert inserted[0][0] == "custom_tables"
    assert inserted[0][1][0]["label"] == "Cold Chain Equipment"
    assert inserted[0][1][0]["table_name"] == "custom_cold_chain_equipment"
    assert inserted[1][0] == "custom_table_columns"
    assert inserted[1][1][0]["column_name"] == "status"
    assert table["label"] == "Cold Chain Equipment"


def test_create_table_rejects_empty_label():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.create_table("  ", [{"label": "Status", "type": "text"}])


def test_create_table_rejects_no_columns():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.create_table("Cold Chain", [])


def test_create_table_rejects_unknown_column_type():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.create_table("Cold Chain", [{"label": "Status", "type": "boolean"}])


def test_create_table_rejects_duplicate_column_labels():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.create_table("Cold Chain", [
            {"label": "Status", "type": "text"}, {"label": "status", "type": "number"},
        ])


def test_create_table_rejects_name_collision(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else []
    ))
    with pytest.raises(custom_data.CustomDataError):
        custom_data.create_table("Cold Chain", [{"label": "Status", "type": "text"}])


def test_add_column_success(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else [_column_row()]
    ))
    ddl_calls = []
    monkeypatch.setattr(
        custom_data.local_db, "add_column",
        lambda table_name, col, ctype: ddl_calls.append((table_name, col, ctype)),
    )
    monkeypatch.setattr(custom_data.local_db, "insert_many", lambda *a, **k: None)

    table = custom_data.add_column("t1", "Last Checked", "date")
    assert ddl_calls == [("custom_cold_chain", "last_checked", "date")]
    assert table is not None


def test_add_column_returns_none_for_missing_table(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    assert custom_data.add_column("does-not-exist", "Notes", "text") is None


def test_add_column_rejects_duplicate_name(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else [_column_row()]
    ))
    with pytest.raises(custom_data.CustomDataError):
        custom_data.add_column("t1", "Status", "text")


def test_delete_column_success(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else [_column_row()]
    ))
    dropped = []
    monkeypatch.setattr(custom_data.local_db, "drop_column", lambda table_name, col: dropped.append((table_name, col)))
    monkeypatch.setattr(custom_data.local_db, "delete_by_id", lambda table, record_id: True)

    assert custom_data.delete_column("t1", "c1") is True
    assert dropped == [("custom_cold_chain", "status")]


def test_delete_column_returns_false_for_unknown_column(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else []
    ))
    assert custom_data.delete_column("t1", "does-not-exist") is False


def test_delete_table_success(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else [_column_row()]
    ))
    dropped = []
    monkeypatch.setattr(custom_data.local_db, "drop_table", lambda table_name: dropped.append(table_name))
    deleted = []
    monkeypatch.setattr(
        custom_data.local_db, "delete_by_id",
        lambda table, record_id: deleted.append((table, record_id)) or True,
    )

    assert custom_data.delete_table("t1") is True
    assert dropped == ["custom_cold_chain"]
    assert ("custom_table_columns", "c1") in deleted
    assert ("custom_tables", "t1") in deleted


def test_delete_table_returns_false_for_unknown_table(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    assert custom_data.delete_table("does-not-exist") is False


def test_delete_row_success(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else []
    ))
    monkeypatch.setattr(
        custom_data.local_db, "delete_by_id",
        lambda table, record_id: table == "custom_cold_chain" and record_id == "r1",
    )
    assert custom_data.delete_row("t1", "r1") is True


def test_delete_row_returns_false_for_unknown_table(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    assert custom_data.delete_row("does-not-exist", "r1") is False


def test_list_records_success(monkeypatch):
    fake_rows = [{"id": "r1", "status": "ok"}]
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables" else (fake_rows if table == "custom_cold_chain" else [])
    ))
    assert custom_data.list_records("t1") == fake_rows


def test_list_records_returns_none_for_unknown_table(monkeypatch):
    monkeypatch.setattr(custom_data.local_db, "fetch_all", lambda table, order_by=None: [])
    assert custom_data.list_records("does-not-exist") is None


def test_build_schema_prompt_includes_user_prompt():
    question = custom_data.build_schema_prompt("track cold-chain equipment status per facility")
    assert "cold-chain equipment status per facility" in question


def test_parse_schema_response_valid():
    raw = json.dumps({"label": "Cold Chain Equipment", "columns": [
        {"label": "Facility", "type": "text"}, {"label": "Last Checked", "type": "date"},
    ]})
    result = custom_data.parse_schema_response(raw)
    assert result["label"] == "Cold Chain Equipment"
    assert result["columns"] == [{"label": "Facility", "type": "text"}, {"label": "Last Checked", "type": "date"}]


def test_parse_schema_response_strips_code_fence():
    raw = "```json\n" + json.dumps({"label": "X", "columns": [{"label": "A", "type": "text"}]}) + "\n```"
    result = custom_data.parse_schema_response(raw)
    assert result["label"] == "X"


def test_parse_schema_response_rejects_invalid_json():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_schema_response("not json")


def test_parse_schema_response_rejects_missing_label():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_schema_response(json.dumps({"columns": [{"label": "A", "type": "text"}]}))


def test_parse_schema_response_rejects_empty_columns():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_schema_response(json.dumps({"label": "X", "columns": []}))


def test_parse_schema_response_rejects_unknown_column_type():
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_schema_response(json.dumps({"label": "X", "columns": [{"label": "A", "type": "boolean"}]}))


def test_propose_schema_calls_ai_client(monkeypatch):
    raw = json.dumps({"label": "X", "columns": [{"label": "A", "type": "text"}]})
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw)
    result = custom_data.propose_schema("groq", "key123", "track something")
    assert result["label"] == "X"


def test_build_extraction_question_includes_columns():
    table = {"label": "Cold Chain", "columns": [
        {"column_name": "facility", "column_type": "text", "label": "Facility"},
        {"column_name": "checked_on", "column_type": "date", "label": "Checked On"},
    ]}
    question = custom_data.build_extraction_question(table, "")
    assert "facility" in question
    assert "checked_on" in question


def test_parse_extraction_response_valid():
    table = {"columns": [
        {"column_name": "facility", "column_type": "text", "label": "Facility"},
        {"column_name": "temp_c", "column_type": "number", "label": "Temp C"},
        {"column_name": "checked_on", "column_type": "date", "label": "Checked On"},
    ]}
    raw = json.dumps([{"facility": "DHQ Hospital", "temp_c": 4.5, "checked_on": "2026-08-16"}])
    rows = custom_data.parse_extraction_response(raw, table)
    assert rows == [{"facility": "DHQ Hospital", "temp_c": 4.5, "checked_on": "2026-08-16"}]


def test_parse_extraction_response_rejects_bad_number():
    table = {"columns": [{"column_name": "temp_c", "column_type": "number", "label": "Temp C"}]}
    raw = json.dumps([{"temp_c": "not a number"}])
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_extraction_response(raw, table)


def test_parse_extraction_response_rejects_bad_date():
    table = {"columns": [{"column_name": "checked_on", "column_type": "date", "label": "Checked On"}]}
    raw = json.dumps([{"checked_on": "not a date"}])
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_extraction_response(raw, table)


def test_parse_extraction_response_defaults_missing_text_to_empty_string():
    table = {"columns": [{"column_name": "notes", "column_type": "text", "label": "Notes"}]}
    rows = custom_data.parse_extraction_response(json.dumps([{}]), table)
    assert rows == [{"notes": ""}]


def test_parse_extraction_response_rejects_empty_array():
    table = {"columns": [{"column_name": "notes", "column_type": "text", "label": "Notes"}]}
    with pytest.raises(custom_data.CustomDataError):
        custom_data.parse_extraction_response("[]", table)


def test_parse_placement_response_valid():
    raw = json.dumps({
        "title": "Cold Chain Status", "narrative": "Most facilities report working refrigeration.",
        "placement": "after:facility-readiness",
    })
    result = custom_data.parse_placement_response(raw)
    assert result["title"] == "Cold Chain Status"
    assert result["placement"] == "after:facility-readiness"


def test_parse_placement_response_falls_back_on_hallucinated_anchor():
    raw = json.dumps({"title": "X", "narrative": "Y", "placement": "after:not-a-real-anchor"})
    result = custom_data.parse_placement_response(raw)
    assert result["placement"] == "new_section"


def test_parse_placement_response_falls_back_on_malformed_json():
    result = custom_data.parse_placement_response("not json at all")
    assert result["placement"] == "new_section"


def test_parse_placement_response_falls_back_on_wrong_shape():
    result = custom_data.parse_placement_response(json.dumps(["not", "a", "dict"]))
    assert result["placement"] == "new_section"


def test_preview_extraction_returns_rows_without_inserting(monkeypatch):
    table = {
        "id": "t1", "label": "Cold Chain", "table_name": "custom_cold_chain",
        "columns": [{"column_name": "status", "column_type": "text", "label": "Status"}],
    }
    monkeypatch.setattr(custom_data, "get_table", lambda table_id: table if table_id == "t1" else None)
    monkeypatch.setattr(custom_data.ai_client, "ask", lambda provider, key, question, context: json.dumps(
        [{"status": "working"}]
    ))

    def fail_insert(*a, **k):
        raise AssertionError("preview must never write to the database")

    monkeypatch.setattr(custom_data.local_db, "insert_many", fail_insert)

    rows = custom_data.preview_extraction("groq", "key123", "t1", "document text", "")
    assert rows == [{"status": "working"}]


def test_preview_extraction_returns_none_for_unknown_table(monkeypatch):
    monkeypatch.setattr(custom_data, "get_table", lambda table_id: None)
    assert custom_data.preview_extraction("groq", "key123", "does-not-exist", "doc", "") is None


def test_add_rows_validates_inserts_and_stores_placement(monkeypatch):
    table = {
        "id": "t1", "label": "Cold Chain", "table_name": "custom_cold_chain",
        "columns": [{"column_name": "status", "column_type": "text", "label": "Status"},
                    {"column_name": "temp_c", "column_type": "number", "label": "Temp C"}],
    }
    monkeypatch.setattr(custom_data, "get_table", lambda table_id: table if table_id == "t1" else None)
    monkeypatch.setattr(
        custom_data.ai_client, "ask",
        lambda provider, key, question, context: json.dumps(
            {"title": "Cold Chain Status", "narrative": "All good.", "placement": "new_section"}
        ),
    )
    inserted = []
    monkeypatch.setattr(
        custom_data.local_db, "insert_many",
        lambda table_name, fieldnames, records: inserted.append((table_name, records)),
    )
    monkeypatch.setattr(
        custom_data.local_db, "fetch_all",
        lambda table_name, order_by=None: [{"id": "r1", "status": "working", "temp_c": 4.5}],
    )
    updates = []
    monkeypatch.setattr(
        custom_data.local_db, "update_by_id",
        lambda table_name, record_id, fields: updates.append((table_name, record_id, fields)),
    )

    # raw, untrusted values as submitted from the browser - temp_c is a
    # numeric-looking string here (matching a real HTML number input's
    # value), re-validated fresh regardless of whether it originated
    # from an AI preview or manual typing.
    rows = custom_data.add_rows("t1", [{"status": "working", "temp_c": "4.5"}], "groq", "key123")

    assert inserted[0][0] == "custom_cold_chain"
    assert inserted[0][1][0]["status"] == "working"
    assert inserted[0][1][0]["temp_c"] == 4.5
    assert updates == [("custom_tables", "t1", {
        "report_title": "Cold Chain Status", "report_narrative": "All good.", "report_placement": "new_section",
    })]
    assert len(rows) == 1


def test_add_rows_returns_none_for_unknown_table(monkeypatch):
    monkeypatch.setattr(custom_data, "get_table", lambda table_id: None)
    assert custom_data.add_rows("does-not-exist", [{"status": "x"}], "groq", "key123") is None


def test_add_rows_rejects_empty_list(monkeypatch):
    table = {"id": "t1", "label": "Cold Chain", "table_name": "custom_cold_chain",
              "columns": [{"column_name": "status", "column_type": "text", "label": "Status"}]}
    monkeypatch.setattr(custom_data, "get_table", lambda table_id: table)
    with pytest.raises(custom_data.CustomDataError):
        custom_data.add_rows("t1", [], "groq", "key123")


def test_add_rows_rejects_invalid_value(monkeypatch):
    table = {"id": "t1", "label": "Cold Chain", "table_name": "custom_cold_chain",
              "columns": [{"column_name": "temp_c", "column_type": "number", "label": "Temp C"}]}
    monkeypatch.setattr(custom_data, "get_table", lambda table_id: table)
    with pytest.raises(custom_data.CustomDataError):
        custom_data.add_rows("t1", [{"temp_c": "not a number"}], "groq", "key123")
