import importlib

report_mod = importlib.import_module("scripts.14_build_html_report")


def test_landcover_shift_callout_empty_when_no_district_exceeds_threshold():
    metrics = [
        {"district": "Alpha", "centroid_shift_km": 1.2},
        {"district": "Beta", "centroid_shift_km": 4.9},
    ]
    assert report_mod.landcover_shift_callout_html(metrics) == ""


def test_landcover_shift_callout_lists_districts_above_threshold():
    metrics = [
        {"district": "Alpha", "centroid_shift_km": 1.2},
        {"district": "Beta", "centroid_shift_km": 8.4},
    ]
    html = report_mod.landcover_shift_callout_html(metrics)
    assert "Beta" in html
    assert "8.4" in html
    assert "Alpha" not in html


def test_landcover_shift_callout_sorts_by_shift_descending():
    metrics = [
        {"district": "Alpha", "centroid_shift_km": 6.0},
        {"district": "Beta", "centroid_shift_km": 12.0},
    ]
    html = report_mod.landcover_shift_callout_html(metrics)
    assert html.index("Beta") < html.index("Alpha")


def test_landcover_shift_callout_handles_missing_field():
    metrics = [{"district": "Alpha"}]  # no centroid_shift_km key at all
    assert report_mod.landcover_shift_callout_html(metrics) == ""
