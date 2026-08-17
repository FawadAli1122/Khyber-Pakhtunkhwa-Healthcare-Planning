"""Tests for scripts/14_build_html_report.py's _insert_custom_sections()
- the pure string-manipulation function that places each custom table's
rendered section either after a named existing anchor or as a new
section before <footer>, operating on the already-fully-assembled report
HTML string. See docs/superpowers/specs/
2026-08-16-admin-custom-tables-design.md section 6.
"""
import importlib

report_mod = importlib.import_module("scripts.14_build_html_report")


def _table(table_id="t1", placement="new_section"):
    return {
        "id": table_id, "label": "Cold Chain", "report_title": "Cold Chain Status",
        "report_narrative": "", "report_placement": placement, "columns": [], "rows": [],
    }


def test_insert_custom_sections_after_named_anchor():
    html_text = '<section id="facility-readiness"><p>x</p></section><footer>foo</footer>'
    result = report_mod._insert_custom_sections(html_text, [_table(placement="after:facility-readiness")])
    assert result.index("facility-readiness") < result.index("custom-t1")
    assert result.index("custom-t1") < result.index("<footer>")


def test_insert_custom_sections_new_section_before_footer():
    html_text = '<section id="facility-readiness"></section><footer>foo</footer>'
    result = report_mod._insert_custom_sections(html_text, [_table(placement="new_section")])
    assert result.index("custom-t1") < result.index("<footer>")


def test_insert_custom_sections_no_tables_leaves_html_unchanged():
    html_text = '<section id="facility-readiness"></section><footer>foo</footer>'
    assert report_mod._insert_custom_sections(html_text, []) == html_text


def test_insert_custom_sections_falls_back_to_new_section_for_missing_anchor():
    html_text = '<section id="facility-readiness"></section><footer>foo</footer>'
    result = report_mod._insert_custom_sections(html_text, [_table(placement="after:does-not-exist")])
    assert result.index("custom-t1") < result.index("<footer>")
