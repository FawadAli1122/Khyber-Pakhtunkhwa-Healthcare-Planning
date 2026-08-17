import importlib
from pathlib import Path

report_mod = importlib.import_module("scripts.14_build_html_report")
REPORT_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "14_build_html_report.py"


def test_findings_html_reports_travel_time_not_distance():
    metrics = [{
        "district": "TestDistrict", "need_tier": "Critical", "gap_score": "90.0",
        "govt_pvt_institutions": "5", "beds_per_1000": "0.5", "doctors_per_1000": "0.1",
        "accessibility_min": 42,
    }]
    html = report_mod.findings_html(metrics)
    assert "42 min travel-time to nearest mapped facility" in html
    assert "km straight-line" not in html


def test_findings_html_handles_missing_accessibility():
    metrics = [{
        "district": "TestDistrict", "need_tier": "Critical", "gap_score": "90.0",
        "govt_pvt_institutions": "5", "beds_per_1000": "0.5", "doctors_per_1000": "0.1",
        "accessibility_min": "",
    }]
    html = report_mod.findings_html(metrics)
    assert "n/a min travel-time to nearest mapped facility" in html


def test_methodology_html_describes_routed_accessibility():
    html = report_mod.methodology_html()
    assert "accessibility_min" in html
    assert "accessibility_km" not in html
    assert "network- and terrain-adjusted travel-time" in html


def test_report_source_has_no_stale_straight_line_or_no_routing_wording():
    source = REPORT_SCRIPT.read_text(encoding="utf-8")
    assert "straight-line accessibility" not in source
    assert "straight-line distance" not in source
    assert "no routing engine was available" not in source
    assert "travel-time accessibility" in source
