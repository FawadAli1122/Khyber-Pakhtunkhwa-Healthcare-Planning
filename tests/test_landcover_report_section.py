import importlib

report_mod = importlib.import_module("scripts.14_build_html_report")


def test_landcover_composition_rows_sorted_by_pct_descending():
    composition = [
        {"class_value": "10", "label": "Tree cover", "area_km2": "100.0", "pct_area": "10.0"},
        {"class_value": "60", "label": "Bare / sparse vegetation", "area_km2": "500.0", "pct_area": "50.0"},
    ]
    html = report_mod.landcover_composition_rows_html(composition)
    assert html.index("Bare / sparse vegetation") < html.index("Tree cover")
    assert "50.0%" in html
    assert "500" in html


def test_district_landcover_rows_sorted_by_built_up_ascending():
    rows = [
        {
            "district": "MostBuiltUp", "dominant_class": "Built-up", "built_up_pct": "40.0",
            "tree_cover_pct": "10.0", "cropland_pct": "20.0", "bare_sparse_vegetation_pct": "5.0",
            "snow_and_ice_pct": "0.0",
        },
        {
            "district": "LeastBuiltUp", "dominant_class": "Grassland", "built_up_pct": "1.0",
            "tree_cover_pct": "30.0", "cropland_pct": "5.0", "bare_sparse_vegetation_pct": "10.0",
            "snow_and_ice_pct": "0.0",
        },
    ]
    html = report_mod.district_landcover_rows_html(rows)
    assert html.index("LeastBuiltUp") < html.index("MostBuiltUp")
    assert "1.0%" in html
    assert "Grassland" in html


def test_district_landcover_rows_handles_empty_district():
    rows = [{
        "district": "NoData", "dominant_class": "", "built_up_pct": "", "tree_cover_pct": "",
        "cropland_pct": "", "bare_sparse_vegetation_pct": "", "snow_and_ice_pct": "",
    }]
    html = report_mod.district_landcover_rows_html(rows)
    assert "n/a" in html
    assert "0.0%" in html


def test_report_source_reflects_landcover_data_in_dashboard():
    source = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "scripts" / "14_build_html_report.py"
    ).read_text(encoding="utf-8")
    assert 'id="land-cover"' in source
    assert "render_landcover_map" in source
