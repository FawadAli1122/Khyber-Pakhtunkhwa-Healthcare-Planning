# KP Healthcare Planning — Development Statistics + DEM + Multi-Horizon Extension

Status: Approved design, pre-implementation
Date: 2026-08-14
Extends: [2026-08-14-kp-healthcare-gis-planning-design.md](./2026-08-14-kp-healthcare-gis-planning-design.md)

## 1. Purpose

Extend the existing KP healthcare planning pipeline with three additions the user
requested:

1. Integrate KP Bureau of Statistics' "Development Statistics" publication (all
   healthcare-related and directly-supporting data) into the QGIS layers.
2. Download and integrate real DEM (elevation) data for the whole province into
   the healthcare planning.
3. Produce 3-year, 5-year, and 20-year healthcare system plans (in HTML) with
   concrete recommendations at each horizon.

## 2. Confirmed During Brainstorming

- **No "Development Statistics 2025" exists yet.** KP BOS publishes annually
  with a lag; the latest is **"Development Statistics of Khyber Pakhtunkhwa
  2024"** (published Sep 2024), downloaded and confirmed at
  `data/raw/kp_development_statistics_2024.pdf` (454 pages, ~190 tables,
  extracted from a `.rar` served by `kpbos.gov.pk`'s API — see Task notes for
  the exact download mechanism, since the file isn't at a stable public URL).
- **DEM source:** Copernicus GLO-30 (ESA/Sinergise), confirmed publicly
  streamable with no authentication via
  `https://copernicus-dem-30m.s3.amazonaws.com/...` (AWS Open Data Registry),
  30m native resolution, readable via GDAL's `/vsicurl/` without downloading
  full tiles. `rasterio` installed and confirmed working (bundles its own GDAL
  on Windows, no separate GDAL install needed).
- User chose: healthcare-focused + key-context scope for Dev Stats (not all
  ~190 tables), and native 30m DEM resolution (not resampled).
- Forecast horizons: **3-year (2029), 5-year (2031), 20-year (2046)**, measured
  from the current date (2026-08-14), superseding the prior pipeline's fixed
  2030/2035 horizons.

## 3. Development Statistics 2024 — Tables In Scope

Located and verified extractable (page numbers are 1-based PDF page indices,
not the document's own printed page numbers, since the two diverge):

| Table | Content | PDF pages | Use |
|---|---|---|---|
| 104 | Provincial govt health institutions & beds, 2021-2023 trend | 202 | Report trend context |
| 105 | District/tehsil govt health institutions & beds by type (latest=2023 snapshot; the table repeats 3x for 2021/2022/2023 across pages 203-211, use only the last/2023 instance at pages 209-211) | 209-211 | District shapefile attributes: govt institutions & beds by type |
| 106 | District/tehsil hospitals & bed strength (govt total, own vs attached) | 212-215 (3-year repeat, use last instance) | Cross-check against Table 105 |
| 107 | District registered private hospitals & bed-capacity ranges | ~216-224 (locate at implementation time via text search for "Table No. 107") | District shapefile: private beds |
| 108 | District population per govt hospital/dispensary bed | locate via search | District shapefile: population-per-bed (official) |
| 109/110 | District medical & paramedical staff posted | locate via search | District shapefile: staffing |
| 112 | District registered private medical practitioners | locate via search | District shapefile: private practitioners |
| 113 | District patients treated | locate via search | Report context |
| 195 | District road lengths (Total/High-type/Low-type, 3-year repeat, use latest) | 413+ | District shapefile: road_km (official, supersedes/supplements OSM road length) |
| 182/183 | ADP sector-wise budget allocations (provincial, KP/MA/AIP/Total, FY2023-24 and FY2024-25) | 395-396 | Report narrative: Health-sector budget line, not a per-district attribute |

Exact page locations for tables not yet pinned down are found at
implementation time the same way the ones above were: PyMuPDF full-text
search for the `"Table No. <N>"` marker, then PyMuPDF/pdfplumber table
extraction on that page range. Because the same table title repeats 3x
(one instance per year covered), always take the **last** (most recent
year) instance, and verify against the corresponding row in Table 104
(which gives clean provincial-level year-by-year totals to cross-check
against).

**Column ambiguity risk:** these tables use compact multi-level headers
(e.g. "Nos./Beds" repeated per institution-type group) that don't extract
cleanly as flat text. The implementation must inspect the actual header
row structure per table before writing the column-mapping dict — do not
assume the mapping from the exploratory dump above without verifying the
header row for that specific table.

## 4. DEM Acquisition & Integration

- **Source:** Copernicus DEM GLO-30, 1°×1° COG tiles at
  `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM/Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM.tif`
  where `{NS}` is `N`, `{EW}` is `E` for KP's range (all tiles are N/E for
  this province). KP's bounding box (31.0-36.9N, 69.2-74.1E) requires tiles
  for integer lat 31-36 and lon 69-74 (up to 36 tiles; skip any that 404 —
  some edge tiles may not exist if there's no `.tif` at that exact
  coordinate, though KP's full bbox is land so this is unlikely).
- **Processing:** build a GDAL VRT mosaicking the tiles (referenced via
  `/vsicurl/`, no local download of full tiles), warp/clip to the KP
  province polygon (from `boundaries.json`) at native ~30m resolution,
  write `gis/KP_DEM.tif` (EPSG:4326, compressed GeoTIFF — LZW or DEFLATE to
  keep the "few hundred MB" deliverable reasonable).
- **Per-district zonal stats:** for each of the 35 districts, compute mean
  elevation (m), min/max elevation (m), and mean slope (degrees, computed
  from the elevation raster via `richdem` or a simple numpy gradient — pick
  whichever is available; a numpy-based slope calc from the clipped raster
  is an acceptable fallback if no dedicated terrain library is available).
  Write to `data/processed/district_terrain.csv`
  (`district,mean_elev_m,min_elev_m,max_elev_m,mean_slope_deg`).
- **Terrain difficulty score:** replace the `classify_terrain()`
  boolean (`scripts/08_compute_district_metrics.py`) with a continuous
  `terrain_difficulty` in [0,1], min-max normalized from a blend of
  `mean_elev_m` and `mean_slope_deg` (equal-weighted after each is
  independently min-max scaled across the 35 districts). This replaces
  the `terrain_penalty` term in the gap-score feature matrix
  (`scripts/09_gap_score_and_clusters.py`) — same 0.15 weight slot, now
  continuous instead of a 0/1 flag. Keep the old hand-classified
  `classify_terrain()` mountainous/plains label too (as a human-readable
  `terrain` column) since the report's prose references it, but derive it
  from the new data: mountainous if `terrain_difficulty > 0.5`, else
  plains — so the label is now data-driven, not hardcoded.

## 5. Multi-Horizon Forecast (3/5/20-Year)

- Replace `scripts/10_forecast_demand.py`'s fixed `2030`/`2035` targets with
  `HORIZONS = {"3yr": 2029, "5yr": 2031, "20yr": 2046}` (computed from
  today's date, 2026-08-14 → +3/+5/+20 years, matching calendar years).
- For each horizon: project population (existing exponential growth-rate
  method, unchanged), and facilities-needed (existing
  population-per-facility norm, unchanged) — just at the three new target
  years instead of two.
- `district_metrics.csv` / `KP_District_Gap_Scores.shp` fields become
  `pop_2029, pop_2031, pop_2046, fac_nd29, fac_nd31, fac_nd46` (still ≤10
  chars for the shapefile DBF).
- Also project **beds needed** at each horizon using the Dev-Stats-derived
  current beds-per-1000 baseline (Table 105/106/107 data) against a WHO-ish
  benchmark ratio (document the exact ratio used and that it's a
  simplification, consistent with the existing facilities-needed
  methodology) — this is new, giving the report concrete bed-capacity
  numbers per horizon, not just facility counts.

## 6. Facility Count Cross-Validation

Add a comparison table to the report: my merged KPHCC+OSM facility count per
district vs. Dev Stats Table 105's official government institution count
per district (the two count different things — mine includes private
clinics/pharmacies OSM/KPHCC-visible, Dev Stats counts only government
institutions — so the report must explain the difference, not present them
as if they should match, and call out districts with material gaps as a
data-quality finding in their own right).

## 7. Output Changes

**New/changed files:**
- `gis/KP_DEM.tif` — new raster layer
- `data/processed/district_terrain.csv` — new zonal-stats table
- `data/processed/dev_stats_health.csv` — extracted Dev Stats health tables,
  one row per district, columns per Section 3
- `data/processed/dev_stats_roads.csv` — extracted Table 195, one row per
  district
- `scripts/08_compute_district_metrics.py` — terrain classification now
  data-driven (Section 4)
- `scripts/09_gap_score_and_clusters.py` — continuous terrain feature
- `scripts/10_forecast_demand.py` — 3/5/20-year horizons (Section 5)
- `KP_Districts.shp` / `KP_District_Gap_Scores.shp` — new attribute columns
  (mean_elev, mean_slope, govt_beds, govt_inst, pvt_beds, road_km, and the
  renamed horizon fields)
- `gis/KP_Healthcare_Plan.qgz` — DEM layer added with an elevation color
  ramp + hillshade-style single-band pseudocolor renderer
- `report/KP_Healthcare_Plan.html` — new sections (Section 8) and
  restructured Future Planning section

## 8. HTML Report Structure Changes

Insert after "Current State":
- **"Official Infrastructure Context (Development Statistics 2024)"** —
  government institution/bed counts, private hospital bed-capacity ranges,
  staffing, road lengths per district, with the cross-validation table from
  Section 6, and the Health-sector ADP budget figures for provincial
  narrative context. States plainly that KP BOS's latest edition is 2024 (no
  2025 exists yet).
- **"Terrain & Elevation"** — embeds a DEM-derived elevation map (matplotlib,
  same embedding approach as the existing choropleth maps), explains the
  terrain-difficulty methodology from Section 4, and lists districts by
  mean elevation/slope.

Replace "Future Planning & Emerging-Technology Recommendations" with three
explicit horizon subsections, each getting its own population/facility/bed
projection table and horizon-appropriate recommendations:
- **3-Year Plan (through 2029):** operational/deployable-now measures —
  mobile health units, KPHCC licensing outreach, telemedicine pilot sites,
  staffing reallocation to the highest-gap districts identified.
- **5-Year Plan (through 2031):** infrastructure build-out at the ML-suggested
  sites, drone-resupply pilots for the most terrain-isolated Critical-tier
  districts, expanded specialist telemedicine network.
- **20-Year Plan (through 2046):** systemic transformation — projected
  facility/bed shortfall at the 20-year horizon, new DHQ/THQ-tier hospitals
  in districts whose projected 2046 population outgrows current capacity by
  the largest margin, workforce pipeline (medical college seats vs.
  projected staffing need), climate/terrain-resilient infrastructure given
  DEM-identified high-elevation districts.

Update the disclaimer to cite Development Statistics 2024 and Copernicus
GLO-30 DEM as sources, and to note the population-per-facility and
beds-per-1000 norms used for the 20-year projection are simplifications, not
official MoH capacity-planning standards.

## 9. Out of Scope

- The ~180 non-health, non-road Dev Stats tables (agriculture, industry,
  sports, etc.) — per user's explicit scope choice.
- Slope/aspect-based routing or true terrain-cost accessibility modeling —
  the terrain difficulty score is a per-district scalar, not a routing
  input; accessibility remains the straight-line proxy from the original
  design.
- Resampling the DEM to a coarser resolution — per user's explicit choice
  of native 30m.
