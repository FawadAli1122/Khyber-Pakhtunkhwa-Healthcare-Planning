# KP Healthcare Plan

**An AI-orchestrated healthcare infrastructure planning platform for Khyber Pakhtunkhwa (KP), Pakistan.**

This project takes real public data — the 2023 Digital Census, the province's licensed-facility registry, OpenStreetMap, government development statistics, satellite elevation and land-cover data — and turns it into a district-by-district assessment of healthcare access, a facility-gap score, machine-learning-driven new-site recommendations, and an interactive planning dashboard, admin panel, and Telegram bot on top.

It's included here as a portfolio piece for **AI automation, AI-assisted planning, and AI-assisted policy analysis**: almost every non-trivial decision in this codebase — architecture choices, data-source evaluation, bug diagnosis, even catching its own design mistakes before they shipped — was made by an AI agent working through a documented brainstorm → spec → plan → build → test cycle. That full paper trail is included, unedited, in [`docs/superpowers/`](docs/superpowers/) — see [The AI-driven development process](#the-ai-driven-development-process) below.

> **Honesty note:** this is a demonstration/portfolio project, not an official Government of Khyber Pakhtunkhwa product. The underlying data is real and public; the analysis and recommendations are illustrative of what an AI-assisted planning tool can do, not a substitute for expert policy review.

---

## Table of contents

- [What it does](#what-it-does)
- [How it works (architecture)](#how-it-works-architecture)
- [Repository structure](#repository-structure)
- [Getting started](#getting-started)
- [Database integration](#database-integration)
- [Telegram bot integration](#telegram-bot-integration)
- [AI provider integration](#ai-provider-integration)
- [Fetching and clipping the DEM / land-cover rasters](#fetching-and-clipping-the-dem--land-cover-rasters)
- [Data sources & attribution](#data-sources--attribution)
- [The AI-driven development process](#the-ai-driven-development-process)
- [Testing](#testing)
- [Known limitations & honest caveats](#known-limitations--honest-caveats)
- [License](#license)

---

## What it does

- **A 25-stage geospatial data pipeline** (`scripts/01_...` through `scripts/25_...`) that fetches, cleans, merges, and analyzes public data from six independent sources into one consistent per-district dataset — automatically, end to end, via `python scripts/run_all.py`.
- **A composite facility-access gap score** per district (0–100), combining population, existing facility/bed density, real network- and terrain-weighted **travel-time accessibility** (not a straight-line distance proxy), and terrain difficulty — with tiered "need" classification and 3/5/20-year demand forecasting.
- **ML-driven new-site suggestions**: for the worst-scoring districts, a population-weighted clustering step recommends concrete candidate coordinates for new facilities, automatically adjusted away from water/snow/wetland pixels using real land-cover data.
- **A self-contained HTML planning report** (`report/KP_Healthcare_Plan.html`) and a **QGIS project** (`gis/KP_Healthcare_Plan.qgz`) with six styled map layers (districts, gap-score choropleth, roads, facilities, suggested sites, province boundary).
- **A FastAPI web server** serving an interactive dashboard, a chat-style "Ask AI" panel that answers planning questions grounded in the real data, and a full **admin panel** for managing the data.
- **AI-powered document extraction**: upload a scanned facility report or equipment note and an LLM (your choice of Anthropic, OpenAI, Gemini, Grok, or Groq) extracts structured data from it — with every AI output validated and shown for review before it's written anywhere.
- **A Telegram bot** with near-total feature parity with the admin panel — every admin action (add data, apply overrides, browse/edit the database, ask the AI, download the report/map) is also a chat command, using long-polling so no public URL or webhook is required.
- **A centralized, properly-typed PostgreSQL data layer**: the app bundles and manages its own private local Postgres instance (no separate database server to install/configure), with a generic **Database Browser** that can view and edit *any* table — including the pipeline's own computed outputs — from both the web admin panel and Telegram, with zero per-table code.

---

## How it works (architecture)

```
                    ┌─────────────────────────────────────────────────┐
                    │   Public data sources (see Data sources below)  │
                    │  Census · KPHCC · OSM · Marham.pk · Dev Stats   │
                    │        · Copernicus DEM · ESA WorldCover        │
                    └───────────────────────┬───────────────────────┘
                                             │  scripts/01_… – 24_…
                                             │  (fetch → geocode → merge →
                                             │   compute → forecast → suggest)
                                             ▼
                    ┌───────────────────────────────────────────────┐
                    │            data/processed/*.csv, *.json         │
                    │  (per-district metrics, merged facilities,      │
                    │   suggested sites, terrain, land cover, …)      │
                    └──────┬───────────────────────────┬─────────────┘
                            │  scripts/12_…/13_…/14_…    │  scripts/25_sync_processed_to_db.py
                            ▼                            ▼
                ┌────────────────────────┐   ┌─────────────────────────────┐
                │  gis/*.shp + .qgz       │   │  Bundled local PostgreSQL     │
                │  report/*.html          │   │  (pipeline_* tables, properly │
                │  (static planning       │   │   typed, refreshed every run) │
                │   artifacts)            │   └───────────────┬───────────────┘
                └────────────────────────┘                    │
                                                                 ▼
                    ┌───────────────────────────────────────────────────┐
                    │              server/ — FastAPI application          │
                    │  Dashboard · Admin panel · Ask-AI chat · Telegram   │
                    │  bot · Database Browser · Database Ingestion        │
                    └───────────────────────────────────────────────────┘
```

**Two clearly separated layers**, matching how the code is organized:

1. **The pipeline** (`scripts/`) — pure batch processing. Every stage reads its inputs from files (or the database, for the three admin-editable overlay stores) and writes its outputs to files. It has no web server, no AI calls at request time, and can be re-run from scratch at any point (`python scripts/run_all.py`) — every stage is idempotent.
2. **The application** (`server/`) — a FastAPI app that serves the dashboard/report, the admin panel, and the Telegram bot. It reuses the pipeline's outputs and, for a handful of admin-entered data types, writes directly to the bundled database and triggers a partial pipeline re-run (`scripts/run_downstream.py` or `scripts/run_downstream_facilities.py`) so the report/dashboard/map stay in sync.

`server/` never gets imported by `scripts/` — the dependency only ever runs one way, `server → scripts.lib`, so the pipeline stays usable completely standalone (e.g. in a CI job, or by someone who only wants the data/report and not the web app).

### The gap score, briefly

Each district's score blends: population density, existing government+private facility and bed density, **travel-time accessibility** to the nearest known facility (a real routing computation over the OpenStreetMap road network, weighted by DEM-derived terrain difficulty, originating from the population-weighted "where people actually live" point rather than a geometric centroid), and that terrain difficulty itself. Districts are bucketed into Critical/High/Medium/Low need tiers, and demand is forecast 3/5/20 years out using the same population growth trend the census data implies.

---

## Repository structure

<details>
<summary><strong>server/</strong> — the FastAPI web application (click to expand)</summary>

| File | What it does |
|---|---|
| `app.py` | FastAPI application factory; manages the bundled database's startup/shutdown lifecycle. |
| `__main__.py` | Entry point — `python -m server`. |
| `auth.py` | Admin password hashing (PBKDF2) and signed session cookies — stdlib only, no extra crypto dependency. |
| `keystore.py` | Wraps the OS keyring (Windows Credential Manager / macOS Keychain / Linux Secret Service) for every secret this app ever stores: admin password hash, AI provider keys, Telegram bot token, database credentials. **Nothing sensitive is ever written to a file.** |
| `admin_ui.py` | HTML/CSS/JS for the entire admin panel (all sections listed below). |
| `chat_ui.py` | The "Ask AI" chat panel injected into the public dashboard. |
| `routes/dashboard.py` | Serves the dashboard/report at `/`. |
| `routes/admin.py` | Every `/admin/*` route — login, all admin API endpoints. |
| `ai_client.py` | Real chat/completion calls to all five supported AI providers. |
| `providers.py` | Validates a stored/candidate API key against each provider with a real lightweight test call. |
| `report_context.py` | Builds the compact text digest of the plan's data that grounds every "Ask AI" answer. |
| `document_extraction.py` | Normalizes an uploaded PDF/DOCX/XLSX/image into plain text for AI extraction. |
| `supplemental_data.py` | AI-extracted facility/district records (equipment, staffing, readiness) — admin-entered, DB-backed. |
| `metric_overrides.py` | AI-assisted corrections to core pipeline numbers (population, health stats), with plausibility checks. |
| `bot_facilities.py` | Facilities added via the Telegram bot's `/addpoint` — a fourth facility source alongside KPHCC/OSM/Marham. |
| `custom_data.py` | Admin-defined custom database tables — real dynamic `CREATE TABLE`, with AI-proposed schemas and AI-decided report placement. |
| `db_ingestion.py` | Read-only browsing/AI-extraction from an **external** database the admin connects to. |
| `db_browser.py` | Generic view/edit access to **every** table in the bundled database — the primitive behind the admin panel's "Database Browser" and the bot's `/localtables`/`/localview`/`/localedit`. |
| `pdf_export.py` | Renders the HTML report to a PDF via a headless browser (Playwright). |
| `telegram_bot.py` | Bot bootstrap, command registry, long-polling loop. |
| `telegram_admin_records.py` / `telegram_admin_tables.py` / `telegram_admin_db.py` | Telegram commands mirroring the admin panel's records/custom-tables/database sections. |
| `telegram_rebuild.py` | Shared helpers that trigger the right partial pipeline re-run after a bot-driven data change. |
| `telegram_ui.py` | Small shared Telegram UI helper (provider-selection keyboards). |

</details>

<details>
<summary><strong>scripts/</strong> — the 25-stage data pipeline (click to expand)</summary>

Run the whole thing with `python scripts/run_all.py`, or a specific stage directly, e.g. `python scripts/09_gap_score_and_clusters.py`. Every stage is idempotent — safe to re-run.

| Stage | What it does |
|---|---|
| `01_fetch_boundaries.py` | Fetch KP province + district boundaries (HDX/OCHA Pakistan). |
| `02_compile_population.py` | Compile district population from the Pakistan Bureau of Statistics' 2023 Digital Census. |
| `03_fetch_facilities_kphcc.py` | Scrape the KP Health Care Commission's public licensed-facility registry. |
| `04_geocode_kphcc_facilities.py` | Geocode each KPHCC facility's free-text address via OSM Nominatim. |
| `05_fetch_facilities_osm.py` | Fetch OpenStreetMap healthcare facility points. |
| `06_fetch_roads_osm.py` | Fetch the OSM road network (motorway → residential). |
| `07_merge_facilities.py` | Merge KPHCC + OSM + Marham.pk + bot-added facilities, deduplicated. |
| `07b_apply_metric_overrides.py` | Apply any admin-approved data overrides on top of the raw census/dev-stats numbers. |
| `08_compute_district_metrics.py` | Compute per-district area, density, facility/bed rates, etc. |
| `09_gap_score_and_clusters.py` | The composite facility-access gap score and need tiers. |
| `10_forecast_demand.py` | Project population/facility/bed need 3/5/20 years out. |
| `11_suggest_new_sites.py` | ML (KMeans)-based new-facility site suggestions for the worst-scoring districts. |
| `12_write_shapefiles.py` | Assemble every processed table into the six `gis/*.shp` layers. |
| `13_build_qgis_project.py` | Hand-author the QGIS project file loading all six layers, styled. |
| `13b_build_qgis_project_pyqgis.py` | Alternative QGIS-project builder using PyQGIS itself (requires a real QGIS install). |
| `14_build_html_report.py` | Render the self-contained `report/KP_Healthcare_Plan.html`. |
| `15_fetch_dem.py` | Fetch + clip the Copernicus GLO-30 elevation model (see [Fetching the DEM](#fetching-and-clipping-the-dem--land-cover-rasters)). |
| `16_compute_dem_zonal_stats.py` | Per-district elevation/slope statistics from the DEM. |
| `16b_compute_travel_time_accessibility.py` | Real network+terrain-weighted travel-time routing (the accessibility_min metric). |
| `17_extract_devstats_health.py` | Extract health institution/bed/staffing tables from the government Development Statistics PDF. |
| `18_extract_devstats_roads.py` | Extract district road-length tables from the same PDF series. |
| `19_extract_devstats_budget.py` | Extract the Health sector's development budget figures. |
| `20_cross_validate_facility_counts.py` | Sanity-check the merged facility count against the official government count. |
| `21_fetch_facilities_marham.py` | Fetch facility listings from Marham.pk (a commercial healthcare directory). |
| `22_geocode_marham_facilities.py` | Geocode Marham listings lacking real coordinates. |
| `23_fetch_landcover.py` | Fetch + clip the ESA WorldCover 2021 land-cover raster. |
| `24_compute_landcover_zonal_stats.py` | Per-district and province-wide land-cover composition. |
| `25_sync_processed_to_db.py` | Load every `data/processed/*` file into the bundled Postgres database. |
| `run_all.py` | Runs every stage above, in dependency order. |
| `run_downstream.py` | Re-runs only the stages affected by an admin-approved data override (fast). |
| `run_downstream_facilities.py` | Re-runs only the stages affected by a facility add/delete (fast). |
| `load_and_style.py` | Fallback: build the QGIS project from inside QGIS's own Python console. |

**`scripts/lib/`** holds the shared pure-logic modules the numbered scripts import: `local_db.py` (the bundled-database engine — see [Database integration](#database-integration)), `routing.py` (the travel-time router), `terrain.py`/`geo_utils.py`/`landcover.py` (shared geometry/terrain/land-cover helpers), `districts.py` (canonical district-name normalization across all data sources), `facility_readiness.py` (the WHO SARA readiness framework reference data), `qgis_render.py` (renders the QGIS project to PNG for the bot's `/map`), `shp_writer.py`/`pdf_tables.py`/`http_utils.py`/`dashboard_data.py`/`dashboard_assets.py` (format-specific I/O helpers), and `custom_tables.py`/`supplemental_records.py` (report-build-side readers for the two admin-editable data types).

</details>

<details>
<summary><strong>data/</strong>, <strong>gis/</strong>, <strong>report/</strong> — inputs and generated outputs (click to expand)</summary>

- **`data/raw/`** — the untouched source data each fetch stage downloads: government PDFs (`kp_development_statistics_2024/2025.pdf`, `pbs_kp_census_2023.pdf`), OSM extracts (`osm_facilities.json`, `osm_settlements.json`), the province/district boundary GeoJSON (`pak_admin2.geojson`), raw KPHCC/Marham facility listings, and 35 per-district census JSON files under `census_districts/`. (`osm_roads.json` — the full KP road network, ~220MB — is excluded here for GitHub's 100MB file limit; regenerate it with `python scripts/06_fetch_roads_osm.py`.)
- **`data/processed/`** — every pipeline stage's output: `district_metrics.csv` (the final per-district table), `facilities_merged.csv` (the deduplicated ~1,600-facility list), `boundaries.json`, `suggested_sites.csv`, `district_travel_time.csv`, `district_terrain.csv`, `district_landcover.csv`/`landcover_composition.csv`, five `dev_stats_*.csv` files, `facility_cross_validation.csv`, and the two geocoded-facility caches.
- **`gis/`** — the six shapefile layers (`KP_Districts`, `KP_District_Gap_Scores`, `KP_Healthcare_Facilities`, `KP_Roads`, `KP_Suggested_New_Sites`, `KP_Province_Boundary`) and the QGIS project `KP_Healthcare_Plan.qgz`. The two source rasters (`KP_DEM.tif`, `KP_LandCover.tif`, ~580MB together) aren't included — see the fetching section below.
- **`report/KP_Healthcare_Plan.html`** — the generated, self-contained planning report. Open it directly in any browser — no server required.

</details>

<details>
<summary><strong>docs/superpowers/</strong> — the AI-driven development trail (click to expand)</summary>

`specs/` (22 files) and `plans/` (22 files) — the complete, unedited design specs and implementation plans an AI agent wrote for every feature in this project, including the ones it later found real bugs in and fixed. See [The AI-driven development process](#the-ai-driven-development-process).

</details>

<details>
<summary><strong>tests/</strong> — the automated test suite (click to expand)</summary>

667 tests across the pipeline (`scripts/lib/`, numbered scripts) and the application (every `server/` module), all mocked against real database/API interfaces (no test ever touches a real database, AI provider, or Telegram server) — see [Testing](#testing).

</details>

**Top level:** `requirements.txt` (Python dependencies), `Start Dashboard.bat` (one-click Windows launcher — starts the server, waits for it to come up, opens the dashboard in your browser), `LICENSE`, `.gitignore`.

---

## Getting started

**Prerequisites:**
- Python 3.12+
- [PostgreSQL 16](https://www.postgresql.org/download/) — only the command-line binaries (`initdb`, `pg_ctl`) are needed; the app bootstraps and manages its own private database instance, you don't need to run or configure a Postgres server yourself. **Windows note:** `scripts/lib/local_db.py` currently has the binary path hardcoded to `C:\Program Files\PostgreSQL\16\bin` (line ~132, `PG_BIN`) — on macOS/Linux, change this to your own `pg_ctl`/`initdb` location (typically `/usr/lib/postgresql/16/bin` or wherever your package manager installed it).
- [QGIS](https://qgis.org/) (optional) — only needed to open `gis/KP_Healthcare_Plan.qgz` interactively, or to use the Telegram bot's `/map` command.

**Install:**

```bash
pip install -r requirements.txt
playwright install chromium   # one-time, needed for PDF report export
```

**Run the data pipeline** (optional — `data/processed/`, `gis/`, and `report/` already ship with real, pre-computed output; only re-run this if you want to regenerate everything from scratch, or after fetching the DEM/land-cover rasters):

```bash
python scripts/run_all.py
```

**Run the app:**

```bash
python -m server
# then open http://127.0.0.1:8420 in your browser
```

On Windows, `Start Dashboard.bat` does all of this automatically — starts the server in its own console window, waits for it to respond, and opens your default browser to the dashboard.

The **first time** you open `/admin`, you'll be asked to set an admin password (stored as a salted hash in the OS keyring, never in a file). Everything else — AI provider keys, the Telegram bot token, external database credentials — is configured from inside the admin panel afterward, also stored only in the OS keyring.

---

## Database integration

This app is built around one philosophy: **one centralized, properly-typed database, accessible generically from every surface** — not a scattered collection of CSV files with bespoke per-feature code.

### The bundled local database (default, zero setup)

`scripts/lib/local_db.py` bootstraps and owns a completely private PostgreSQL instance on first run — its own data directory (`data/pgdata/`, gitignored), its own port (`5544`, distinct from any Postgres you might already have installed), its own generated credentials (stored in the OS keyring). You never need to create a database, run a migration, or manage a connection string — `local_db.ensure_running()` handles all of it, called automatically by the server's own startup.

Three kinds of tables live in it:
1. **Admin-overlay tables** (`supplemental_records`, `metric_overrides`, `bot_facilities`) — data entered through the admin panel or Telegram bot, persisted directly, no source file.
2. **Custom Data Tables** (`custom_tables`/`custom_table_columns` registry + one real dynamic table per admin-created table) — the admin panel's "Custom Data Tables" section lets you define a brand-new table (explicit form, or an AI-proposed schema from a document) and the pipeline's report generation will incorporate its data automatically.
3. **`pipeline_*` tables** — a properly-typed, queryable mirror of *every* file in `data/processed/`, refreshed by `scripts/25_sync_processed_to_db.py` at the end of every pipeline run. The pipeline itself still reads/writes plain CSV/JSON files (simpler, and geospatial libraries like `rasterio`/`geopandas` want files) — this stage just also loads the *results* into real typed Postgres columns (`NUMERIC`, `DATE`, `BOOLEAN`, `JSONB` for geometry) so they're centrally queryable. **Note:** editing a `pipeline_*` row is allowed but will be silently overwritten the next time the pipeline reruns — these are computed outputs, not primary data.

### Accessing the database

Every table above — all three kinds — is automatically visible and editable from:
- **Admin panel → "Database Browser"**: pick a table from a dropdown, see every row, edit any cell inline.
- **Telegram**: `/localtables` (list every table), `/localview <table>` (see its rows), `/localedit <table> <row#>` (edit a value).

This works generically because `db_browser.py` queries Postgres's own `information_schema` directly — a new table (from a pipeline change or an admin-created Custom Data Table) appears automatically, with no new code required for either surface.

### Connecting to an external database instead

The admin panel's **"Database Ingestion"** section (and the bot's `/dbconnect`/`/dbtables`/`/dbpreview`/`/dbingest`) let you connect to a *separate*, external PostgreSQL database — e.g. a real hospital records system — browse its tables read-only, and use AI to extract structured supplemental data from a preview of its content. This is deliberately kept read-only and separate from the bundled database above; credentials you enter here are stored in the OS keyring, same as everything else.

---

## Telegram bot integration

1. Message [@BotFather](https://t.me/BotFather) on Telegram, run `/newbot`, and follow the prompts to get a bot token (looks like `123456789:ABC-DEF...`).
2. Find your own numeric Telegram user ID (message [@userinfobot](https://t.me/userinfobot) to get it) — this app only ever responds to one allowlisted user, by design.
3. Open the admin panel → **"Telegram Bot"** section, paste in the token and your user ID, save. The bot connects via long-polling — **no public URL, webhook, or port-forwarding needed**, it works identically on a laptop behind a home router as it would on a public server.

Once connected, the bot mirrors almost the entire admin panel as chat commands:

| Command | Does |
|---|---|
| `/report` | Download the current PDF report. |
| `/map` | Render the current map with all layers (requires a real QGIS install). |
| `/ask <question>` | Ask the AI about the current data. |
| `/keys`, `/setkey <provider> <key>` | List/set an AI provider key. |
| `/addpoint` | Add a new facility (guided). |
| `/addrecord` | Extract a document (photo/file) and add it to the report. |
| `/supplemental`, `/overrides`, `/facilities` | List/delete admin-entered records. |
| `/override` | Apply a new pipeline-data override (e.g. correct a population figure). |
| `/tables [name]`, `/newtable`, `/addrow <table>` | Manage Custom Data Tables. |
| `/dbconnect`, `/dbtables`, `/dbpreview <table>`, `/dbingest <table>` | External database ingestion. |
| `/localtables`, `/localview <table>`, `/localedit <table> <row#>` | Browse/edit the bundled database directly. |
| `/cancel` | Cancel an in-progress guided command. |

---

## AI provider integration

Five providers are supported out of the box — **Anthropic (Claude), OpenAI, Google Gemini, xAI Grok, and Groq** — configurable from the admin panel's key-management section or via the bot's `/setkey <provider> <key>`. Every key is validated with a real lightweight test call before being saved, and stored only in the OS keyring.

AI is used for exactly four things in this app, always producing bounded, validated structured output — **never freeform HTML or raw database writes**:
1. **Document extraction** — turning an uploaded PDF/DOCX/image into a structured record (equipment note, facility readiness, a Custom Data Table row).
2. **Schema inference** — proposing a table structure from a natural-language description (always shown for review before creation).
3. **Report placement** — deciding where a newly added Custom Data Table's section should go in the report, and writing its narrative (validated against a fixed allowlist of real report anchors).
4. **"Ask AI"** — answering a planning question grounded only in a compact digest of the real data (never invents facility counts or figures it doesn't have).

---

## Fetching and clipping the DEM / land-cover rasters

Two source rasters aren't included in this repository (580MB combined, over GitHub's size-friendly range) but are fully reproducible with one command each:

```bash
python scripts/15_fetch_dem.py        # -> gis/KP_DEM.tif      (~440MB, ~30m resolution)
python scripts/23_fetch_landcover.py  # -> gis/KP_LandCover.tif (~140MB, 10m resolution)
```

**How this actually works** (both scripts follow the identical pattern): rather than downloading a huge globally-tiled dataset and clipping it locally, they open the *specific* publicly-hosted [Cloud-Optimized GeoTIFF (COG)](https://www.cogeo.org/) tiles covering KP's bounding box directly over HTTPS, via GDAL's `/vsicurl/` virtual filesystem — this only ever downloads the byte ranges actually needed for the province's extent, not the full tiles. The tiles are mosaicked (`rasterio.merge.merge`) and then clipped to the exact province polygon from `data/processed/boundaries.json` via `rasterio.mask.mask` — the same "clip to a vector mask" operation QGIS's own **Raster → Extraction → Clip Raster by Mask Layer** tool performs, just scripted.

**Data sources, if you want to explore or download tiles yourself:**
- **Copernicus DEM GLO-30** (elevation, ~30m): public domain, no signup, hosted on the [AWS Open Data Registry](https://registry.opendata.aws/copernicus-dem/). Individual tiles follow the pattern `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N{lat}_00_E{lon}_00_DEM/Copernicus_DSM_COG_10_N{lat}_00_E{lon}_00_DEM.tif` — KP spans roughly 31–37°N, 69–75°E.
- **ESA WorldCover 2021 v200** (land cover, 10m, 11 classes): public domain, no signup — official site [esa-worldcover.org](https://esa-worldcover.org/en), also mirrored on the [AWS Open Data Registry](https://registry.opendata.aws/esa-worldcover-vito/). Tiles follow a 3°×3° grid: `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N{lat}E{lon}_Map.tif`.

**Manual alternative (no Python needed):** download the relevant tiles above directly, open them in QGIS alongside `gis/KP_Province_Boundary.shp`, and use **Raster → Extraction → Clip Raster by Mask Layer** (or, from the command line, `gdalwarp -cutline gis/KP_Province_Boundary.shp -crop_to_cutline input_tile.tif output_clipped.tif`).

---

## Data sources & attribution

| Source | Used for | Link |
|---|---|---|
| Pakistan Bureau of Statistics, 2023 Digital Census | District population | [census23.pbos.gov.pk](https://census23.pbos.gov.pk) |
| HDX / OCHA Pakistan Common Operational Datasets | Province/district boundaries | [data.humdata.org](https://data.humdata.org) |
| KP Health Care Commission | Licensed facility registry | Public KPHCC facility directory |
| OpenStreetMap | Crowd-sourced facilities, roads, settlements | [openstreetmap.org](https://www.openstreetmap.org), via the Overpass API |
| Marham.pk | Commercial healthcare directory (supplementary facilities) | [marham.pk](https://www.marham.pk) |
| KP Bureau of Statistics, Development Statistics of Khyber Pakhtunkhwa 2024/2025 | Health institution/bed/staffing, roads, budget | Government of KP publication |
| Copernicus DEM GLO-30 (ESA/Sinergise) | Elevation, terrain difficulty | [registry.opendata.aws/copernicus-dem](https://registry.opendata.aws/copernicus-dem/) |
| ESA WorldCover 2021 v200 (ESA/VITO) | Land cover, site-suggestion filtering | [esa-worldcover.org](https://esa-worldcover.org/en) |

All of the above are public datasets, used here for demonstration/portfolio purposes. See the note at the bottom of [`LICENSE`](LICENSE).

---

## The AI-driven development process

Every feature in this codebase — from the initial GIS pipeline to the Telegram bot to the database-centralization work that produced this very README — was built by an AI agent (Claude) working through a consistent, disciplined cycle, and the entire trail is preserved in [`docs/superpowers/`](docs/superpowers/):

1. **Brainstorm** — classify the request's scope, ask clarifying questions, propose approaches with trade-offs.
2. **Spec** — write a design document (`docs/superpowers/specs/`), self-review it for gaps/contradictions, get it approved.
3. **Plan** — turn the spec into a bite-sized, test-driven implementation plan (`docs/superpowers/plans/`).
4. **Build** — strict TDD (failing test → implementation → passing test) for every task, committed incrementally.
5. **Verify** — run the *real* pipeline against *real* data, drive the *real* admin panel and Telegram bot, not just mocked unit tests.

That last step is where this project's most interesting bugs were actually found — not by code review, but by genuinely running the thing. A few examples preserved in the plan documents: a Windows-specific subprocess hang that looked like a broken database but wasn't; a severe routing-performance cliff that only showed up at KP's real road-network scale; and, during the database-centralization work, the discovery that the bundled database's entire character encoding had been silently wrong since the day it was first created — caught only because a live sync run against real, non-English facility names failed outright, and (as documented in `docs/superpowers/plans/2026-08-17-processed-data-db-sync.md`) fixed at the root rather than worked around.

---

## Testing

```bash
pytest -q
```

667 tests, all mocked against real interfaces (database cursors, AI HTTP calls, Telegram's async API) — no test ever touches a live database, a real AI provider, or a real Telegram server. Live/manual verification against the real running app is treated as a separate, deliberate step (see above), not a substitute for automated coverage.

---

## Known limitations & honest caveats

- **Windows-first.** `PG_BIN` in `scripts/lib/local_db.py` is hardcoded to a Windows PostgreSQL install path; the Telegram bot's `/map` command shells out to a QGIS-bundled `python-qgis.bat`, which is Windows-specific as written. Both are straightforward to adapt for macOS/Linux (different binary paths, different QGIS Python entry point) but haven't been generalized.
- **QGIS is optional but required for `/map`.** The rest of the app (dashboard, report, admin panel, every other bot command) has no QGIS dependency at all.
- **This is a planning/demonstration tool**, not an official government system — treat gap scores and site suggestions as illustrative of the *methodology*, not a substitute for expert review before any real infrastructure decision.
- **Data currency**: `data/processed/` ships with real output from the last time the pipeline was run — re-run `python scripts/run_all.py` for fresh source data.

---

## License

MIT — see [`LICENSE`](LICENSE). The bundled third-party datasets remain subject to their own original sources' terms; see the note at the end of the LICENSE file.
