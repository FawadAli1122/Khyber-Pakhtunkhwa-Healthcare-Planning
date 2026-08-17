# KP Healthcare System Planning — GIS + AI Pipeline

Status: Approved design, pre-implementation
Date: 2026-08-14

## 1. Purpose

Produce a QGIS-ready healthcare system planning package for Khyber Pakhtunkhwa (KP)
province, Pakistan: real administrative boundaries, real 2023 census population,
real healthcare facility data from multiple sources, a road network layer, an
AI/ML-based facility-access gap analysis, and a comprehensive HTML planning report
documenting data sources, methodology, and future recommendations.

This is a **planning aid built from best-available open/official data**, not an
official government policy document. The report must say so explicitly.

## 2. Environment Constraints (verified during brainstorming)

- Windows, Python 3.12.2, internet access confirmed.
- `geopandas`/`pyogrio` are installed but **broken** (numpy2 vs. pyarrow ABI
  conflict) — do not depend on them, and do not spend implementation time
  trying to fix them.
- No QGIS installation available in this environment to test `.qgz` files
  against directly.
- Usable, working libraries confirmed present: `requests`, `shapely` 2.0.6,
  `beautifulsoup4` 4.12.3, `lxml`, `matplotlib` 3.9.2, `scikit-learn` 1.5.1.
  `pyshp` is not yet installed — install it at implementation time (pure
  Python, no numpy ABI dependency, safe to add).

## 3. Tooling Approach

Pure `shapely` + `pyshp` + `requests` + `bs4` + `matplotlib` + `scikit-learn`
pipeline. No geopandas/fiona/pyogrio anywhere in the pipeline.

- Geometry construction/ops: `shapely`
- Shapefile (.shp/.shx/.dbf/.prj) writing: `pyshp`
- HTTP fetch (Overpass, HDX/GADM, KPHCC scrape, Nominatim geocoding):
  `requests` + `bs4`/`lxml` for HTML parsing
- Static map rendering for the HTML report: `matplotlib`, plotting shapely
  geometries directly (no basemap/geopandas plotting helpers)
- ML/scoring: `scikit-learn` (`MinMaxScaler`, `KMeans`)

## 4. Data Sources

### 4.1 Administrative boundaries
- Primary: HDX/OCHA Pakistan COD-AB (Common Operational Datasets — Admin
  Boundaries), which reflects the post-2018 FATA-merger district structure
  used by PBS.
- Fallback: GADM Pakistan level-2 boundaries, if HDX is unreachable or
  missing merged-district detail.
- KP province polygon = dissolve of all KP district polygons.
- Exact current district list/count (~35, pending confirmation at fetch
  time) confirmed from the boundary dataset itself, not assumed in advance.

### 4.2 Population
- Pakistan Bureau of Statistics (PBS) 2023 Digital Census (7th census),
  district-wise population for KP. Compiled via web research into a sourced
  CSV (`data/processed/kp_district_population_2023.csv`) with a citation
  column per row (source URL/publication).
- Where available, also capture the district's prior census figure(s) to
  derive a compound annual growth rate for the demand-forecasting model; if
  unavailable per-district, fall back to the KP provincial average growth
  rate.

### 4.3 Healthcare facilities (three sources, merged)
1. **KPHCC Licensed HCEs registry** (primary, official) —
   `https://hcc.kp.gov.pk/licensed-hces/?search=&district=&category=&date=`.
   Confirmed structure: plain server-rendered HTML table, paginated via
   `?page=N` query param, ~28 pages / 280 records. Columns: licence no.,
   issue/expire date, HCE category, public/private, name, address, district,
   beds. Scrape all pages with `requests`+`bs4`, respecting a small delay
   between requests. No coordinates provided — geocode `address + ", " +
   district + ", Khyber Pakhtunkhwa, Pakistan"` via OSM Nominatim (free,
   rate-limited to 1 req/sec per its usage policy). If geocoding fails or is
   low-confidence, fall back to placing the point at the district centroid
   and flag it. Note in the report: this registry has zero entries for
   several newly-merged tribal districts — a genuine finding (licensing
   regime not yet extended there), not a scraping bug.
2. **OpenStreetMap** (fills category/geographic gaps) — Overpass API query
   for `amenity=hospital|clinic|doctors|pharmacy` and `healthcare=*` tags,
   clipped to the KP boundary polygon.
3. **Google Places** (optional, supplemental) — only if the user supplies an
   API key later. Until then, proceed without it; the pipeline should be
   structured so this source can be added as a later enrichment pass without
   re-running everything else.

Each facility record gets: `name`, `category` (normalized to a common tier
scheme: Hospital/RHC-THQ-DHQ tier where derivable/Clinic/Lab/Pharmacy/GP),
`public_private`, `beds` (if known), `district`, `address`, `lat`, `lon`,
`source` (KPHCC/OSM/Google), `geocode_precision` (exact/street/
district-centroid/osm-native). Simple dedup pass across sources by name
similarity + proximity (e.g. same district, name token overlap, within
~500m) — mark duplicates rather than silently dropping, keep the
highest-precision record as primary.

### 4.4 Roads
- OSM Overpass query for `highway=motorway|trunk|primary|secondary`, clipped
  to KP boundary. Used as an accessibility proxy (straight-line distance to
  nearest facility, not full routing, since no routing engine is available
  in this environment).

## 5. Processing & Analysis

1. Join population CSV to district polygons by name (handle known name
   variants, e.g. "Dir Lower" vs "Lower Dir" — the KPHCC dropdown itself has
   duplicate/inconsistent naming, confirmed during brainstorming; build a
   small name-normalization map).
2. Point-in-polygon spatial join (shapely) to assign each facility and each
   settlement/road segment to its containing district.
3. Per-district metrics: area (km², computed in an equal-area or UTM
   projection, not WGS84 degrees), population, population density,
   facility count by tier, beds per 1,000 population, straight-line
   distance from district population centroid to nearest facility (proxy
   accessibility), terrain classification (mountainous vs. plains — hand
   classified from known KP geography, e.g. Chitral/Kohistan/Swat/Shangla/
   Upper-Lower Dir/Battagram/Buner/Torghar as mountainous).
4. **Gap score**: weighted composite (scikit-learn `MinMaxScaler` to
   normalize population density, inverse facility density, accessibility
   distance, terrain penalty) into a 0–100 underservice score per district.
5. **Need-tier clustering**: `KMeans` on the normalized feature set to group
   districts into Critical/High/Moderate/Low need tiers (label clusters by
   their mean gap score after fitting, not by arbitrary cluster index).
6. **Demand forecast**: exponential population projection per district to
   2030 and 2035 using derived/provincial growth rate; compare against
   Pakistan health-facility population norms (BHU/RHC/THQ/DHQ ratios) to
   estimate facilities/beds needed by each horizon.
7. **ML site suggestion**: for the top-N (e.g. 10) worst-scoring districts,
   population-weighted KMeans over in-district settlement points (OSM
   `place=town|village`) to propose new-facility candidate coordinates
   (cluster centroids of population most distant from existing facilities).
8. **Emerging-tech narrative**: written recommendations mapped to need-tier
   clusters and terrain (e.g. telemedicine hubs + drone medical resupply for
   Critical-tier mountainous districts, AI-assisted triage/queue systems for
   high-density urban districts, mobile health units + satellite
   connectivity for border/tribal districts with sparse facility coverage).

All analysis code should be readable/transparent (documented formulas, not
opaque black boxes) since this feeds a report meant to justify real planning
decisions.

## 6. Output Shapefiles (WGS84 / EPSG:4326, folder `gis/`)

| Layer | Geometry | Key attributes |
|---|---|---|
| `KP_Province_Boundary` | Polygon (1 feature) | name, area_km2, total_population |
| `KP_Districts` | Polygon | district, division, area_km2, population_2023, pop_density, terrain_type |
| `KP_Healthcare_Facilities` | Point | name, category, public_private, beds, district, source, geocode_precision |
| `KP_Roads` | Line | road_class, name |
| `KP_District_Gap_Scores` | Polygon | district, gap_score, need_tier, pop_2030, pop_2035, facilities_needed_2030, facilities_needed_2035 |
| `KP_Suggested_New_Sites` | Point | district, priority_rank, rationale |

Each `.shp` ships with matching `.shx`/`.dbf`/`.prj` (WGS84 WKT).

## 7. QGIS Deliverable

- Hand-authored `.qgz`/`.qgs` project file at `gis/KP_Healthcare_Plan.qgz`
  referencing all layers above by relative path, with styling baked in:
  graduated choropleth on `KP_Districts.population_2023`, graduated
  red-yellow-green on `KP_District_Gap_Scores.gap_score`, categorized
  symbology on `KP_Healthcare_Facilities.category`, simple line style for
  roads, distinct marker for suggested new sites.
- Fallback `scripts/load_and_style.py`: a PyQGIS console script that
  reconstructs the same layer loading + styling programmatically, for the
  user to run from QGIS's Python console if the hand-built `.qgz` has any
  version-compatibility issue (this can't be tested against a real QGIS
  install in this environment, so the fallback is required, not optional).

## 8. HTML Report (`report/KP_Healthcare_Plan.html`)

Self-contained HTML, static maps embedded as base64 PNGs rendered from the
same shapefiles via matplotlib (province overview, population choropleth,
facility distribution, gap-score heatmap). Sections:

1. Executive summary
2. Data sources & methodology, with citations (PBS census publication,
   HDX/GADM boundary dataset, KPHCC registry URL, OSM/Overpass, Nominatim)
3. Current-state maps and district data tables (population, area, density,
   facility counts by tier, beds/1,000 pop)
4. AI/ML methodology explained in plain terms (gap-score formula, clustering
   approach, forecasting model, site-suggestion method) — transparent, not a
   black box
5. Findings: ranked underserved-district list, notable gaps (e.g. licensing
   registry coverage gap in tribal districts)
6. Future planning: emerging-tech recommendations per need-tier/terrain,
   phased short/medium/long-term roadmap, projected facilities/beds needed
   by 2030/2035
7. Limitations & disclaimers: open-data gaps, geocoding precision caveats,
   straight-line accessibility proxy (not real routing), not an official
   government document

Also published as a Claude Artifact (same content) for a shareable link, in
addition to the local file.

## 9. Folder Layout

```
E:\Healthcare System Planning\
  data/
    raw/            # unmodified fetched data (boundary downloads, scraped HTML/JSON dumps)
    processed/       # cleaned CSVs, joined tables
  scripts/
    01_fetch_boundaries.py
    02_compile_population.py
    03_fetch_facilities_kphcc.py
    04_fetch_facilities_osm.py
    05_fetch_roads.py
    06_merge_facilities.py
    07_analysis.py
    08_write_shapefiles.py
    09_build_qgis_project.py
    load_and_style.py         # PyQGIS fallback console script
    10_build_html_report.py
  gis/
    KP_Province_Boundary.shp (+ .shx/.dbf/.prj)
    KP_Districts.shp (...)
    KP_Healthcare_Facilities.shp (...)
    KP_Roads.shp (...)
    KP_District_Gap_Scores.shp (...)
    KP_Suggested_New_Sites.shp (...)
    KP_Healthcare_Plan.qgz
  report/
    KP_Healthcare_Plan.html
  docs/superpowers/specs/
    2026-08-14-kp-healthcare-gis-planning-design.md
```

## 10. Out of Scope (for this pass)

- Real road-network routing/travel-time (no routing engine available;
  straight-line distance is the documented proxy)
- Google Places enrichment (deferred until/unless the user supplies an API
  key; pipeline structured so it can be added later without a full rerun)
- Automated QGIS testing (no QGIS install available; mitigated via the
  PyQGIS fallback script)
