"""Unit tests for scripts/25_sync_processed_to_db.py. Every local_db call
is mocked or avoided entirely - these test the pure row-building functions
only. See docs/superpowers/specs/2026-08-17-processed-data-db-sync-design.md.
"""
import importlib
import json

import pytest

sync_mod = importlib.import_module("scripts.25_sync_processed_to_db")
# Leading digit makes "from scripts.25_sync_processed_to_db import X" invalid
# syntax - matches tests/test_landcover_zonal.py's established pattern for
# every other numbered pipeline script.


def test_csv_rows_casts_numeric_columns_and_leaves_text_as_is(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("district,mean_elev_m\nChitral,3200.5\n", encoding="utf-8")
    columns, rows = sync_mod.csv_rows(csv_path, ["district"], ["mean_elev_m"])
    assert columns == [("district", "text"), ("mean_elev_m", "number")]
    assert rows[0]["district"] == "Chitral"
    assert rows[0]["mean_elev_m"] == 3200.5
    assert isinstance(rows[0]["mean_elev_m"], float)


def test_csv_rows_blank_numeric_becomes_none(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("name,beds\nSiddiqui Clinic,\n", encoding="utf-8")
    _columns, rows = sync_mod.csv_rows(csv_path, ["name"], ["beds"])
    assert rows[0]["beds"] is None


def test_csv_rows_blank_text_stays_empty_string(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("name,public_private\nAdnan Neurology Clinic,\n", encoding="utf-8")
    _columns, rows = sync_mod.csv_rows(csv_path, ["name", "public_private"], [])
    assert rows[0]["public_private"] == ""


def test_csv_rows_generates_a_unique_id_per_row(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("district\nChitral\nSwat\n", encoding="utf-8")
    _columns, rows = sync_mod.csv_rows(csv_path, ["district"], [])
    assert rows[0]["id"] != rows[1]["id"]
    assert all(isinstance(r["id"], str) and r["id"] for r in rows)


def test_csv_rows_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(sync_mod.local_db.LocalDbError, match="missing.csv"):
        sync_mod.csv_rows(tmp_path / "missing.csv", ["district"], [])


def test_csv_tables_covers_all_fourteen_csv_sources():
    filenames = {entry[0] for entry in sync_mod.CSV_TABLES}
    assert filenames == {
        "dev_stats_health.csv", "dev_stats_immunization.csv", "dev_stats_malaria.csv",
        "dev_stats_patients_treated.csv", "dev_stats_roads.csv", "district_landcover.csv",
        "district_metrics.csv", "district_terrain.csv", "district_travel_time.csv",
        "facilities_merged.csv", "facility_cross_validation.csv", "kp_district_population_2023.csv",
        "landcover_composition.csv", "suggested_sites.csv",
    }


def test_csv_tables_table_names_are_all_pipeline_prefixed():
    for _filename, table_name, _text_cols, _numeric_cols in sync_mod.CSV_TABLES:
        assert table_name.startswith("pipeline_")


def test_boundaries_rows_splits_into_district_and_province(tmp_path):
    path = tmp_path / "boundaries.json"
    path.write_text(json.dumps({
        "source": "test",
        "districts": [
            {"district": "Chitral", "division": "Malakand Division", "area_km2": 14850.0,
             "geometry": {"type": "Polygon", "coordinates": [[[71.0, 35.0], [71.1, 35.0], [71.0, 35.1]]]}},
            {"district": "Peshawar", "division": None, "area_km2": 1257.0,
             "geometry": {"type": "Polygon", "coordinates": [[[71.5, 34.0], [71.6, 34.0], [71.5, 34.1]]]}},
        ],
        "province_geometry": {"type": "Polygon", "coordinates": [[[70.0, 33.0], [75.0, 33.0], [70.0, 37.0]]]},
    }), encoding="utf-8")
    (district_columns, district_rows), (province_columns, province_rows) = sync_mod.boundaries_rows(path)
    assert district_columns == [("district", "text"), ("division", "text"), ("area_km2", "number"), ("geometry", "json")]
    assert len(district_rows) == 2
    assert district_rows[0]["district"] == "Chitral"
    assert district_rows[0]["geometry"]["type"] == "Polygon"
    assert district_rows[1]["division"] == ""  # None -> "" (TEXT, not NULL - a district always has *a* division in principle)
    assert province_columns == [("label", "text"), ("geometry", "json")]
    assert len(province_rows) == 1
    assert province_rows[0]["label"] == "Khyber Pakhtunkhwa"
    assert province_rows[0]["geometry"]["type"] == "Polygon"


def test_dev_stats_budget_rows_one_row_per_fiscal_year(tmp_path):
    path = tmp_path / "dev_stats_budget.json"
    path.write_text(json.dumps({
        "fy2024_25": {"kp": 22409.0, "ma": 6192.0, "aip": 3886.0, "total": 32487.0,
                      "provincial_total": 350587.0, "share_pct": 9.27},
        "fy2025_26": {"kp": 33915.0, "ma": 7331.0, "aip": 5574.0, "total": 46820.0,
                      "provincial_total": 500788.0, "share_pct": 9.35},
    }), encoding="utf-8")
    columns, rows = sync_mod.dev_stats_budget_rows(path)
    assert columns == [("fiscal_year", "text"), ("kp", "number"), ("ma", "number"), ("aip", "number"),
                        ("total", "number"), ("provincial_total", "number"), ("share_pct", "number")]
    assert len(rows) == 2
    fiscal_years = {r["fiscal_year"] for r in rows}
    assert fiscal_years == {"fy2024_25", "fy2025_26"}
    row = next(r for r in rows if r["fiscal_year"] == "fy2024_25")
    assert row["kp"] == 22409.0
    assert row["share_pct"] == 9.27


def test_kphcc_raw_rows_null_beds_becomes_none(tmp_path):
    path = tmp_path / "kphcc_facilities_geocoded.json"
    path.write_text(json.dumps([
        {"licence_no": "05-0058/26", "issue_date": "2029-03-25", "expire_date": "2029-03-25",
         "category": "General Practitioner Clinic", "public_private": "Private",
         "name": "Saeed Medical Clinic", "address": "Main Bazar", "district": "Upper Dir",
         "beds": None, "lon": 72.03, "lat": 35.28, "geo_precision": "district_centroid"},
    ]), encoding="utf-8")
    columns, rows = sync_mod.kphcc_raw_rows(path)
    assert ("issue_date", "date") in columns
    assert ("beds", "number") in columns
    assert rows[0]["beds"] is None
    assert rows[0]["issue_date"] == "2029-03-25"
    assert rows[0]["district"] == "Upper Dir"


def test_marham_raw_rows_casts_has_real_coords_to_bool(tmp_path):
    path = tmp_path / "marham_facilities_geocoded.json"
    path.write_text(json.dumps([
        {"name": "Shafiq Medical Centre", "url": "https://example.com/x", "telephone": "0992381586",
         "street_address": "Mansehra Road", "district": "Abbottabad", "lat": 34.19, "lon": 73.23,
         "has_real_coords": True, "category": "Other", "geo_precision": "source"},
    ]), encoding="utf-8")
    columns, rows = sync_mod.marham_raw_rows(path)
    assert ("has_real_coords", "boolean") in columns
    assert rows[0]["has_real_coords"] is True
    assert isinstance(rows[0]["has_real_coords"], bool)


def test_main_syncs_all_nineteen_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_mod, "PROCESSED", tmp_path)
    for filename, _table, text_cols, numeric_cols in sync_mod.CSV_TABLES:
        header = ",".join(text_cols + numeric_cols)
        (tmp_path / filename).write_text(header + "\n", encoding="utf-8")
    (tmp_path / "boundaries.json").write_text(json.dumps({
        "source": "test", "districts": [], "province_geometry": {"type": "Polygon", "coordinates": []},
    }), encoding="utf-8")
    (tmp_path / "dev_stats_budget.json").write_text(json.dumps({}), encoding="utf-8")
    (tmp_path / "kphcc_facilities_geocoded.json").write_text("[]", encoding="utf-8")
    (tmp_path / "marham_facilities_geocoded.json").write_text("[]", encoding="utf-8")

    calls = []
    monkeypatch.setattr(sync_mod.local_db, "replace_table", lambda table, columns, rows: calls.append(table))

    sync_mod.main()

    synced_tables = set(calls)
    expected_csv_tables = {table for _f, table, _t, _n in sync_mod.CSV_TABLES}
    expected = expected_csv_tables | {
        "pipeline_district_boundaries", "pipeline_province_boundary",
        "pipeline_dev_stats_budget", "pipeline_facilities_kphcc_raw", "pipeline_facilities_marham_raw",
    }
    assert synced_tables == expected
    assert len(calls) == 19
