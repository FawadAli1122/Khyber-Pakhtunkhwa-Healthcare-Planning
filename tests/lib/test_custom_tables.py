"""Unit tests for scripts/lib/custom_tables.py - the report-build-side
read/render counterpart to server/custom_data.py's admin-panel
orchestration. Every local_db call is mocked, matching
tests/lib/test_supplemental_records.py's established pattern. See
docs/superpowers/specs/2026-08-16-admin-custom-tables-design.md.
"""
from scripts.lib import custom_tables


def _table_row(table_id="t1"):
    return {"id": table_id, "label": "Cold Chain", "table_name": "custom_cold_chain",
            "created_at": "2026-08-16T00:00:00+00:00", "report_title": "Cold Chain Status",
            "report_narrative": "All good.", "report_placement": "new_section"}


def _column_row(table_id="t1"):
    return {"id": "c1", "custom_table_id": table_id, "label": "Status",
            "column_name": "status", "column_type": "text"}


def test_list_tables_with_data_omits_empty_tables(monkeypatch):
    monkeypatch.setattr(custom_tables.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables"
        else [_column_row()] if table == "custom_table_columns"
        else []
    ))
    assert custom_tables.list_tables_with_data() == []


def test_list_tables_with_data_includes_populated_tables(monkeypatch):
    rows = [{"id": "r1", "added_at": "2026-08-16T00:00:00+00:00", "status": "working"}]
    monkeypatch.setattr(custom_tables.local_db, "fetch_all", lambda table, order_by=None: (
        [_table_row()] if table == "custom_tables"
        else [_column_row()] if table == "custom_table_columns"
        else rows
    ))
    result = custom_tables.list_tables_with_data()
    assert len(result) == 1
    assert result[0]["rows"] == rows
    assert result[0]["columns"] == [_column_row()]


def test_render_section_html_includes_title_narrative_and_rows():
    table = {
        "id": "t1", "label": "Cold Chain", "report_title": "Cold Chain Status",
        "report_narrative": "All good.", "columns": [_column_row()],
        "rows": [{"id": "r1", "status": "working"}],
    }
    html = custom_tables.render_section_html(table)
    assert 'id="custom-t1"' in html
    assert "Cold Chain Status" in html
    assert "All good." in html
    assert "working" in html


def test_render_section_html_falls_back_to_label_when_no_title():
    table = {
        "id": "t1", "label": "Cold Chain", "report_title": "", "report_narrative": "",
        "columns": [_column_row()], "rows": [{"id": "r1", "status": "working"}],
    }
    html = custom_tables.render_section_html(table)
    assert "Cold Chain" in html


def test_render_section_html_escapes_untrusted_content():
    table = {
        "id": "t1", "label": "X", "report_title": "<script>alert(1)</script>",
        "report_narrative": "", "columns": [_column_row()],
        "rows": [{"id": "r1", "status": "<b>working</b>"}],
    }
    html = custom_tables.render_section_html(table)
    assert "<script>" not in html
    assert "<b>working</b>" not in html


def test_render_section_html_handles_none_cell_value():
    table = {
        "id": "t1", "label": "X", "report_title": "X", "report_narrative": "",
        "columns": [_column_row()], "rows": [{"id": "r1", "status": None}],
    }
    html = custom_tables.render_section_html(table)  # must not raise
    assert 'id="custom-t1"' in html
