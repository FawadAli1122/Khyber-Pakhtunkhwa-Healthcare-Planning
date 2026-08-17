# Land Use / Land Cover Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ESA WorldCover 2021 land use/land cover data for Khyber Pakhtunkhwa to the GIS pipeline - a new paletted QGIS layer, plus a filter that keeps ML-suggested new-facility sites out of physically-unbuildable terrain (water/snow/wetland).

**Architecture:** A new fetch stage (`scripts/23_fetch_landcover.py`) mirrors `scripts/15_fetch_dem.py`'s exact pattern (mosaic public AWS-hosted COG tiles via `/vsicurl/`, clip to the province polygon) to produce `gis/KP_LandCover.tif`. Two consumers: `scripts/13_build_qgis_project.py` (new paletted raster layer, ESA's official 11-class colors) and `scripts/11_suggest_new_sites.py` (a pure, independently-testable adjustment function that nudges a KMeans-derived candidate site to a real nearby settlement when its raw centroid lands in water/snow/wetland).

**Tech Stack:** Python 3.12, `rasterio` (already a dependency), GDAL's `/vsicurl/` virtual filesystem, `shapely`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-16-landcover-integration-design.md`

## Global Constraints

- ESA WorldCover 2021 v200, 10m resolution, public/no-auth on AWS S3 (`s3://esa-worldcover`, `eu-central-1`).
- Excluded land-cover classes for site suggestion are exactly `{70: "Snow and ice", 80: "Permanent water bodies", 90: "Herbaceous wetland"}` - every other class (including Tree cover, Cropland, Built-up) stays allowed; sub-optimal terrain is never treated as disqualifying.
- `gis/KP_LandCover.tif` and its temp mosaic file are gitignored, matching `gis/KP_DEM.tif`'s exact existing precedent (regenerable, too large for a normal git repo).
- No gap-score/district-need-tier changes; no new HTML report section; no per-district land-cover composition statistics - explicitly out of scope for this pass.
- The fetch stage and QGIS layer-styling code get no automated tests, matching `15_fetch_dem.py`'s and `13_build_qgis_project.py`'s own established precedent (external raster I/O / hand-authored XML styling, verified manually against the real pipeline). The site-suggestion adjustment logic is a pure function and *does* get real unit tests.

---

### Task 1: `scripts/23_fetch_landcover.py` - fetch, mosaic, clip

**Files:**
- Create: `scripts/23_fetch_landcover.py`
- Modify: `.gitignore`
- Modify: `scripts/run_all.py`

**Interfaces:**
- Consumes: `data/processed/boundaries.json` (existing, from `01_fetch_boundaries.py`).
- Produces: `gis/KP_LandCover.tif` - consumed by Task 2 and Task 3.

No automated tests (matches `15_fetch_dem.py`'s own established precedent for this exact kind of external-raster-I/O script). Verified manually in Task 4.

- [ ] **Step 1: Implement**

Create `scripts/23_fetch_landcover.py`:

```python
"""Mosaic ESA WorldCover 2021 (v200) land-cover tiles covering Khyber
Pakhtunkhwa and clip to the province polygon at native 10m resolution.
Tiles are read directly over HTTPS via GDAL's /vsicurl/ virtual
filesystem (rasterio bundles GDAL on Windows) - no full-tile downloads,
only the byte ranges needed for the clip window are fetched. Source: ESA
WorldCover 2021 v200 (ESA/VITO), public, no authentication, via the AWS
Open Data Registry. Mirrors scripts/15_fetch_dem.py's exact pattern -
see that script's own docstring for why this technique is used. See
docs/superpowers/specs/2026-08-16-landcover-integration-design.md.

Pixel values are discrete land-cover class codes (10=Tree cover,
20=Shrubland, 30=Grassland, 40=Cropland, 50=Built-up, 60=Bare/sparse
vegetation, 70=Snow and ice, 80=Permanent water bodies, 90=Herbaceous
wetland, 95=Mangroves, 100=Moss and lichen), not a continuous field like
the DEM - nodata is 0 (ESA's own convention; no valid class code is 0).
"""
import json
import sys
from pathlib import Path

import rasterio
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.errors import RasterioIOError
from shapely.geometry import shape, mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"

TILE_URL_TEMPLATE = (
    "/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
    "v200/2021/map/ESA_WorldCover_10m_2021_v200_N{lat:02d}E{lon:03d}_Map.tif"
)

# KP spans ~31.0-36.9N, 69.2-74.1E -> ESA WorldCover's 3-degree tile grid,
# named by each tile's south-west corner. Verified reachable (HTTP 200)
# for all 6 combinations below at spec-writing time.
LAT_RANGE = (30, 33, 36)
LON_RANGE = (69, 72)


def open_available_tiles():
    datasets = []
    for lat in LAT_RANGE:
        for lon in LON_RANGE:
            url = TILE_URL_TEMPLATE.format(lat=lat, lon=lon)
            try:
                ds = rasterio.open(url)
                datasets.append(ds)
            except RasterioIOError:
                continue  # tile doesn't exist at this coordinate (shouldn't happen inside KP's bbox, but don't hard-fail)
    if not datasets:
        raise RuntimeError("No ESA WorldCover tiles could be opened for KP's bounding box.")
    print(f"Opened {len(datasets)} land-cover tiles")
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
    mosaic_path = GIS_DIR / "_landcover_mosaic_tmp.tif"
    with rasterio.open(mosaic_path, "w", **mosaic_meta) as dst:
        dst.write(mosaic_array)

    with rasterio.open(mosaic_path) as src:
        nodata = 0
        clipped_array, clipped_transform = mask(src, [mapping(province_geom)], crop=True, nodata=nodata)
        clipped_meta = src.meta.copy()
        clipped_meta.update(
            {
                "height": clipped_array.shape[1],
                "width": clipped_array.shape[2],
                "transform": clipped_transform,
                "compress": "deflate",
                "nodata": nodata,
            }
        )

    landcover_path = GIS_DIR / "KP_LandCover.tif"
    with rasterio.open(landcover_path, "w", **clipped_meta) as dst:
        dst.write(clipped_array)

    mosaic_path.unlink()

    valid = clipped_array[clipped_array != nodata]
    classes = sorted(set(valid.tolist())) if valid.size else []
    print(f"Wrote {landcover_path}: {clipped_array.shape[1]}x{clipped_array.shape[2]} px, "
          f"classes present: {classes}")


if __name__ == "__main__":
    main()
```

Add to `.gitignore`, following the existing `gis/KP_DEM.tif` block's exact pattern:

```gitignore

# Same reasoning as gis/KP_DEM.tif above - too large for a normal git repo,
# regenerate with:
#   python scripts/23_fetch_landcover.py
gis/KP_LandCover.tif
gis/KP_LandCover.tif.aux.xml
gis/_landcover_mosaic_tmp.tif
```

In `scripts/run_all.py`, add `"23_fetch_landcover.py",` to `STAGES` immediately after the existing `"16_compute_dem_zonal_stats.py",` line (both are one-time raster fetches needing only `boundaries.json`; this places it well before both `11_suggest_new_sites.py` and `13_build_qgis_project.py`, which need `KP_LandCover.tif`):

```python
    "16_compute_dem_zonal_stats.py",          # needs KP_DEM.tif (15) + boundaries.json
    "23_fetch_landcover.py",                  # needs boundaries.json (01) - independent of 15/16, grouped here since both are one-time raster fetches
    "16b_compute_travel_time_accessibility.py",  # needs 06 (roads) + 07 (facilities) + 16 (terrain) + boundaries.json
```

- [ ] **Step 2: Commit**

```bash
git add scripts/23_fetch_landcover.py scripts/run_all.py .gitignore
git commit -m "feat: add ESA WorldCover land-cover fetch stage

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: QGIS paletted land-cover layer

**Files:**
- Modify: `scripts/13_build_qgis_project.py`

**Interfaces:**
- Consumes: `gis/KP_LandCover.tif` (Task 1).
- Produces: nothing new for later tasks - this is a leaf consumer.

No automated tests (matches this file's own established precedent - no existing test file covers any of its layer-styling code either). Verified manually in Task 4.

- [ ] **Step 1: Implement**

In `scripts/13_build_qgis_project.py`, add a new module-level constant right after the existing `LAYERS` list:

```python
LANDCOVER_CLASSES = [
    (10, "#006400", "Tree cover"),
    (20, "#ffbb22", "Shrubland"),
    (30, "#ffff4c", "Grassland"),
    (40, "#f096ff", "Cropland"),
    (50, "#fa0000", "Built-up"),
    (60, "#b4b4b4", "Bare / sparse vegetation"),
    (70, "#f0f0f0", "Snow and ice"),
    (80, "#0064c8", "Permanent water bodies"),
    (90, "#0096a0", "Herbaceous wetland"),
    (95, "#00cf75", "Mangroves"),
    (100, "#fae6a0", "Moss and lichen"),
]
```

Add `{"id": "landcover", "file": "KP_LandCover.tif", "name": "Land Cover (ESA WorldCover 2021)", "geom": "Raster"},` to `LAYERS`, right after the existing `"dem"` entry:

```python
LAYERS = [
    {"id": "dem", "file": "KP_DEM.tif", "name": "Elevation (DEM)", "geom": "Raster"},
    {"id": "landcover", "file": "KP_LandCover.tif", "name": "Land Cover (ESA WorldCover 2021)", "geom": "Raster"},
    {"id": "province", "file": "KP_Province_Boundary.shp", "name": "KP Province Boundary", "geom": "Polygon"},
    ...
```

Add a new function right after the existing `_raster_layer_xml`:

```python
def _paletted_raster_layer_xml(layer_id, filename, layer_name, class_colors):
    """class_colors: list of (value, color, label) tuples for a
    singlebandpseudocolor renderer with an EXACT (not interpolated) color
    ramp - correct for discrete class-code rasters like land cover,
    unlike the DEM's continuous elevation gradient above."""
    items = "\n".join(
        f'              <item value="{value}" color="{color}" label="{label}"/>'
        for value, color, label in class_colors
    )
    return f"""
    <maplayer type="raster">
      <id>{layer_id}</id>
      <datasource>./{filename}</datasource>
      <layername>{layer_name}</layername>
      {SRS_XML}
      <provider>gdal</provider>
      <pipe>
        <rasterrenderer type="singlebandpseudocolor" band="1">
          <rastershader>
            <colorrampshader colorRampType="EXACT">
{items}
            </colorrampshader>
          </rastershader>
        </rasterrenderer>
      </pipe>
    </maplayer>"""
```

In `build_qgs_xml()`, add the new layer to `layers_xml`, right after the existing `dem` entry:

```python
    layers_xml = [
        _raster_layer_xml(ids["dem"], by_id["dem"]["file"], by_id["dem"]["name"]),
        _paletted_raster_layer_xml(
            ids["landcover"], by_id["landcover"]["file"], by_id["landcover"]["name"], LANDCOVER_CLASSES,
        ),
        _simple_polygon_layer_xml(by_id["province"], ids["province"], color="255,255,255,0"),
        ...
```

- [ ] **Step 2: Commit**

```bash
git add scripts/13_build_qgis_project.py
git commit -m "feat: add paletted land-cover layer to the QGIS project

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Land-cover-aware site-suggestion filter

**Files:**
- Modify: `scripts/11_suggest_new_sites.py`
- Modify: `tests/test_suggest_sites.py`

**Interfaces:**
- Consumes: `gis/KP_LandCover.tif` (Task 1), via a new `_make_landcover_sampler()`.
- Produces: `EXCLUDED_LANDCOVER_CLASSES`, `_adjust_for_landcover(lon, lat, cluster_idx, labels, settlements, sample_landcover)`, `pick_candidate_sites(settlements, existing_facilities, n_sites, sample_landcover=None)` (existing name, new optional keyword parameter - every existing call site/test keeps working unchanged when it's omitted).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_suggest_sites.py`:

```python
def test_adjust_for_landcover_leaves_valid_centroid_unchanged():
    settlements = [{"lat": 34.0, "lon": 71.0, "population": 500}]
    labels = [0]
    lon, lat, note = sites_mod._adjust_for_landcover(
        71.5, 34.5, 0, labels, settlements, sample_landcover=lambda lon, lat: 40  # Cropland - allowed
    )
    assert (lon, lat, note) == (71.5, 34.5, None)


def test_adjust_for_landcover_falls_back_to_highest_population_settlement():
    settlements = [
        {"lat": 34.0, "lon": 71.0, "population": 500},
        {"lat": 34.1, "lon": 71.1, "population": 5000},
    ]
    labels = [0, 0]

    def sample_landcover(lon, lat):
        if (lon, lat) == (71.5, 34.5):
            return 80  # Permanent water bodies - excluded
        return 40  # Cropland - allowed for both real settlements

    lon, lat, note = sites_mod._adjust_for_landcover(71.5, 34.5, 0, labels, settlements, sample_landcover)
    assert (lon, lat) == (71.1, 34.1)  # the higher-population settlement
    assert "Permanent water bodies" in note


def test_adjust_for_landcover_skips_settlements_that_are_also_excluded():
    settlements = [
        {"lat": 34.0, "lon": 71.0, "population": 5000},  # higher population but also excluded
        {"lat": 34.1, "lon": 71.1, "population": 500},   # lower population but allowed
    ]
    labels = [0, 0]

    def sample_landcover(lon, lat):
        if (lon, lat) == (71.5, 34.5):
            return 70  # Snow and ice - excluded
        if (lon, lat) == (71.0, 34.0):
            return 90  # Herbaceous wetland - also excluded
        return 40  # Cropland - allowed

    lon, lat, note = sites_mod._adjust_for_landcover(71.5, 34.5, 0, labels, settlements, sample_landcover)
    assert (lon, lat) == (71.1, 34.1)  # skipped the excluded one, used the allowed one
    assert "Snow and ice" in note


def test_adjust_for_landcover_keeps_centroid_when_all_settlements_excluded():
    settlements = [{"lat": 34.0, "lon": 71.0, "population": 500}]
    labels = [0]

    def sample_landcover(lon, lat):
        return 80  # Permanent water bodies - excluded everywhere in this fixture

    lon, lat, note = sites_mod._adjust_for_landcover(71.5, 34.5, 0, labels, settlements, sample_landcover)
    assert (lon, lat) == (71.5, 34.5)  # kept the original centroid
    assert "manual site verification" in note


def test_pick_candidate_sites_without_sample_landcover_is_unchanged():
    # Backward compatibility: every pre-existing caller/test omits
    # sample_landcover entirely.
    settlements = [{"lat": 34.0 + i * 0.01, "lon": 71.0 + i * 0.01, "population": 100} for i in range(20)]
    sites = sites_mod.pick_candidate_sites(settlements, [], n_sites=3)
    assert len(sites) == 3


def test_pick_candidate_sites_applies_landcover_adjustment():
    settlements = [
        {"lat": 34.00, "lon": 71.00, "population": 500},
        {"lat": 34.001, "lon": 71.001, "population": 500},
    ]

    def sample_landcover(lon, lat):
        return 80  # Permanent water bodies - excluded, forces a fallback to a real settlement

    sites = sites_mod.pick_candidate_sites(settlements, [], n_sites=1, sample_landcover=sample_landcover)
    assert len(sites) == 1
    assert "Permanent water bodies" in sites[0]["rationale"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_suggest_sites.py -v`
Expected: FAIL (`AttributeError: module 'scripts.11_suggest_new_sites' has no attribute '_adjust_for_landcover'`)

- [ ] **Step 3: Implement**

Add near the top of `scripts/11_suggest_new_sites.py`, alongside the existing constants:

```python
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"

EXCLUDED_LANDCOVER_CLASSES = {
    70: "Snow and ice",
    80: "Permanent water bodies",
    90: "Herbaceous wetland",
}
```

Add a new function, right before `pick_candidate_sites`:

```python
def _adjust_for_landcover(lon, lat, cluster_idx, labels, settlements, sample_landcover):
    """Returns (lon, lat, adjustment_note). adjustment_note is None when
    the original centroid's land cover is fine as-is. A KMeans cluster
    centroid is a synthetic geometric point - it can land in a river or
    snowfield even though the real settlements feeding that cluster are
    legitimate, inhabited (and therefore buildable) locations, so this
    falls back to the highest-population real settlement in the same
    cluster whose own land cover is allowed, rather than searching for
    the nearest valid raster pixel with no settlement backing it."""
    excluded_label = EXCLUDED_LANDCOVER_CLASSES.get(sample_landcover(lon, lat))
    if excluded_label is None:
        return lon, lat, None

    cluster_settlements = sorted(
        (s for s, label in zip(settlements, labels) if label == cluster_idx),
        key=lambda s: s.get("population", 1), reverse=True,
    )
    for s in cluster_settlements:
        if sample_landcover(s["lon"], s["lat"]) not in EXCLUDED_LANDCOVER_CLASSES:
            return s["lon"], s["lat"], f"adjusted from a nearby cluster centroid falling in {excluded_label}"

    return lon, lat, (
        f"cluster centroid falls in {excluded_label} and no member settlement offered a clear "
        "alternative - manual site verification recommended"
    )


def _make_landcover_sampler():
    landcover_path = GIS_DIR / "KP_LandCover.tif"
    if not landcover_path.exists():
        return None
    import rasterio
    ds = rasterio.open(landcover_path)

    def sample(lon, lat):
        try:
            row, col = ds.index(lon, lat)
            value = ds.read(1, window=((row, row + 1), (col, col + 1)))[0, 0]
        except IndexError:
            return None  # outside the raster's extent - treat as unknown, never excluded
        return int(value)

    return sample
```

Replace `pick_candidate_sites`'s signature and cluster-scoring loop:

```python
def pick_candidate_sites(settlements, existing_facilities, n_sites, sample_landcover=None):
    if not settlements:
        return []
    n_sites = min(n_sites, len(settlements))
    coords = np.array([[s["lon"], s["lat"]] for s in settlements])
    weights = np.array([max(s.get("population", 1), 1) for s in settlements])

    km = KMeans(n_clusters=n_sites, n_init=10, random_state=42)
    km.fit(coords, sample_weight=weights)
    centers = km.cluster_centers_
    labels = km.labels_

    scored_centers = []
    for cluster_idx, (lon, lat) in enumerate(centers):
        adjustment_note = None
        if sample_landcover is not None:
            lon, lat, adjustment_note = _adjust_for_landcover(
                lon, lat, cluster_idx, labels, settlements, sample_landcover
            )
        if existing_facilities:
            nearest_km = min(haversine_km(lon, lat, f["lon"], f["lat"]) for f in existing_facilities)
        else:
            nearest_km = float("inf")
        scored_centers.append((nearest_km, lon, lat, adjustment_note))

    scored_centers.sort(key=lambda t: t[0], reverse=True)  # farthest-from-care first
    results = []
    for nearest_km, lon, lat, adjustment_note in scored_centers:
        base = "Nearby settlement" if adjustment_note else "Population-weighted settlement cluster centroid"
        if nearest_km == float("inf"):
            rationale = f"{base}; no existing facility currently mapped in this district"
        else:
            rationale = f"{base}, ~{nearest_km:.1f} km from nearest existing facility"
        if adjustment_note:
            rationale += f" ({adjustment_note})"
        results.append({"lat": round(lat, 5), "lon": round(lon, 5), "rationale": rationale})
    return results
```

(This replaces the existing function body entirely - the old version's `for lon, lat in centers:` loop and its trailing list-comprehension return are both superseded by the version above.)

In `main()`, add right after the existing `boundaries = json.loads(...)` line:

```python
    sample_landcover = _make_landcover_sampler()
```

And update the `pick_candidate_sites(...)` call inside the `for priority, row in enumerate(...)` loop to pass it through:

```python
        sites = pick_candidate_sites(
            district_settlements, district_facilities, SITES_PER_DISTRICT, sample_landcover=sample_landcover,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_suggest_sites.py -v`
Expected: PASS (all tests, existing + new).

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/11_suggest_new_sites.py tests/test_suggest_sites.py
git commit -m "feat: adjust ML-suggested sites away from unbuildable land cover

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Full test suite and live manual verification

**Files:** none (verification only).

This feature fetches a real external raster dataset for the first time via this specific tile grid/URL scheme, so per this project's established cadence it needs verification against the real pipeline - not just mocks.

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 2: Run the fetch stage for real**

Run: `python scripts/23_fetch_landcover.py`
Expected: succeeds, prints a class list that looks sane for KP (expect at least Cropland (40), Built-up (50), Bare/sparse vegetation (60), Tree cover (10); Snow and ice (70) plausible given KP's high-elevation districts). Confirm `gis/KP_LandCover.tif` exists.

- [ ] **Step 3: Check the output file size**

Run: `ls -la gis/KP_LandCover.tif` (or `Get-Item` in PowerShell). If it's anywhere near or over ~100MB, this is expected (10m resolution is much finer than the DEM's ~30m) and already handled - `.gitignore` already excludes it (Task 1) - but note the actual size for the record; no action needed unless something looks wildly larger than land-cover's typical high compressibility would suggest (a multi-GB file would indicate a real bug, e.g. the clip not actually cropping to the province polygon).

- [ ] **Step 4: Confirm a known real coordinate resolves correctly**

Run this inline script:

```bash
python -c "
import rasterio
ds = rasterio.open('gis/KP_LandCover.tif')
# Tarbela Dam reservoir, a real, well-known large water body in KP.
row, col = ds.index(72.70, 34.09)
value = ds.read(1, window=((row, row+1), (col, col+1)))[0, 0]
print('Tarbela Dam sampled class:', value, '(expect 80, Permanent water bodies)')
"
```

Expected: prints `80`. If it doesn't, the tile mosaic/CRS/coordinate order has a real bug - stop and investigate before proceeding (this is the concrete, real-data check the spec's testing section calls for).

- [ ] **Step 5: Run the QGIS project builder and confirm the layer's XML**

Run: `python scripts/13_build_qgis_project.py`, then confirm the new layer landed correctly:

```bash
python -c "
import zipfile
with zipfile.ZipFile('gis/KP_Healthcare_Plan.qgz') as zf:
    content = zf.read('KP_Healthcare_Plan.qgs').decode('utf-8')
assert 'KP_LandCover.tif' in content
assert 'colorRampType=\"EXACT\"' in content
assert 'Permanent water bodies' in content
assert 'Built-up' in content
print('Land cover layer XML present and correctly styled')
"
```

Expected: prints the confirmation line with no assertion errors.

- [ ] **Step 6: Run the site-suggestion stage for real and inspect the output**

Run: `python scripts/11_suggest_new_sites.py`, then `cat data/processed/suggested_sites.csv` (or open in a text editor). Confirm every row has a sensible lat/lon within KP's bounds and a well-formed rationale string. If any row's rationale contains "manual site verification recommended" or "adjusted from a nearby cluster centroid", read it and sanity-check the district - a genuine land-cover adjustment firing on real data is a good sign the feature is doing real work, not a bug (but if it fires on *every* district, that's suspicious and worth double-checking the raster/coordinate handling).

- [ ] **Step 7: Rebuild downstream artifacts and confirm no unexpected diff**

Run: `python scripts/12_write_shapefiles.py && python scripts/14_build_html_report.py`. Run `git status`/`git diff` - `gis/KP_Suggested_New_Sites.*` and the report's suggested-sites table may legitimately differ from the last committed run (real new data), review the diff for plausibility rather than expecting byte-identical output this time. `gis/KP_Healthcare_Plan.qgz`'s own diff-after-rebuild non-determinism (random layer ids) is already a known, harmless pattern from prior sessions - `git checkout` it back if nothing else in it changed meaningfully, or commit it now if the new layer is expected to be part of the tracked project file going forward (it should be - the QGIS project is meant to include the new layer).

- [ ] **Step 8: Report findings**

If everything above checks out clean, this task (and the whole plan) is done. If anything looks wrong (fetch fails, the Tarbela Dam check doesn't return water, the QGIS layer XML is malformed, site suggestions look nonsensical), that's a real bug to fix with its own test (where automatable) before considering this complete.
