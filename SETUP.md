# Complete Setup Guide

A single, ordered, numbered walkthrough for getting this app **fully functional on your own computer** — from a bare machine to a running dashboard, admin panel, populated database, and (optionally) a live Telegram bot. Follow the steps in order; each one says exactly what to run and what you should see.

> This repository already ships with real, pre-computed data (`data/processed/`, `gis/*.shp`, `report/KP_Healthcare_Plan.html`) — so you can see a fully working app (Steps 1–9) without re-downloading anything. Steps 10–11 (fetching the DEM/land-cover rasters and re-running the full pipeline) are only needed if you want to regenerate everything from the original public sources yourself.

For a higher-level tour of what each file/folder is, see [`README.md`](README.md). This file is the linear "do this, then this" checklist.

---

## Part 1 — Install prerequisites

1. **Install Python 3.12 or newer.** Download from [python.org/downloads](https://www.python.org/downloads/). Confirm it worked:
   ```bash
   python --version
   ```

2. **Install PostgreSQL 16** (only the server binaries — you do *not* need to create a database, set a password, or configure anything by hand). Download from [postgresql.org/download](https://www.postgresql.org/download/). This app bootstraps and runs its own **private, self-contained** Postgres instance on top of these binaries — it never touches any Postgres server you might already have running.
   - **Windows:** the installer defaults to `C:\Program Files\PostgreSQL\16\bin` — that's what this project expects out of the box.
   - **macOS/Linux:** installed binaries usually land somewhere like `/usr/lib/postgresql/16/bin` or `/opt/homebrew/opt/postgresql@16/bin`. Open `scripts/lib/local_db.py`, find the line `PG_BIN = Path(r"C:\Program Files\PostgreSQL\16\bin")` (around line 132), and change it to your actual path. This is the one code change needed to run this project outside Windows.

3. **(Optional) Install QGIS** — only needed if you want to open `gis/KP_Healthcare_Plan.qgz` yourself, or use the Telegram bot's `/map` command. Download from [qgis.org](https://qgis.org/download/). Everything else in this app (dashboard, report, admin panel, every other bot command) works with no QGIS install at all.

4. **Get the code onto your machine.** If you're reading this file, you already have it — otherwise, clone or download the repository and open a terminal inside its folder.

5. **Install the Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

6. **Install Playwright's browser binary** (one-time, needed only for the "Download PDF" report-export feature):
   ```bash
   playwright install chromium
   ```

---

## Part 2 — Start the app and bootstrap the database

7. **Start the server:**
   ```bash
   python -m server
   ```
   On Windows, you can instead double-click **`Start Dashboard.bat`**, which does this same step, waits for the server to respond, and opens your browser automatically — then skip to Step 9.

   **What actually happens here, in order** (this is the "make the database install and functional" part): the very first time this runs, it calls `local_db.ensure_running()`, which:
   - Runs `initdb` to create a brand-new, private PostgreSQL data directory at `data/pgdata/` (gitignored — this is pure local runtime state, never committed), with UTF-8 encoding and a randomly generated password stored in your OS's own secret storage (Windows Credential Manager / macOS Keychain / Linux Secret Service) — never in a file.
   - Starts that private Postgres instance on port `5544` (deliberately different from any Postgres you might already have installed, so nothing conflicts).
   - Creates the `kp_healthcare` database and its schema (the tables `supplemental_records`, `metric_overrides`, `bot_facilities`, `custom_tables`, `custom_table_columns` — all empty on a fresh install, since this is a fresh clone with no prior admin-entered data).

   You should see `Uvicorn running on http://127.0.0.1:8420` in the terminal, with no errors above it. **Leave this terminal window open** — closing it stops the server (and, per the note in [Troubleshooting](#troubleshooting), doesn't always stop the database cleanly along with it).

8. **Populate the database with the real, already-included data.** In a *second* terminal (leave the server running in the first one), run:
   ```bash
   python scripts/25_sync_processed_to_db.py
   ```
   This is the step that actually "populates the database with data": it reads every file already sitting in `data/processed/` (real KP district metrics, ~1,600 merged healthcare facilities, population, terrain, land cover, travel time, development statistics — computed ahead of time and shipped with this repo) and loads it into 19 properly-typed tables in the database you just bootstrapped in Step 7, prefixed `pipeline_*`. You should see 19 lines like `Synced pipeline_facilities: 1584 rows`, ending in `=== Processed data sync complete ===`.

   **Verify it worked** — either open `http://127.0.0.1:8420/admin` (once you've done Step 9 below) and check the "Database Browser" section, or from the command line:
   ```bash
   "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -p 5544 -U kp_admin -d kp_healthcare -c "\dt pipeline_*"
   ```
   (adjust the `psql` path for your OS/install location, same as Step 2). It'll prompt for a password — get it with:
   ```bash
   python -c "import keyring; print(keyring.get_password('kp-healthcare-plan', 'local_db_password'))"
   ```
   You should see 19 rows listed, all named `pipeline_*`.

9. **Open the dashboard**: go to `http://127.0.0.1:8420` in your browser. You should see the full interactive planning dashboard, already populated with real data — no further setup needed to look around.

---

## Part 3 — First-time admin setup

10. **Open the admin panel**: go to `http://127.0.0.1:8420/admin`. The very first visit shows a one-time setup form (not a login) — choose a password (at least 8 characters) and confirm it. This gets stored as a salted hash in your OS's secret storage, never as plain text anywhere. Every future visit to `/admin` will ask for this password to log in.

11. **You're now fully functional** — the dashboard, the report, and every admin-panel data-management section (Supplemental Records, Pipeline Overrides, Bot-Added Facilities, Custom Data Tables, and the **Database Browser**, which lets you view/edit any of the 19 `pipeline_*` tables you just populated, or any other table in the database) all work right now, with zero further configuration. The remaining steps below (AI keys, Telegram, regenerating raw data) are all **optional add-ons**, not required for the app to work.

---

## Part 4 (optional) — Enable AI features

The dashboard's "Ask AI" chat, AI-powered document upload/extraction, and a few admin conveniences need at least one AI provider key configured. Without one, everything else in the app still works — you'll just see a clear "no provider configured" message on the AI-specific features.

12. Get a free/low-cost API key from any **one** of these five providers (you only need one to start):
    - [Anthropic (Claude)](https://console.anthropic.com/settings/keys)
    - [OpenAI](https://platform.openai.com/api-keys)
    - [Google Gemini](https://aistudio.google.com/apikey)
    - [xAI (Grok)](https://console.x.ai/)
    - [Groq](https://console.groq.com/keys) — has a generous free tier, a good first choice for trying this out.

13. In the admin panel, find the key-management section (5 rows, one per provider), paste your key into the matching row, and click **Test** then **Save**. It's validated with a real lightweight call to that provider before being stored — again, only in your OS's secret storage, never in a file.

---

## Part 5 (optional) — Enable the Telegram bot

14. Message [**@BotFather**](https://t.me/BotFather) on Telegram, send `/newbot`, follow its prompts (choose a name and a username ending in `bot`), and copy the token it gives you — a string that looks like `123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`.

15. Message [**@userinfobot**](https://t.me/userinfobot) to get your own numeric Telegram user ID (this app only ever responds to one allow-listed user, by design — nobody else can use your bot even if they find it).

16. In the admin panel's **"Telegram Bot"** section, paste in the token and your user ID, and save. The bot connects immediately via long-polling — no public URL, port-forwarding, or webhook setup needed, it works the same whether you're on a laptop behind a home router or a public server.

17. Open a chat with your new bot in Telegram and send `/start` — you should get a welcome message listing every available command. Try `/localtables` to see it list the same 19 `pipeline_*` tables (plus the others) you populated in Step 8, or `/ask <a planning question>` if you configured an AI key in Part 4.

---

## Part 6 (optional) — Regenerate everything from scratch, including the DEM and land-cover rasters

Skip this entire part if you're happy with the real, already-included data from Steps 1–9. This is only for regenerating the full pipeline yourself from the original public sources.

### Where to download the DEM and land-cover data

Both rasters are pulled from public, free, no-signup-required cloud data registries — you don't need an account anywhere for this.

18. **DEM (elevation) — Copernicus GLO-30**, ~30m resolution, public domain, hosted on the [AWS Open Data Registry](https://registry.opendata.aws/copernicus-dem/). You can browse/download individual tiles directly at `https://copernicus-dem-30m.s3.amazonaws.com/` (tile naming pattern: `Copernicus_DSM_COG_10_N{lat}_00_E{lon}_00_DEM`), but the easiest way is to let this project fetch and clip exactly what it needs automatically:
    ```bash
    python scripts/15_fetch_dem.py
    ```
    This opens the ~6 tiles covering Khyber Pakhtunkhwa's bounding box (31–37°N, 69–75°E) directly over HTTPS — only the byte ranges actually needed, not full downloads — mosaics them, and clips the result to the exact province boundary polygon, writing `gis/KP_DEM.tif` (~440MB).

19. **Land cover — ESA WorldCover 2021 v200**, 10m resolution, 11 classes, public domain, official site [esa-worldcover.org](https://esa-worldcover.org/en), also mirrored on the [AWS Open Data Registry](https://registry.opendata.aws/esa-worldcover-vito/) (tile pattern: `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N{lat}E{lon}_Map.tif`, on a 3°×3° grid). Same automated approach:
    ```bash
    python scripts/23_fetch_landcover.py
    ```
    Writes `gis/KP_LandCover.tif` (~140MB).

    **Prefer to download and clip manually instead?** Grab the relevant tiles from either link above, open them in QGIS alongside `gis/KP_Province_Boundary.shp`, and use **Raster → Extraction → Clip Raster by Mask Layer** — or from the command line: `gdalwarp -cutline gis/KP_Province_Boundary.shp -crop_to_cutline downloaded_tile.tif clipped_output.tif`.

### The other raw data sources (already included, listed here for reference/re-fetching)

| Data | Source | Re-fetch with |
|---|---|---|
| District population | Pakistan Bureau of Statistics, 2023 Digital Census — [census23.pbos.gov.pk](https://census23.pbos.gov.pk) | `python scripts/02_compile_population.py` |
| Province/district boundaries | HDX/OCHA Pakistan — [data.humdata.org](https://data.humdata.org) | `python scripts/01_fetch_boundaries.py` |
| Licensed facilities | KP Health Care Commission public registry | `python scripts/03_fetch_facilities_kphcc.py` |
| Crowd-sourced facilities/roads | OpenStreetMap, via Overpass — [openstreetmap.org](https://www.openstreetmap.org) | `python scripts/05_fetch_facilities_osm.py`, `python scripts/06_fetch_roads_osm.py` |
| Additional facility listings | Marham.pk — [marham.pk](https://www.marham.pk) | `python scripts/21_fetch_facilities_marham.py` |
| Health/roads/budget statistics | KP Bureau of Statistics, Development Statistics of Khyber Pakhtunkhwa 2024/2025 (government PDF, already included in `data/raw/`) | `python scripts/17_extract_devstats_health.py`, `18_...roads.py`, `19_...budget.py` |

### Run the whole pipeline

20. Once the two rasters exist (Steps 18–19), regenerate the entire dataset from scratch — every processed CSV, every shapefile, the QGIS project, the HTML report, **and** the database sync (this runs `scripts/25_sync_processed_to_db.py` automatically as its very last step, so make sure your server from Step 7 is still running first):
    ```bash
    python scripts/run_all.py
    ```
    This runs all 25 pipeline stages in dependency order and prints `=== Pipeline complete ===` at the end. Expect this to take a while — the road-network fetch and the routing computation over KP's full real network are the slowest steps (tens of minutes), everything else is fast.

21. Refresh `http://127.0.0.1:8420` — the dashboard now reflects your freshly regenerated data.

---

## Verifying everything

22. Run the automated test suite (667 tests, all mocked — none of them touch your real database, AI provider, or Telegram bot, so this is safe to run any time):
    ```bash
    pytest -q
    ```
    Expect `667 passed`.

---

## Troubleshooting

- **"Failed to start the local database" / port 5544 already in use.** Something else is already using that port, or a previous run's Postgres process is still alive. Stop it: `python -c "from scripts.lib import local_db; local_db.stop()"`, then try Step 7 again.
- **Closed the server's terminal window and now the database seems stuck running.** This is expected — force-closing the server's console doesn't run its graceful shutdown, so the bundled Postgres can keep running in the background even after the app itself has stopped. Run the same `local_db.stop()` command above to clean it up, or just leave it running and restart the server normally (`ensure_running()` will detect it's already up).
- **`psql`/`initdb`/`pg_ctl` not found, or wrong-looking errors from `local_db.py`.** Double-check the `PG_BIN` path in `scripts/lib/local_db.py` (Step 2) actually points at your real PostgreSQL 16 `bin/` folder.
- **AI features say "no provider configured" even after saving a key.** Make sure you clicked **Test** and it showed success before **Save** — an invalid key is rejected before it's ever stored.
- **Telegram bot doesn't respond.** Confirm the token and your numeric user ID were saved correctly in the admin panel's Telegram Bot section, and that your machine has outbound internet access to `api.telegram.org` (no proxy/firewall blocking it) — the bot uses long-polling, so it needs to reach Telegram's servers, not the other way around.
