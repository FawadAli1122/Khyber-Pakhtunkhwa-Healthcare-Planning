# Land Use / Land Cover Integration — Design Spec

## 1. Problem

The GIS pipeline has no land use/land cover (LULC) data at all today — no way for a planner looking at the QGIS project to see whether a district's terrain is forested, agricultural, built-up, or water, and no way for the ML-based new-facility site suggestions (`scripts/11_suggest_new_sites.py`) to know whether a suggested point actually falls somewhere a facility could physically be built. This feature adds real LULC data to both.

## 2. Data Source

**ESA WorldCover 2021 (v200)**, 10m resolution, 11 land cover classes, hosted publicly on AWS S3 (`s3://esa-worldcover`, `eu-central-1`), no authentication required. Chosen over the alternatives the user raised:
- NASA MODIS Land Cover (MCD12Q1): 500m resolution (50x coarser), requires a NASA Earthdata login this project has no credential-handling for anywhere else.
- USGS NLCD: continental-US only, does not cover Pakistan.

Tiles are named by their south-west corner on a 3°×3° grid: `ESA_WorldCover_10m_2021_v200_N{lat}E{lon}_Map.tif`. KP's bounding box (31.0–36.9N, 69.2–74.1E, matching `KP_BBOX` as already defined in `05_fetch_facilities_osm.py`/`06_fetch_roads_osm.py`/`11_suggest_new_sites.py`/`22_geocode_marham_facilities.py`) is covered by exactly 6 tiles, all verified reachable (HTTP 200) at spec-writing time:

```
N30E069  N30E072
N33E069  N33E072
N36E069  N36E072
```

Class values and their official ESA colors (used for QGIS styling, section 5) — verified against ESA's own published legend at implementation time before use, working values below:

| Value | Class | Color |
|-------|-------|-------|
| 10 | Tree cover | `#006400` |
| 20 | Shrubland | `#ffbb22` |
| 30 | Grassland | `#ffff4c` |
| 40 | Cropland | `#f096ff` |
| 50 | Built-up | `#fa0000` |
| 60 | Bare / sparse vegetation | `#b4b4b4` |
| 70 | Snow and ice | `#f0f0f0` |
| 80 | Permanent water bodies | `#0064c8` |
| 90 | Herbaceous wetland | `#0096a0` |
| 95 | Mangroves | `#00cf75` |
| 100 | Moss and lichen | `#fae6a0` |

## 3. Scope

**In scope:**
- New pipeline stage fetches, mosaics, and clips WorldCover to KP's province polygon, writing `gis/KP_LandCover.tif` (30m DEM's sibling, not a replacement).
- New paletted QGIS raster layer using the official ESA class colors above.
- Site-suggestion filter: reject a KMeans-derived candidate site landing in Permanent water bodies (80), Snow and ice (70), or Herbaceous wetland (90) — the only classes where construction is physically impossible, not merely suboptimal — and fall back to the real, population-weighted nearest settlement within that cluster instead.
- The affected site's report rationale text gets one extra clause only when a fallback actually fires.

**Explicit non-goals (this pass):**
- No gap-score/district-need-tier changes — confirmed with the user; this stays presentational + site-suggestion only.
- No new HTML report section.
- No per-district land-cover composition statistics (e.g. "% forest," "% cropland").
- Tree cover, Shrubland, Grassland, Cropland, Bare/sparse vegetation, Built-up, Mangroves, and Moss/lichen are never excluded from site suggestion — sub-optimal terrain is not treated as disqualifying; only the three physically-impossible classes above are.

## 4. Fetch Stage (`scripts/23_fetch_landcover.py`)

Mirrors `scripts/15_fetch_dem.py`'s established pattern exactly:
- Opens each of the 6 tiles above via `rasterio` over GDAL's `/vsicurl/` virtual filesystem (byte-range HTTPS reads, no full-tile download — same technique already used for the DEM).
- Mosaics with `rasterio.merge.merge`, bounded to the province polygon's bounds (from `data/processed/boundaries.json`).
- Clips to the exact province polygon via `rasterio.mask.mask` (not just the bounding rectangle).
- Writes `gis/KP_LandCover.tif`.
- Wired into `scripts/run_all.py`'s `STAGES` list immediately after the DEM stage (`15_fetch_dem.py`), since both are one-time-per-run external raster fetches with no other pipeline dependency between them.

## 5. QGIS Layer (`scripts/13_build_qgis_project.py`)

A new raster layer entry (alongside the existing `{"id": "dem", "file": "KP_DEM.tif", ...}` entry) pointing at `KP_LandCover.tif`, styled as a **paletted/unique-values renderer** (not DEM's continuous gradient — WorldCover's pixel values are discrete class codes, not a continuous field) using the 11 official ESA colors from section 2. Layer order: below all vector layers (facilities, roads, choropleth, suggested sites) so those remain visible on top, same z-order treatment the DEM layer already gets.

## 6. Site-Suggestion Filter (`scripts/11_suggest_new_sites.py`)

In `pick_candidate_sites()`: after KMeans produces cluster centers, for each candidate:
1. Sample `KP_LandCover.tif` at the centroid's (lon, lat) via `rasterio`.
2. If the sampled class is one of `{70, 80, 90}` (Snow/ice, Permanent water, Herbaceous wetland):
   - Among that cluster's member settlements (via `km.labels_`), pick the one with the highest population (ties broken by proximity to the original centroid).
   - Use that settlement's real coordinates as the candidate site instead of the centroid.
   - Append a clause to the existing rationale string noting the adjustment (e.g. `"...adjusted from a nearby cluster centroid falling in Permanent water bodies"`), naming the actual excluded class encountered.
3. If the sampled class is anything else (including all 8 non-excluded classes), the original centroid is used unchanged — current behavior, no rationale change.

If every settlement in a cluster is itself unusable (degenerate case — a whole cluster's raw centroid *and* its highest-population settlement both land in an excluded class), keep the original centroid and note in the rationale that manual site verification is recommended — matching this project's established "surface the real limitation honestly rather than hide it" precedent (e.g. the Marham cross-validation tolerance, the Facility Readiness empty-state).

## 7. Testing

- `scripts/23_fetch_landcover.py`: no automated tests for the fetch/mosaic/clip logic itself (matches `15_fetch_dem.py`'s own established precedent — external raster I/O, verified manually against the real pipeline run, not mocked).
- The site-suggestion filter's *decision logic* (given a sampled class value and cluster settlements, which coordinate and rationale clause result) is a pure function, extracted and unit-tested with a fake/small raster sample — mirrors how `terrain.py`'s scoring logic is unit-tested independently of the real DEM I/O that feeds it.
- QGIS layer styling has no automated test (matches every other layer-styling code in `13_build_qgis_project.py`, which has none either) — verified manually via the real generated `.qgz` file's data-level correctness (checking the layer's renderer/class definitions programmatically), consistent with this project's own documented conclusion that QGIS screenshot verification is structurally unreliable in this environment and data-level checks are used instead.

## 8. Open Questions / Risks Explicitly Accepted

- ESA WorldCover 2021 is a single snapshot year (not annually updated in this pipeline) — acceptable, matching the DEM's own one-time-fetch treatment; a real land-cover change (e.g. new construction since 2021) would only be caught by a future manual re-run, not automatically.
- The `gis/KP_LandCover.tif` output will be a real file of nontrivial size (10m resolution over all of KP) — checked against git's ~100MB practical ceiling during implementation, following the exact precedent already set for `gis/KP_DEM.tif` and `data/raw/osm_roads.json` (gitignored if it exceeds that line, never committed).
