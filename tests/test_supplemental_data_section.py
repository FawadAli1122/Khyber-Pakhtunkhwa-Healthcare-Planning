import importlib

report_mod = importlib.import_module("scripts.14_build_html_report")


def test_supplemental_data_rows_html_empty_state():
    html = report_mod.supplemental_data_rows_html([])
    assert "No additional information has been added yet." in html


def test_supplemental_data_rows_html_renders_populated_records():
    records = [
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit, operational", "source_document": "equip.pdf"},
        {"district": "Chitral", "facility": "", "category": "outbreak",
         "label": "Cholera", "detail": "12 confirmed cases", "source_document": "outbreak.txt"},
    ]
    html = report_mod.supplemental_data_rows_html(records)
    assert "Peshawar" in html
    assert "MRI Machine" in html
    assert "Chitral" in html
    assert "Cholera" in html
    assert "No additional information has been added yet." not in html


def test_supplemental_data_rows_html_sorted_by_district():
    records = [
        {"district": "Chitral", "facility": "", "category": "outbreak", "label": "Cholera", "detail": ""},
        {"district": "Abbottabad", "facility": "", "category": "equipment", "label": "X-ray", "detail": ""},
    ]
    html = report_mod.supplemental_data_rows_html(records)
    assert html.index("Abbottabad") < html.index("Chitral")


def test_supplemental_data_rows_html_handles_none_fields_without_crashing():
    # local_db.fetch_all() normalizes NULL database columns to "" before
    # any caller sees them (scripts/lib/local_db.py), but this render
    # function's own html.escape() calls must not crash even if a record
    # with a genuine None value ever reaches it directly.
    records = [
        {"district": "Peshawar", "facility": None, "category": "equipment",
         "label": "MRI Machine", "detail": None, "source_document": None, "added_at": None},
    ]
    html = report_mod.supplemental_data_rows_html(records)
    assert "None" not in html
    assert "Peshawar" in html
    assert "MRI Machine" in html


def test_supplemental_data_rows_html_escapes_untrusted_content():
    records = [
        {"district": "Peshawar<script>alert(1)</script>", "facility": "<b>Fake</b> Clinic",
         "category": "equipment", "label": "X & Y", "detail": "<img src=x onerror=alert(1)>",
         "source_document": "a<b>.pdf"},
    ]
    html = report_mod.supplemental_data_rows_html(records)
    assert "<script>" not in html
    assert "<b>Fake</b>" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;" in html
    assert "X &amp; Y" in html
