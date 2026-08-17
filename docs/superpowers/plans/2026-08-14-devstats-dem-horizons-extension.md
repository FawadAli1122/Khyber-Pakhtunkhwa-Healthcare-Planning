# Development Statistics + DEM + Multi-Horizon Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing KP healthcare pipeline with Development Statistics 2024 data, a real Copernicus DEM raster, data-driven terrain scoring, and a 3/5/20-year multi-horizon forecast, all reflected in the shapefiles, QGIS project, and HTML report.

**Architecture:** New numbered scripts (15-21) slot into the existing `scripts/` pipeline after the current stage 14; existing stages 08 (district metrics), 09 (gap scoring), 10 (forecast), 12 (shapefiles), 13 (QGIS project), and 14 (HTML report) are edited in place to consume the new data.

**Tech Stack:** adds `rasterio` (installed, bundles GDAL) for DEM work; reuses `pymupdf`/`pdfplumber` (both installed) for PDF table extraction; no other new dependencies.

## Global Constraints

- No "Development Statistics 2025" exists; use the already-downloaded `data/raw/kp_development_statistics_2024.pdf` (454 pages) as the 2024 edition, and say so explicitly in the report.
- DEM: Copernicus GLO-30, streamed via `/vsicurl/` (no local full-tile downloads), native 30m, clipped to the province polygon — per the user's explicit choice, do not resample coarser.
- Dev Stats scope is healthcare-focused + key context only (health tables in full, roads, ADP health budget line) — do not digitize unrelated sectors.
- Every PDF-table-extraction script must include a cross-validation assertion against a known provincial total from the PDF itself (Table 104's trend row for health data; Table 195's KP-wide total row for roads) — a script that produces numbers with no sanity check against the source document is not acceptable.
- DBF field names stay ≤10 characters (existing constraint, still applies to all new shapefile attributes).
- Forecast horizon years are computed from `date.today()`, not hardcoded as 2029/2031/2046, so the pipeline stays correct if re-run in a later year — but since the design fixed the horizon *lengths* (+3/+5/+20 years) per the user's request, hardcode the offsets (3, 5, 20), not the resulting years.

---

## File Structure

```
scripts/
  15_fetch_dem.py                    # Copernicus DEM mosaic + clip -> gis/KP_DEM.tif
  16_compute_dem_zonal_stats.py      # per-district elevation/slope -> data/processed/district_terrain.csv
  lib/
    pdf_tables.py                     # shared PDF table-location/extraction helpers
  17_extract_devstats_health.py      # Tables 104-113 subset -> data/processed/dev_stats_health.csv
  18_extract_devstats_roads.py       # Table 195 -> data/processed/dev_stats_roads.csv
  19_extract_devstats_budget.py      # Tables 182/183 Health row -> data/processed/dev_stats_budget.json
  08_compute_district_metrics.py     # EDIT: data-driven terrain classification
  09_gap_score_and_clusters.py       # EDIT: continuous terrain_difficulty feature
  10_forecast_demand.py              # EDIT: 3/5/20-year horizons + beds-needed
  20_cross_validate_facility_counts.py  # merged vs Dev Stats govt institutions -> data/processed/facility_cross_validation.csv
  12_write_shapefiles.py             # EDIT: new district/gap-score attributes
  13_build_qgis_project.py           # EDIT: add DEM raster layer
  14_build_html_report.py            # EDIT: new sections, 3-horizon restructure
tests/
  test_pdf_tables.py
  test_dem_zonal.py
  verify_dem.py
  verify_devstats_health.py
  verify_devstats_roads.py
  verify_district_terrain.py
```

---

### Task 1: Fetch and clip Copernicus DEM

**Files:**
- Create: `scripts/15_fetch_dem.py`
- Test: `tests/verify_dem.py`

**Interfaces:**
- Consumes: `data/processed/boundaries.json` (province geometry)
- Produces: `gis/KP_DEM.tif` (EPSG:4326, native ~30m, clipped to KP province polygon, DEFLATE-compressed GeoTIFF)

- [ ] **Step 1: Implement the DEM mosaic + clip script**

```python
# scripts/15_fetch_dem.py
"""Mosaic Copernicus GLO-30 DEM tiles covering Khyber Pakhtunkhwa and clip
to the province polygon at native ~30m resolution. Tiles are read directly
over HTTPS via GDAL's /vsicurl/ virtual filesystem (rasterio bundles GDAL on
Windows) — no full-tile downloads, only the byte ranges needed for the clip
window are fetched. Source: Copernicus DEM GLO-30 (ESA/Sinergise), public,
no authentication, via the AWS Open Data Registry."""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.errors import RasterioIOError
from shapely.geometry import shape, mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"

TILE_URL_TEMPLATE = (
    "/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM.tif"
)

# KP spans ~31.0-36.9N, 69.2-74.1E -> integer tile grid.
LAT_RANGE = range(31, 37)   # 31..36 inclusive
LON_RANGE = range(69, 75)   # 69..74 inclusive


def open_available_tiles():
    datasets = []
    opened_urls = []
    for lat in LAT_RANGE:
        for lon in LON_RANGE:
            url = TILE_URL_TEMPLATE.format(lat=lat, lon=lon)
            try:
                ds = rasterio.open(url)
                datasets.append(ds)
                opened_urls.append(url)
            except RasterioIOError:
                continue  # tile doesn't exist at this coordinate (shouldn't happen inside KP's bbox, but don't hard-fail)
    if not datasets:
        raise RuntimeError("No Copernicus DEM tiles could be opened for KP's bounding box.")
    print(f"Opened {len(datasets)} DEM tiles")
    return datasets


def main():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    province_geom = shape(boundaries["province_geometry"])

    datasets = open_available_tiles()
    mosaic_array, mosaic_transform = merge(datasets, bounds=province_geom.bounds)
    mosaic_meta = datasets[0].meta.copy()
    mosaic_meta.update(
        {
            "height": mosaic_array.shape[1],
            "width": mosaic_array.shape[2],
            "transform": mosaic_transform,
            "compress": "deflate",
        }
    )
    for ds in datasets:
        ds.close()

    GIS_DIR.mkdir(parents=True, exist_ok=True)
    mosaic_path = GIS_DIR / "_dem_mosaic_tmp.tif"
    with rasterio.open(mosaic_path, "w", **mosaic_meta) as dst:
        dst.write(mosaic_array)

    with rasterio.open(mosaic_path) as src:
        clipped_array, clipped_transform = mask(src, [mapping(province_geom)], crop=True, nodata=src.nodata or -9999.0)
        clipped_meta = src.meta.copy()
        clipped_meta.update(
            {
                "height": clipped_array.shape[1],
                "width": clipped_array.shape[2],
                "transform": clipped_transform,
                "compress": "deflate",
                "nodata": src.nodata or -9999.0,
            }
        )

    dem_path = GIS_DIR / "KP_DEM.tif"
    with rasterio.open(dem_path, "w", **clipped_meta) as dst:
        dst.write(clipped_array)

    mosaic_path.unlink()

    valid = clipped_array[clipped_array != clipped_meta["nodata"]]
    print(f"Wrote {dem_path}: {clipped_array.shape[1]}x{clipped_array.shape[2]} px, "
          f"elevation range {valid.min():.0f}-{valid.max():.0f} m")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `python scripts/15_fetch_dem.py`
Expected: prints tile count (~30-36) and `Wrote gis/KP_DEM.tif: ... elevation range ... m` with a plausible KP range (roughly 200m in the southern plains to 7000m+ near Tirich Mir in Chitral). If any tile fails to open, the script continues with the rest — but if the final elevation range looks wrong (e.g. all zeros, or missing the high end near 7000m), investigate before proceeding; don't silently accept a bad mosaic.

- [ ] **Step 3: Write verification**

```python
# tests/verify_dem.py
from pathlib import Path
import numpy as np
import rasterio

DEM_PATH = Path(__file__).resolve().parent.parent / "gis" / "KP_DEM.tif"


def main():
    assert DEM_PATH.exists(), f"Missing {DEM_PATH}"
    with rasterio.open(DEM_PATH) as src:
        assert src.crs.to_epsg() == 4326, f"Expected EPSG:4326, got {src.crs}"
        arr = src.read(1)
        nodata = src.nodata
        valid = arr[arr != nodata] if nodata is not None else arr
        assert valid.size > 0, "DEM has no valid pixels"
        assert 100 < valid.min() < 2000, f"Unexpected min elevation: {valid.min()}"
        assert 3000 < valid.max() < 9000, f"Unexpected max elevation: {valid.max()} (KP's highest peak, Tirich Mir, is ~7690m)"
        # Native ~30m resolution check: pixel size should be close to 30m in degrees (~0.00027778deg)
        px_deg = abs(src.transform.a)
        assert 0.0002 < px_deg < 0.0004, f"Unexpected pixel size: {px_deg} degrees (expected ~30m native)"
    print(f"OK: KP_DEM.tif valid, elevation {valid.min():.0f}-{valid.max():.0f} m, pixel size {px_deg:.6f} deg")


if __name__ == "__main__":
    main()
```

Run: `python tests/verify_dem.py`
Expected: `OK: KP_DEM.tif valid, elevation ... m, pixel size ... deg`

- [ ] **Step 4: Commit**

```bash
git add scripts/15_fetch_dem.py tests/verify_dem.py gis/KP_DEM.tif
git commit -m "feat: fetch and clip Copernicus GLO-30 DEM for KP province

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Per-district DEM zonal statistics

**Files:**
- Create: `scripts/16_compute_dem_zonal_stats.py`
- Test: `tests/test_dem_zonal.py`
- Test: `tests/verify_district_terrain.py`

**Interfaces:**
- Consumes: `gis/KP_DEM.tif`, `data/processed/boundaries.json`
- Produces:
  - `scripts/16_compute_dem_zonal_stats.compute_slope_degrees(elevation: np.ndarray, pixel_size_m: float) -> np.ndarray` (importable/unit-tested)
  - `data/processed/district_terrain.csv` with columns `district,mean_elev_m,min_elev_m,max_elev_m,mean_slope_deg`

- [ ] **Step 1: Write failing test for slope computation**

```python
# tests/test_dem_zonal.py
import importlib
import numpy as np

zonal_mod = importlib.import_module("scripts.16_compute_dem_zonal_stats")


def test_flat_surface_has_zero_slope():
    flat = np.full((10, 10), 500.0)
    slope = zonal_mod.compute_slope_degrees(flat, pixel_size_m=30.0)
    assert np.allclose(slope, 0.0, atol=1e-6)


def test_steep_ramp_has_positive_slope():
    # Elevation increases by 30m per 30m pixel along axis 1 -> ~45 degree slope
    ramp = np.tile(np.arange(10) * 30.0, (10, 1))
    slope = zonal_mod.compute_slope_degrees(ramp, pixel_size_m=30.0)
    interior = slope[2:-2, 2:-2]
    assert np.all(interior > 30), f"Expected steep slope, got {interior}"


def test_slope_shape_matches_input():
    arr = np.random.rand(20, 15) * 1000
    slope = zonal_mod.compute_slope_degrees(arr, pixel_size_m=30.0)
    assert slope.shape == arr.shape
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_dem_zonal.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# scripts/16_compute_dem_zonal_stats.py
"""Per-district elevation and slope statistics from gis/KP_DEM.tif, used to
replace the hand-classified mountainous/plains terrain flag with a
continuous, data-driven terrain-difficulty score (scripts/08 and
scripts/09). Slope is computed with a simple central-difference gradient
(numpy) rather than a dedicated terrain library, since this environment
doesn't have one installed and a first-derivative gradient is sufficient
for a per-district mean-slope summary statistic (not for pixel-level
terrain analysis)."""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask
from shapely.geometry import shape, mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"
DEM_PATH = GIS_DIR / "KP_DEM.tif"

# Degrees-to-meters conversion at KP's approximate latitude, consistent with
# scripts/lib/geo_utils.py's equirectangular projection.
METERS_PER_DEGREE_LAT = 111320.0


def compute_slope_degrees(elevation, pixel_size_m):
    dy, dx = np.gradient(elevation, pixel_size_m)
    slope_rad = np.arctan(np.sqrt(dx ** 2 + dy ** 2))
    return np.degrees(slope_rad)


def main():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())

    with rasterio.open(DEM_PATH) as src:
        pixel_size_deg = abs(src.transform.a)
        pixel_size_m = pixel_size_deg * METERS_PER_DEGREE_LAT
        nodata = src.nodata

        rows = []
        for d in boundaries["districts"]:
            geom = shape(d["geometry"])
            try:
                clipped, _ = mask(src, [mapping(geom)], crop=True, nodata=nodata)
            except ValueError:
                # geometry doesn't overlap the raster at all (shouldn't happen for KP districts)
                rows.append({"district": d["district"], "mean_elev_m": "", "min_elev_m": "", "max_elev_m": "", "mean_slope_deg": ""})
                continue
            band = clipped[0]
            valid = band[band != nodata] if nodata is not None else band
            if valid.size == 0:
                rows.append({"district": d["district"], "mean_elev_m": "", "min_elev_m": "", "max_elev_m": "", "mean_slope_deg": ""})
                continue
            slope = compute_slope_degrees(np.where(band == nodata, np.nan, band), pixel_size_m)
            valid_slope = slope[~np.isnan(slope)]
            rows.append(
                {
                    "district": d["district"],
                    "mean_elev_m": round(float(valid.mean()), 1),
                    "min_elev_m": round(float(valid.min()), 1),
                    "max_elev_m": round(float(valid.max()), 1),
                    "mean_slope_deg": round(float(valid_slope.mean()), 2) if valid_slope.size else "",
                }
            )

    out_path = PROCESSED / "district_terrain.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "mean_elev_m", "min_elev_m", "max_elev_m", "mean_slope_deg"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote district_terrain.csv for {len(rows)} districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dem_zonal.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run for real and verify**

Run: `python scripts/16_compute_dem_zonal_stats.py`

```python
# tests/verify_district_terrain.py
import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "district_terrain.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 25, f"Too few districts: {len(rows)}"
    by_district = {r["district"]: r for r in rows}
    for name in ("district",):
        pass
    missing_elev = [r["district"] for r in rows if r["mean_elev_m"] == ""]
    assert not missing_elev, f"Districts with no elevation data: {missing_elev}"
    # Sanity: a known high-elevation district should be well above a known low-elevation one.
    chitral = by_district.get("Upper Chitral")
    peshawar = by_district.get("Peshawar")
    assert chitral and peshawar, "Expected both Upper Chitral and Peshawar in the terrain data"
    assert float(chitral["mean_elev_m"]) > float(peshawar["mean_elev_m"]), (
        f"Expected Upper Chitral ({chitral['mean_elev_m']}m) to be higher than Peshawar ({peshawar['mean_elev_m']}m)"
    )
    print(f"OK: district_terrain.csv covers {len(rows)} districts; "
          f"Upper Chitral {chitral['mean_elev_m']}m > Peshawar {peshawar['mean_elev_m']}m")


if __name__ == "__main__":
    main()
```

Run: `python tests/verify_district_terrain.py`
Expected: `OK: district_terrain.csv covers 35 districts; Upper Chitral ...m > Peshawar ...m`

- [ ] **Step 6: Commit**

```bash
git add scripts/16_compute_dem_zonal_stats.py tests/test_dem_zonal.py tests/verify_district_terrain.py data/processed/district_terrain.csv
git commit -m "feat: compute per-district elevation and slope from DEM

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: PDF table extraction helpers + Development Statistics health data

**Files:**
- Create: `scripts/lib/pdf_tables.py`
- Create: `scripts/17_extract_devstats_health.py`
- Test: `tests/test_pdf_tables.py`
- Test: `tests/verify_devstats_health.py`

**Interfaces:**
- Consumes: `data/raw/kp_development_statistics_2024.pdf`, `scripts.lib.districts.normalize_district`
- Produces:
  - `scripts.lib.pdf_tables.find_table_pages(doc, table_no: int) -> list[int]` (0-based page indices where `"Table No. {table_no}"` appears, importable/unit-tested)
  - `scripts.lib.pdf_tables.extract_table_rows(doc, page_index: int) -> list[list[str]]` (pdfplumber grid-based extraction, importable/unit-tested)
  - `data/processed/dev_stats_health.csv` with columns:
    `district,govt_institutions,govt_beds,pvt_hospitals,pvt_beds,medical_staff,paramedical_staff,pvt_practitioners,pop_per_bed`

- [ ] **Step 1: Write failing tests for the shared helpers**

```python
# tests/test_pdf_tables.py
import importlib
pdf_tables = importlib.import_module("scripts.lib.pdf_tables")

import fitz


def _make_test_pdf(tmp_path, pages_text):
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    path = str(tmp_path / "test.pdf")
    doc.save(path)
    doc.close()
    return path


def test_find_table_pages_locates_marker(tmp_path):
    path = _make_test_pdf(tmp_path, ["Intro page", "Table No. 105 data here", "Other content", "Table No. 105 repeated"])
    doc = fitz.open(path)
    pages = pdf_tables.find_table_pages(doc, 105)
    assert pages == [1, 3]
    doc.close()


def test_find_table_pages_no_match_returns_empty(tmp_path):
    path = _make_test_pdf(tmp_path, ["No tables here"])
    doc = fitz.open(path)
    pages = pdf_tables.find_table_pages(doc, 999)
    assert pages == []
    doc.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_pdf_tables.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the shared helper module**

```python
# scripts/lib/pdf_tables.py
"""Shared helpers for locating and extracting tables from the Development
Statistics of Khyber Pakhtunkhwa 2024 PDF. Table titles/numbers repeat
across pages (a table spanning many districts continues on the next page
under the same "Table No. N" marker), and the same table number can also
appear 2-3x total for different year-snapshots in some sections — callers
are responsible for picking the right occurrence (see
scripts/17_extract_devstats_health.py for the "use the last occurrence"
convention used throughout this project)."""
import pdfplumber


def find_table_pages(doc, table_no):
    """doc: an open fitz.Document. Returns 0-based page indices whose text
    contains the literal marker "Table No. {table_no}" (case-sensitive,
    matching the PDF's own formatting)."""
    marker = f"Table No. {table_no}"
    pages = []
    for i in range(len(doc)):
        if marker in doc[i].get_text():
            pages.append(i)
    return pages


def extract_table_rows(pdf_path, page_index):
    """Grid-based table extraction via pdfplumber for a single 0-based page
    index. Returns a list of rows, each a list of cell strings (None cells
    become empty strings). Prefer this over raw text extraction for actual
    tabular data since it respects the PDF's line/grid structure."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        table = page.extract_table()
        if table is None:
            return []
        return [[(cell or "").strip() for cell in row] for row in table]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pdf_tables.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Locate exact pages for the remaining health tables**

Run this exploratory one-liner to pin down the page indices for tables not
already located during design (107, 108, 109, 110, 112, 113) before writing
the extraction script:

```bash
python -c "
import fitz
doc = fitz.open('data/raw/kp_development_statistics_2024.pdf')
import sys
sys.path.insert(0, '.')
from scripts.lib.pdf_tables import find_table_pages
for n in [104, 105, 106, 107, 108, 109, 110, 112, 113]:
    print(n, find_table_pages(doc, n))
"
```

Expected: a list of page indices per table number (each printed as
`105 [202, 203, 204, 205, 206, 207, 208, 209, 210]` or similar — tables
that repeat 3x for 2021/2022/2023 will show ~9 pages; single-year tables
will show 1-3). Record the actual output in the implementation — this
plan's Step 6 code below uses placeholder page-index variables
(`GOVT_INST_PAGES`, etc.) that must be filled in with these real indices,
not left as guesses.

- [ ] **Step 6: Implement the health data extraction script**

```python
# scripts/17_extract_devstats_health.py
"""Extract district-level government/private health institution, bed,
staffing, and practitioner counts from Development Statistics of Khyber
Pakhtunkhwa 2024 (data/raw/kp_development_statistics_2024.pdf), tables
104-113. Cross-validates the extracted district sum against Table 104's
own KP-wide provincial total for the same year, so a parsing error shows
up as a failed assertion rather than silently-wrong numbers."""
import csv
import json
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district
from scripts.lib.pdf_tables import find_table_pages, extract_table_rows

PDF_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "kp_development_statistics_2024.pdf"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

# KP-wide provincial totals for the most recent year (2023) from Table 104's
# trend row, used as the cross-validation target for Table 105's district
# sum. Confirmed during design: 2023 row is "192 | 22,266 | 959 | 0 | 126 |
# 1835 | 57 | 0" (institutions, beds, ...). Update these constants if the
# real extraction step (Step 5) finds the 2024 edition's 2023 row differs
# from this design-time reading.
KP_TOTAL_GOVT_INSTITUTIONS_2023 = 192
KP_TOTAL_GOVT_BEDS_2023 = 22266


def parse_int(s):
    s = s.replace(",", "").strip()
    if not s or s == "-":
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def main():
    doc = fitz.open(PDF_PATH)

    # Step 5's exploratory run fills these in with real page indices for
    # this specific PDF; do not leave them as empty lists.
    table_105_pages = find_table_pages(doc, 105)
    assert table_105_pages, "Could not locate Table No. 105 in the PDF"
    # The table repeats once per year covered; take the final third of the
    # page run as the most recent (2023) snapshot.
    third = max(len(table_105_pages) // 3, 1)
    latest_105_pages = table_105_pages[-third:] if len(table_105_pages) >= 3 else table_105_pages

    district_totals = {}  # canonical district name -> {institutions, beds}
    current_district = None
    for page_idx in latest_105_pages:
        rows = extract_table_rows(str(PDF_PATH), page_idx)
        for row in rows:
            if not row or not row[0]:
                continue
            label = row[0].strip()
            # District header rows are followed by tehsil sub-rows; only
            # the district-level row (not indented tehsil rows) carries the
            # canonical district name matching data/processed/boundaries.json.
            candidate = normalize_district(label)
            numeric_cells = [parse_int(c) for c in row[1:] if c]
            if not numeric_cells:
                continue
            if candidate not in district_totals:
                district_totals[candidate] = {"institutions": 0, "beds": 0}
            # Columns 1 and 2 (after the label) are institutions count and
            # bed count for this row per the observed header structure
            # (Nos. | Beds repeated per institution-type group) — the
            # first Nos./Beds pair is the row total.
            if len(numeric_cells) >= 2:
                district_totals[candidate]["institutions"] += numeric_cells[0]
                district_totals[candidate]["beds"] += numeric_cells[1]

    total_institutions = sum(v["institutions"] for v in district_totals.values())
    total_beds = sum(v["beds"] for v in district_totals.values())
    inst_diff_pct = abs(total_institutions - KP_TOTAL_GOVT_INSTITUTIONS_2023) / KP_TOTAL_GOVT_INSTITUTIONS_2023 * 100
    assert inst_diff_pct < 15, (
        f"Extracted institution total ({total_institutions}) differs from Table 104's "
        f"provincial total ({KP_TOTAL_GOVT_INSTITUTIONS_2023}) by {inst_diff_pct:.1f}% - "
        "the column mapping in this script is likely wrong; inspect the raw extracted "
        "rows with extract_table_rows() directly before trusting this output."
    )

    rows_out = []
    for district, vals in sorted(district_totals.items()):
        rows_out.append(
            {
                "district": district,
                "govt_institutions": vals["institutions"],
                "govt_beds": vals["beds"],
                "pvt_hospitals": "",
                "pvt_beds": "",
                "medical_staff": "",
                "paramedical_staff": "",
                "pvt_practitioners": "",
                "pop_per_bed": "",
            }
        )

    out_path = PROCESSED / "dev_stats_health.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["district", "govt_institutions", "govt_beds", "pvt_hospitals", "pvt_beds",
                        "medical_staff", "paramedical_staff", "pvt_practitioners", "pop_per_bed"],
        )
        writer.writeheader()
        writer.writerows(rows_out)
    print(f"Wrote dev_stats_health.csv for {len(rows_out)} districts "
          f"(provincial total {total_institutions} institutions, {total_beds} beds; "
          f"cross-check target {KP_TOTAL_GOVT_INSTITUTIONS_2023}/{KP_TOTAL_GOVT_BEDS_2023})")


if __name__ == "__main__":
    main()
```

**Note for the implementer:** the column-index assumptions in the row-parsing
loop above (`numeric_cells[0]` = institutions, `numeric_cells[1]` = beds) are
a best-effort reading from the design-time exploration in the spec's Section
3 and **must be verified against `extract_table_rows()`'s actual output**
for the real page indices found in Step 5 before trusting the cross-validation
result. If the assertion fails, print the raw rows for one page and fix the
column mapping — this is expected, ordinary iteration on a real-world PDF
table, not a sign the approach is wrong.

- [ ] **Step 7: Run and iterate until the cross-validation assertion passes**

Run: `python scripts/17_extract_devstats_health.py`
Expected: eventually `Wrote dev_stats_health.csv for 35 districts (provincial total ... cross-check target ...)` with the extracted total within 15% of the known provincial total. Debug the column mapping against real `extract_table_rows()` output if it fails — do not loosen the assertion threshold as a way to make a wrong extraction pass.

- [ ] **Step 8: Write and run verification**

```python
# tests/verify_devstats_health.py
import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "dev_stats_health.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 20, f"Too few districts with Dev Stats health data: {len(rows)}"
    for r in rows:
        assert int(r["govt_institutions"]) >= 0
        assert int(r["govt_beds"]) >= 0
    print(f"OK: dev_stats_health.csv covers {len(rows)} districts")


if __name__ == "__main__":
    main()
```

Run: `python tests/verify_devstats_health.py`
Expected: `OK: dev_stats_health.csv covers N districts`

- [ ] **Step 9: Commit**

```bash
git add scripts/lib/pdf_tables.py scripts/17_extract_devstats_health.py tests/test_pdf_tables.py tests/verify_devstats_health.py data/processed/dev_stats_health.csv
git commit -m "feat: extract Development Statistics 2024 health tables with provincial cross-check

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Extend Dev Stats health extraction (private beds, staffing, practitioners)

**Files:**
- Modify: `scripts/17_extract_devstats_health.py`

**Interfaces:**
- Consumes/Produces: same `dev_stats_health.csv`, filling in the columns left blank in Task 3 (`pvt_hospitals`, `pvt_beds`, `medical_staff`, `paramedical_staff`, `pvt_practitioners`, `pop_per_bed`)

- [ ] **Step 1: Locate the remaining tables' pages using the same `find_table_pages` helper** (Table 107 = private hospitals/beds, Table 109 or 110 = staff posted — use whichever the design-time TOC marks as the "actually posted" or most current one, Table 112 = private practitioners, Table 108 = population per bed).

- [ ] **Step 2: Add an extraction function per table following the same pattern as Table 105** (locate → take latest occurrence if repeated → parse rows by matching `normalize_district()` against the row label → sum/assign numeric columns), merging results into the same `district_totals`-style dict keyed by canonical district name before writing the final CSV.

- [ ] **Step 3: Re-run and verify no column is left blank for any district with data in the source table**

Run: `python scripts/17_extract_devstats_health.py`
Expected: same success message as Task 3, now with all 8 data columns populated (some districts may legitimately have 0 private hospitals — that's a valid value, not a missing one; only flag truly blank cells).

- [ ] **Step 4: Update and re-run verification**

Extend `tests/verify_devstats_health.py` to also assert `pvt_beds`, `medical_staff`, etc. parse as non-negative integers (or blank only where the source table has no row for that district).

Run: `python tests/verify_devstats_health.py`
Expected: `OK: ...`

- [ ] **Step 5: Commit**

```bash
git add scripts/17_extract_devstats_health.py tests/verify_devstats_health.py data/processed/dev_stats_health.csv
git commit -m "feat: extend Dev Stats extraction with private beds, staffing, practitioners

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Extract Development Statistics road lengths (Table 195)

**Files:**
- Create: `scripts/18_extract_devstats_roads.py`
- Test: `tests/verify_devstats_roads.py`

**Interfaces:**
- Consumes: `data/raw/kp_development_statistics_2024.pdf`, `scripts.lib.pdf_tables`, `scripts.lib.districts.normalize_district`
- Produces: `data/processed/dev_stats_roads.csv` with columns `district,road_km_total,road_km_high_type,road_km_low_type`

- [ ] **Step 1: Locate Table 195's pages** via `find_table_pages(doc, 195)` (design-time exploration found it starting around PDF page 413/0-based 412; confirm the exact page(s) and whether it repeats for multiple years — Section 3 of the spec notes a 3-year repeat pattern, use the latest).

- [ ] **Step 2: Implement extraction** following the same pattern as Task 3 (`extract_table_rows`, match district labels via `normalize_district`, parse the Total/High-type/Low-type numeric triplet for the latest year), with a cross-validation assertion against the table's own "Khyber Pakhtunkhwa" total row (confirmed at design time: latest-year total ≈ 30,103 km, high-type ≈ 24,197 km, low-type ≈ 5,906 km — update these constants if the real extraction reads different values and document why).

- [ ] **Step 3: Run and verify**

Run: `python scripts/18_extract_devstats_roads.py`
Expected: `Wrote dev_stats_roads.csv for N districts (total ... km, cross-check target ... km)`

```python
# tests/verify_devstats_roads.py
import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "dev_stats_roads.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 20, f"Too few districts with road data: {len(rows)}"
    for r in rows:
        assert float(r["road_km_total"]) >= 0
    print(f"OK: dev_stats_roads.csv covers {len(rows)} districts")


if __name__ == "__main__":
    main()
```

Run: `python tests/verify_devstats_roads.py`
Expected: `OK: dev_stats_roads.csv covers N districts`

- [ ] **Step 4: Commit**

```bash
git add scripts/18_extract_devstats_roads.py tests/verify_devstats_roads.py data/processed/dev_stats_roads.csv
git commit -m "feat: extract Development Statistics district road lengths

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Extract ADP health-sector budget (provincial context)

**Files:**
- Create: `scripts/19_extract_devstats_budget.py`

**Interfaces:**
- Consumes: `data/raw/kp_development_statistics_2024.pdf`
- Produces: `data/processed/dev_stats_budget.json`: `{"fy2023_24": {"kp": ..., "ma": ..., "aip": ..., "total": ...}, "fy2024_25": {...}}` (Health sector row only, in Rs. Million)

- [ ] **Step 1: Implement** — locate Tables 182 and 183 (confirmed at design time: PDF pages 395-396), extract via `extract_table_rows`, find the row whose label matches "Health"/"E&SE" or the specific health-sector line item (confirm the exact sector label used in this table during implementation — Dev Stats ADP tables commonly label it "Health" or "H&Population Welfare"; print all row labels from the extracted table if unsure rather than guessing).

```python
# scripts/19_extract_devstats_budget.py
"""Extract the Health sector's ADP (Annual Development Programme) budget
allocation for FY2023-24 and FY2024-25 from Development Statistics of KP
2024, Tables 182/183 — provincial-level figures used for report narrative
context only (not a per-district shapefile attribute)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.pdf_tables import find_table_pages, extract_table_rows
import fitz

PDF_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "kp_development_statistics_2024.pdf"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"


def parse_amount(s):
    s = s.replace(",", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def extract_health_row(page_idx):
    rows = extract_table_rows(str(PDF_PATH), page_idx)
    for row in rows:
        if row and row[0] and "health" in row[0].strip().lower() and "public" not in row[0].strip().lower():
            nums = [parse_amount(c) for c in row[1:] if c]
            if len(nums) >= 4:
                return {"kp": nums[0], "ma": nums[1], "aip": nums[2], "total": nums[3]}
    return None


def main():
    doc = fitz.open(PDF_PATH)
    pages_182 = find_table_pages(doc, 182)
    pages_183 = find_table_pages(doc, 183)
    assert pages_182 and pages_183, "Could not locate ADP allocation tables 182/183"

    fy2023_24 = extract_health_row(pages_182[0])
    fy2024_25 = extract_health_row(pages_183[0])
    assert fy2023_24 and fy2024_25, (
        "Could not find a Health sector row in Tables 182/183 - print the raw rows "
        "from extract_table_rows() and check the actual sector label used."
    )

    out = {"fy2023_24": fy2023_24, "fy2024_25": fy2024_25}
    (PROCESSED / "dev_stats_budget.json").write_text(json.dumps(out, indent=2))
    print(f"Wrote dev_stats_budget.json: FY23-24 Health total Rs.{fy2023_24['total']}M, "
          f"FY24-25 Rs.{fy2024_25['total']}M")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run and verify manually**

Run: `python scripts/19_extract_devstats_budget.py`
Expected: `Wrote dev_stats_budget.json: FY23-24 Health total Rs....M, FY24-25 Rs....M` with plausible values (Rs. thousands of millions, consistent with the other sector rows seen during design-time exploration, e.g. Agriculture was ~Rs.8,960M in FY23-24).

- [ ] **Step 3: Commit**

```bash
git add scripts/19_extract_devstats_budget.py data/processed/dev_stats_budget.json
git commit -m "feat: extract Health sector ADP budget allocation for report context

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Data-driven terrain classification + continuous gap-score feature

**Files:**
- Modify: `scripts/08_compute_district_metrics.py`
- Modify: `scripts/09_gap_score_and_clusters.py`
- Modify: `tests/test_district_metrics.py`
- Modify: `tests/test_gap_scoring.py`

**Interfaces:**
- Consumes: `data/processed/district_terrain.csv`
- Produces: `district_metrics.csv` gains `mean_elev_m`, `mean_slope_deg`, `terrain_difficulty` (0-1 continuous) columns; `terrain` (mountainous/plains label) is now derived from `terrain_difficulty > 0.5` instead of the hardcoded set; `compute_gap_scores()` uses `terrain_difficulty` directly instead of a 0/1 flag

- [ ] **Step 1: Update `classify_terrain` tests to reflect the new signature**

```python
# tests/test_district_metrics.py -- replace the terrain-related tests with:
def test_terrain_difficulty_scales_0_to_1():
    from scripts.__dict__ import __name__ as _  # noqa - keep import style consistent
    import importlib
    metrics_mod = importlib.import_module("scripts.08_compute_district_metrics")
    rows = [
        {"district": "A", "mean_elev_m": 200, "mean_slope_deg": 1},
        {"district": "B", "mean_elev_m": 4000, "mean_slope_deg": 25},
        {"district": "C", "mean_elev_m": 2000, "mean_slope_deg": 12},
    ]
    scored = metrics_mod.compute_terrain_difficulty(rows)
    by_name = {r["district"]: r["terrain_difficulty"] for r in scored}
    assert by_name["A"] < by_name["C"] < by_name["B"]
    assert all(0 <= v <= 1 for v in by_name.values())


def test_terrain_label_derived_from_difficulty():
    import importlib
    metrics_mod = importlib.import_module("scripts.08_compute_district_metrics")
    assert metrics_mod.terrain_label(0.8) == "mountainous"
    assert metrics_mod.terrain_label(0.2) == "plains"
    assert metrics_mod.terrain_label(0.5) == "plains"  # boundary is exclusive on the mountainous side
```

Remove the old `test_classify_terrain_known_mountainous_district` /
`test_classify_terrain_known_plains_district` /
`test_classify_terrain_unknown_defaults_to_plains` tests — the hardcoded
district-name-based classification they tested no longer exists.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_district_metrics.py -v`
Expected: FAIL (new functions don't exist yet)

- [ ] **Step 3: Implement in `scripts/08_compute_district_metrics.py`**

Replace the `MOUNTAINOUS_DISTRICTS` set and `classify_terrain()` function
with:

```python
def compute_terrain_difficulty(rows):
    """rows: list of dicts with mean_elev_m and mean_slope_deg (numeric).
    Returns the same rows with a terrain_difficulty field added: the mean
    of independently min-max-scaled elevation and slope, in [0,1]."""
    elevs = [float(r["mean_elev_m"]) for r in rows]
    slopes = [float(r["mean_slope_deg"]) for r in rows]
    elev_lo, elev_hi = min(elevs), max(elevs)
    slope_lo, slope_hi = min(slopes), max(slopes)
    out = []
    for r in rows:
        elev_n = (float(r["mean_elev_m"]) - elev_lo) / (elev_hi - elev_lo) if elev_hi > elev_lo else 0.0
        slope_n = (float(r["mean_slope_deg"]) - slope_lo) / (slope_hi - slope_lo) if slope_hi > slope_lo else 0.0
        row = dict(r)
        row["terrain_difficulty"] = round((elev_n + slope_n) / 2, 4)
        out.append(row)
    return out


def terrain_label(terrain_difficulty):
    return "mountainous" if terrain_difficulty > 0.5 else "plains"
```

Update `main()` to load `data/processed/district_terrain.csv`, join it to
the per-district rows by `district`, call `compute_terrain_difficulty()`
across all 35 districts' rows together (so the min-max scaling is relative
to the full province, not per-row), and use `terrain_label()` in place of
the old `classify_terrain(name)` call. Add `mean_elev_m`, `mean_slope_deg`,
`terrain_difficulty` to the output CSV's fieldnames.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_district_metrics.py -v`
Expected: PASS

- [ ] **Step 5: Update `scripts/09_gap_score_and_clusters.py`'s feature matrix**

Replace the `terrain_penalty` computation in `_feature_matrix()`:

```python
# Before: terrain_penalty = np.array([1.0 if r["terrain"] == "mountainous" else 0.0 for r in rows]).reshape(-1, 1)
# After:
terrain_penalty = np.array([float(r["terrain_difficulty"]) for r in rows]).reshape(-1, 1)
```

(No MinMaxScaler needed for this column since `terrain_difficulty` is
already in [0,1] from Task 7 Step 3.)

- [ ] **Step 6: Update `tests/test_gap_scoring.py`'s `make_row` helper** to
include a `terrain_difficulty` field (e.g. `1.0` for the "mountainous"
rows, `0.0` for "plains" rows in the existing test cases) instead of the
string `terrain` field, and update assertions accordingly.

- [ ] **Step 7: Run full test suite and re-run the pipeline stages in order**

Run: `pytest tests/test_district_metrics.py tests/test_gap_scoring.py -v`
Expected: all pass

Run: `python scripts/08_compute_district_metrics.py && python scripts/09_gap_score_and_clusters.py`
Expected: both succeed; spot-check that `district_metrics.csv` now has `terrain_difficulty` values and that known mountainous districts (Upper Chitral, Upper Kohistan) score high.

- [ ] **Step 8: Commit**

```bash
git add scripts/08_compute_district_metrics.py scripts/09_gap_score_and_clusters.py tests/test_district_metrics.py tests/test_gap_scoring.py data/processed/district_metrics.csv
git commit -m "feat: replace hand-classified terrain flag with DEM-derived continuous score

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Multi-horizon forecast (3/5/20-year) + beds-needed projection

**Files:**
- Modify: `scripts/10_forecast_demand.py`
- Modify: `tests/test_forecasting.py`

**Interfaces:**
- Produces: `district_metrics.csv` gains `pop_2029, pop_2031, pop_2046, fac_nd29, fac_nd31, fac_nd46, beds_nd29, beds_nd31, beds_nd46` in place of the old `pop_2030, pop_2035, fac_nd30, fac_nd35`
- Produces: `scripts/10_forecast_demand.beds_needed(population, beds_per_1000=1.0) -> int` (importable/unit-tested; 1.0 beds/1000 is the design's documented simplification — cite this explicitly as not an official MoH standard)

- [ ] **Step 1: Add a failing test for `beds_needed`**

```python
# tests/test_forecasting.py -- add:
def test_beds_needed_uses_beds_per_1000_norm():
    import importlib
    forecast_mod = importlib.import_module("scripts.10_forecast_demand")
    # 250,000 population at 1.0 beds/1000 -> 250 beds
    assert forecast_mod.beds_needed(250000, beds_per_1000=1.0) == 250


def test_beds_needed_zero_population():
    import importlib
    forecast_mod = importlib.import_module("scripts.10_forecast_demand")
    assert forecast_mod.beds_needed(0) == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_forecasting.py -v`
Expected: FAIL (`beds_needed` doesn't exist)

- [ ] **Step 3: Implement**

```python
# Add to scripts/10_forecast_demand.py:
DEFAULT_BEDS_PER_1000 = 1.0  # simplified planning norm, not an official MoH standard - documented in the HTML report

def beds_needed(population, beds_per_1000=DEFAULT_BEDS_PER_1000):
    if population <= 0:
        return 0
    return round(population / 1000 * beds_per_1000)
```

Replace the hardcoded `CENSUS_YEAR = 2023` / `2030` / `2035` targets with:

```python
from datetime import date

HORIZON_OFFSETS = {"29": 3, "31": 5, "46": 20}  # suffixes match the field-name convention (2-digit year)
BASE_YEAR = date.today().year
```

And in `main()`, replace the single pop_2030/pop_2035 block with a loop
over `HORIZON_OFFSETS`, computing `target_year = BASE_YEAR + offset` for
each, projecting population from `pop_2023` at the district's growth rate
for `target_year - CENSUS_YEAR` years (still anchored to the census year
for the growth calculation — only the *target* years change), and writing
`pop_{suffix}`, `fac_nd{suffix}`, `beds_nd{suffix}` (using
`current_beds = int(row.get("govt_beds", 0) or 0)` sourced from the
Dev-Stats-joined data if available, else 0, subtracted from
`beds_needed(pop_at_horizon)` the same way `fac_nd*` subtracts current
facility count). Keep `CENSUS_YEAR = 2023` for the growth-rate math.

- [ ] **Step 4: Run tests, then run for real**

Run: `pytest tests/test_forecasting.py -v`
Expected: PASS

Run: `python scripts/10_forecast_demand.py`
Expected: `Updated district_metrics.csv with {2029,2031,2046} forecasts for 35 districts` (adjust the print message to reflect the new horizon years)

- [ ] **Step 5: Verify monotonicity across horizons**

Run:
```bash
python -c "
import csv
rows = list(csv.DictReader(open('data/processed/district_metrics.csv', encoding='utf-8')))
for r in rows:
    assert int(r['pop_2029']) >= int(r['population_2023']), r
    assert int(r['pop_2031']) >= int(r['pop_2029']), r
    assert int(r['pop_2046']) >= int(r['pop_2031']), r
print('OK: horizons monotonic for', len(rows), 'districts')
"
```
Expected: `OK: horizons monotonic for 35 districts`

- [ ] **Step 6: Commit**

```bash
git add scripts/10_forecast_demand.py tests/test_forecasting.py data/processed/district_metrics.csv
git commit -m "feat: replace 2030/2035 forecast with 3/5/20-year horizons + beds-needed

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Facility count cross-validation (merged vs. Dev Stats official)

**Files:**
- Create: `scripts/20_cross_validate_facility_counts.py`

**Interfaces:**
- Consumes: `data/processed/district_metrics.csv` (`facility_count`), `data/processed/dev_stats_health.csv` (`govt_institutions`)
- Produces: `data/processed/facility_cross_validation.csv`: `district,merged_facility_count,govt_institutions_official,difference,note`

- [ ] **Step 1: Implement**

```python
# scripts/20_cross_validate_facility_counts.py
"""Compare the pipeline's merged KPHCC+OSM facility count per district
against Development Statistics 2024's official government institution
count. These count different things (mine includes private clinics and
pharmacies visible in KPHCC/OSM; Dev Stats counts only government
institutions) so this is not a "should match" reconciliation — it's a
transparency table surfaced in the report explaining where and how much
the two diverge, per district."""
import csv
from pathlib import Path

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"


def main():
    with open(PROCESSED / "district_metrics.csv", newline="", encoding="utf-8") as f:
        merged_counts = {r["district"]: int(r["facility_count"]) for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_health.csv", newline="", encoding="utf-8") as f:
        official_counts = {r["district"]: int(r["govt_institutions"]) for r in csv.DictReader(f) if r["govt_institutions"]}

    rows = []
    for district in sorted(merged_counts):
        merged = merged_counts[district]
        official = official_counts.get(district)
        if official is None:
            rows.append({"district": district, "merged_facility_count": merged, "govt_institutions_official": "", "difference": "", "note": "No Dev Stats entry for this district"})
            continue
        diff = merged - official
        note = "Merged count includes private facilities not in Dev Stats' government-only tally" if diff > 0 else (
            "Dev Stats shows more government institutions than our merged source data captured" if diff < 0 else "Counts match"
        )
        rows.append({"district": district, "merged_facility_count": merged, "govt_institutions_official": official, "difference": diff, "note": note})

    out_path = PROCESSED / "facility_cross_validation.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "merged_facility_count", "govt_institutions_official", "difference", "note"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote facility_cross_validation.csv for {len(rows)} districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run and spot-check**

Run: `python scripts/20_cross_validate_facility_counts.py`
Expected: `Wrote facility_cross_validation.csv for 35 districts`. Manually inspect a few rows to confirm the `note` field reads sensibly (not required to write a formal test for this one — it's a narrative/context CSV, not a modeling input).

- [ ] **Step 3: Commit**

```bash
git add scripts/20_cross_validate_facility_counts.py data/processed/facility_cross_validation.csv
git commit -m "feat: cross-validate merged facility counts against Dev Stats official counts

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: Extend shapefile attributes (districts + gap scores)

**Files:**
- Modify: `scripts/12_write_shapefiles.py`
- Modify: `tests/verify_shapefiles.py` (field-presence checks only, feature counts unchanged)

**Interfaces:**
- `KP_Districts.shp` gains fields: `mean_elev` (F), `mean_slop` (F, note 10-char DBF limit), `terr_diff` (F), `govt_inst` (N), `govt_beds` (N), `pvt_beds` (N), `road_km` (F)
- `KP_District_Gap_Scores.shp` field renames: `pop_2030`→`pop_2029`, `pop_2035`→`pop_2031`, add `pop_2046`, `fac_nd30`→`fac_nd29`, `fac_nd35`→`fac_nd31`, add `fac_nd46`, add `beds_nd29`, `beds_nd31`, `beds_nd46`

- [ ] **Step 1: Update `DISTRICT_FIELDS` and `write_districts()`**

```python
DISTRICT_FIELDS = [
    ("district", "C", 50, 0), ("division", "C", 50, 0), ("area_km2", "F", 12, 2),
    ("pop_2023", "N", 12, 0), ("pop_dens", "F", 10, 2), ("terrain", "C", 20, 0),
    ("mean_elev", "F", 10, 1), ("mean_slop", "F", 8, 2), ("terr_diff", "F", 6, 4),
    ("govt_inst", "N", 6, 0), ("govt_beds", "N", 8, 0), ("pvt_beds", "N", 8, 0),
    ("road_km", "F", 10, 2),
]
```

Load `data/processed/dev_stats_health.csv` and `data/processed/dev_stats_roads.csv`
alongside the existing `district_metrics.csv` join in `write_districts()`,
keyed by `district`, defaulting missing values to `0`.

- [ ] **Step 2: Update `GAP_FIELDS` and `write_gap_scores()`**

```python
GAP_FIELDS = [
    ("district", "C", 50, 0), ("gap_score", "F", 8, 2), ("need_tier", "C", 10, 0),
    ("pop_2029", "N", 12, 0), ("pop_2031", "N", 12, 0), ("pop_2046", "N", 12, 0),
    ("fac_nd29", "N", 6, 0), ("fac_nd31", "N", 6, 0), ("fac_nd46", "N", 6, 0),
    ("beds_nd29", "N", 8, 0), ("beds_nd31", "N", 8, 0), ("beds_nd46", "N", 8, 0),
]
```

Update `write_gap_scores()`'s field access to match the renamed
`district_metrics.csv` columns from Task 8.

- [ ] **Step 3: Run and verify**

Run: `python scripts/12_write_shapefiles.py`
Expected: `Wrote all 6 shapefile layers to gis/` (unchanged message; same 6 layers, more fields)

Run: `python tests/verify_shapefiles.py`
Expected: all 6 `OK: ...` lines as before (feature counts unchanged)

Add a quick field-presence spot check:
```bash
python -c "
import shapefile
r = shapefile.Reader('gis/KP_Districts')
fields = [f[0] for f in r.fields[1:]]
for expected in ('mean_elev', 'govt_inst', 'road_km'):
    assert expected in fields, f'{expected} missing from KP_Districts fields: {fields}'
print('OK: new district fields present')
"
```

- [ ] **Step 4: Commit**

```bash
git add scripts/12_write_shapefiles.py tests/verify_shapefiles.py gis/
git commit -m "feat: add Dev Stats and DEM attributes to district/gap-score shapefiles

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: Add DEM layer to the QGIS project

**Files:**
- Modify: `scripts/13_build_qgis_project.py`
- Modify: `scripts/load_and_style.py`

**Interfaces:**
- `.qgz` gains a raster `maplayer` entry for `KP_DEM.tif` with a singleband pseudocolor renderer (elevation color ramp)

- [ ] **Step 1: Add a raster layer XML builder to `scripts/13_build_qgis_project.py`**

```python
def _raster_layer_xml(layer_id, filename, layer_name):
    return f"""
    <maplayer type="raster">
      <id>{layer_id}</id>
      <datasource>./{filename}</datasource>
      <layername>{layer_name}</layername>
      <provider>gdal</provider>
      <pipe>
        <rasterrenderer type="singlebandpseudocolor" band="1">
          <rastershader>
            <colorrampshader colorRampType="INTERPOLATED">
              <item value="200" color="#1a9850" label="200 m"/>
              <item value="1500" color="#fee08b" label="1500 m"/>
              <item value="3500" color="#d73027" label="3500 m"/>
              <item value="7700" color="#ffffff" label="7700 m"/>
            </colorrampshader>
          </rastershader>
        </rasterrenderer>
      </pipe>
    </maplayer>"""
```

Add `{"id": "dem", "file": "KP_DEM.tif", "name": "Elevation (DEM)"}` to the
`LAYERS` list (placed first in `layer_order_entries` / last in
`layer_tree_entries` so it renders beneath the vector layers), and call
`_raster_layer_xml()` for it in `build_qgs_xml()` instead of one of the
vector-layer builder functions.

- [ ] **Step 2: Run and validate**

Run: `python scripts/13_build_qgis_project.py`
Expected: `Wrote gis/KP_Healthcare_Plan.qgz`

Run the existing `.qgz` structure check, updated to expect 7 layers instead of 6:
```bash
python -c "
import zipfile, xml.etree.ElementTree as ET
with zipfile.ZipFile('gis/KP_Healthcare_Plan.qgz') as zf:
    qgs = [n for n in zf.namelist() if n.endswith('.qgs')][0]
    root = ET.fromstring(zf.read(qgs))
    layer_count = len(root.findall('.//maplayer'))
    assert layer_count == 7, f'Expected 7 maplayer entries (6 vector + 1 raster), got {layer_count}'
print('OK: .qgz contains 7 layers including DEM raster')
"
```

- [ ] **Step 3: Add the DEM layer to the PyQGIS fallback script** (`scripts/load_and_style.py`), using `QgsRasterLayer` + a `QgsSingleBandPseudoColorRenderer` mirroring the same color stops, added via a new `add_raster_layer()` helper analogous to the existing `add_layer()`.

- [ ] **Step 4: Commit**

```bash
git add scripts/13_build_qgis_project.py scripts/load_and_style.py gis/KP_Healthcare_Plan.qgz
git commit -m "feat: add DEM raster layer to QGIS project with elevation color ramp

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12: Rebuild HTML report — new sections + 3/5/20-year restructure

**Files:**
- Modify: `scripts/14_build_html_report.py`

**Interfaces:**
- Consumes: `data/processed/district_terrain.csv`, `data/processed/dev_stats_health.csv`, `data/processed/dev_stats_roads.csv`, `data/processed/dev_stats_budget.json`, `data/processed/facility_cross_validation.csv`, the updated `district_metrics.csv` (new horizon field names)

- [ ] **Step 1: Add a DEM elevation map renderer** following the existing
`render_population_map`/`render_gap_score_map` pattern, but reading pixel
values directly from `gis/KP_DEM.tif` via `rasterio` (imshow with a
terrain-appropriate colormap, e.g. `matplotlib.cm.terrain`), clipped
visually to the province outline already drawn from `boundaries.json`.

- [ ] **Step 2: Add the "Official Infrastructure Context" section** after
"Current State": a table joining `dev_stats_health.csv` and
`dev_stats_roads.csv` per district (govt institutions/beds, private
beds, staff, road km), the `facility_cross_validation.csv` table with its
`note` column rendered as-is, and a short paragraph citing the
`dev_stats_budget.json` Health-sector ADP figures for both fiscal years.
State explicitly that Development Statistics 2024 is KP BOS's latest
edition (no 2025 exists yet).

- [ ] **Step 3: Add the "Terrain & Elevation" section** with the new DEM
map, a paragraph explaining the `terrain_difficulty` methodology (mirrors
the design spec's Section 4 language), and a table of districts sorted by
`mean_elev_m` descending with `mean_slope_deg` alongside.

- [ ] **Step 4: Replace "Future Planning" with three horizon subsections**
(3-year/2029, 5-year/2031, 20-year/2046), each rendering its own table from
`district_metrics.csv`'s `pop_{suffix}`/`fac_nd{suffix}`/`beds_nd{suffix}`
columns for the top-10 gap-score districts, followed by the
horizon-appropriate recommendations prose from the design spec's Section 8
(operational measures for 3-year; infrastructure build-out for 5-year;
systemic transformation for 20-year, explicitly citing the largest
projected 2046 shortfalls).

- [ ] **Step 5: Update the disclaimer** to cite Development Statistics 2024
(KP BOS's latest edition) and Copernicus GLO-30 DEM (ESA/Sinergise via AWS
Open Data) as sources, and to flag the 1.0-beds-per-1000 and
population-per-facility norms as planning simplifications, not official
MoH capacity standards.

- [ ] **Step 6: Rebuild and visually verify**

Run: `python scripts/14_build_html_report.py`
Expected: `Wrote report/KP_Healthcare_Plan.html`

Run the existing self-containment check (image count will now be 4, not 3 — population, gap score, facility distribution, elevation):
```bash
python -c "
from pathlib import Path
html = Path('report/KP_Healthcare_Plan.html').read_text(encoding='utf-8')
assert html.count('data:image/png;base64,') == 4, 'Expected 4 embedded maps (added elevation map)'
assert '3-Year' in html and '5-Year' in html and '20-Year' in html
print('OK: report has 4 maps and all three horizon sections')
"
```

Then open it in a browser (local HTTP server, as in the original
implementation) and visually scroll through the new sections, checking for
CSS collisions, table overflow, and that the DEM map and new tables render
correctly against the existing design tokens (reuse the same `--accent`,
`--tier-color`, table/card styling already established — do not introduce
a new visual language for the new sections).

- [ ] **Step 7: Commit**

```bash
git add scripts/14_build_html_report.py report/KP_Healthcare_Plan.html
git commit -m "feat: add Dev Stats/terrain sections and 3/5/20-year horizon planning to report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 13: Final verification and Artifact republish

**Files:** none new

- [ ] **Step 1: Run the complete verification suite**

```bash
pytest tests/lib tests/test_merge_facilities.py tests/test_district_metrics.py tests/test_gap_scoring.py tests/test_forecasting.py tests/test_suggest_sites.py tests/test_pdf_tables.py tests/test_dem_zonal.py -v
python tests/verify_boundaries.py
python tests/verify_population.py
python tests/verify_kphcc_facilities.py
python tests/verify_merged_facilities.py
python tests/verify_district_metrics.py
python tests/verify_dem.py
python tests/verify_district_terrain.py
python tests/verify_devstats_health.py
python tests/verify_devstats_roads.py
python tests/verify_shapefiles.py
```
Expected: every suite passes, every verify script prints `OK: ...`.

- [ ] **Step 2: Update `scripts/run_all.py`** to include the new stages (15-20) in the correct dependency order (DEM and Dev Stats extraction can run any time after boundaries; terrain/gap-score/forecast must run after them; shapefiles/QGIS/report last).

- [ ] **Step 3: Republish the Artifact**

Publish `report/KP_Healthcare_Plan.html` to the same Artifact URL from the
original implementation (pass the existing `url` so it updates in place
rather than minting a new link).

- [ ] **Step 4: Commit**

```bash
git add scripts/run_all.py
git commit -m "feat: extend pipeline runner with DEM and Dev Stats stages

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Final report to user**

Summarize: what new data was integrated (Dev Stats 2024 tables, DEM), the
new shapefile attributes and QGIS layer, the three horizon years and their
headline projections (province-wide and for the top Critical-tier
districts), the facility-count cross-validation finding, and a reminder
that Dev Stats 2024 (not 2025) and the straight-line accessibility proxy
remain the documented limitations.

---

## Self-Review Notes

- **Spec coverage:** DEM fetch+clip (Task 1), zonal stats (Task 2), Dev
  Stats health tables (Tasks 3-4), roads (Task 5), ADP budget (Task 6),
  data-driven terrain (Task 7), 3/5/20-year horizons + beds (Task 8),
  facility cross-validation (Task 9), shapefile attributes (Task 10), QGIS
  DEM layer (Task 11), report restructure (Task 12), final verification +
  republish (Task 13) — all Section 7/8 spec items have a task.
- **Placeholder scan:** the only "TBD"-shaped items are Task 3/4/5's exact
  PDF page indices for tables not pinned down during design — these are
  handled via a documented `find_table_pages()` exploratory step (Task 3
  Step 5) with real code to run, not a vague "figure this out later", and
  the cross-validation assertions ensure a wrong page/column choice fails
  loudly rather than producing silently-wrong data.
- **Type consistency:** `district_metrics.csv` field names introduced across
  Tasks 7-8 (`terrain_difficulty`, `mean_elev_m`, `mean_slope_deg`,
  `pop_2029`/`pop_2031`/`pop_2046`, `fac_nd29`/`31`/`46`,
  `beds_nd29`/`31`/`46`) are used consistently by Task 9 (cross-validation),
  Task 10 (shapefile field mapping), and Task 12 (report tables) — verified
  no task references the old `terrain` boolean or `pop_2030`/`pop_2035`
  names after Task 7/8 land.
- **Scope:** stays within the user's explicit choices (healthcare-focused
  Dev Stats subset, native-resolution DEM, 3/5/20-year horizons replacing
  2030/2035) — no unrelated sector data, no DEM resampling.
