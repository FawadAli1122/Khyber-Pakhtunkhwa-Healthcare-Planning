"""Reloads every file in data/processed/ into properly-typed Postgres
tables (prefixed pipeline_) in the bundled local database, run as the last
stage of run_all.py/run_downstream.py/run_downstream_facilities.py. The
numbered pipeline scripts themselves are untouched - they keep reading/
writing these files exactly as before; this stage is purely additive,
always resyncing everything currently on disk regardless of which upstream
stage actually changed it (simpler and strictly correct - a table whose
source file didn't change this run just gets reloaded with identical
data). See docs/superpowers/specs/
2026-08-17-processed-data-db-sync-design.md.
"""
import csv
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import local_db

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"


def _new_id():
    return uuid.uuid4().hex[:12]


def _to_number(value):
    """CSV blank ("") or JSON null (None) -> None (SQL NULL) - a real gap
    in the source data, never fabricated as 0. Otherwise cast to float."""
    if value is None or value == "":
        return None
    return float(value)


def _require_file(path):
    if not path.exists():
        raise local_db.LocalDbError(
            f"{path.name} not found in data/processed/ - run the full pipeline before syncing"
        )
    return path


def csv_rows(path, text_columns, numeric_columns):
    """Reads the CSV at `path` (csv.DictReader). Returns (columns, rows):
    columns is [(name, "text"|"number"), ...] in text_columns then
    numeric_columns order; rows is [{"id": <uuid>, ...}, ...] with every
    numeric_columns value cast via _to_number() (blank -> None) and every
    text_columns value kept as the raw string. Pure aside from the file
    read - no database access."""
    _require_file(path)
    columns = [(c, "text") for c in text_columns] + [(c, "number") for c in numeric_columns]
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row = {"id": _new_id()}
            for c in text_columns:
                row[c] = raw.get(c, "")
            for c in numeric_columns:
                row[c] = _to_number(raw.get(c))
            rows.append(row)
    return columns, rows


# (source filename, table name, text columns, numeric columns) - every CSV
# file under data/processed/ maps 1:1 to one pipeline_* table via csv_rows().
CSV_TABLES = [
    ("dev_stats_health.csv", "pipeline_dev_stats_health",
     ["district"],
     ["govt_institutions", "govt_beds", "pvt_hospitals", "pvt_beds", "medical_staff",
      "paramedical_staff", "pvt_practitioners", "pop_per_bed"]),
    ("dev_stats_immunization.csv", "pipeline_dev_stats_immunization",
     ["district"],
     ["bcg", "opv0", "opv_dpt1", "opv_dpt2", "opv_dpt3", "measles", "tt1", "tt2", "tt3", "tt4", "tt5"]),
    ("dev_stats_malaria.csv", "pipeline_dev_stats_malaria",
     ["district"], ["blood_slides_examined", "malaria_cases", "malaria_cases_treated"]),
    ("dev_stats_patients_treated.csv", "pipeline_dev_stats_patients_treated",
     ["district"], ["patients_total_2024", "patients_indoor_2024", "patients_outdoor_2024"]),
    ("dev_stats_roads.csv", "pipeline_dev_stats_roads",
     ["district"], ["road_km_total", "road_km_high_type", "road_km_low_type"]),
    ("district_landcover.csv", "pipeline_district_landcover",
     ["district", "dominant_class"],
     ["area_km2", "tree_cover_pct", "shrubland_pct", "grassland_pct", "cropland_pct", "built_up_pct",
      "bare_sparse_vegetation_pct", "snow_and_ice_pct", "permanent_water_bodies_pct",
      "herbaceous_wetland_pct", "mangroves_pct", "moss_and_lichen_pct"]),
    ("district_metrics.csv", "pipeline_district_metrics",
     ["district", "division", "terrain", "need_tier"],
     ["area_km2", "population_2023", "pop_density", "mean_elev_m", "mean_slope_deg",
      "govt_pvt_institutions", "facility_count", "beds_per_1000", "doctors_per_1000",
      "accessibility_min", "centroid_shift_km", "terrain_difficulty", "gap_score", "pop_2029",
      "fac_nd29", "beds_nd29", "pop_2031", "fac_nd31", "beds_nd31", "pop_2046", "fac_nd46", "beds_nd46"]),
    ("district_terrain.csv", "pipeline_district_terrain",
     ["district"], ["mean_elev_m", "min_elev_m", "max_elev_m", "mean_slope_deg"]),
    ("district_travel_time.csv", "pipeline_district_travel_time",
     ["district", "point_source"], ["accessibility_min", "centroid_shift_km"]),
    ("facilities_merged.csv", "pipeline_facilities",
     ["name", "category", "public_private", "district", "source", "geo_precision", "is_duplicate_of"],
     ["beds", "lat", "lon"]),
    ("facility_cross_validation.csv", "pipeline_facility_cross_validation",
     ["district", "note"], ["merged_facility_count", "govt_institutions_official", "difference"]),
    ("kp_district_population_2023.csv", "pipeline_population",
     ["district", "division", "source_url"],
     ["population_2023", "population_prior", "prior_census_year", "growth_rate_pct"]),
    ("landcover_composition.csv", "pipeline_landcover_composition",
     ["label"], ["class_value", "area_km2", "pct_area"]),
    ("suggested_sites.csv", "pipeline_suggested_sites",
     ["district", "rationale"], ["priority", "lat", "lon"]),
]


def boundaries_rows(path):
    """boundaries.json splits into two tables: one row per district
    (pipeline_district_boundaries) and one single row for the whole
    province outline (pipeline_province_boundary, which has no metadata
    of its own in the source file beyond the raw geometry - "label" is
    supplied here, not read from the file). Returns
    ((district_columns, district_rows), (province_columns, province_rows))."""
    _require_file(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    district_columns = [("district", "text"), ("division", "text"), ("area_km2", "number"), ("geometry", "json")]
    district_rows = [
        {
            "id": _new_id(),
            "district": d["district"],
            "division": d.get("division") or "",
            "area_km2": _to_number(d.get("area_km2")),
            "geometry": d["geometry"],
        }
        for d in data["districts"]
    ]
    province_columns = [("label", "text"), ("geometry", "json")]
    province_rows = [{"id": _new_id(), "label": "Khyber Pakhtunkhwa", "geometry": data["province_geometry"]}]
    return (district_columns, district_rows), (province_columns, province_rows)


def dev_stats_budget_rows(path):
    """dev_stats_budget.json is a dict keyed by fiscal year (e.g.
    "fy2024_25") - each value becomes one row, with the dict key stored as
    the fiscal_year column."""
    _require_file(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    numeric_fields = ["kp", "ma", "aip", "total", "provincial_total", "share_pct"]
    columns = [("fiscal_year", "text")] + [(f, "number") for f in numeric_fields]
    rows = [
        {"id": _new_id(), "fiscal_year": fiscal_year, **{f: _to_number(values.get(f)) for f in numeric_fields}}
        for fiscal_year, values in data.items()
    ]
    return columns, rows


def kphcc_raw_rows(path):
    """kphcc_facilities_geocoded.json - a flat list of facility dicts, one
    row per facility. issue_date/expire_date are already ISO "YYYY-MM-DD"
    strings, which Postgres accepts directly for a DATE column."""
    _require_file(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    columns = [
        ("licence_no", "text"), ("issue_date", "date"), ("expire_date", "date"), ("category", "text"),
        ("public_private", "text"), ("name", "text"), ("address", "text"), ("district", "text"),
        ("beds", "number"), ("lon", "number"), ("lat", "number"), ("geo_precision", "text"),
    ]
    rows = [
        {
            "id": _new_id(),
            "licence_no": r.get("licence_no", ""),
            "issue_date": r.get("issue_date") or None,
            "expire_date": r.get("expire_date") or None,
            "category": r.get("category", ""),
            "public_private": r.get("public_private", ""),
            "name": r.get("name", ""),
            "address": r.get("address", ""),
            "district": r.get("district", ""),
            "beds": _to_number(r.get("beds")),
            "lon": _to_number(r.get("lon")),
            "lat": _to_number(r.get("lat")),
            "geo_precision": r.get("geo_precision", ""),
        }
        for r in data
    ]
    return columns, rows


def marham_raw_rows(path):
    """marham_facilities_geocoded.json - a flat list of facility dicts, one
    row per facility. has_real_coords is the only boolean-typed source
    field in the whole data/processed/ tree."""
    _require_file(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    columns = [
        ("name", "text"), ("url", "text"), ("telephone", "text"), ("street_address", "text"),
        ("district", "text"), ("category", "text"), ("geo_precision", "text"),
        ("lat", "number"), ("lon", "number"), ("has_real_coords", "boolean"),
    ]
    rows = [
        {
            "id": _new_id(),
            "name": r.get("name", ""),
            "url": r.get("url", ""),
            "telephone": r.get("telephone", ""),
            "street_address": r.get("street_address", ""),
            "district": r.get("district", ""),
            "category": r.get("category", ""),
            "geo_precision": r.get("geo_precision", ""),
            "lat": _to_number(r.get("lat")),
            "lon": _to_number(r.get("lon")),
            "has_real_coords": bool(r.get("has_real_coords")),
        }
        for r in data
    ]
    return columns, rows


def main():
    for filename, table_name, text_cols, numeric_cols in CSV_TABLES:
        columns, rows = csv_rows(PROCESSED / filename, text_cols, numeric_cols)
        local_db.replace_table(table_name, columns, rows)
        print(f"Synced {table_name}: {len(rows)} rows")

    (district_columns, district_rows), (province_columns, province_rows) = boundaries_rows(PROCESSED / "boundaries.json")
    local_db.replace_table("pipeline_district_boundaries", district_columns, district_rows)
    print(f"Synced pipeline_district_boundaries: {len(district_rows)} rows")
    local_db.replace_table("pipeline_province_boundary", province_columns, province_rows)
    print(f"Synced pipeline_province_boundary: {len(province_rows)} rows")

    columns, rows = dev_stats_budget_rows(PROCESSED / "dev_stats_budget.json")
    local_db.replace_table("pipeline_dev_stats_budget", columns, rows)
    print(f"Synced pipeline_dev_stats_budget: {len(rows)} rows")

    columns, rows = kphcc_raw_rows(PROCESSED / "kphcc_facilities_geocoded.json")
    local_db.replace_table("pipeline_facilities_kphcc_raw", columns, rows)
    print(f"Synced pipeline_facilities_kphcc_raw: {len(rows)} rows")

    columns, rows = marham_raw_rows(PROCESSED / "marham_facilities_geocoded.json")
    local_db.replace_table("pipeline_facilities_marham_raw", columns, rows)
    print(f"Synced pipeline_facilities_marham_raw: {len(rows)} rows")

    print("=== Processed data sync complete ===")


if __name__ == "__main__":
    main()
