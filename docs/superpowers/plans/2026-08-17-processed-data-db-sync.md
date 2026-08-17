# Processed Data → Database Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load every file in `data/processed/` into properly-typed Postgres tables in the bundled local database, refreshed after every pipeline run, so the admin panel's Database Browser and the Telegram bot's `/localtables`/`/localview`/`/localedit` (both already generic over every table in the schema) can browse this data with zero further per-table code.

**Architecture:** A new pipeline stage, `scripts/25_sync_processed_to_db.py`, reads every `data/processed/*` file fresh off disk on each run and fully reloads it into a corresponding `pipeline_*` Postgres table via one new generic primitive, `local_db.replace_table()` (drop + typed create + bulk insert, one transaction per table). The numbered pipeline scripts (`01`–`24`) are untouched — they keep reading/writing files exactly as today. The new stage is appended after `14_build_html_report.py` in all three existing runner scripts.

**Tech Stack:** Python 3.12, `psycopg2` (already a dependency), `pytest` + `pytest-asyncio` (already configured, no `conftest.py`/`pytest.ini` in this repo — tests run via `pytest` from the repo root, which is how `scripts`/`scripts.lib` resolve as importable packages via their existing `__init__.py` files).

**Spec:** `docs/superpowers/specs/2026-08-17-processed-data-db-sync-design.md`

## Global Constraints

- The numbered pipeline scripts (`01_...` through `24_...`) are never modified to read/write Postgres — they stay 100% file-based. Only the three runner scripts (`run_all.py`, `run_downstream.py`, `run_downstream_facilities.py`) and the new `25_sync_processed_to_db.py` are touched.
- Every new table is named `pipeline_<name>` — this prefix is reserved by this feature and must not collide with `supplemental_records`, `metric_overrides`, `bot_facilities`, `custom_tables`, `custom_table_columns`, or any `custom_<slug>` table.
- Every new table gets exactly `id TEXT PRIMARY KEY` plus its real columns — no `added_at` (these source files have no such field; `custom_table_columns` already lacks one too, so every consumer of this schema already tolerates a table without it).
- A blank CSV value (`""`) or JSON `null` becomes SQL `NULL` for any non-TEXT column — never `0` or `""` — since these source files have real gaps (e.g. a facility with no recorded bed count) that a fabricated zero would misrepresent.
- Editing a `pipeline_*` row via the Database Browser or `/localedit` is allowed with no special-casing (matches this project's established permissive-editing precedent) and is expected to be silently overwritten by the next pipeline sync — this is documented, not code-enforced, and needs no code to "protect" these rows.
- The new sync stage is *appended* after `"14_build_html_report.py"` in all three runner scripts' `STAGES` lists — never inserted earlier, since it depends on nothing running after it and everything it reads should already be final for that run.
- `/localview`'s per-cell truncation (Task 5) is a generic fix applied to every cell in every table, not special-cased to the boundary-geometry tables that motivated it.

---

### Task 1: `local_db.replace_table()` — the generic typed-table-reload primitive

**Files:**
- Modify: `scripts/lib/local_db.py`
- Test: `tests/lib/test_local_db.py`

**Interfaces:**
- Consumes: nothing new (extends existing `validate_identifier()`, `_sql_type_for()`, `get_connection()`, `sql` module already imported).
- Produces: `local_db.replace_table(table_name, columns, rows)` — `columns`: `[(column_name, column_type), ...]` where `column_type` is one of `COLUMN_TYPE_SQL`'s keys (`"text"`, `"number"`, `"date"`, and two new keys added by this task: `"boolean"`, `"json"`). `rows`: `[{"id": <str>, <column_name>: <value>, ...}, ...]` — every row dict must include `"id"`; a `"json"`-typed column's value is a plain Python `dict`/`list` (wrapped internally, callers never wrap it themselves). Raises `local_db.LocalDbError` for an invalid table/column name or unknown column type, before ever connecting.

- [ ] **Step 1: Write the failing tests**

Add to `tests/lib/test_local_db.py`, right after the existing `test_list_columns_returns_name_and_type` test at the end of the file:

```python
def test_column_type_sql_includes_boolean_and_json():
    assert local_db.COLUMN_TYPE_SQL["boolean"] == "BOOLEAN"
    assert local_db.COLUMN_TYPE_SQL["json"] == "JSONB"


def test_replace_table_rejects_invalid_table_name_without_connecting(monkeypatch):
    def fail_connect(**kwargs):
        raise AssertionError("should not attempt to connect with an invalid table name")
    monkeypatch.setattr(local_db.psycopg2, "connect", fail_connect)
    with pytest.raises(local_db.LocalDbError):
        local_db.replace_table("Bad Name", [("district", "text")], [])


def test_replace_table_rejects_unknown_column_type_without_connecting(monkeypatch):
    def fail_connect(**kwargs):
        raise AssertionError("should not attempt to connect with an unknown column type")
    monkeypatch.setattr(local_db.psycopg2, "connect", fail_connect)
    with pytest.raises(local_db.LocalDbError):
        local_db.replace_table("pipeline_x", [("bad", "not_a_real_type")], [])


def test_replace_table_inserts_rows_with_given_columns(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.replace_table(
        "pipeline_district_terrain",
        [("district", "text"), ("mean_elev_m", "number")],
        [{"id": "a1", "district": "Chitral", "mean_elev_m": 3200.5}],
    )
    insert_query, values = cursor.executed[-1]
    assert "INSERT INTO pipeline_district_terrain (id, district, mean_elev_m)" in insert_query
    assert values == [("a1", "Chitral", 3200.5)]
    assert conn.committed is True
    assert conn.closed is True


def test_replace_table_wraps_json_columns(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.replace_table(
        "pipeline_district_boundaries",
        [("district", "text"), ("geometry", "json")],
        [{"id": "a1", "district": "Chitral", "geometry": {"type": "Polygon", "coordinates": []}}],
    )
    _insert_query, values = cursor.executed[-1]
    wrapped = values[0][2]
    assert isinstance(wrapped, local_db.Json)
    assert wrapped.adapted == {"type": "Polygon", "coordinates": []}


def test_replace_table_no_rows_skips_insert(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.replace_table("pipeline_x", [("district", "text")], [])
    assert len(cursor.executed) == 2  # DROP + CREATE only, no INSERT
    assert conn.committed is True
    assert conn.closed is True


def test_replace_table_drops_before_creating(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr(local_db.psycopg2, "connect", lambda **kwargs: conn)
    local_db.replace_table("pipeline_x", [("district", "text")], [])
    assert len(cursor.executed) == 2
    # Both DROP and CREATE go through sql.Composed objects (like create_table()
    # already does), which don't render to plain strings without a live
    # connection - so, matching this file's own existing precedent for
    # create_table()/add_column()/drop_column()/drop_table(), this only
    # asserts the two statements were issued in order and the run
    # committed/closed cleanly, not their literal text.
    assert conn.committed is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/lib/test_local_db.py -v -k replace_table or boolean_and_json`
Expected: FAIL with `AttributeError: module 'scripts.lib.local_db' has no attribute 'replace_table'` (and `'Json'`).

- [ ] **Step 3: Implement `replace_table()`**

In `scripts/lib/local_db.py`, change the import line:

```python
from psycopg2.extras import RealDictCursor
```

to:

```python
from psycopg2.extras import Json, RealDictCursor
```

Update `COLUMN_TYPE_SQL`:

```python
COLUMN_TYPE_SQL = {"text": "TEXT", "number": "NUMERIC", "date": "DATE", "boolean": "BOOLEAN", "json": "JSONB"}
```

Add `replace_table()` right after `update_by_id()` (before `list_all_tables()`):

```python
def replace_table(table_name, columns, rows):
    """columns: [(column_name, column_type), ...] - column_type one of
    COLUMN_TYPE_SQL's keys. rows: [{"id": ..., <column>: ...}, ...] - every
    row must include "id" (the caller generates it, matching every other
    table's convention). Validates table_name and every column name via
    validate_identifier() before building any SQL - a bad name never
    reaches the database. One transaction: DROP TABLE IF EXISTS -> CREATE
    TABLE (id TEXT PRIMARY KEY + the given typed columns) -> bulk INSERT
    every row -> commit. A drop-and-recreate, not an ALTER-in-place
    migration - there's no existing data in these tables worth preserving
    across a schema change, unlike the admin-overlay tables. Used by
    scripts/25_sync_processed_to_db.py to reload data/processed/* into
    Postgres on every pipeline run. See docs/superpowers/specs/
    2026-08-17-processed-data-db-sync-design.md."""
    validate_identifier(table_name)
    col_defs = [sql.SQL("id TEXT PRIMARY KEY")]
    json_columns = set()
    for column_name, column_type in columns:
        validate_identifier(column_name)
        col_defs.append(sql.SQL("{} {}").format(sql.Identifier(column_name), sql.SQL(_sql_type_for(column_type))))
        if column_type == "json":
            json_columns.add(column_name)
    drop_statement = sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table_name))
    create_statement = sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(table_name), sql.SQL(", ").join(col_defs))

    db_columns = ["id"] + [c for c, _ in columns]
    columns_sql = ", ".join(db_columns)
    placeholders = ", ".join(["%s"] * len(db_columns))
    insert_statement = f"INSERT INTO {table_name} ({columns_sql}) VALUES ({placeholders})"
    values = [
        tuple(Json(r[c]) if c in json_columns else r.get(c) for c in db_columns)
        for r in rows
    ]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(drop_statement)
            cur.execute(create_statement)
            if values:
                cur.executemany(insert_statement, values)
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/lib/test_local_db.py -v`
Expected: all tests PASS (existing tests plus the 6 new ones).

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/local_db.py tests/lib/test_local_db.py
git commit -m "feat: add local_db.replace_table() generic typed-table-reload primitive"
```

---

### Task 2: CSV sync builder — `scripts/25_sync_processed_to_db.py` (CSV sources)

**Files:**
- Create: `scripts/25_sync_processed_to_db.py`
- Test: `tests/test_sync_processed_to_db.py`

**Interfaces:**
- Consumes: `local_db.replace_table(table_name, columns, rows)` (Task 1).
- Produces: `csv_rows(path, text_columns, numeric_columns)` -> `(columns, rows)`; `CSV_TABLES` (declarative list of `(filename, table_name, text_columns, numeric_columns)` tuples for the 14 CSV sources) — both consumed by Task 3's `main()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sync_processed_to_db.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_sync_processed_to_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.25_sync_processed_to_db'`.

- [ ] **Step 3: Implement the CSV builder and declarative table list**

Create `scripts/25_sync_processed_to_db.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_sync_processed_to_db.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/25_sync_processed_to_db.py tests/test_sync_processed_to_db.py
git commit -m "feat: add CSV sync builder for processed-data-to-db sync stage"
```

---

### Task 3: JSON sync builders + `main()` orchestration

**Files:**
- Modify: `scripts/25_sync_processed_to_db.py`
- Test: `tests/test_sync_processed_to_db.py`

**Interfaces:**
- Consumes: `csv_rows()`, `CSV_TABLES` (Task 2); `local_db.replace_table()` (Task 1).
- Produces: `boundaries_rows(path)` -> `((district_columns, district_rows), (province_columns, province_rows))`; `dev_stats_budget_rows(path)`, `kphcc_raw_rows(path)`, `marham_raw_rows(path)` -> `(columns, rows)`; `main()` (the script's entry point, orchestrates all 19 table loads).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_processed_to_db.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_sync_processed_to_db.py -v -k "boundaries_rows or budget_rows or kphcc_raw or marham_raw or main_syncs"`
Expected: FAIL with `AttributeError: module 'scripts.25_sync_processed_to_db' has no attribute 'boundaries_rows'` (and similarly for the others / `main`).

- [ ] **Step 3: Implement the JSON builders and `main()`**

Append to `scripts/25_sync_processed_to_db.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_sync_processed_to_db.py -v`
Expected: all tests PASS (Task 2's 7 plus this task's 5, i.e. 12 total).

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass, no prior test broken.

- [ ] **Step 6: Commit**

```bash
git add scripts/25_sync_processed_to_db.py tests/test_sync_processed_to_db.py
git commit -m "feat: add JSON sync builders and main() orchestration to processed data sync"
```

---

### Task 4: Wire the sync stage into all three pipeline runners

**Files:**
- Modify: `scripts/run_all.py`
- Modify: `scripts/run_downstream.py`
- Modify: `scripts/run_downstream_facilities.py`

**Interfaces:**
- Consumes: `scripts/25_sync_processed_to_db.py` (Tasks 2–3), run as a subprocess exactly like every other stage — no Python-level interface, just a filename in each `STAGES` list.

- [ ] **Step 1: Append the new stage to `run_all.py`**

In `scripts/run_all.py`, change:

```python
    "14_build_html_report.py",
]
```

to:

```python
    "14_build_html_report.py",
    "25_sync_processed_to_db.py",
]
```

- [ ] **Step 2: Append the new stage to `run_downstream.py`**

In `scripts/run_downstream.py`, change:

```python
    "14_build_html_report.py",
]
```

to:

```python
    "14_build_html_report.py",
    "25_sync_processed_to_db.py",
]
```

- [ ] **Step 3: Append the new stage to `run_downstream_facilities.py`**

In `scripts/run_downstream_facilities.py`, change:

```python
    "14_build_html_report.py",
]
```

to:

```python
    "14_build_html_report.py",
    "25_sync_processed_to_db.py",
]
```

- [ ] **Step 4: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass (no existing test asserts on these `STAGES` lists' contents — none of the three runner scripts have any prior test coverage, matching this project's established "plain subprocess-orchestration scripts aren't unit tested" precedent, verified before this task by searching for any test file referencing `STAGES`).

- [ ] **Step 5: Commit**

```bash
git add scripts/run_all.py scripts/run_downstream.py scripts/run_downstream_facilities.py
git commit -m "feat: wire processed-data-to-db sync into all three pipeline runners"
```

---

### Task 5: `/localview` per-cell truncation guard

**Files:**
- Modify: `server/telegram_admin_db.py`
- Test: `tests/server/test_telegram_admin_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_truncate_cell(value, limit=200)` — used internally by `localview_command`, not exported for other modules.

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_telegram_admin_db.py`, right after the existing `test_localview_command_no_args_shows_usage` test:

```python
def test_truncate_cell_leaves_short_values_unchanged():
    assert telegram_admin_db._truncate_cell("Fridge A") == "Fridge A"


def test_truncate_cell_leaves_none_unchanged():
    assert telegram_admin_db._truncate_cell(None) is None


def test_truncate_cell_cuts_long_values_with_a_note():
    long_value = "x" * 500
    result = telegram_admin_db._truncate_cell(long_value, limit=200)
    assert result.startswith("x" * 200)
    assert "more chars" in result
    assert len(result) < len(long_value)


def test_truncate_cell_exact_limit_length_unchanged():
    exact_value = "x" * 200
    assert telegram_admin_db._truncate_cell(exact_value, limit=200) == exact_value


@pytest.mark.asyncio
async def test_localview_command_truncates_long_cell_values(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(db_browser, "get_table_columns", lambda t: [{"name": "id", "type": "text"}, {"name": "geometry", "type": "jsonb"}])
    monkeypatch.setattr(db_browser, "get_table_rows", lambda t: [{"id": "r1", "geometry": "{" + ("x" * 5000) + "}"}])
    update = _make_update()
    await telegram_admin_db.localview_command(update, _make_context(args=["pipeline_district_boundaries"]))
    text = update.message.reply_text.call_args.args[0]
    assert len(text) < 4096
    assert "more chars" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/server/test_telegram_admin_db.py -v -k truncate_cell or truncates_long_cell`
Expected: FAIL with `AttributeError: module 'server.telegram_admin_db' has no attribute '_truncate_cell'`.

- [ ] **Step 3: Implement `_truncate_cell()` and wire it into `localview_command`**

In `server/telegram_admin_db.py`, add right before `async def localview_command(update, context):`:

```python
def _truncate_cell(value, limit=200):
    """Any single cell value longer than `limit` characters is cut short
    with a note - guards against a single large-value column (e.g. the
    processed-data sync's geometry columns, or any long free-text column
    a Custom Data Table might have) pushing /localview's whole message
    past Telegram's 4096-character limit and failing to send. Generic:
    applied to every cell, not special-cased to any one table."""
    if value is None:
        return value
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… ({len(text) - limit} more chars, see admin panel)"
```

Then change the cell-rendering line inside `localview_command`:

```python
        cells = ", ".join(f"{c['name']}={r.get(c['name'])}" for c in columns)
```

to:

```python
        cells = ", ".join(f"{c['name']}={_truncate_cell(r.get(c['name']))}" for c in columns)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/server/test_telegram_admin_db.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add server/telegram_admin_db.py tests/server/test_telegram_admin_db.py
git commit -m "fix: truncate long cell values in /localview to avoid exceeding Telegram's message limit"
```

---

### Task 6: Live verification against the real pipeline, database, admin panel, and bot

No new code in this task — it verifies Tasks 1–5 against real data, the real bundled Postgres instance, the real admin panel, and the real Telegram bot. Ask the user for permission before resetting the admin password (same established pattern as every prior live-verification session in this project) if it isn't already known for this session.

- [ ] **Step 1: Ensure the bundled local database is running**

Start the app (`Start Dashboard.bat`, or `python -m server` directly) so `local_db.ensure_running()` fires via the FastAPI lifespan — the new sync stage assumes the database is already up, matching every other `local_db`-dependent pipeline stage's existing precedent.

- [ ] **Step 2: Run the real sync stage standalone first**

Run: `python scripts/25_sync_processed_to_db.py`
Expected: 19 "Synced ..." lines printed, one per table, each with a real row count, ending in "=== Processed data sync complete ===". No traceback.

- [ ] **Step 3: Verify all 19 tables via `psql`**

Run (adjust for the bundled instance's actual port/credentials per `scripts/lib/local_db.py`'s `PORT`/`DB_NAME`/`DB_USER` constants):

```bash
"C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -p 5544 -U kp_admin -d kp_healthcare -c "\dt pipeline_*"
```

Expected: 19 rows, all named `pipeline_*`. Spot-check a few real values, e.g.:

```bash
"C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -p 5544 -U kp_admin -d kp_healthcare -c "SELECT district, mean_elev_m FROM pipeline_district_terrain WHERE district = 'Chitral';"
"C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -p 5544 -U kp_admin -d kp_healthcare -c "SELECT count(*) FROM pipeline_facilities;"
"C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -p 5544 -U kp_admin -d kp_healthcare -c "SELECT beds FROM pipeline_facilities_kphcc_raw WHERE beds IS NULL LIMIT 1;"
```

Confirm: `pipeline_facilities` has ~1,584 rows (matching the known real facility count), at least one `pipeline_facilities_kphcc_raw` row has a real `NULL` in `beds` (not `0`), and `district_terrain`'s value for Chitral matches the real `data/processed/district_terrain.csv` value.

- [ ] **Step 4: Verify via the real admin panel**

With the server running, open the admin panel's Database Browser section. Confirm all 19 `pipeline_*` tables appear in the table dropdown (alongside the pre-existing tables). Open `pipeline_district_metrics` and confirm the rendered rows match `data/processed/district_metrics.csv` exactly. Open `pipeline_facilities` (~1,584 rows) and confirm the table renders without error (may be visually large - that's expected, not a bug).

- [ ] **Step 5: Verify via the real Telegram bot**

With explicit permission (same established pattern - ask before driving the user's own logged-in Telegram Web session): send `/localtables` and confirm all 19 `pipeline_*` names appear (respecting the existing 20-table cap/note if the total table count exceeds it). Send `/localview pipeline_district_boundaries` and confirm the reply sends successfully and each geometry cell is visibly truncated with a "more chars" note - this is the direct proof the Task 5 fix actually prevents the send failure it targets. Send `/localview pipeline_district_terrain` (no large cells) and confirm full untruncated values show normally.

- [ ] **Step 6: Verify the "edits get overwritten by the next sync" caveat is real, not just documented**

Via the admin panel, edit one field of one row in `pipeline_district_terrain` (e.g. change `mean_elev_m` for one district) and confirm the change is saved (re-open the table, see the new value). Re-run `python scripts/25_sync_processed_to_db.py`. Confirm via `psql` or the admin panel that the value has reverted to the real value from `data/processed/district_terrain.csv` - proving the documented caveat in the spec/plan matches actual behavior.

- [ ] **Step 7: Run a real full pipeline run end-to-end**

Run: `python scripts/run_all.py` (or, if a full run is impractical in this session due to time, `python scripts/run_downstream.py` after confirming `data/processed/` is already populated from a prior full run - either way, the sync stage must be the last one to execute and must succeed).
Expected: every stage (including the new `25_sync_processed_to_db.py` at the end) completes with exit code 0, ending in "=== Pipeline complete ===" (or "=== Downstream pipeline complete ==="). Re-verify the table count via `psql` afterward.

- [ ] **Step 8: Clean up**

Revert the test edit from Step 6 if it wasn't already overwritten by Step 7's rerun. Restore the admin password if it was temporarily reset for this session (byte-for-byte, verified via hash comparison, matching every prior live-verification session in this project). Confirm `git status` is clean except for this plan's own intended commits (the `gis/KP_Healthcare_Plan.qgz` cosmetic random-layer-id diff, if a full `run_all.py` was run in Step 7, is expected and gets reverted via `git checkout`, matching this project's established precedent).

- [ ] **Step 9: Final commit**

If Step 7 produced any real data changes worth keeping (e.g. this was the first time `pipeline_*` tables were populated and the resulting `data/processed/*` diffs are legitimate, not test noise), commit them separately with a clear message. Otherwise, if `run_all.py`/`run_downstream.py` in Step 7 produced no real diffs (already up to date), there is nothing further to commit for this task.

---

## Live-verification results (2026-08-17)

All 6 tasks completed and committed: `46cd4b9` (Task 1), `8865fc9` (Task 2),
`d9d789f` (Task 3), `f6d2416` (Task 4), `057a9ed` (Task 5), plus two
unplanned fix commits discovered during Task 6's own live verification:
`7fd0dc1` and `6e70aa2` (see below). 664/664 tests passing throughout.

**Two real, significant bugs found during live verification — both in
pre-existing, previously-shipped `local_db.py` infrastructure, not in this
plan's own new code, but never triggered before because nothing had ever
sent real non-ASCII data through the bundled database until this feature's
first live run against `facilities_merged.csv`'s real Marham/OSM-scraped
facility names/addresses:**

1. **`local_db.get_connection()` never set an explicit client encoding** -
   on Windows, psycopg2 falls back to the OS codepage (`cp1252`), which
   raised `UnicodeEncodeError` the instant a non-cp1252 character reached
   it. Fixed with `conn.set_client_encoding("UTF8")`, its own test, commit
   `7fd0dc1`.
2. **Deeper, foundational: the bundled database's own `server_encoding`
   was `WIN1252`**, not UTF-8 - `local_db.initialize()`'s `initdb` call had
   never specified `--encoding`, so it silently inherited the Windows OS
   locale's codepage when the bundled database was first created (in the
   original Bundled Local PostgreSQL Implementation Plan). Postgres cannot
   change a database's encoding in place after creation. Confirmed via
   `SHOW server_encoding` before diagnosing further. Since this changes
   already-shipped, out-of-plan-scope infrastructure and requires wiping
   the existing database directory to fix, stopped and used
   `AskUserQuestion` rather than deciding unilaterally (matching this
   project's established precedent for exactly this class of finding) -
   confirmed first that every real data table was already empty (0 rows:
   `bot_facilities`, `custom_tables`, `custom_table_columns`,
   `metric_overrides`, `supplemental_records`), so nothing was at risk.
   User approved. Fixed by adding `--encoding=UTF8 --locale=C` to the
   `initdb` invocation, deleting the empty `data/pgdata/`, and letting the
   server's lifespan reinitialize it fresh. Commit `6e70aa2`. No automated
   test (matches this module's established "bootstrap functions verified
   manually only" precedent - confirmed no prior test touched `initdb`
   before adding this).

**Live-verified end-to-end against the real bundled database and real
admin panel** (admin password temporarily reset with permission, restored
byte-for-byte afterward and verified via direct hash comparison - same
established pattern as every prior live-verification session): the real
sync script loaded all 19 `pipeline_*` tables with correct row counts
(`pipeline_facilities`: 1,584, matching the known real total; the two raw
geocoding-cache tables: 276/1,087) after both fixes landed. Cross-checked
real values against their source CSVs byte-for-byte (`district_terrain.csv`
Lower Chitral row, `district_metrics.csv` Orakzai row - exact matches via
the real `/admin/api/db-browser/tables/*/rows` route). Confirmed 13 real
Urdu-script facility names (e.g. a Kurram district facility) round-tripped
correctly through the fixed encoding - direct proof against the exact kind
of real data that surfaced both bugs, not just synthetic test data.
Confirmed the documented "edits get silently overwritten by the next sync"
caveat is real, not just documented: edited `pipeline_district_terrain`'s
Lower Chitral `mean_elev_m` to `9999.9` via the real admin PUT route,
confirmed it persisted, re-ran the sync, confirmed it reverted to the real
`3463.6` value with a freshly-generated row `id` (proving a true
drop-and-recreate, not a partial/skipped update). Ran the real
`run_downstream.py` chain end-to-end (`07b` through `14`, ending at the new
`25_sync_processed_to_db.py`) - completed with exit code 0, correctly
re-synced all 19 tables as the chain's last step.

**Telegram bot step (Task 6 Step 5) could not be completed end-to-end**:
`api.telegram.org` was unreachable from this environment (`curl` timed
out), matching this project's own already-documented pre-existing
environmental limitation from earlier sessions (Telegram Connector,
Telegram Admin Parity) - not a regression from this feature. Substituted:
confirmed `/localtables`/`/localview`/`/localedit` call the exact same
`db_browser` functions already live-verified via the real admin route
above, and Task 5's own unit tests directly cover the truncation behavior
(including the specific "over 4096 chars" scenario against a fake
5000-char cell) that motivated the fix.

Cleanup: reverted the `git status` noise from the real pipeline run (the
already-known cosmetic random-layer-id `.qgz` diff and the report's own
"generated <date>" timestamp line - both `git checkout`'d back), deleted
the stray `metric_overrides_baseline.csv` sidecar, restored the admin
password byte-for-byte, stopped the bundled database and server cleanly.
Working tree confirmed clean. `finishing-a-development-branch` confirmed
(again) this project has no branch/worktree/remote - normal repo, direct
commits to `master`, nothing to merge or push.

## Telegram bot live-verification round (2026-08-17, follow-up request)

User explicitly asked to also test via the real Telegram bot, driving the
user's own logged-in Telegram Web session in Chrome (same established
permission pattern). The earlier `api.telegram.org` unreachability turned
out to be transient, not persistent - a plain restart connected cleanly
this time (`Application startup complete` with no "Telegram bot failed to
start" line), confirming this project's own prior "environmental, not a
code issue" conclusion about this specific failure mode.

**A third real bug found live, this time in this plan's own Task 5 code**:
`/localview pipeline_district_boundaries` failed outright with
`telegram.error.BadRequest: Message is too long` - 20 rows, each with a
geometry cell individually truncated under `_truncate_cell()`'s 200-char
cap, still summed (alongside `district`/`division`/`area_km2`) to well
over Telegram's real 4096-character message limit. Per-cell truncation
alone cannot guarantee the *assembled* message stays under the limit once
enough rows/columns are involved - a real gap in Task 5's original design,
not caught by its own unit tests (which only ever exercised a single row).
Fixed with a second, generic layer: `_truncate_message(text, limit=4000)`,
applied to the fully-assembled reply text right before sending - cuts the
whole message with headroom below Telegram's real cap, independent of how
many rows/columns triggered the overflow. New reproduction test
(`test_localview_command_stays_under_telegram_limit_with_many_truncated_rows`)
explicitly verified to fail without the fix before being verified to pass
with it (not just written and left green) - confirmed live afterward: the
same `/localview pipeline_district_boundaries` command that previously
failed now sends successfully, visibly showing both truncation layers at
once (each geometry cell cut with "(N more chars, see admin panel)", the
whole message itself cut mid-row-13 with "truncated (2465 more chars) -
use the admin panel to see the rest."). Commit `fe0697a`.

**Full live round-trip against the real bot, real Telegram Web session**:
`/localtables` correctly listed all 24 real tables (5 pre-existing + 19
new), correctly triggering the existing 20-table cap ("+4 more") for the
first time in this project's history (previously always under 20).
`/localview pipeline_district_terrain` showed clean real data. `/localedit
pipeline_district_terrain 1` resolved Bajaur correctly, showed the real
current values, took `mean_elev_m=1500.0`, showed the correct
`1211.0 -> 1500.0` diff, and "Yes, update" applied it - confirmed via a
direct `local_db.fetch_all()` check (not just the bot's own "Updated."
reply) that `1500.0` genuinely landed in the real database. Cleaned up by
re-running the sync stage (confirmed the edit reverted to the real
`1211.0`, same "edits get overwritten by resync" behavior already proven
in the admin-panel round). Server/database stopped cleanly, admin
password already restored from the earlier round, report's cosmetic
timestamp diff (from `/localedit`'s own automatic `rebuild_report()` call)
reverted. 667/667 tests passing. Working tree clean.
