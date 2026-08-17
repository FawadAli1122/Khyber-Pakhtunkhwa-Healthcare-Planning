# Telegram Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the admin interact with the KP Healthcare Plan dashboard from Telegram — view the report/map, ask the AI, manage API keys, and add a new facility from the field — via a single-user-allowlisted bot running alongside the existing admin server.

**Architecture:** A new `server/telegram_bot.py` module runs a `python-telegram-bot` `Application` in long-polling mode as a background component of the same asyncio event loop `python -m server` already runs (no webhook, no public URL). Every handler is a thin wrapper: authorize, then call existing server-side logic (`pdf_export`, `report_context`, `ai_client`, `keystore`) or narrowly new logic (`bot_facilities.py`, `qgis_render.py`, `run_downstream_facilities.py`). A new append-only facility overlay (`bot_facilities.csv`) becomes a fourth source in `07_merge_facilities.py`, alongside KPHCC/OSM/Marham. Map rendering runs PyQGIS as a subprocess through QGIS's own bundled Python interpreter, since PyQGIS cannot be imported into the regular server process at all.

**Tech Stack:** Python 3.12, FastAPI, `python-telegram-bot` v20+ (new dependency — this project tracks no dependency manifest file; installed ad-hoc like every other library here), PyQGIS (via `python-qgis.bat`, never imported into the regular environment), shapely, pytest + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-08-16-telegram-connector-design.md`

## Global Constraints

- Single allowlisted Telegram user id, checked on every handler via `_authorized(update)`. Unauthorized senders get a fixed "Not authorized." reply, nothing else.
- The bot's own token and allowed user id live in `keystore.py` (OS credential store), settable **only** via the admin panel. AI provider keys are settable via **both** the admin panel and the bot's `/setkey` command.
- `/addpoint` validates a shared location against **both** `KP_BBOX` and the real KP province polygon (not the bbox alone) before writing anything — see `scripts/07_merge_facilities.py`'s own documented reason the bbox alone isn't sufficient.
- PyQGIS (`scripts/lib/qgis_render.py`) runs only via `C:\Program Files\QGIS 4.0.0\bin\python-qgis.bat`, invoked as a subprocess — never imported into `server/`'s regular Python environment. No automated pytest coverage for it (matches `13b_build_qgis_project_pyqgis.py`'s own precedent — verified: no existing test imports `qgis`).
- Every Telegram API call is mocked in automated tests. Live verification needs a real bot token (via @BotFather) and the user's real Telegram user id, configured through the admin panel.
- Any subprocess call from inside a bot handler (rebuild scripts, PyQGIS render) uses `await asyncio.to_thread(subprocess.run, ...)` so it never blocks the bot's single asyncio event loop while it runs.
- New routes/UI reuse this project's established patterns exactly: `_require_auth` session-cookie auth for admin routes, the `initRecordsTable`/`renderRecordRow`/`showEmptyRow` JS helpers already built for Manage Records, and the `rebuild_warning`-in-a-200-response pattern for subprocess failures.

---

### Task 1: `server/keystore.py` — Telegram config storage

**Files:**
- Modify: `server/keystore.py`
- Test: `tests/server/test_keystore.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `keystore.get_telegram_config() -> dict | None` (`{"token": str, "allowed_user_id": str}`), `keystore.set_telegram_config(config)`, `keystore.delete_telegram_config()` — consumed by Task 6 (bot module) and Task 12 (admin routes).

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_keystore.py`:

```python
TELEGRAM_CONFIG = {"token": "123456:ABC-DEF", "allowed_user_id": "987654321"}


def test_telegram_config_roundtrip(fake_store):
    assert keystore.get_telegram_config() is None
    keystore.set_telegram_config(TELEGRAM_CONFIG)
    assert keystore.get_telegram_config() == TELEGRAM_CONFIG


def test_set_telegram_config_overwrites_previous(fake_store):
    keystore.set_telegram_config(TELEGRAM_CONFIG)
    other = dict(TELEGRAM_CONFIG, token="999999:XYZ")
    keystore.set_telegram_config(other)
    assert keystore.get_telegram_config() == other


def test_delete_telegram_config_removes_it(fake_store):
    keystore.set_telegram_config(TELEGRAM_CONFIG)
    keystore.delete_telegram_config()
    assert keystore.get_telegram_config() is None


def test_delete_telegram_config_missing_is_a_noop(fake_store):
    keystore.delete_telegram_config()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_keystore.py -v`
Expected: the 4 new tests FAIL (`AttributeError: module 'server.keystore' has no attribute 'get_telegram_config'`).

- [ ] **Step 3: Implement**

In `server/keystore.py`, add near `DB_CONNECTION_KEY`:

```python
TELEGRAM_CONFIG_KEY = "telegram_config"
```

Add at the end of the file, mirroring `get_db_connection`/`set_db_connection`/`delete_db_connection` exactly:

```python
def get_telegram_config():
    raw = keyring.get_password(SERVICE_NAME, TELEGRAM_CONFIG_KEY)
    if raw is None:
        return None
    return json.loads(raw)


def set_telegram_config(config):
    keyring.set_password(SERVICE_NAME, TELEGRAM_CONFIG_KEY, json.dumps(config))


def delete_telegram_config():
    try:
        keyring.delete_password(SERVICE_NAME, TELEGRAM_CONFIG_KEY)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent - deleting a non-existent entry is a no-op
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_keystore.py -v`
Expected: PASS (all tests, including every pre-existing one).

- [ ] **Step 5: Commit**

```bash
git add server/keystore.py tests/server/test_keystore.py
git commit -m "feat: add Telegram bot config storage to keystore

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `server/bot_facilities.py` — new facility overlay store

**Files:**
- Create: `server/bot_facilities.py`
- Test: `tests/server/test_bot_facilities.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `bot_facilities.load_records(path=RECORDS_PATH)`, `bot_facilities.delete_record(record_id, path=RECORDS_PATH) -> bool`, `bot_facilities.add_facility(name, district, lat, lon, category, added_by) -> dict` — consumed by Task 10 (`/addpoint`) and Task 12 (admin routes).

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_bot_facilities.py`:

```python
"""Unit tests for server/bot_facilities.py. Same id/backfill/delete_record
shape as server/supplemental_data.py and server/metric_overrides.py - see
docs/superpowers/specs/2026-08-16-manage-records-design.md and
docs/superpowers/specs/2026-08-16-telegram-connector-design.md section 8.
"""
from server import bot_facilities


def test_append_and_load_records_round_trip(tmp_path):
    path = tmp_path / "bot_facilities.csv"
    bot_facilities.append_records(
        [{"id": "aaa111", "name": "Field Clinic", "district": "Peshawar", "lat": "34.01",
          "lon": "71.58", "category": "Clinic", "added_at": "2026-08-16T00:00:00+00:00",
          "added_by": "555"}],
        path=path,
    )
    records = bot_facilities.load_records(path=path)
    assert len(records) == 1
    assert records[0]["name"] == "Field Clinic"
    assert records[0]["district"] == "Peshawar"


def test_load_records_returns_empty_list_when_file_missing(tmp_path):
    path = tmp_path / "does_not_exist.csv"
    assert bot_facilities.load_records(path=path) == []


def test_load_records_backfills_missing_ids_and_persists(tmp_path):
    path = tmp_path / "bot_facilities.csv"
    path.write_text(
        "name,district,lat,lon,category,added_at,added_by\n"
        "Field Clinic,Peshawar,34.01,71.58,Clinic,2026-08-16T00:00:00+00:00,555\n",
        encoding="utf-8",
    )
    first_load = bot_facilities.load_records(path=path)
    assert len(first_load) == 1
    assert first_load[0]["id"]
    second_load = bot_facilities.load_records(path=path)
    assert second_load[0]["id"] == first_load[0]["id"]


def test_delete_record_removes_only_matching_row(tmp_path):
    path = tmp_path / "bot_facilities.csv"
    bot_facilities.append_records(
        [
            {"id": "aaa111", "name": "Field Clinic", "district": "Peshawar", "lat": "34.01",
             "lon": "71.58", "category": "Clinic", "added_at": "2026-08-16T00:00:00+00:00", "added_by": "555"},
            {"id": "bbb222", "name": "Rural Post", "district": "Chitral", "lat": "35.85",
             "lon": "71.78", "category": "Facility", "added_at": "2026-08-16T00:01:00+00:00", "added_by": "555"},
        ],
        path=path,
    )
    deleted = bot_facilities.delete_record("aaa111", path=path)
    assert deleted is True
    remaining = bot_facilities.load_records(path=path)
    assert len(remaining) == 1
    assert remaining[0]["id"] == "bbb222"


def test_delete_record_returns_false_for_unknown_id(tmp_path):
    path = tmp_path / "bot_facilities.csv"
    assert bot_facilities.delete_record("does-not-exist", path=path) is False


def test_add_facility_writes_a_record(tmp_path, monkeypatch):
    path = tmp_path / "bot_facilities.csv"
    monkeypatch.setattr(bot_facilities, "RECORDS_PATH", path)
    record = bot_facilities.add_facility(
        name="Field Clinic", district="Peshawar", lat=34.01, lon=71.58,
        category="Clinic", added_by="555",
    )
    assert record["name"] == "Field Clinic"
    assert record["id"]
    assert "added_at" in record
    saved = bot_facilities.load_records(path=path)
    assert len(saved) == 1
    assert saved[0]["id"] == record["id"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_bot_facilities.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'server.bot_facilities'`).

- [ ] **Step 3: Implement**

Create `server/bot_facilities.py`:

```python
"""Facilities added via the Telegram bot's /addpoint command - a fourth
facility source alongside KPHCC/OSM/Marham, merged in by
scripts/07_merge_facilities.py. Append-only overlay, same id/backfill/
delete_record shape as server/supplemental_data.py and
server/metric_overrides.py. See docs/superpowers/specs/
2026-08-16-manage-records-design.md and docs/superpowers/specs/
2026-08-16-telegram-connector-design.md section 8.
"""
import csv
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
RECORDS_PATH = PROCESSED / "bot_facilities.csv"

FIELDNAMES = ("id", "name", "district", "lat", "lon", "category", "added_at", "added_by")


def load_records(path=RECORDS_PATH):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        records = [{k: (v if v is not None else "") for k, v in row.items()} for row in csv.DictReader(f)]
    if any(not record.get("id") for record in records):
        for record in records:
            if not record.get("id"):
                record["id"] = uuid.uuid4().hex[:12]
        _write_records(records, path)
    return records


def _write_records(records, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in FIELDNAMES})


def append_records(records, path=RECORDS_PATH):
    path = Path(path)
    is_new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in FIELDNAMES})


def delete_record(record_id, path=RECORDS_PATH):
    path = Path(path)
    records = load_records(path=path)
    remaining = [r for r in records if r.get("id") != record_id]
    if len(remaining) == len(records):
        return False
    _write_records(remaining, path)
    return True


def add_facility(name, district, lat, lon, category, added_by):
    record = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "district": district,
        "lat": lat,
        "lon": lon,
        "category": category,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "added_by": added_by,
    }
    append_records([record])
    return record
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_bot_facilities.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add server/bot_facilities.py tests/server/test_bot_facilities.py
git commit -m "feat: add bot_facilities.py overlay store for Telegram-added facilities

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `scripts/07_merge_facilities.py` — bot facilities as a fourth source

**Files:**
- Modify: `scripts/07_merge_facilities.py`
- Test: `tests/test_merge_facilities.py`

**Interfaces:**
- Consumes: `data/processed/bot_facilities.csv` rows shaped like `bot_facilities.FIELDNAMES` (Task 2) — read as plain CSV dict rows here, not via the `bot_facilities` module (this script must stay runnable standalone without importing `server/`, matching the project's one-way `server/` → `scripts/lib/` import constraint).
- Produces: `merge(kphcc, osm, marham, bot, districts)` (new 5-arg signature, was 4) - consumed by Task 4's downstream pipeline indirectly via `main()`.

- [ ] **Step 1: Write the failing tests**

First, update every existing `merge_mod.merge(...)` call in `tests/test_merge_facilities.py` to insert `[]` for the new `bot` parameter before `districts`. Apply this exact replacement 8 times (once per existing call site — each occurrence has different surrounding args, so match on the trailing `, districts)` each time):

```python
# Before (8 occurrences across the file), e.g.:
merged = merge_mod.merge(kphcc, osm, [], districts)
# After:
merged = merge_mod.merge(kphcc, osm, [], [], districts)
```

The 8 existing call sites and their exact required edits:
- `test_merge_flags_close_same_name_as_duplicate`: `merge_mod.merge(kphcc, osm, [], districts)` → `merge_mod.merge(kphcc, osm, [], [], districts)`
- `test_merge_keeps_distinct_facilities_separate`: same pattern.
- `test_merge_drops_osm_records_outside_kp_entirely`: `merge_mod.merge([], osm, [], districts)` → `merge_mod.merge([], osm, [], [], districts)`
- `test_merge_keeps_osm_records_inside_any_provided_district`: same pattern as above.
- `test_merge_skips_kphcc_records_with_no_coordinates`: `merge_mod.merge(kphcc, [], [], districts)` → `merge_mod.merge(kphcc, [], [], [], districts)`
- `test_merge_adds_marham_records_with_own_source_label`: `merge_mod.merge([], [], marham, districts)` → `merge_mod.merge([], [], marham, [], districts)`
- `test_merge_flags_marham_duplicate_of_osm_even_without_kphcc`: `merge_mod.merge([], osm, marham, districts)` → `merge_mod.merge([], osm, marham, [], districts)`
- `test_merge_marham_distinct_from_kphcc_and_osm_not_flagged`: `merge_mod.merge(kphcc, [], marham, districts)` → `merge_mod.merge(kphcc, [], marham, [], districts)`

Then add new tests at the end of the file:

```python
def test_merge_adds_bot_records_with_own_source_label():
    districts = [{
        "district": "Bannu",
        "geometry": Polygon([(70.0, 32.0), (71.0, 32.0), (71.0, 33.0), (70.0, 33.0)]),
    }]
    bot = [{
        "name": "Field Clinic", "category": "Clinic", "district": "Bannu",
        "lat": "32.5", "lon": "70.5",
    }]
    merged = merge_mod.merge([], [], [], bot, districts)
    assert len(merged) == 1
    assert merged[0]["source"] == "Bot"
    assert merged[0]["is_duplicate_of"] is None
    assert merged[0]["lat"] == 32.5
    assert merged[0]["lon"] == 70.5


def test_merge_flags_bot_duplicate_of_existing_kphcc_record():
    districts = [{
        "district": "Bannu",
        "geometry": Polygon([(70.0, 32.0), (71.0, 32.0), (71.0, 33.0), (70.0, 33.0)]),
    }]
    kphcc = [{
        "name": "City Hospital", "category": "Hospital", "public_private": "Public",
        "beds": 50, "district": "Bannu", "lon": 70.5, "lat": 32.5, "geo_precision": "street",
    }]
    bot = [{
        "name": "City Hospital", "category": "Hospital", "district": "Bannu",
        "lat": "32.5001", "lon": "70.5001",
    }]
    merged = merge_mod.merge(kphcc, [], [], bot, districts)
    assert len(merged) == 2
    bot_rec = next(r for r in merged if r["source"] == "Bot")
    assert bot_rec["is_duplicate_of"] == "City Hospital"


def test_merge_drops_bot_records_outside_kp_entirely():
    districts = [{
        "district": "Abbottabad",
        "geometry": Polygon([(73.0, 34.0), (73.5, 34.0), (73.5, 34.5), (73.0, 34.5)]),
    }]
    bot = [{
        # Same safety-net case as the existing OSM out-of-KP test - a
        # bot-submitted point that somehow ended up outside the real KP
        # province polygon must be dropped, not force-assigned to the
        # nearest district, even though /addpoint already validates this
        # before writing the record (defense in depth).
        "name": "Far Away Clinic", "category": "Clinic", "district": "Abbottabad",
        "lat": "30.0", "lon": "75.0",
    }]
    merged = merge_mod.merge([], [], [], bot, districts)
    assert merged == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_merge_facilities.py -v`
Expected: every test FAILS with a `TypeError: merge() takes 4 positional arguments but 5 were given` or `missing 1 required positional argument: 'districts'` (signature mismatch - `merge()` hasn't been changed yet).

- [ ] **Step 3: Implement**

In `scripts/07_merge_facilities.py`, update the module docstring's opening line and the `merge()` signature/body. Replace:

```python
def merge(kphcc, osm, marham, districts):
```

with:

```python
def merge(kphcc, osm, marham, bot, districts):
```

Add a new loop for `bot` records right after the existing Marham loop (before the "Flag duplicates" comment block):

```python
    for r in bot:
        if r.get("lat") in (None, "") or r.get("lon") in (None, ""):
            continue  # unresolved - shouldn't happen (/addpoint always collects a real location), but matches the same guard KPHCC/Marham use
        lat, lon = float(r["lat"]), float(r["lon"])
        if province_geom is not None and not province_geom.contains(Point(lon, lat)):
            continue  # same safety net OSM/Marham get - /addpoint already validates this before writing, but never trust a single validation point
        records.append(
            {
                "name": r["name"],
                "category": r["category"],
                "public_private": "",
                "beds": None,
                "district": normalize_district(r["district"]),  # already resolved+validated at /addpoint time, like Marham's own district field
                "lat": lat,
                "lon": lon,
                "source": "Bot",
                "geo_precision": "bot_shared_location",
                "is_duplicate_of": None,
            }
        )
```

Update the comment right before the dedup loop from "Records are appended in KPHCC-then-OSM-then-Marham order" to "Records are appended in KPHCC-then-OSM-then-Marham-then-Bot order" - no code change needed there, the loop already iterates every record in `records` regardless of how many sources contributed to it.

Update `main()`:

```python
def main():
    kphcc = json.loads((PROCESSED / "kphcc_facilities_geocoded.json").read_text())
    osm = json.loads((RAW / "osm_facilities.json").read_text())
    marham = json.loads((PROCESSED / "marham_facilities_geocoded.json").read_text())
    bot_path = PROCESSED / "bot_facilities.csv"
    bot = list(csv.DictReader(bot_path.open(newline="", encoding="utf-8"))) if bot_path.exists() else []
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    districts = [{"district": d["district"], "geometry": shape(d["geometry"])} for d in boundaries["districts"]]

    merged = merge(kphcc, osm, marham, bot, districts)
```

(the rest of `main()` - the `out_path`/`fieldnames`/`writer` block and the final print - is unchanged).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_merge_facilities.py -v`
Expected: PASS (all 11 tests - 8 pre-existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add scripts/07_merge_facilities.py tests/test_merge_facilities.py
git commit -m "feat: merge bot-added facilities as a fourth source

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `scripts/run_downstream_facilities.py` — new stage runner

**Files:**
- Create: `scripts/run_downstream_facilities.py`

**Interfaces:**
- Consumes: nothing new (invokes existing numbered pipeline stages as subprocesses).
- Produces: an entry point invoked by Task 10's `/addpoint` handler and Task 12's bot-facilities delete route.

No dedicated test - matches `scripts/run_downstream.py`'s own established precedent (verified: no test file exists for it either). It's a thin stage runner, verified via Task 14's manual end-to-end pass.

- [ ] **Step 1: Create the script**

Create `scripts/run_downstream_facilities.py`:

```python
"""Re-runs the pipeline stages that depend on the merged facility set, for
use after the Telegram bot's /addpoint command (or an admin-panel delete
of a bot-added facility) changes data/processed/bot_facilities.csv - NOT
a full pipeline re-run (skips the expensive fetch/geocode/DEM stages,
same rationale as run_downstream.py, but starts one stage earlier at
07_merge_facilities.py since a new facility changes the merged set
itself, not just an overridden number). See docs/superpowers/specs/
2026-08-16-telegram-connector-design.md section 9.
"""
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
STAGES = [
    "07_merge_facilities.py",
    "07b_apply_metric_overrides.py",
    "08_compute_district_metrics.py",
    "09_gap_score_and_clusters.py",
    "10_forecast_demand.py",
    "11_suggest_new_sites.py",
    "20_cross_validate_facility_counts.py",
    "12_write_shapefiles.py",
    "13_build_qgis_project.py",
    "14_build_html_report.py",
]


def main():
    for stage in STAGES:
        print(f"=== Running {stage} ===")
        result = subprocess.run([sys.executable, str(SCRIPTS_DIR / stage)])
        if result.returncode != 0:
            print(f"Stage {stage} failed with exit code {result.returncode}; stopping.")
            sys.exit(result.returncode)
    print("=== Downstream facilities pipeline complete ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test it imports and runs its stage list correctly against real data**

Run: `python scripts/run_downstream_facilities.py`
Expected: runs all 10 stages against whatever's currently in `data/processed/bot_facilities.csv` (empty/absent at this point in the plan, so `07_merge_facilities.py` runs its existing 3-source merge unchanged) and ends with `=== Downstream facilities pipeline complete ===`, exit code 0. Confirm via `git status`/`git diff` that the regenerated outputs match what's already committed (no real facility was added yet, so nothing should actually change).

- [ ] **Step 3: Commit**

```bash
git add scripts/run_downstream_facilities.py
git commit -m "feat: add run_downstream_facilities.py stage runner

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `scripts/lib/qgis_render.py` — headless map-to-PNG rendering

**Files:**
- Create: `scripts/lib/qgis_render.py`

**Interfaces:**
- Consumes: a `.qgz` project path.
- Produces: a PNG file at a given output path — invoked as a subprocess by Task 9's `/map` handler.

No automated pytest coverage (see Global Constraints - PyQGIS cannot be imported into the regular test-running environment). Verified manually in Task 14.

- [ ] **Step 1: Create the script**

Create `scripts/lib/qgis_render.py`:

```python
"""Renders a QGIS project (.qgz) to a PNG via PyQGIS's headless map
renderer, for the Telegram bot's /map command. Must run through QGIS's
own bundled Python interpreter (Windows:
"C:\\Program Files\\QGIS 4.0.0\\bin\\python-qgis.bat" <this file> <qgz_path> <output_png_path>),
never the project's regular pure-Python environment - it imports the
qgis package, exactly like scripts/13b_build_qgis_project_pyqgis.py,
which is why this lives in scripts/lib/ as a standalone script (invoked
via subprocess) rather than an importable function despite the
directory name. See docs/superpowers/specs/
2026-08-16-telegram-connector-design.md section 10.
"""
import sys

from qgis.core import QgsApplication, QgsMapRendererParallelJob, QgsMapSettings, QgsProject, QgsRectangle
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor


def render_to_png(qgz_path, output_path, width=1600, height=1200):
    project = QgsProject.instance()
    if not project.read(qgz_path):
        raise RuntimeError(f"Failed to load project: {qgz_path}")

    layers = list(project.mapLayers().values())
    if not layers:
        raise RuntimeError("Project has no layers to render")

    full_extent = QgsRectangle()
    full_extent.setMinimal()
    for layer in layers:
        full_extent.combineExtentWith(layer.extent())

    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setBackgroundColor(QColor(255, 255, 255))
    settings.setOutputSize(QSize(width, height))
    settings.setExtent(full_extent)
    settings.setDestinationCrs(project.crs())

    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()
    if not image.save(output_path, "PNG"):
        raise RuntimeError(f"Failed to save rendered image to {output_path}")


def main():
    if len(sys.argv) != 3:
        print("Usage: python-qgis.bat qgis_render.py <qgz_path> <output_png_path>", file=sys.stderr)
        sys.exit(2)
    qgz_path, output_path = sys.argv[1], sys.argv[2]

    QgsApplication.setPrefixPath(r"C:\Program Files\QGIS 4.0.0\apps\qgis", True)
    qgs = QgsApplication([], False)
    qgs.initQgis()
    try:
        render_to_png(qgz_path, output_path)
        print(f"Wrote {output_path}")
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Manually verify it renders a real PNG**

Run: `"C:\Program Files\QGIS 4.0.0\bin\python-qgis.bat" scripts/lib/qgis_render.py "gis/KP_Healthcare_Plan.qgz" "C:\Users\DELL\AppData\Local\Temp\claude\map_test.png"`
Expected: prints `Wrote C:\Users\DELL\AppData\Local\Temp\claude\map_test.png`, exit code 0. Confirm the file exists and is a real, non-trivial PNG:

```bash
python -c "
from PIL import Image
img = Image.open(r'C:\Users\DELL\AppData\Local\Temp\claude\map_test.png')
assert img.size == (1600, 1200)
assert img.getbbox() is not None, 'image is entirely blank'
print('real, non-blank PNG at', img.size)
"
```

Expected: `real, non-blank PNG at (1600, 1200)`. Delete the temp file afterward.

- [ ] **Step 3: Commit**

```bash
git add scripts/lib/qgis_render.py
git commit -m "feat: add qgis_render.py for headless map-to-PNG rendering

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `server/telegram_bot.py` Part A — module skeleton, authorization, bot lifecycle, `/start`

**Files:**
- Create: `server/telegram_bot.py`
- Test: `tests/server/test_telegram_bot.py`

**Interfaces:**
- Consumes: `keystore.get_telegram_config()` (Task 1).
- Produces: `telegram_bot._authorized(update) -> bool`, `telegram_bot.build_application(token) -> Application`, `telegram_bot.start_bot_task() -> bool`, `telegram_bot.stop_bot_task()`, `telegram_bot.start_command(update, context)` - consumed by every later Task in this module and by Task 11 (`server/app.py`) and Task 12 (admin routes).

- [ ] **Step 1: Install the new dependency**

Run: `pip install python-telegram-bot`
Expected: installs successfully (this project has no dependency manifest file - every dependency is installed ad-hoc, matching Playwright/shapely/psycopg2/PyQGIS before it).

- [ ] **Step 2: Write the failing tests**

Create `tests/server/test_telegram_bot.py`:

```python
"""Unit tests for server/telegram_bot.py. Every Telegram API call is
mocked - no real bot, no real network call, in any test here. See
docs/superpowers/specs/2026-08-16-telegram-connector-design.md.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from server import keystore, telegram_bot

TELEGRAM_CONFIG = {"token": "123456:ABC-DEF", "allowed_user_id": "987654321"}


def _make_update(user_id=987654321):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message = AsyncMock()
    return update


def _make_context(args=None):
    context = MagicMock()
    context.args = args or []
    context.user_data = {}
    return context


def test_authorized_allows_the_allowlisted_user(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=987654321)
    assert telegram_bot._authorized(update) is True


def test_authorized_rejects_any_other_user(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111111111)
    assert telegram_bot._authorized(update) is False


def test_authorized_rejects_everyone_when_not_configured(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: None)
    update = _make_update(user_id=987654321)
    assert telegram_bot._authorized(update) is False


def test_build_application_registers_all_commands():
    application = telegram_bot.build_application("fake-token")
    handlers = application.handlers[0]
    # ConversationHandler (addpoint) has no .commands attribute of its own
    # - its /addpoint entry point lives one level down, inside
    # entry_points - so it's checked separately, by identity, rather than
    # folded into the same .commands scan as the six plain CommandHandlers.
    command_names = set()
    for handler in handlers:
        if hasattr(handler, "commands"):
            command_names |= set(handler.commands)
    assert {"start", "report", "map", "ask", "keys", "setkey"} <= command_names
    assert telegram_bot.addpoint_conversation in handlers


@pytest.mark.asyncio
async def test_start_command_authorized_sends_help(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    await telegram_bot.start_command(update, _make_context())
    update.message.reply_text.assert_awaited_once()
    assert "/report" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_start_command_unauthorized_sends_generic_rejection(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111111111)
    await telegram_bot.start_command(update, _make_context())
    update.message.reply_text.assert_awaited_once_with("Not authorized.")


@pytest.mark.asyncio
async def test_start_bot_task_returns_false_when_not_configured(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: None)
    result = await telegram_bot.start_bot_task()
    assert result is False


@pytest.mark.asyncio
async def test_start_bot_task_returns_false_on_application_failure(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)

    def failing_build(token):
        raise RuntimeError("invalid token")

    monkeypatch.setattr(telegram_bot, "build_application", failing_build)
    result = await telegram_bot.start_bot_task()
    assert result is False


@pytest.mark.asyncio
async def test_stop_bot_task_is_a_noop_when_nothing_running():
    telegram_bot._application = None
    await telegram_bot.stop_bot_task()  # must not raise
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/server/test_telegram_bot.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'server.telegram_bot'`).

- [ ] **Step 4: Implement**

Create `server/telegram_bot.py`:

```python
"""Telegram bot connector for the KP Healthcare Plan dashboard - long
polling, single-allowlisted-user auth, running as a background component
of the same asyncio event loop server/app.py's FastAPI app runs on. Every
handler is a thin wrapper: authorize, then call existing server-side
logic (pdf_export, report_context, ai_client, keystore) or narrowly new
logic (bot_facilities, qgis_render, run_downstream_facilities) - no
business logic duplicated between the web routes and the bot. See
docs/superpowers/specs/2026-08-16-telegram-connector-design.md.
"""
from telegram.ext import Application, CommandHandler

from server import keystore

HELP_TEXT = (
    "KP Healthcare Plan bot.\n\n"
    "/report - download the current PDF report\n"
    "/map - render the current map with all layers\n"
    "/ask <question> - ask the AI about the current data\n"
    "/keys - list configured AI provider keys\n"
    "/setkey <provider> <key> - set an AI provider key\n"
    "/addpoint - add a new facility (guided)\n"
    "/cancel - cancel an in-progress /addpoint"
)

_application = None


def _authorized(update):
    config = keystore.get_telegram_config()
    if not config:
        return False
    user = update.effective_user
    if user is None:
        return False
    return str(user.id) == str(config["allowed_user_id"])


async def start_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    await update.message.reply_text(HELP_TEXT)


def build_application(token):
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("map", map_command))
    application.add_handler(CommandHandler("ask", ask_command))
    application.add_handler(CommandHandler("keys", keys_command))
    application.add_handler(CommandHandler("setkey", setkey_command))
    application.add_handler(addpoint_conversation)
    return application


async def start_bot_task():
    global _application
    config = keystore.get_telegram_config()
    if not config:
        return False
    try:
        application = build_application(config["token"])
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
    except Exception:
        _application = None
        return False
    _application = application
    return True


async def stop_bot_task():
    global _application
    if _application is None:
        return
    await _application.updater.stop()
    await _application.stop()
    await _application.shutdown()
    _application = None
```

This won't import successfully yet - `build_application` references `report_command`/`map_command`/`ask_command`/`keys_command`/`setkey_command`/`addpoint_conversation`, which don't exist until Tasks 7-10. Add temporary stubs at the end of the file for now, so this task's own tests can run in isolation:

```python
async def report_command(update, context):
    raise NotImplementedError  # implemented in Task 8


async def map_command(update, context):
    raise NotImplementedError  # implemented in Task 9


async def ask_command(update, context):
    raise NotImplementedError  # implemented in Task 8


async def keys_command(update, context):
    raise NotImplementedError  # implemented in Task 7


async def setkey_command(update, context):
    raise NotImplementedError  # implemented in Task 7


from telegram.ext import ConversationHandler

addpoint_conversation = ConversationHandler(entry_points=[], states={}, fallbacks=[])  # replaced in Task 10
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/server/test_telegram_bot.py -v`
Expected: PASS (all 9 tests). If `pytest-asyncio` isn't already configured for this project, check `tests/server/test_supplemental_data_route.py`'s existing async test handling first - this project already has async route tests, so `pytest-asyncio` is already a dependency; no new test-runner setup should be needed.

- [ ] **Step 6: Commit**

```bash
git add server/telegram_bot.py tests/server/test_telegram_bot.py
git commit -m "feat: add telegram_bot.py skeleton - auth, lifecycle, /start

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: `server/telegram_bot.py` Part B — `/keys` and `/setkey`

**Files:**
- Modify: `server/telegram_bot.py`
- Test: `tests/server/test_telegram_bot.py`

**Interfaces:**
- Consumes: `keystore.PROVIDERS`, `keystore.list_status()`, `keystore.get_key()`, `keystore.set_key()` (existing).
- Produces: real `keys_command`/`setkey_command` implementations, replacing Task 6's stubs.

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_telegram_bot.py`:

```python
@pytest.mark.asyncio
async def test_keys_command_lists_provider_status(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "list_status", lambda: [
        {"provider": "anthropic", "configured": True, "hint": "****1234"},
        {"provider": "groq", "configured": False, "hint": None},
    ])
    update = _make_update()
    await telegram_bot.keys_command(update, _make_context())
    reply = update.message.reply_text.call_args[0][0]
    assert "anthropic: configured" in reply
    assert "groq: not configured" in reply


@pytest.mark.asyncio
async def test_setkey_command_sets_the_key(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    set_calls = []
    monkeypatch.setattr(keystore, "set_key", lambda provider, key: set_calls.append((provider, key)))
    update = _make_update()
    await telegram_bot.setkey_command(update, _make_context(args=["groq", "gsk-xyz"]))
    assert set_calls == [("groq", "gsk-xyz")]
    update.message.reply_text.assert_awaited_once_with("groq key saved.")


@pytest.mark.asyncio
async def test_setkey_command_unknown_provider_rejected(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    await telegram_bot.setkey_command(update, _make_context(args=["bogus", "key"]))
    reply = update.message.reply_text.call_args[0][0]
    assert "Unknown provider: bogus" in reply


@pytest.mark.asyncio
async def test_setkey_command_missing_args_shows_usage(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    await telegram_bot.setkey_command(update, _make_context(args=["groq"]))
    update.message.reply_text.assert_awaited_once_with("Usage: /setkey <provider> <key>")


@pytest.mark.asyncio
async def test_keys_command_unauthorized_rejected(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111111111)
    await telegram_bot.keys_command(update, _make_context())
    update.message.reply_text.assert_awaited_once_with("Not authorized.")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_telegram_bot.py -v -k "keys_command or setkey_command"`
Expected: FAIL with `NotImplementedError` (Task 6's stubs).

- [ ] **Step 3: Implement**

In `server/telegram_bot.py`, replace the `keys_command`/`setkey_command` stubs with:

```python
async def keys_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    statuses = keystore.list_status()
    lines = [f"{s['provider']}: {'configured' if s['configured'] else 'not configured'}" for s in statuses]
    await update.message.reply_text("\n".join(lines))


async def setkey_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setkey <provider> <key>")
        return
    provider, key = context.args[0], " ".join(context.args[1:])
    if provider not in keystore.PROVIDERS:
        await update.message.reply_text(f"Unknown provider: {provider}. Choose from: {', '.join(keystore.PROVIDERS)}")
        return
    keystore.set_key(provider, key)
    await update.message.reply_text(f"{provider} key saved.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_telegram_bot.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 5: Commit**

```bash
git add server/telegram_bot.py tests/server/test_telegram_bot.py
git commit -m "feat: add /keys and /setkey bot commands

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: `server/telegram_bot.py` Part C — `/report` and `/ask`

**Files:**
- Modify: `server/telegram_bot.py`
- Test: `tests/server/test_telegram_bot.py`

**Interfaces:**
- Consumes: `pdf_export.render_report_pdf()`, `report_context.build_context()`, `ai_client.ask()`, `ai_client.AIProviderError` (all existing).
- Produces: real `report_command`/`ask_command` implementations, replacing Task 6's stubs.

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_telegram_bot.py`:

```python
from pathlib import Path

from server import ai_client


@pytest.mark.asyncio
async def test_report_command_sends_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    report_path = tmp_path / "report.html"
    report_path.write_text("<html>report</html>", encoding="utf-8")
    monkeypatch.setattr(telegram_bot, "REPORT_PATH", report_path)
    monkeypatch.setattr(telegram_bot.pdf_export, "render_report_pdf", lambda html_text: b"%PDF-fake")
    update = _make_update()
    await telegram_bot.report_command(update, _make_context())
    update.message.reply_document.assert_awaited_once()
    assert update.message.reply_document.call_args.kwargs["document"] == b"%PDF-fake"


@pytest.mark.asyncio
async def test_report_command_not_built_yet(monkeypatch, tmp_path):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(telegram_bot, "REPORT_PATH", tmp_path / "does_not_exist.html")
    update = _make_update()
    await telegram_bot.report_command(update, _make_context())
    reply = update.message.reply_text.call_args[0][0]
    assert "not built yet" in reply.lower()


@pytest.mark.asyncio
async def test_ask_command_answers_using_first_configured_provider(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_key", lambda provider: "sk-real" if provider == "groq" else None)
    monkeypatch.setattr(telegram_bot.report_context, "build_context", lambda: "digest text")
    monkeypatch.setattr(telegram_bot.ai_client, "ask", lambda provider, key, question, context: "the answer")
    update = _make_update()
    await telegram_bot.ask_command(update, _make_context(args=["What", "is", "the", "gap", "score?"]))
    update.message.reply_text.assert_awaited_once_with("the answer")


@pytest.mark.asyncio
async def test_ask_command_no_provider_configured(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_key", lambda provider: None)
    update = _make_update()
    await telegram_bot.ask_command(update, _make_context(args=["hi"]))
    reply = update.message.reply_text.call_args[0][0]
    assert "add one in the admin panel first" in reply


@pytest.mark.asyncio
async def test_ask_command_missing_question_shows_usage(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    await telegram_bot.ask_command(update, _make_context(args=[]))
    update.message.reply_text.assert_awaited_once_with("Usage: /ask <question>")


@pytest.mark.asyncio
async def test_ask_command_provider_error_becomes_plain_reply(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(keystore, "get_key", lambda provider: "sk-real" if provider == "groq" else None)
    monkeypatch.setattr(telegram_bot.report_context, "build_context", lambda: "digest text")

    def failing_ask(provider, key, question, context):
        raise ai_client.AIProviderError("rate limited")

    monkeypatch.setattr(telegram_bot.ai_client, "ask", failing_ask)
    update = _make_update()
    await telegram_bot.ask_command(update, _make_context(args=["hi"]))
    reply = update.message.reply_text.call_args[0][0]
    assert "rate limited" in reply
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_telegram_bot.py -v -k "report_command or ask_command"`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement**

In `server/telegram_bot.py`, add these imports near the top (alongside the existing `from server import keystore`):

```python
import asyncio
from pathlib import Path

from server import ai_client, pdf_export, report_context
```

Add module constants right after the imports:

```python
ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "report" / "KP_Healthcare_Plan.html"
```

Replace the `report_command`/`ask_command` stubs with:

```python
async def report_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if not REPORT_PATH.exists():
        await update.message.reply_text("Report not built yet - run the pipeline first.")
        return
    html_text = REPORT_PATH.read_text(encoding="utf-8")
    pdf_bytes = await asyncio.to_thread(pdf_export.render_report_pdf, html_text)
    await update.message.reply_document(document=pdf_bytes, filename="KP_Healthcare_Plan.pdf")


async def ask_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask <question>")
        return
    provider = next((p for p in keystore.PROVIDERS if keystore.get_key(p)), None)
    if provider is None:
        await update.message.reply_text("No AI provider configured - add one in the admin panel first.")
        return
    key = keystore.get_key(provider)
    context_text = report_context.build_context()
    try:
        answer = await asyncio.to_thread(ai_client.ask, provider, key, question, context_text)
    except ai_client.AIProviderError as exc:
        await update.message.reply_text(f"AI request failed: {exc}")
        return
    await update.message.reply_text(answer)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_telegram_bot.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 5: Commit**

```bash
git add server/telegram_bot.py tests/server/test_telegram_bot.py
git commit -m "feat: add /report and /ask bot commands

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: `server/telegram_bot.py` Part D — `/map`

**Files:**
- Modify: `server/telegram_bot.py`
- Test: `tests/server/test_telegram_bot.py`

**Interfaces:**
- Consumes: `scripts/lib/qgis_render.py` (Task 5), invoked as a subprocess.
- Produces: real `map_command` implementation, replacing Task 6's stub.

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_telegram_bot.py`:

```python
class FakeCompletedProcess:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


@pytest.mark.asyncio
async def test_map_command_renders_and_sends_photo(monkeypatch, tmp_path):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    qgz_path = tmp_path / "project.qgz"
    qgz_path.write_bytes(b"fake qgz")
    monkeypatch.setattr(telegram_bot, "QGZ_PATH", qgz_path)

    def fake_run(args, **kwargs):
        output_path = Path(args[-1])
        output_path.write_bytes(b"\x89PNG fake")
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(telegram_bot.subprocess, "run", fake_run)
    update = _make_update()
    await telegram_bot.map_command(update, _make_context())
    update.message.reply_photo.assert_awaited_once()
    assert update.message.reply_photo.call_args.kwargs["photo"] == b"\x89PNG fake"


@pytest.mark.asyncio
async def test_map_command_project_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    monkeypatch.setattr(telegram_bot, "QGZ_PATH", tmp_path / "does_not_exist.qgz")
    update = _make_update()
    await telegram_bot.map_command(update, _make_context())
    replies = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("not built yet" in r.lower() for r in replies)


@pytest.mark.asyncio
async def test_map_command_render_failure_becomes_plain_reply(monkeypatch, tmp_path):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    qgz_path = tmp_path / "project.qgz"
    qgz_path.write_bytes(b"fake qgz")
    monkeypatch.setattr(telegram_bot, "QGZ_PATH", qgz_path)
    monkeypatch.setattr(
        telegram_bot.subprocess, "run",
        lambda args, **kwargs: FakeCompletedProcess(returncode=1, stderr="Traceback: PyQGIS failure"),
    )
    update = _make_update()
    await telegram_bot.map_command(update, _make_context())
    replies = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("rendering failed" in r.lower() for r in replies)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_telegram_bot.py -v -k "map_command"`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement**

In `server/telegram_bot.py`, add these imports:

```python
import subprocess
import tempfile
```

Add module constants alongside `REPORT_PATH`:

```python
QGIS_PYTHON = r"C:\Program Files\QGIS 4.0.0\bin\python-qgis.bat"
RENDER_SCRIPT = ROOT / "scripts" / "lib" / "qgis_render.py"
QGZ_PATH = ROOT / "gis" / "KP_Healthcare_Plan.qgz"
```

Replace the `map_command` stub with:

```python
async def map_command(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return
    if not QGZ_PATH.exists():
        await update.message.reply_text("Map not built yet - run the pipeline first.")
        return
    await update.message.reply_text("Rendering map...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = Path(tmp_dir) / "map.png"
        result = await asyncio.to_thread(
            subprocess.run,
            [QGIS_PYTHON, str(RENDER_SCRIPT), str(QGZ_PATH), str(output_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0 or not output_path.exists():
            await update.message.reply_text(f"Map rendering failed: {result.stderr[-500:]}")
            return
        await update.message.reply_photo(photo=output_path.read_bytes())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_telegram_bot.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 5: Commit**

```bash
git add server/telegram_bot.py tests/server/test_telegram_bot.py
git commit -m "feat: add /map bot command

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: `server/telegram_bot.py` Part E — `/addpoint` conversation and `/cancel`

**Files:**
- Modify: `server/telegram_bot.py`
- Test: `tests/server/test_telegram_bot.py`

**Interfaces:**
- Consumes: `bot_facilities.add_facility()` (Task 2), `scripts.lib.geo_utils.find_containing_district` (existing), `scripts/run_downstream_facilities.py` (Task 4), invoked as a subprocess.
- Produces: the real `addpoint_conversation` `ConversationHandler`, replacing Task 6's empty stub.

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_telegram_bot.py`:

```python
from shapely.geometry import Polygon
from telegram.ext import ConversationHandler

from server import bot_facilities

FAKE_DISTRICTS = [{
    "district": "Peshawar",
    "geometry": Polygon([(71.4, 33.9), (71.7, 33.9), (71.7, 34.1), (71.4, 34.1)]),
}]


def _make_location_update(lat, lon, user_id=987654321):
    update = _make_update(user_id=user_id)
    update.message.location = MagicMock()
    update.message.location.latitude = lat
    update.message.location.longitude = lon
    return update


@pytest.mark.asyncio
async def test_addpoint_start_authorized_asks_for_name(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update()
    context = _make_context()
    state = await telegram_bot.addpoint_start(update, context)
    assert state == telegram_bot.NAME
    update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_addpoint_start_unauthorized_ends_conversation(monkeypatch):
    monkeypatch.setattr(keystore, "get_telegram_config", lambda: TELEGRAM_CONFIG)
    update = _make_update(user_id=111111111)
    state = await telegram_bot.addpoint_start(update, _make_context())
    assert state == ConversationHandler.END


@pytest.mark.asyncio
async def test_addpoint_name_then_category_stores_user_data():
    update = _make_update()
    context = _make_context()
    update.message.text = "Field Clinic"
    state = await telegram_bot.addpoint_name(update, context)
    assert state == telegram_bot.CATEGORY
    assert context.user_data["name"] == "Field Clinic"

    update.message.text = "Clinic"
    state = await telegram_bot.addpoint_category(update, context)
    assert state == telegram_bot.LOCATION
    assert context.user_data["category"] == "Clinic"


@pytest.mark.asyncio
async def test_addpoint_location_inside_kp_adds_facility(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_bot, "_load_districts", lambda: FAKE_DISTRICTS)
    records_path = tmp_path / "bot_facilities.csv"
    monkeypatch.setattr(bot_facilities, "RECORDS_PATH", records_path)
    monkeypatch.setattr(
        telegram_bot.subprocess, "run",
        lambda args, **kwargs: FakeCompletedProcess(returncode=0),
    )
    update = _make_location_update(lat=34.0, lon=71.55)
    context = _make_context()
    context.user_data["name"] = "Field Clinic"
    context.user_data["category"] = "Clinic"

    state = await telegram_bot.addpoint_location(update, context)

    assert state == ConversationHandler.END
    saved = bot_facilities.load_records(path=records_path)
    assert len(saved) == 1
    assert saved[0]["name"] == "Field Clinic"
    assert saved[0]["district"] == "Peshawar"
    replies = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("Peshawar" in r for r in replies)


@pytest.mark.asyncio
async def test_addpoint_location_outside_kp_rejected_without_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_bot, "_load_districts", lambda: FAKE_DISTRICTS)
    records_path = tmp_path / "bot_facilities.csv"
    monkeypatch.setattr(bot_facilities, "RECORDS_PATH", records_path)
    update = _make_location_update(lat=30.0, lon=75.0)  # nowhere near KP
    context = _make_context()
    context.user_data["name"] = "Field Clinic"
    context.user_data["category"] = "Clinic"

    state = await telegram_bot.addpoint_location(update, context)

    assert state == ConversationHandler.END
    assert bot_facilities.load_records(path=records_path) == []
    reply = update.message.reply_text.call_args[0][0]
    assert "outside" in reply.lower()


@pytest.mark.asyncio
async def test_addpoint_location_missing_prompts_again():
    update = _make_update()
    update.message.location = None
    context = _make_context()
    state = await telegram_bot.addpoint_location(update, context)
    assert state == telegram_bot.LOCATION


@pytest.mark.asyncio
async def test_addpoint_cancel_ends_conversation():
    update = _make_update()
    context = _make_context()
    context.user_data["name"] = "Field Clinic"
    state = await telegram_bot.addpoint_cancel(update, context)
    assert state == ConversationHandler.END
    assert context.user_data == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_telegram_bot.py -v -k "addpoint"`
Expected: FAIL (`AttributeError: module 'server.telegram_bot' has no attribute 'addpoint_start'`, etc.).

- [ ] **Step 3: Implement**

In `server/telegram_bot.py`, add these imports:

```python
import json

from shapely.geometry import Point, shape
from shapely.ops import unary_union
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ConversationHandler, MessageHandler, filters

from scripts.lib.geo_utils import find_containing_district
from server import bot_facilities
```

Add module constants:

```python
KP_BBOX = (31.0, 69.2, 36.9, 74.1)  # lat_min, lon_min, lat_max, lon_max - same constant every geo-fetch script in this project uses
BOUNDARIES_PATH = ROOT / "data" / "processed" / "boundaries.json"
RUN_DOWNSTREAM_FACILITIES_SCRIPT = ROOT / "scripts" / "run_downstream_facilities.py"

NAME, CATEGORY, LOCATION = range(3)

_districts_cache = None
```

Add the district-loading and validation helpers, right before the conversation handler functions:

```python
def _load_districts():
    global _districts_cache
    if _districts_cache is None:
        boundaries = json.loads(BOUNDARIES_PATH.read_text(encoding="utf-8"))
        _districts_cache = [
            {"district": d["district"], "geometry": shape(d["geometry"])}
            for d in boundaries["districts"]
        ]
    return _districts_cache


def _is_within_kp(lon, lat):
    lat_min, lon_min, lat_max, lon_max = KP_BBOX
    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        return False
    districts = _load_districts()
    province_geom = unary_union([d["geometry"] for d in districts])
    return province_geom.contains(Point(lon, lat))


def _resolve_district(lon, lat):
    return find_containing_district(lon, lat, _load_districts())
```

Replace the `addpoint_conversation = ConversationHandler(entry_points=[], states={}, fallbacks=[])` stub (and remove the now-redundant `from telegram.ext import ConversationHandler` line right above it, since it's now imported at the top) with:

```python
async def addpoint_start(update, context):
    if not _authorized(update):
        await update.message.reply_text("Not authorized.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("What's the facility's name?")
    return NAME


async def addpoint_name(update, context):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("What category is it? (e.g. Hospital, Clinic, Pharmacy)")
    return CATEGORY


async def addpoint_category(update, context):
    context.user_data["category"] = update.message.text.strip()
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Share location", request_location=True)]],
        one_time_keyboard=True, resize_keyboard=True,
    )
    await update.message.reply_text("Now share the facility's location.", reply_markup=keyboard)
    return LOCATION


async def addpoint_location(update, context):
    location = update.message.location
    if location is None:
        await update.message.reply_text("Please share a location using the button, or /cancel.")
        return LOCATION
    lon, lat = location.longitude, location.latitude

    if not _is_within_kp(lon, lat):
        await update.message.reply_text(
            "That location is outside Khyber Pakhtunkhwa - not added.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    district = _resolve_district(lon, lat)
    record = bot_facilities.add_facility(
        name=context.user_data["name"],
        district=district,
        lat=lat,
        lon=lon,
        category=context.user_data["category"],
        added_by=str(update.effective_user.id),
    )
    context.user_data.clear()

    await update.message.reply_text(
        f"Adding {record['name']} to {district}... this may take a few minutes.",
        reply_markup=ReplyKeyboardRemove(),
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(RUN_DOWNSTREAM_FACILITIES_SCRIPT)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        await update.message.reply_text(f"Facility saved, but the rebuild failed: {result.stderr[-500:]}")
    else:
        await update.message.reply_text(f"Done - {record['name']} added to {district}.")
    return ConversationHandler.END


async def addpoint_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


addpoint_conversation = ConversationHandler(
    entry_points=[CommandHandler("addpoint", addpoint_start)],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addpoint_name)],
        CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, addpoint_category)],
        LOCATION: [MessageHandler(filters.LOCATION, addpoint_location)],
    },
    fallbacks=[CommandHandler("cancel", addpoint_cancel)],
)
```

`addpoint_location` uses `sys.executable`, so also add `import sys` near the top imports if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_telegram_bot.py -v`
Expected: PASS (every test in the file).

- [ ] **Step 5: Commit**

```bash
git add server/telegram_bot.py tests/server/test_telegram_bot.py
git commit -m "feat: add /addpoint conversation and /cancel bot commands

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: `server/app.py` — lifespan wiring

**Files:**
- Modify: `server/app.py`
- Test: `tests/server/test_app_lifespan.py`

**Interfaces:**
- Consumes: `telegram_bot.start_bot_task()`, `telegram_bot.stop_bot_task()` (Task 6).
- Produces: `create_app()` now takes a `lifespan` - no change to its own call signature, consumed identically by every existing test and by `server/__main__.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/server/test_app_lifespan.py`:

```python
"""Verifies server/app.py's lifespan starts/stops the Telegram bot task.
Every existing TestClient(create_app()) call elsewhere in this project's
test suite is unaffected - Starlette's TestClient only runs the ASGI
lifespan protocol when used as `with TestClient(app) as client:`, which
no pre-existing test in this project does.
"""
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from server import telegram_bot
from server.app import create_app


def test_lifespan_starts_and_stops_the_bot_task(monkeypatch):
    start_mock = AsyncMock(return_value=True)
    stop_mock = AsyncMock()
    monkeypatch.setattr(telegram_bot, "start_bot_task", start_mock)
    monkeypatch.setattr(telegram_bot, "stop_bot_task", stop_mock)

    with TestClient(create_app()) as client:
        client.get("/")  # exercise a request inside the running lifespan

    start_mock.assert_awaited_once()
    stop_mock.assert_awaited_once()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/server/test_app_lifespan.py -v`
Expected: FAIL (`start_mock`/`stop_mock` never called - `create_app()` has no lifespan yet).

- [ ] **Step 3: Implement**

Replace the full contents of `server/app.py`:

```python
"""FastAPI application factory. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md and
2026-08-16-telegram-connector-design.md section 5.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from server import telegram_bot
from server.routes import admin, dashboard


@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_bot.start_bot_task()
    yield
    await telegram_bot.stop_bot_task()


def create_app():
    app = FastAPI(title="KP Healthcare Plan", lifespan=lifespan)
    app.include_router(dashboard.router)
    app.include_router(admin.router)
    return app


app = create_app()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/server/test_app_lifespan.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite to confirm no existing test regressed**

Run: `pytest tests/ -q`
Expected: all tests pass, same count as before plus this task's one new test - confirming no pre-existing `TestClient(create_app())` call anywhere in the suite is affected by the new lifespan (none of them use `with`).

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/server/test_app_lifespan.py
git commit -m "feat: wire the Telegram bot task into server/app.py's lifespan

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12: Admin routes — Telegram config and bot-facilities records

**Files:**
- Modify: `server/routes/admin.py`
- Test: `tests/server/test_telegram_admin_route.py`, `tests/server/test_bot_facilities_route.py`

**Interfaces:**
- Consumes: `keystore.get_telegram_config/set_telegram_config/delete_telegram_config` (Task 1), `telegram_bot.start_bot_task/stop_bot_task` (Task 6), `bot_facilities.load_records/delete_record` (Task 2), `run_downstream_facilities.py` (Task 4).
- Produces: `GET/POST/DELETE /admin/api/telegram/config`, `GET /admin/api/bot-facilities/records`, `DELETE /admin/api/bot-facilities/records/{id}` - consumed by Task 13's admin panel UI.

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_telegram_admin_route.py`:

```python
"""End-to-end /admin/api/telegram/config tests via FastAPI's TestClient.
telegram_bot.start_bot_task/stop_bot_task are mocked - no real bot
started in any test here. keyring is mocked too, same pattern as
tests/server/test_supplemental_data_route.py.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from server import keystore
from server.app import create_app
from server.routes import admin as admin_route


class FakeStore:
    def __init__(self):
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        del self.data[(service, username)]


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(keystore.keyring, "get_password", store.get_password)
    monkeypatch.setattr(keystore.keyring, "set_password", store.set_password)
    monkeypatch.setattr(keystore.keyring, "delete_password", store.delete_password)
    return store


@pytest.fixture
def client(fake_store):
    return TestClient(create_app())


SETUP_FORM = {"password": "hunter2hunter2", "confirm": "hunter2hunter2"}


def _login(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})


def test_get_telegram_config_requires_authentication(client):
    response = client.get("/admin/api/telegram/config")
    assert response.status_code == 401


def test_get_telegram_config_reports_not_configured(client):
    _login(client)
    response = client.get("/admin/api/telegram/config")
    assert response.status_code == 200
    assert response.json() == {"configured": False}


def test_save_telegram_config_starts_the_bot(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(admin_route.telegram_bot, "stop_bot_task", AsyncMock())
    monkeypatch.setattr(admin_route.telegram_bot, "start_bot_task", AsyncMock(return_value=True))
    response = client.post(
        "/admin/api/telegram/config",
        json={"token": "123:ABC", "allowed_user_id": "987654321"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert keystore.get_telegram_config() == {"token": "123:ABC", "allowed_user_id": "987654321"}


def test_save_telegram_config_reports_bot_start_failure(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(admin_route.telegram_bot, "stop_bot_task", AsyncMock())
    monkeypatch.setattr(admin_route.telegram_bot, "start_bot_task", AsyncMock(return_value=False))
    response = client.post(
        "/admin/api/telegram/config",
        json={"token": "bad-token", "allowed_user_id": "987654321"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "bot_warning" in body


def test_delete_telegram_config_clears_it_and_stops_the_bot(client, monkeypatch):
    _login(client)
    keystore.set_telegram_config({"token": "123:ABC", "allowed_user_id": "987654321"})
    stop_mock = AsyncMock()
    monkeypatch.setattr(admin_route.telegram_bot, "stop_bot_task", stop_mock)
    response = client.delete("/admin/api/telegram/config")
    assert response.status_code == 200
    assert keystore.get_telegram_config() is None
    stop_mock.assert_awaited_once()
```

Create `tests/server/test_bot_facilities_route.py`:

```python
"""End-to-end /admin/api/bot-facilities/records tests via FastAPI's
TestClient. The downstream-rebuild subprocess call is mocked - no real
pipeline run in any test here. Same keyring-mocking pattern as
tests/server/test_supplemental_data_route.py.
"""
import pytest
from fastapi.testclient import TestClient

from server import bot_facilities, keystore
from server.app import create_app
from server.routes import admin as admin_route


class FakeStore:
    def __init__(self):
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        del self.data[(service, username)]


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(keystore.keyring, "get_password", store.get_password)
    monkeypatch.setattr(keystore.keyring, "set_password", store.set_password)
    monkeypatch.setattr(keystore.keyring, "delete_password", store.delete_password)
    return store


@pytest.fixture
def client(fake_store):
    return TestClient(create_app())


SETUP_FORM = {"password": "hunter2hunter2", "confirm": "hunter2hunter2"}


def _login(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})


class FakeCompletedProcess:
    returncode = 0
    stderr = ""


def test_list_bot_facilities_requires_authentication(client):
    response = client.get("/admin/api/bot-facilities/records")
    assert response.status_code == 401


def test_list_bot_facilities_returns_records(client, monkeypatch):
    _login(client)
    fake_records = [{"id": "aaa111", "name": "Field Clinic", "district": "Peshawar",
                      "lat": "34.0", "lon": "71.5", "category": "Clinic",
                      "added_at": "2026-08-16T00:00:00+00:00", "added_by": "555"}]
    monkeypatch.setattr(bot_facilities, "load_records", lambda: fake_records)
    response = client.get("/admin/api/bot-facilities/records")
    assert response.status_code == 200
    assert response.json() == {"records": fake_records}


def test_delete_bot_facility_requires_authentication(client):
    response = client.delete("/admin/api/bot-facilities/records/aaa111")
    assert response.status_code == 401


def test_delete_bot_facility_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(bot_facilities, "delete_record", lambda record_id, path=None: True)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.delete("/admin/api/bot-facilities/records/aaa111")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_delete_bot_facility_unknown_id_returns_404(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(bot_facilities, "delete_record", lambda record_id, path=None: False)
    response = client.delete("/admin/api/bot-facilities/records/does-not-exist")
    assert response.status_code == 404


def test_delete_bot_facility_rebuild_failure_still_returns_deleted(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(bot_facilities, "delete_record", lambda record_id, path=None: True)

    class FailedProcess:
        returncode = 1
        stderr = "Traceback: something broke"

    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FailedProcess())
    response = client.delete("/admin/api/bot-facilities/records/aaa111")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert "rebuild_warning" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_telegram_admin_route.py tests/server/test_bot_facilities_route.py -v`
Expected: FAIL with 404s (routes don't exist yet).

- [ ] **Step 3: Implement**

In `server/routes/admin.py`, update the import block:

```python
from server import (
    admin_ui,
    ai_client,
    auth,
    bot_facilities,
    db_ingestion,
    document_extraction,
    keystore,
    metric_overrides,
    providers,
    supplemental_data,
    telegram_bot,
)
```

Add a new module constant next to `RUN_DOWNSTREAM_SCRIPT`:

```python
RUN_DOWNSTREAM_FACILITIES_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "run_downstream_facilities.py"
```

Add these routes right after `delete_metric_override_record` (before `save_db_connection`):

```python
@router.get("/admin/api/telegram/config")
def get_telegram_config(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    config = keystore.get_telegram_config()
    if not config:
        return JSONResponse({"configured": False})
    return JSONResponse({
        "configured": True,
        "token_hint": keystore.mask(config["token"]),
        "allowed_user_id": config["allowed_user_id"],
    })


@router.post("/admin/api/telegram/config")
async def save_telegram_config(
    kp_admin_session: str | None = Cookie(default=None),
    token: str = Body(...),
    allowed_user_id: str = Body(...),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    keystore.set_telegram_config({"token": token, "allowed_user_id": allowed_user_id})
    await telegram_bot.stop_bot_task()
    started = await telegram_bot.start_bot_task()
    if not started:
        return JSONResponse({"ok": True, "bot_warning": "Saved, but the bot failed to start - check the token."})
    return JSONResponse({"ok": True})


@router.delete("/admin/api/telegram/config")
async def delete_telegram_config(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    keystore.delete_telegram_config()
    await telegram_bot.stop_bot_task()
    return JSONResponse({"ok": True})


@router.get("/admin/api/bot-facilities/records")
def list_bot_facilities(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    return JSONResponse({"records": bot_facilities.load_records()})


@router.delete("/admin/api/bot-facilities/records/{record_id}")
def delete_bot_facility(record_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    found = bot_facilities.delete_record(record_id)
    if not found:
        return JSONResponse({"detail": "No record with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(RUN_DOWNSTREAM_FACILITIES_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse(
            {"deleted": True, "rebuild_warning": "Downstream pipeline rebuild timed out after 600 seconds"}
        )
    if result.returncode != 0:
        return JSONResponse(
            {"deleted": True, "rebuild_warning": f"Downstream pipeline rebuild failed: {result.stderr[-500:]}"}
        )
    return JSONResponse({"deleted": True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_telegram_admin_route.py tests/server/test_bot_facilities_route.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add server/routes/admin.py tests/server/test_telegram_admin_route.py tests/server/test_bot_facilities_route.py
git commit -m "feat: add admin routes for Telegram config and bot-facilities records

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 13: Admin panel UI — Telegram Bot section and Bot-Added Facilities table

**Files:**
- Modify: `server/admin_ui.py`

**Interfaces:**
- Consumes: `GET/POST/DELETE /admin/api/telegram/config`, `GET/DELETE /admin/api/bot-facilities/records[/{id}]` (Task 12); `initRecordsTable`/`showEmptyRow` JS helpers (already built for Manage Records).
- Produces: nothing new for later tasks.

No dedicated automated test - `admin_ui.py` is a pure HTML/CSS/JS rendering module with no existing unit tests in this codebase (same established reasoning as the Manage Records feature's UI tasks). Verified manually in Task 14.

- [ ] **Step 1: Add CSS for the Telegram status text**

In `server/admin_ui.py`, append to the end of `ADMIN_CSS` (right after the existing `#supplemental-records-status, #override-records-status { display: none; margin-top: 0.5rem; }` line):

```css
#telegram-status { display: none; margin-top: 0.5rem; font-size: 0.85rem; }
#telegram-status.ok { color: var(--accent-2); display: block; }
#telegram-status.bad { color: var(--danger); display: block; }
#bot-facilities-status { display: none; margin-top: 0.5rem; }
```

- [ ] **Step 2: Add the JS for the Telegram config section and the bot-facilities table refresh**

In `server/admin_ui.py`'s `ADMIN_JS`, add a new `refreshBotFacilities()` function right after the existing `refreshOverrideRecords()` function:

```javascript
  function refreshBotFacilities() {
    initRecordsTable({
      listUrl: "/admin/api/bot-facilities/records",
      deleteUrlPrefix: "/admin/api/bot-facilities/records/",
      tbodyId: "bot-facilities-tbody",
      statusId: "bot-facilities-status",
      columns: ["name", "district", "category", "lat", "lon", "added_at", "added_by"],
    });
  }
```

Inside the `document.addEventListener("DOMContentLoaded", function () { ... });` block, add this right after the existing `dbIngestBtn` block (before the final `refreshSupplementalRecords(); refreshOverrideRecords();` calls):

```javascript
    function loadTelegramStatus() {
      var statusEl = byId("telegram-status");
      if (!statusEl) return;
      apiCall("GET", "/admin/api/telegram/config").then(function (result) {
        if (result.data && result.data.configured) {
          statusEl.textContent = "Configured (token " + result.data.token_hint + ", user id " + result.data.allowed_user_id + ")";
          statusEl.className = "ok";
        } else {
          statusEl.textContent = "Not configured";
          statusEl.className = "bad";
        }
      });
    }

    var telegramSaveBtn = byId("telegram-save-btn");
    if (telegramSaveBtn) {
      loadTelegramStatus();

      telegramSaveBtn.addEventListener("click", function () {
        var token = byId("telegram-token").value.trim();
        var userId = byId("telegram-user-id").value.trim();
        if (!token || !userId) return;
        telegramSaveBtn.disabled = true;
        telegramSaveBtn.textContent = "Saving...";
        apiCall("POST", "/admin/api/telegram/config", { token: token, allowed_user_id: userId }).then(function (result) {
          telegramSaveBtn.disabled = false;
          telegramSaveBtn.textContent = "Save";
          var statusEl = byId("telegram-status");
          if (result.status === 200) {
            byId("telegram-token").value = "";
            if (result.data && result.data.bot_warning) {
              statusEl.textContent = result.data.bot_warning;
              statusEl.className = "bad";
            } else {
              loadTelegramStatus();
            }
          } else {
            statusEl.textContent = (result.data && result.data.detail) || "Save failed";
            statusEl.className = "bad";
          }
        });
      });

      var telegramDeleteBtn = byId("telegram-delete-btn");
      telegramDeleteBtn.addEventListener("click", function () {
        apiCall("DELETE", "/admin/api/telegram/config").then(function () {
          loadTelegramStatus();
        });
      });
    }
```

Update the final two lines of the `DOMContentLoaded` block from:

```javascript
    refreshSupplementalRecords();
    refreshOverrideRecords();
  });
```

to:

```javascript
    refreshSupplementalRecords();
    refreshOverrideRecords();
    refreshBotFacilities();
  });
```

- [ ] **Step 3: Add the HTML sections**

In `render_admin_panel`, insert two new `upload-section` blocks directly after the "Database Ingestion" section's closing `</div>` and before the final `<p style="margin-top:1.5rem">...Log Out...` line:

```html
<div class="upload-section">
  <h2>Telegram Bot</h2>
  <p class="hint">Interact with this dashboard from Telegram - view the report/map, ask the AI, manage keys, and add facilities from the field. Create a bot via @BotFather to get a token, and find your numeric user id via @userinfobot.</p>
  <label for="telegram-token">Bot token</label>
  <input type="password" id="telegram-token" placeholder="Paste bot token" autocomplete="off">
  <label for="telegram-user-id">Your Telegram user id</label>
  <input type="text" id="telegram-user-id" placeholder="e.g. 123456789">
  <button type="button" class="primary" id="telegram-save-btn">Save</button>
  <button type="button" class="danger" id="telegram-delete-btn">Delete</button>
  <p id="telegram-status"></p>
</div>
<div class="upload-section">
  <h2>Bot-Added Facilities</h2>
  <p class="hint">Every facility added via the Telegram bot's /addpoint command. Delete one to remove it and rebuild the map/report automatically.</p>
  <div class="records-table-wrap">
    <table class="records-table">
      <thead>
        <tr><th>Name</th><th>District</th><th>Category</th><th>Lat</th><th>Lon</th><th>Added</th><th>Added By</th><th></th></tr>
      </thead>
      <tbody id="bot-facilities-tbody">
        <tr><td colspan="8" class="records-empty">Loading...</td></tr>
      </tbody>
    </table>
  </div>
  <p id="bot-facilities-status" class="error"></p>
</div>
```

- [ ] **Step 4: Manually smoke-test the page renders without a JS error**

Run: `python -c "from server.admin_ui import render_admin_panel; html = render_admin_panel([]); assert 'telegram-save-btn' in html; assert 'bot-facilities-tbody' in html; assert 'refreshBotFacilities' in html; print('renders OK')"`
Expected: `renders OK`

- [ ] **Step 5: Run the full test suite to confirm nothing else regressed**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add server/admin_ui.py
git commit -m "feat: add Telegram Bot config and Bot-Added Facilities sections to the admin panel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 14: Full test suite and live manual verification with a real bot

**Files:** none (verification only).

This feature touches a real external API (Telegram) and a real desktop GIS library (PyQGIS via subprocess), so per this project's established cadence it needs manual verification against the real running server with a real bot - not just mocks.

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 2: Create a real Telegram bot**

In Telegram, message **@BotFather**, send `/newbot`, follow the prompts (choose a name and a username ending in `bot`). Copy the resulting token. Message **@userinfobot** (or any similar "get my Telegram id" bot) to get your own numeric Telegram user id.

- [ ] **Step 3: Start the server and configure the bot**

Run: `python -m server`, log in at `http://127.0.0.1:8420/admin`, go to the new "Telegram Bot" section, paste the token and your user id, click Save. Confirm the status shows "Configured".

- [ ] **Step 4: Message the bot from Telegram and exercise every command**

Open a chat with the bot (search its username in Telegram) and send:
- `/start` - confirm the help text lists all six commands.
- `/keys` - confirm it lists the currently configured providers (matching whatever's set in the admin panel).
- `/setkey <provider> <a-real-key-you-already-have-configured>` (or a throwaway one) - confirm `/keys` reflects the change afterward.
- `/report` - confirm a real PDF arrives as a document.
- `/map` - confirm a real PNG arrives as a photo, showing the province/districts/facilities/roads layers.
- `/ask <a question about the data>` - confirm a real AI-generated answer arrives, using whichever provider is configured.

- [ ] **Step 5: Exercise `/addpoint` end-to-end**

Send `/addpoint`, provide a name (e.g. "Test Field Clinic") and category (e.g. "Clinic") when prompted, then tap the "Share location" button and share a real location genuinely inside KP (e.g. near Peshawar). Confirm the bot replies with the resolved district and a "this may take a few minutes" message, then eventually a completion message. Once done:

```bash
cd "E:\Healthcare System Planning" && grep "Test Field Clinic" data/processed/bot_facilities.csv data/processed/facilities_merged.csv
```

Expected: the facility appears in both files with the correct district. Confirm it also shows up in the admin panel's new "Bot-Added Facilities" table without a page reload issue (refresh the page to check).

- [ ] **Step 6: Confirm the out-of-KP rejection works for real**

Send `/addpoint` again, provide a name/category, then share a location clearly outside KP (e.g. a point in central Islamabad). Confirm the bot rejects it with the "outside Khyber Pakhtunkhwa" message and nothing gets added (`grep` the CSV again to confirm no new row).

- [ ] **Step 7: Confirm unauthorized access is rejected**

From a **different** Telegram account (or ask someone else, or use Telegram's own test/second-account feature if available), message the bot. Confirm every command gets the generic "Not authorized." reply with no other information.

- [ ] **Step 8: Delete the test facility from the admin panel**

In the "Bot-Added Facilities" table, delete the test facility added in Step 5 using the two-step confirm. Confirm the rebuild runs and the facility disappears from `data/processed/bot_facilities.csv` and `data/processed/facilities_merged.csv`.

- [ ] **Step 9: Clean up**

Following this project's established manual-verification cleanup discipline: confirm `data/processed/bot_facilities.csv` is empty/absent again (delete it if it's just a header-only leftover), rebuild the report/pipeline if needed so committed output isn't stale, and confirm via `git status`/`git diff` that nothing changed relative to what's already committed. If the AI provider key used for `/setkey`/`/ask` testing was added solely for this test, remove it. Delete the Telegram bot's configuration from the admin panel if you don't want to keep it running, or leave it configured if you do (your choice - either is a valid end state).

- [ ] **Step 10: Stop the server**

Stop the `python -m server` process (PowerShell `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` + `Stop-Process -Force`, matching this project's established Windows-specific cleanup).

- [ ] **Step 11: Report findings**

If everything above checks out clean, this task (and the whole plan) is done - no further commit needed beyond what Tasks 1-13 already made (Step 9's cleanup should leave the tree clean). If anything looks wrong (a command doesn't respond, `/addpoint` doesn't correctly validate or resolve the district, the map/report don't render correctly, unauthorized access isn't actually rejected), that's a real bug to fix with its own test before considering this complete.
