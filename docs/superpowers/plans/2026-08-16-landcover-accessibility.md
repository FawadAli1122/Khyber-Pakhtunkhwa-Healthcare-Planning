# Land-Cover-Weighted Accessibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route each district's `accessibility_min` (travel time to nearest facility) from a Built-up-land-cover-weighted point instead of the plain geometric centroid, so accessibility is measured from where people actually live.

**Architecture:** `scripts/16b_compute_travel_time_accessibility.py`'s `build_districts_with_centroids()` gains an optional `landcover_path` parameter; when given, it clips `gis/KP_LandCover.tif` per district and uses the mean coordinate of Built-up pixels as the routing origin, falling back to the existing geometric centroid when a district has none. A new `centroid_shift_km` column threads through `district_travel_time.csv` → `district_metrics.csv` → the report. `scripts/09_gap_score_and_clusters.py` (the gap-score formula itself) is untouched.

**Tech Stack:** Python 3.12, `rasterio` (already a dependency), `shapely`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-16-landcover-accessibility-design.md`

## Global Constraints

- `scripts/09_gap_score_and_clusters.py` gets zero changes - `accessibility_min` is consumed exactly as before; only its upstream routing origin changes.
- Built-up class code is `50` (matches `13_build_qgis_project.py`'s `LANDCOVER_CLASSES` and `11_suggest_new_sites.py`'s existing land-cover conventions).
- A district with zero Built-up pixels keeps the existing plain geometric centroid unchanged (`centroid_shift_km = 0.0`, `point_source = "geometric_centroid"`) - not an error, a real and honestly-surfaced case.
- The pixel-averaging logic is a pure function, independently unit-tested without real raster I/O. The real `rasterio.mask.mask()` wrapper around it gets no automated test, matching `15_fetch_dem.py`/`23_fetch_landcover.py`'s own established precedent.
- Flagging threshold for the report callout is a fixed 5km.

---

### Task 1: Built-up-weighted routing origin

**Files:**
- Modify: `scripts/16b_compute_travel_time_accessibility.py`
- Modify: `tests/test_travel_time_accessibility.py`

**Interfaces:**
- Consumes: `gis/KP_LandCover.tif` (existing, from the land-cover integration plan).
- Produces: `_mean_coordinate(landcover_array, transform, target_class=50)`, `build_districts_with_centroids(boundaries, landcover_path=None)` (existing name, new optional parameter - the one existing test that omits it keeps passing unchanged) - now also returns `centroid_shift_km`/`point_source` per district. `district_travel_time.csv` gains `centroid_shift_km`/`point_source` columns - consumed by Task 2.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_travel_time_accessibility.py`:

```python
import numpy as np
from rasterio.transform import Affine


def test_mean_coordinate_returns_none_when_class_absent():
    array = np.array([[10, 10], [20, 20]])
    transform = Affine(0.01, 0, 71.0, 0, -0.01, 34.0)
    assert travel_time_mod._mean_coordinate(array, transform, target_class=50) is None


def test_mean_coordinate_averages_matching_pixel_coordinates():
    # A 2x2 raster where only the top-left pixel (row=0, col=0) is
    # Built-up (50) - its center should be the single returned coordinate.
    array = np.array([[50, 10], [20, 30]])
    transform = Affine(0.01, 0, 71.0, 0, -0.01, 34.0)
    lon, lat = travel_time_mod._mean_coordinate(array, transform, target_class=50)
    assert lon == pytest.approx(71.005)
    assert lat == pytest.approx(33.995)


def test_mean_coordinate_averages_multiple_matching_pixels():
    array = np.array([[50, 50], [20, 30]])
    transform = Affine(0.01, 0, 71.0, 0, -0.01, 34.0)
    lon, lat = travel_time_mod._mean_coordinate(array, transform, target_class=50)
    # Mean of the two top-row pixel centers: (71.005, 33.995) and (71.015, 33.995)
    assert lon == pytest.approx(71.01)
    assert lat == pytest.approx(33.995)


def test_build_districts_with_centroids_falls_back_without_landcover_path():
    # Backward compatibility: the existing call site/test with no
    # landcover_path keeps behaving exactly as before.
    boundaries = {
        "districts": [
            {
                "district": "TestDistrict",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[71.0, 34.0], [71.2, 34.0], [71.2, 34.2], [71.0, 34.2], [71.0, 34.0]]],
                },
            }
        ]
    }
    result = travel_time_mod.build_districts_with_centroids(boundaries)
    assert result[0]["centroid_lon"] == pytest.approx(71.1)
    assert result[0]["centroid_lat"] == pytest.approx(34.1)
    assert result[0]["centroid_shift_km"] == 0.0
    assert result[0]["point_source"] == "geometric_centroid"


def test_build_districts_with_centroids_falls_back_when_landcover_file_missing(tmp_path):
    boundaries = {
        "districts": [
            {
                "district": "TestDistrict",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[71.0, 34.0], [71.2, 34.0], [71.2, 34.2], [71.0, 34.2], [71.0, 34.0]]],
                },
            }
        ]
    }
    result = travel_time_mod.build_districts_with_centroids(boundaries, landcover_path=tmp_path / "does_not_exist.tif")
    assert result[0]["point_source"] == "geometric_centroid"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_travel_time_accessibility.py -v`
Expected: FAIL (`AttributeError: module 'scripts.16b_compute_travel_time_accessibility' has no attribute '_mean_coordinate'`)

- [ ] **Step 3: Implement**

In `scripts/16b_compute_travel_time_accessibility.py`, update the imports:

```python
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask as rasterio_mask
from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib import routing
from scripts.lib.geo_utils import haversine_km
from scripts.lib.terrain import compute_terrain_difficulty

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"

BUILT_UP_CLASS = 50
```

(This adds `numpy`, `rasterio`/`rasterio.mask`, `mapping`, `haversine_km`, `GIS_DIR`, `BUILT_UP_CLASS` to the existing imports - `shape` was already imported, keep it alongside the new `mapping`.)

Replace `build_districts_with_centroids` entirely:

```python
def _mean_coordinate(landcover_array, transform, target_class=BUILT_UP_CLASS):
    """landcover_array: 2D numpy array of land-cover class codes (already
    clipped to a district). Returns (lon, lat) of the mean coordinate of
    all pixels equal to target_class, or None if there are none - pure
    function, independently testable without real raster I/O. See
    docs/superpowers/specs/2026-08-16-landcover-accessibility-design.md."""
    rows, cols = np.where(landcover_array == target_class)
    if len(rows) == 0:
        return None
    xs, ys = rasterio.transform.xy(transform, rows, cols)
    return float(np.mean(xs)), float(np.mean(ys))


def _built_up_weighted_point(geom, landcover_ds):
    """geom: shapely district polygon. landcover_ds: open rasterio dataset
    for KP_LandCover.tif. Returns (lon, lat) or None if the district has
    no Built-up pixels (or the geometry doesn't overlap the raster at
    all)."""
    try:
        clipped, transform = rasterio_mask(landcover_ds, [mapping(geom)], crop=True, nodata=0)
    except ValueError:
        return None  # geometry doesn't overlap the raster at all
    return _mean_coordinate(clipped[0], transform)


def build_districts_with_centroids(boundaries, landcover_path=None):
    """boundaries: parsed boundaries.json dict. landcover_path: optional
    Path to KP_LandCover.tif - when given and it exists, each district's
    routing origin is the mean coordinate of its Built-up land-cover
    pixels (where people actually live) instead of the plain geometric
    centroid, falling back to the geometric centroid for a district with
    no Built-up pixels mapped. Returns {"district", "geometry",
    "centroid_lon", "centroid_lat", "centroid_shift_km", "point_source"}
    per district - the shape scripts.lib.routing.compute_district_accessibility
    expects (plus the two new fields)."""
    landcover_ds = None
    if landcover_path is not None and landcover_path.exists():
        landcover_ds = rasterio.open(landcover_path)

    out = []
    for d in boundaries["districts"]:
        geom = shape(d["geometry"])
        geometric_centroid = geom.centroid

        built_up_point = _built_up_weighted_point(geom, landcover_ds) if landcover_ds is not None else None
        if built_up_point is not None:
            lon, lat = built_up_point
            point_source = "built_up_weighted"
            shift_km = haversine_km(lon, lat, geometric_centroid.x, geometric_centroid.y)
        else:
            lon, lat = geometric_centroid.x, geometric_centroid.y
            point_source = "geometric_centroid"
            shift_km = 0.0

        out.append({
            "district": d["district"],
            "geometry": geom,
            "centroid_lon": lon,
            "centroid_lat": lat,
            "centroid_shift_km": round(shift_km, 2),
            "point_source": point_source,
        })

    if landcover_ds is not None:
        landcover_ds.close()
    return out
```

In `main()`, update the `build_districts_with_centroids(boundaries)` call:

```python
    districts = build_districts_with_centroids(boundaries, landcover_path=GIS_DIR / "KP_LandCover.tif")
```

And update the CSV-writing block to include the two new columns:

```python
    out_path = PROCESSED / "district_travel_time.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "accessibility_min", "centroid_shift_km", "point_source"])
        writer.writeheader()
        for d in districts:
            value = accessibility[d["district"]]
            writer.writerow({
                "district": d["district"],
                "accessibility_min": value if value is not None else "",
                "centroid_shift_km": d["centroid_shift_km"],
                "point_source": d["point_source"],
            })
    print(f"Wrote district_travel_time.csv for {len(districts)} districts")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_travel_time_accessibility.py -v`
Expected: PASS (all tests, existing + new).

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/16b_compute_travel_time_accessibility.py tests/test_travel_time_accessibility.py
git commit -m "feat: route accessibility from a built-up-weighted point instead of the geometric centroid

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Thread `centroid_shift_km` into `district_metrics.csv`

**Files:**
- Modify: `scripts/08_compute_district_metrics.py`

**Interfaces:**
- Consumes: `district_travel_time.csv`'s new `centroid_shift_km` column (Task 1).
- Produces: `district_metrics.csv` gains a `centroid_shift_km` column - consumed by Task 3.

No new automated test (matches this file's own established precedent - it has no dedicated pytest file today, only the standalone `tests/verify_district_metrics.py` script; this is a small, direct CSV-merge addition following the exact same pattern already used for `accessibility_min` two lines above it).

- [ ] **Step 1: Implement**

In `scripts/08_compute_district_metrics.py`, right after the existing:

```python
        travel_row = travel_time.get(name)
        accessibility_min = (
            float(travel_row["accessibility_min"])
            if travel_row and travel_row["accessibility_min"] != ""
            else None
        )
```

add:

```python
        centroid_shift_km = (
            float(travel_row["centroid_shift_km"])
            if travel_row and travel_row.get("centroid_shift_km") not in (None, "")
            else 0.0
        )
```

And in the row dict being built, right after the existing `"accessibility_min": accessibility_min if accessibility_min is not None else "",` line, add:

```python
                "centroid_shift_km": centroid_shift_km,
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add scripts/08_compute_district_metrics.py
git commit -m "feat: merge centroid_shift_km into district_metrics.csv

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Report methodology + shift callout

**Files:**
- Modify: `scripts/14_build_html_report.py`
- Create: `tests/test_landcover_shift_callout.py`

**Interfaces:**
- Consumes: `district_metrics.csv`'s `centroid_shift_km` column (Task 2), already loaded into `metrics` in `build()`.
- Produces: `landcover_shift_callout_html(metrics)` - consumed nowhere else, a leaf report-rendering function.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_landcover_shift_callout.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_landcover_shift_callout.py -v`
Expected: FAIL (`AttributeError: module 'scripts.14_build_html_report' has no attribute 'landcover_shift_callout_html'`)

- [ ] **Step 3: Implement**

Add a new function to `scripts/14_build_html_report.py`, anywhere at module level (e.g. right above `methodology_html`):

```python
LANDCOVER_SHIFT_THRESHOLD_KM = 5.0


def landcover_shift_callout_html(metrics):
    shifted = [
        m for m in metrics
        if float(m.get("centroid_shift_km") or 0) > LANDCOVER_SHIFT_THRESHOLD_KM
    ]
    if not shifted:
        return ""
    shifted.sort(key=lambda m: float(m["centroid_shift_km"]), reverse=True)
    items = ", ".join(f"{m['district']} ({float(m['centroid_shift_km']):.1f} km)" for m in shifted)
    return (
        f"<p>In {len(shifted)} district(s), the built-up-weighted routing point used for accessibility "
        f"measurably differs from the district's plain geometric center - population is clustered away "
        f"from the shape's middle, not evenly distributed: {items}.</p>"
    )
```

Update the existing Methodology bullet list. Find the existing `<strong>Gap score (0&ndash;100):</strong>` bullet (ends with `...distinct dimensions of access, not restatements of the same thing.</li>`) and add a new bullet immediately after it, still inside the same `<ul>`:

```html
  <li><strong>Accessibility routing point:</strong> <code>accessibility_min</code> is routed from each
  district's Built-up-land-cover-weighted point (the mean location of ESA WorldCover 2021 Built-up pixels
  within the district, <code>gis/KP_LandCover.tif</code>) rather than the district polygon's plain geometric
  centroid, so travel time is measured from where people actually live, not the shape's mathematical middle.
  A district with no mapped Built-up pixels keeps the plain geometric centroid unchanged.</li>
```

Find the existing:

```python
{methodology_html()}<section id="current-state">
```

and replace with:

```python
{methodology_html()}{landcover_shift_callout_html(metrics)}<section id="current-state">
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_landcover_shift_callout.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/14_build_html_report.py tests/test_landcover_shift_callout.py
git commit -m "feat: document built-up-weighted accessibility routing in the report methodology

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Full pipeline rerun and live verification

**Files:** none (verification only).

This changes a real input to the gap score for the first time via this mechanism, so it needs verification against the real pipeline, not just mocks - confirming real district rankings shift for a defensible, explainable reason, not due to a bug.

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 2: Re-run the accessibility stage and inspect real shift values**

Run: `python scripts/16b_compute_travel_time_accessibility.py`, then `cat data/processed/district_travel_time.csv`. Confirm every district has a `point_source` of either `built_up_weighted` or `geometric_centroid` (never blank), and that `centroid_shift_km` values look plausible (mostly small, a handful possibly larger for districts with oddly-shaped polygons or offset population centers - not implausibly huge, e.g. not larger than the district's own diameter).

- [ ] **Step 3: Re-run downstream stages and inspect the real gap-score/tier changes**

Run: `python scripts/08_compute_district_metrics.py && python scripts/09_gap_score_and_clusters.py`. Compare `data/processed/district_metrics.csv`'s `gap_score`/`need_tier` columns against the version already committed (`git diff data/processed/district_metrics.csv` if this file is tracked, or the report's own prior "Critical/High/Moderate/Low" tier list). Confirm any tier changes are plausible given the real `centroid_shift_km` values found in Step 2 - a district with a large shift changing tier is expected and correct; a district with `centroid_shift_km` near 0 changing tier would be suspicious and worth investigating before proceeding.

- [ ] **Step 4: Rebuild the rest of the pipeline and the report**

Run: `python scripts/10_forecast_demand.py && python scripts/11_suggest_new_sites.py && python scripts/20_cross_validate_facility_counts.py && python scripts/12_write_shapefiles.py && python scripts/13_build_qgis_project.py && python scripts/14_build_html_report.py` (the bundled local database must be running first - `python -c "from scripts.lib import local_db; local_db.ensure_running()"` - since the report build reads from it; matches this project's own established requirement). Open `report/KP_Healthcare_Plan.html` (or grep it) and confirm the new methodology bullet and (if any district exceeded the 5km threshold) the shift callout paragraph both render with real numbers, not placeholder text.

- [ ] **Step 5: Confirm no unexpected diff elsewhere**

Run `git status`/`git diff`. `data/processed/district_metrics.csv`, `data/processed/district_travel_time.csv`, `gis/KP_District_Gap_Scores.*`, `gis/KP_Suggested_New_Sites.*`, and the report are all expected to genuinely change (real new data) - review for plausibility. `gis/KP_Healthcare_Plan.qgz`'s own random-layer-id non-determinism is already a known, harmless pattern - `git checkout` it back if nothing else in it changed. Stop the bundled local database afterward (`python -c "from scripts.lib import local_db; local_db.stop()"`).

- [ ] **Step 6: Report findings**

If everything above checks out clean and any tier changes are explainable by real `centroid_shift_km` values, this task (and the whole plan) is done. If anything looks wrong (implausible shift distances, a tier change with no corresponding real shift, a crash), that's a real bug to fix with its own test (where automatable) before considering this complete.
