import importlib

report_mod = importlib.import_module("scripts.14_build_html_report")


def test_readiness_section_html_empty_state():
    readiness = {"facilities": [], "districts": []}
    html = report_mod.readiness_section_html(readiness)
    assert "no facility readiness documents have been uploaded yet" in html.lower()
    assert "<table>" not in html


def test_readiness_section_html_populated_state():
    readiness = {
        "facilities": [
            {"facility": "DHQ Hospital", "district": "Peshawar",
             "domain_scores": {"Basic Equipment": 0.5}, "overall_score": 0.5},
        ],
        "districts": [
            {"district": "Peshawar", "mean_score": 0.5, "facilities_assessed": 1},
        ],
    }
    html = report_mod.readiness_section_html(readiness)
    assert "DHQ Hospital" in html
    assert "Peshawar" in html
    assert "Basic Equipment" in html
    assert "50%" in html
    assert "<table>" in html


def test_readiness_section_html_shows_facilities_assessed_count():
    readiness = {
        "facilities": [
            {"facility": "A", "district": "Bannu", "domain_scores": {"Basic Equipment": 1.0}, "overall_score": 1.0},
            {"facility": "B", "district": "Bannu", "domain_scores": {"Basic Equipment": 0.0}, "overall_score": 0.0},
        ],
        "districts": [{"district": "Bannu", "mean_score": 0.5, "facilities_assessed": 2}],
    }
    html = report_mod.readiness_section_html(readiness)
    assert "2" in html


def test_readiness_section_html_escapes_untrusted_facility_name():
    readiness = {
        "facilities": [
            {"facility": "<script>alert(1)</script>", "district": "Peshawar",
             "domain_scores": {"Basic Equipment": 1.0}, "overall_score": 1.0},
        ],
        "districts": [{"district": "Peshawar", "mean_score": 1.0, "facilities_assessed": 1}],
    }
    html = report_mod.readiness_section_html(readiness)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_readiness_section_html_facility_without_name_shows_placeholder():
    readiness = {
        "facilities": [
            {"facility": "", "district": "Peshawar", "domain_scores": {"Basic Equipment": 1.0}, "overall_score": 1.0},
        ],
        "districts": [{"district": "Peshawar", "mean_score": 1.0, "facilities_assessed": 1}],
    }
    html = report_mod.readiness_section_html(readiness)
    assert "&mdash;" in html
