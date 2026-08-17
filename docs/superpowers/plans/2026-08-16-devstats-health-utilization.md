# Dev Stats Health Utilization & Disease Burden Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract three more tables from Development Statistics 2025's Health chapter (Table 120 Patients Treated, Table 123 District Immunization, Table 124 District Malaria) and surface them in the report and the QGIS map — report + GIS enrichment only, no gap-score changes.

**Architecture:** Three new extraction functions in the existing `scripts/17_extract_devstats_health.py` (reusing its already-imported `iter_district_rows`/`iter_district_rows_raw` helpers, one shared-helper bug fix needed first), each writing its own CSV. Those three CSVs join onto the existing `KP_Districts` shapefile layer and a new report subsection, following the exact patterns `dev_stats_health.csv`/`dev_stats_roads.csv` already use in `12_write_shapefiles.py` and `14_build_html_report.py`.

**Tech Stack:** Python, `pdfplumber` (via `scripts.lib.pdf_tables.extract_table_rows`, unchanged), `csv`, existing `scripts.lib.districts.normalize_district`.

**Spec:** [docs/superpowers/specs/2026-08-16-devstats-health-utilization-design.md](../specs/2026-08-16-devstats-health-utilization-design.md)

## Global Constraints

- No gap-score changes. `08_compute_district_metrics.py` and `09_gap_score_and_clusters.py`'s `WEIGHTS` are not touched by this plan.
- Fitz page indices are exact, already verified against the real PDF during brainstorming (not to be re-derived): Table 120 → page 247 (single page, all 3 years in one grid); Table 123 → page 252 (latest of a 3-year repeat at 250/251/252, confirmed via each page's own `(20XX-YY)` header); Table 124 → page 255 (latest of a 3-year repeat at 253/254/255, confirmed via the "2024" label embedded in that page's own table content).
- Table 123's 11 dose columns are **raw counts, not coverage percentages** — no per-district child-population denominator exists in the source to compute a rate against, so none is invented anywhere in this plan (extraction, report, or GIS layer).
- Shapefile DBF field names stay within the existing 10-character convention (`para_staf`, `pop_pbed`, etc.).
- Test convention for this file only: `scripts/17_extract_devstats_health.py` has zero pytest unit tests today (verified only via `tests/verify_devstats_health.py`, a plain script run against real generated output). This plan follows that exact convention for the three new tables rather than introducing inconsistent mocked pytest coverage for this one file. `scripts/lib/pdf_tables.py`'s own functions (tested in `tests/test_pdf_tables.py` via synthetic in-memory PDFs) are unchanged by this plan.
- `12_write_shapefiles.py` and `14_build_html_report.py` are exercised by the full real pipeline run in the final task, not by new pytest fixtures — matching how those two files already have no dedicated per-function pytest suite of their own for this kind of data-plumbing change (their existing tests are the wording/section-presence tests added in the travel-time-routing work, which don't cover new columns).

---

### Task 1: Fix shared title-row filter bug + extract Table 120 (Patients Treated)

**Files:**
- Modify: `scripts/17_extract_devstats_health.py`
- Create: `tests/verify_devstats_patients.py`

**Interfaces:**
- Consumes: `scripts.lib.pdf_tables.extract_table_rows` (existing, unchanged), `scripts.lib.districts.normalize_district` (existing, unchanged).
- Produces: `data/processed/dev_stats_patients_treated.csv` (columns `district, patients_total_2024, patients_indoor_2024, patients_outdoor_2024`), consumed by Task 4 (`12_write_shapefiles.py`) and Task 5 (`14_build_html_report.py`).

A bug found during brainstorming: Table 124's page (fitz 255) has its title text merged into the first data cell of the extracted grid (`"DISTRICT WISE MALARIA CONTROL ACTIVITIES\nIN KHYBER PAKHTUNKHWA\nTable No. 124 (Number)"`), which the current `label.startswith("Table No.")` check does **not** catch (the marker isn't at the start of the string). Fixed here, in the first task touching this file, since it's a safe, backward-compatible generalization (`"Table No." in label` — no real district name could ever contain that substring) needed before Task 3's Table 124 extraction, and it's zero-risk to apply now even though Tables 120/123 don't hit this specific edge case.

- [ ] **Step 1: Fix the title-row filter in `iter_district_rows` and `iter_district_rows_raw`**

Find:

```python
def iter_district_rows(page_index):
    """Yield (canonical_district_name, numeric_cells) for every district
    data row on a page, skipping header rows, the "Khyber Pakhtunkhwa"
    provincial total row, and any stray marker-text rows."""
    rows = extract_table_rows(str(PDF_PATH), page_index)
    for row in rows:
        if not row or not row[0]:
            continue
        label = row[0].replace("\n", " ").strip()
        if not label or label.startswith("Table No.") or label.startswith("Tabel No."):
            continue
        if label in ("District", "Districts") or label == "Khyber Pakhtunkhwa":
            continue
        if label.startswith("Note:") or label.startswith("Source"):
            continue
        clean_label = label.rstrip("*").strip()
        canonical = normalize_district(clean_label)
        numeric_cells = [parse_int(c) for c in row[1:]]
        yield canonical, numeric_cells


def iter_district_rows_raw(page_index):
    """Like iter_district_rows, but yields the raw (stripped) string
    cells instead of parsed integers - needed where "-" means "no data"
    (e.g. a ratio undefined because the denominator is zero) rather than
    a true zero, a distinction parse_int() collapses."""
    rows = extract_table_rows(str(PDF_PATH), page_index)
    for row in rows:
        if not row or not row[0]:
            continue
        label = row[0].replace("\n", " ").strip()
        if not label or label.startswith("Table No.") or label.startswith("Tabel No."):
            continue
        if label in ("District", "Districts") or label == "Khyber Pakhtunkhwa":
            continue
        if label.startswith("Note:") or label.startswith("Source"):
            continue
        clean_label = label.rstrip("*").strip()
        canonical = normalize_district(clean_label)
        yield canonical, [c.strip() for c in row[1:]]
```

Replace with:

```python
def iter_district_rows(page_index):
    """Yield (canonical_district_name, numeric_cells) for every district
    data row on a page, skipping header rows, the "Khyber Pakhtunkhwa"
    provincial total row, and any stray marker-text rows."""
    rows = extract_table_rows(str(PDF_PATH), page_index)
    for row in rows:
        if not row or not row[0]:
            continue
        label = row[0].replace("\n", " ").strip()
        # "in" rather than "startswith": some pages (e.g. Table 124's,
        # fitz page 255) merge the page's floating title text into the
        # first data cell ahead of the "Table No. N" marker, so the
        # marker isn't always at the start of the string. No real
        # district name can ever contain "Table No.", so this widened
        # check is a strict superset of the old one, not a behavior
        # change for any currently-working table.
        if not label or "Table No." in label or "Tabel No." in label:
            continue
        if label in ("District", "Districts") or label == "Khyber Pakhtunkhwa":
            continue
        if label.startswith("Note:") or label.startswith("Source"):
            continue
        clean_label = label.rstrip("*").strip()
        canonical = normalize_district(clean_label)
        numeric_cells = [parse_int(c) for c in row[1:]]
        yield canonical, numeric_cells


def iter_district_rows_raw(page_index):
    """Like iter_district_rows, but yields the raw (stripped) string
    cells instead of parsed integers - needed where "-" means "no data"
    (e.g. a ratio undefined because the denominator is zero) rather than
    a true zero, a distinction parse_int() collapses."""
    rows = extract_table_rows(str(PDF_PATH), page_index)
    for row in rows:
        if not row or not row[0]:
            continue
        label = row[0].replace("\n", " ").strip()
        if not label or "Table No." in label or "Tabel No." in label:
            continue
        if label in ("District", "Districts") or label == "Khyber Pakhtunkhwa":
            continue
        if label.startswith("Note:") or label.startswith("Source"):
            continue
        clean_label = label.rstrip("*").strip()
        canonical = normalize_district(clean_label)
        yield canonical, [c.strip() for c in row[1:]]
```

- [ ] **Step 2: Add the page constant and extraction function**

Find:

```python
PAGE_GOVT_INSTITUTIONS = 230  # Table 112, latest (2024) year page
PAGE_PVT_HOSPITALS = 234      # Table 114
PAGE_POP_PER_BED = 236        # Table 115
PAGE_STAFF_POSTED = 242       # Table 117, latest (2024) year page
PAGE_PVT_PRACTITIONERS = 246  # Table 119
```

Replace with:

```python
PAGE_GOVT_INSTITUTIONS = 230   # Table 112, latest (2024) year page
PAGE_PVT_HOSPITALS = 234       # Table 114
PAGE_POP_PER_BED = 236         # Table 115
PAGE_STAFF_POSTED = 242        # Table 117, latest (2024) year page
PAGE_PVT_PRACTITIONERS = 246   # Table 119
PAGE_PATIENTS_TREATED = 247    # Table 120, single page holds all 3 years in one grid
PAGE_IMMUNIZATION = 252        # Table 123, latest (2023-24) of a 3-year run at pages 250-252
PAGE_MALARIA = 255             # Table 124, latest (2024) of a 3-year run at pages 253-255
```

Find:

```python
def main():
    district_totals = {}
```

Replace with:

```python
def main_patients_treated():
    """Table 120: District Wise Number of Patients Treated. Single page
    holds all three years (2022/2023/2024) in one grid, each year with
    Total/Indoor/Outdoor columns - 9 numeric cells per row, take the last
    3 (2024)."""
    rows_out = []
    for district, cells in iter_district_rows(PAGE_PATIENTS_TREATED):
        if len(cells) < 9:
            continue
        rows_out.append(
            {
                "district": district,
                "patients_total_2024": cells[6],
                "patients_indoor_2024": cells[7],
                "patients_outdoor_2024": cells[8],
            }
        )
    rows_out.sort(key=lambda r: r["district"])

    out_path = PROCESSED / "dev_stats_patients_treated.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["district", "patients_total_2024", "patients_indoor_2024", "patients_outdoor_2024"]
        )
        writer.writeheader()
        writer.writerows(rows_out)
    print(f"Wrote dev_stats_patients_treated.csv for {len(rows_out)} districts")


def main():
    district_totals = {}
```

Find (the bottom of the file):

```python
if __name__ == "__main__":
    main()
```

Replace with:

```python
if __name__ == "__main__":
    main()
    main_patients_treated()
```

- [ ] **Step 3: Write `tests/verify_devstats_patients.py`**

```python
import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "dev_stats_patients_treated.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 35, f"Expected all 35 KP districts, got {len(rows)}"

    for r in rows:
        total = int(r["patients_total_2024"])
        indoor = int(r["patients_indoor_2024"])
        outdoor = int(r["patients_outdoor_2024"])
        assert total >= 0 and indoor >= 0 and outdoor >= 0, f"{r['district']}: negative value found"
        assert total == indoor + outdoor, (
            f"{r['district']}: total ({total}) != indoor ({indoor}) + outdoor ({outdoor}) - "
            "the source table's own structure guarantees this, so a mismatch means a parsing error"
        )

    print(f"OK: dev_stats_patients_treated.csv covers {len(rows)} districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the extraction and the verify script against the real PDF**

Run: `python scripts/17_extract_devstats_health.py`
Expected: prints `Wrote dev_stats_health.csv for 35 districts ...` followed by `Wrote dev_stats_patients_treated.csv for 35 districts`

Run: `python tests/verify_devstats_patients.py`
Expected: `OK: dev_stats_patients_treated.csv covers 35 districts`

If the district count isn't 35, or the total/indoor/outdoor assertion fails, stop and inspect `extract_table_rows(str(PDF_PATH), 247)`'s raw output directly rather than guessing at a fix - the column mapping was verified during brainstorming but should be re-confirmed against the live extraction, not assumed.

- [ ] **Step 5: Run the full test suite to confirm nothing else broke**

Run: `pytest tests/ -q`
Expected: all tests pass (this task didn't touch anything with pytest coverage, but confirm the filter-fix didn't regress the already-working Table 112/114/115/117/119 extractions by also running Step 6 below)

- [ ] **Step 6: Re-verify the pre-existing `dev_stats_health.csv` extraction still passes**

Run: `python tests/verify_devstats_health.py`
Expected: `OK: dev_stats_health.csv covers 35 districts` - confirms the widened title-row filter (`"Table No." in label` instead of `.startswith`) didn't break any of the five tables already using `iter_district_rows`/`iter_district_rows_raw`.

- [ ] **Step 7: Commit**

```bash
git add scripts/17_extract_devstats_health.py tests/verify_devstats_patients.py
git commit -m "feat: extract Table 120 (Patients Treated) from Dev Stats 2025

Also fixes a title-row filter bug in the shared iter_district_rows/
iter_district_rows_raw helpers (found during brainstorming): some pages
merge floating title text into the first data cell ahead of the
\"Table No. N\" marker, which the old startswith() check missed.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Extract Table 123 (District Immunization)

**Files:**
- Modify: `scripts/17_extract_devstats_health.py`
- Create: `tests/verify_devstats_immunization.py`

**Interfaces:**
- Consumes: `iter_district_rows` (Task 1, fixed).
- Produces: `data/processed/dev_stats_immunization.csv` (columns `district, bcg, opv0, opv_dpt1, opv_dpt2, opv_dpt3, measles, tt1, tt2, tt3, tt4, tt5`), consumed by Task 4 and Task 5.

- [ ] **Step 1: Add the extraction function**

Find:

```python
def main():
    district_totals = {}
```

Replace with:

```python
def main_immunization():
    """Table 123: District Wise Expanded Programme on Immunization,
    latest year (2023-24, page 252 of a 3-year repeat at 250-252). 11
    raw dose counts per district - not coverage percentages, since no
    per-district child-population denominator is published in this
    table to compute a rate against."""
    rows_out = []
    for district, cells in iter_district_rows(PAGE_IMMUNIZATION):
        if len(cells) < 11:
            continue
        rows_out.append(
            {
                "district": district,
                "bcg": cells[0],
                "opv0": cells[1],
                "opv_dpt1": cells[2],
                "opv_dpt2": cells[3],
                "opv_dpt3": cells[4],
                "measles": cells[5],
                "tt1": cells[6],
                "tt2": cells[7],
                "tt3": cells[8],
                "tt4": cells[9],
                "tt5": cells[10],
            }
        )
    rows_out.sort(key=lambda r: r["district"])

    out_path = PROCESSED / "dev_stats_immunization.csv"
    fieldnames = ["district", "bcg", "opv0", "opv_dpt1", "opv_dpt2", "opv_dpt3", "measles", "tt1", "tt2", "tt3", "tt4", "tt5"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)
    print(f"Wrote dev_stats_immunization.csv for {len(rows_out)} districts")


def main():
    district_totals = {}
```

Find:

```python
if __name__ == "__main__":
    main()
    main_patients_treated()
```

Replace with:

```python
if __name__ == "__main__":
    main()
    main_patients_treated()
    main_immunization()
```

- [ ] **Step 2: Write `tests/verify_devstats_immunization.py`**

```python
import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "dev_stats_immunization.csv"
DOSE_FIELDS = ["bcg", "opv0", "opv_dpt1", "opv_dpt2", "opv_dpt3", "measles", "tt1", "tt2", "tt3", "tt4", "tt5"]


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 35, f"Expected all 35 KP districts, got {len(rows)}"

    for r in rows:
        for field in DOSE_FIELDS:
            assert int(r[field]) >= 0, f"{r['district']}.{field} is negative: {r[field]}"
        # A zero BCG count would indicate a parsing gap, not a real
        # absence of any birth-dose vaccination in an entire district -
        # mirrors verify_devstats_health.py's govt_institutions > 0 check.
        assert int(r["bcg"]) > 0, f"{r['district']} has zero BCG doses - likely a parsing gap"

    print(f"OK: dev_stats_immunization.csv covers {len(rows)} districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the extraction and verify script against the real PDF**

Run: `python scripts/17_extract_devstats_health.py`
Expected: three "Wrote ..." lines now, ending with `Wrote dev_stats_immunization.csv for 35 districts`

Run: `python tests/verify_devstats_immunization.py`
Expected: `OK: dev_stats_immunization.csv covers 35 districts`

If `bcg > 0` fails for a real district or the row count is off, inspect `extract_table_rows(str(PDF_PATH), 252)` directly before changing the column indices - the mapping was verified live during brainstorming.

- [ ] **Step 4: Commit**

```bash
git add scripts/17_extract_devstats_health.py tests/verify_devstats_immunization.py
git commit -m "feat: extract Table 123 (District Immunization) from Dev Stats 2025

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Extract Table 124 (District Malaria Control)

**Files:**
- Modify: `scripts/17_extract_devstats_health.py`
- Create: `tests/verify_devstats_malaria.py`

**Interfaces:**
- Consumes: `iter_district_rows` (Task 1, fixed - this is the table whose title-row bug motivated that fix).
- Produces: `data/processed/dev_stats_malaria.csv` (columns `district, blood_slides_examined, malaria_cases, malaria_cases_treated`), consumed by Task 4 and Task 5.

- [ ] **Step 1: Add the extraction function**

Find:

```python
def main():
    district_totals = {}
```

Replace with:

```python
def main_malaria():
    """Table 124: District Wise Malaria Control Activities, latest year
    (2024, page 255 of a 3-year repeat at 253-255). This is the page
    whose title text merges into the first data cell (see Task 1's fix
    to iter_district_rows) - if that fix regresses, this extraction is
    where it would first surface as a garbage "district" name."""
    rows_out = []
    for district, cells in iter_district_rows(PAGE_MALARIA):
        if len(cells) < 3:
            continue
        rows_out.append(
            {
                "district": district,
                "blood_slides_examined": cells[0],
                "malaria_cases": cells[1],
                "malaria_cases_treated": cells[2],
            }
        )
    rows_out.sort(key=lambda r: r["district"])

    out_path = PROCESSED / "dev_stats_malaria.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["district", "blood_slides_examined", "malaria_cases", "malaria_cases_treated"]
        )
        writer.writeheader()
        writer.writerows(rows_out)
    print(f"Wrote dev_stats_malaria.csv for {len(rows_out)} districts")


def main():
    district_totals = {}
```

Find:

```python
if __name__ == "__main__":
    main()
    main_patients_treated()
    main_immunization()
```

Replace with:

```python
if __name__ == "__main__":
    main()
    main_patients_treated()
    main_immunization()
    main_malaria()
```

- [ ] **Step 2: Write `tests/verify_devstats_malaria.py`**

```python
import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "dev_stats_malaria.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 35, f"Expected all 35 KP districts, got {len(rows)}"

    for r in rows:
        # A regression of Task 1's title-row filter fix would surface
        # here first, as a garbage district name pulled from the page's
        # merged title text rather than a real KP district.
        assert "Table No." not in r["district"] and "DISTRICT WISE" not in r["district"].upper(), (
            f"Garbage district name found: {r['district']!r} - the title-row filter fix likely regressed"
        )
        assert int(r["blood_slides_examined"]) >= 0
        assert int(r["malaria_cases"]) >= 0
        assert int(r["malaria_cases_treated"]) >= 0

    print(f"OK: dev_stats_malaria.csv covers {len(rows)} districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the extraction and verify script against the real PDF**

Run: `python scripts/17_extract_devstats_health.py`
Expected: four "Wrote ..." lines now, ending with `Wrote dev_stats_malaria.csv for 35 districts`

Run: `python tests/verify_devstats_malaria.py`
Expected: `OK: dev_stats_malaria.csv covers 35 districts`

This is the real test of Task 1's filter fix - if the garbage-district-name assertion fails, the fix from Task 1 needs revisiting, not this extraction's column indices.

- [ ] **Step 4: Commit**

```bash
git add scripts/17_extract_devstats_health.py tests/verify_devstats_malaria.py
git commit -m "feat: extract Table 124 (District Malaria Control) from Dev Stats 2025

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Wire the three new CSVs into the QGIS `KP_Districts` layer

**Files:**
- Modify: `scripts/12_write_shapefiles.py`
- Modify: `tests/verify_shapefiles.py`

**Interfaces:**
- Consumes: `data/processed/dev_stats_patients_treated.csv`, `dev_stats_immunization.csv`, `dev_stats_malaria.csv` (Tasks 1-3).
- Produces: `gis/KP_Districts.shp`/`.dbf` gains 9 new fields (`pat_total`, `pat_indr`, `pat_outdr`, `bcg`, `opv0`, `opv3`, `measles`, `mal_cases`, `mal_trtd`).

- [ ] **Step 1: Add the 9 new fields to `DISTRICT_FIELDS`**

Find:

```python
DISTRICT_FIELDS = [
    ("district", "C", 50, 0), ("division", "C", 50, 0), ("area_km2", "F", 12, 2),
    ("pop_2023", "N", 12, 0), ("pop_dens", "F", 10, 2), ("terrain", "C", 20, 0),
    ("mean_elev", "F", 10, 1), ("mean_slop", "F", 8, 2), ("terr_diff", "F", 6, 4),
    ("fac_count", "N", 6, 0), ("auth_inst", "N", 6, 0), ("govt_inst", "N", 6, 0), ("govt_beds", "N", 8, 0),
    ("pvt_hosp", "N", 6, 0), ("pvt_beds", "N", 8, 0), ("med_staff", "N", 8, 0),
    ("para_staf", "N", 8, 0), ("pvt_prac", "N", 8, 0), ("pop_pbed", "N", 8, 0),
    ("beds_p1k", "F", 8, 3), ("doc_p1k", "F", 8, 3), ("road_km", "F", 10, 2),
]
```

Replace with:

```python
DISTRICT_FIELDS = [
    ("district", "C", 50, 0), ("division", "C", 50, 0), ("area_km2", "F", 12, 2),
    ("pop_2023", "N", 12, 0), ("pop_dens", "F", 10, 2), ("terrain", "C", 20, 0),
    ("mean_elev", "F", 10, 1), ("mean_slop", "F", 8, 2), ("terr_diff", "F", 6, 4),
    ("fac_count", "N", 6, 0), ("auth_inst", "N", 6, 0), ("govt_inst", "N", 6, 0), ("govt_beds", "N", 8, 0),
    ("pvt_hosp", "N", 6, 0), ("pvt_beds", "N", 8, 0), ("med_staff", "N", 8, 0),
    ("para_staf", "N", 8, 0), ("pvt_prac", "N", 8, 0), ("pop_pbed", "N", 8, 0),
    ("beds_p1k", "F", 8, 3), ("doc_p1k", "F", 8, 3), ("road_km", "F", 10, 2),
    # Development Statistics 2025, Tables 120/123/124 (report + GIS
    # enrichment only - not gap-score inputs). Immunization is curated to
    # 4 of the 11 raw dose counts extracted (bcg/opv0/opv3/measles) for a
    # focused choropleth field set; the report table shows all 11.
    ("pat_total", "N", 10, 0), ("pat_indr", "N", 8, 0), ("pat_outdr", "N", 10, 0),
    ("bcg", "N", 8, 0), ("opv0", "N", 8, 0), ("opv3", "N", 8, 0), ("measles", "N", 8, 0),
    ("mal_cases", "N", 8, 0), ("mal_trtd", "N", 8, 0),
]
```

- [ ] **Step 2: Load the three new CSVs and add the fields to each record in `write_districts()`**

Find:

```python
    metrics_by_name = {r["district"]: r for r in district_metrics}
    with open(PROCESSED / "dev_stats_health.csv", newline="", encoding="utf-8") as f:
        health_by_name = {r["district"]: r for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_roads.csv", newline="", encoding="utf-8") as f:
        roads_by_name = {r["district"]: r for r in csv.DictReader(f)}

    records = []
    for d in boundaries["districts"]:
        name = d["district"]
        m = metrics_by_name.get(name, {})
        h = health_by_name.get(name, {})
        rd = roads_by_name.get(name, {})
        records.append(
            {
                "geometry": shape(d["geometry"]),
                "district": name,
                "division": d.get("division") or m.get("division") or "",
                "area_km2": float(m.get("area_km2", d.get("area_km2", 0))),
                "pop_2023": int(m.get("population_2023", 0)),
                "pop_dens": float(m.get("pop_density", 0)),
                "terrain": m.get("terrain", ""),
                "mean_elev": float(m.get("mean_elev_m", 0) or 0),
                "mean_slop": float(m.get("mean_slope_deg", 0) or 0),
                "terr_diff": float(m.get("terrain_difficulty", 0) or 0),
                "fac_count": int(m.get("facility_count", 0) or 0),
                "auth_inst": int(m.get("govt_pvt_institutions", 0) or 0),
                "govt_inst": int(h.get("govt_institutions", 0) or 0),
                "govt_beds": int(h.get("govt_beds", 0) or 0),
                "pvt_hosp": int(h.get("pvt_hospitals", 0) or 0),
                "pvt_beds": int(h.get("pvt_beds", 0) or 0),
                "med_staff": int(h.get("medical_staff", 0) or 0),
                "para_staf": int(h.get("paramedical_staff", 0) or 0),
                "pvt_prac": int(h.get("pvt_practitioners", 0) or 0),
                "pop_pbed": int(h.get("pop_per_bed", 0) or 0),
                "beds_p1k": float(m.get("beds_per_1000", 0) or 0),
                "doc_p1k": float(m.get("doctors_per_1000", 0) or 0),
                "road_km": float(rd.get("road_km_total", 0) or 0),
            }
        )
    write_shapefile(str(GIS_DIR / "KP_Districts"), "POLYGON", records, DISTRICT_FIELDS)
```

Replace with:

```python
    metrics_by_name = {r["district"]: r for r in district_metrics}
    with open(PROCESSED / "dev_stats_health.csv", newline="", encoding="utf-8") as f:
        health_by_name = {r["district"]: r for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_roads.csv", newline="", encoding="utf-8") as f:
        roads_by_name = {r["district"]: r for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_patients_treated.csv", newline="", encoding="utf-8") as f:
        patients_by_name = {r["district"]: r for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_immunization.csv", newline="", encoding="utf-8") as f:
        immunization_by_name = {r["district"]: r for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_malaria.csv", newline="", encoding="utf-8") as f:
        malaria_by_name = {r["district"]: r for r in csv.DictReader(f)}

    records = []
    for d in boundaries["districts"]:
        name = d["district"]
        m = metrics_by_name.get(name, {})
        h = health_by_name.get(name, {})
        rd = roads_by_name.get(name, {})
        pat = patients_by_name.get(name, {})
        imm = immunization_by_name.get(name, {})
        mal = malaria_by_name.get(name, {})
        records.append(
            {
                "geometry": shape(d["geometry"]),
                "district": name,
                "division": d.get("division") or m.get("division") or "",
                "area_km2": float(m.get("area_km2", d.get("area_km2", 0))),
                "pop_2023": int(m.get("population_2023", 0)),
                "pop_dens": float(m.get("pop_density", 0)),
                "terrain": m.get("terrain", ""),
                "mean_elev": float(m.get("mean_elev_m", 0) or 0),
                "mean_slop": float(m.get("mean_slope_deg", 0) or 0),
                "terr_diff": float(m.get("terrain_difficulty", 0) or 0),
                "fac_count": int(m.get("facility_count", 0) or 0),
                "auth_inst": int(m.get("govt_pvt_institutions", 0) or 0),
                "govt_inst": int(h.get("govt_institutions", 0) or 0),
                "govt_beds": int(h.get("govt_beds", 0) or 0),
                "pvt_hosp": int(h.get("pvt_hospitals", 0) or 0),
                "pvt_beds": int(h.get("pvt_beds", 0) or 0),
                "med_staff": int(h.get("medical_staff", 0) or 0),
                "para_staf": int(h.get("paramedical_staff", 0) or 0),
                "pvt_prac": int(h.get("pvt_practitioners", 0) or 0),
                "pop_pbed": int(h.get("pop_per_bed", 0) or 0),
                "beds_p1k": float(m.get("beds_per_1000", 0) or 0),
                "doc_p1k": float(m.get("doctors_per_1000", 0) or 0),
                "road_km": float(rd.get("road_km_total", 0) or 0),
                "pat_total": int(pat.get("patients_total_2024", 0) or 0),
                "pat_indr": int(pat.get("patients_indoor_2024", 0) or 0),
                "pat_outdr": int(pat.get("patients_outdoor_2024", 0) or 0),
                "bcg": int(imm.get("bcg", 0) or 0),
                "opv0": int(imm.get("opv0", 0) or 0),
                "opv3": int(imm.get("opv_dpt3", 0) or 0),
                "measles": int(imm.get("measles", 0) or 0),
                "mal_cases": int(mal.get("malaria_cases", 0) or 0),
                "mal_trtd": int(mal.get("malaria_cases_treated", 0) or 0),
            }
        )
    write_shapefile(str(GIS_DIR / "KP_Districts"), "POLYGON", records, DISTRICT_FIELDS)
```

- [ ] **Step 3: Add the new fields to `tests/verify_shapefiles.py`'s expected-fields check**

Find:

```python
EXPECTED_FIELDS = {
    "KP_Districts": {
        "mean_elev", "mean_slop", "terr_diff", "fac_count", "auth_inst", "govt_inst", "govt_beds",
        "pvt_hosp", "pvt_beds", "med_staff", "para_staf", "pvt_prac", "pop_pbed",
        "beds_p1k", "doc_p1k", "road_km",
    },
    "KP_District_Gap_Scores": {"pop_2029", "pop_2031", "pop_2046", "fac_nd29", "fac_nd31", "fac_nd46", "beds_nd29", "beds_nd31", "beds_nd46"},
}
```

Replace with:

```python
EXPECTED_FIELDS = {
    "KP_Districts": {
        "mean_elev", "mean_slop", "terr_diff", "fac_count", "auth_inst", "govt_inst", "govt_beds",
        "pvt_hosp", "pvt_beds", "med_staff", "para_staf", "pvt_prac", "pop_pbed",
        "beds_p1k", "doc_p1k", "road_km",
        "pat_total", "pat_indr", "pat_outdr", "bcg", "opv0", "opv3", "measles", "mal_cases", "mal_trtd",
    },
    "KP_District_Gap_Scores": {"pop_2029", "pop_2031", "pop_2046", "fac_nd29", "fac_nd31", "fac_nd46", "beds_nd29", "beds_nd31", "beds_nd46"},
}
```

- [ ] **Step 4: Run the shapefile writer and verify script against real data**

Run: `python scripts/12_write_shapefiles.py`
Expected: `Wrote all 6 shapefile layers to gis/` (no traceback - a missing new CSV would raise `FileNotFoundError` here, confirming Tasks 1-3 ran first)

Run: `python tests/verify_shapefiles.py`
Expected: all 6 layers print `OK: ... has N features`, including `KP_Districts` with the 9 new fields present (no `missing expected fields` assertion failure)

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add scripts/12_write_shapefiles.py tests/verify_shapefiles.py
git commit -m "feat: add patients-treated/immunization/malaria fields to the KP_Districts GIS layer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Add the three new tables to the HTML report

**Files:**
- Modify: `scripts/14_build_html_report.py`

**Interfaces:**
- Consumes: `data/processed/dev_stats_patients_treated.csv`, `dev_stats_immunization.csv`, `dev_stats_malaria.csv` (Tasks 1-3).
- Produces: three new `<h3>` subsections inside `<section id="infrastructure-context">`, after "Facility Count Cross-Validation."

- [ ] **Step 1: Load the three new CSVs in `load_data()`**

Find:

```python
    with open(PROCESSED / "dev_stats_roads.csv", newline="", encoding="utf-8") as f:
        dev_roads = list(csv.DictReader(f))
    with open(PROCESSED / "facility_cross_validation.csv", newline="", encoding="utf-8") as f:
        cross_val = list(csv.DictReader(f))
    dev_budget = json.loads((PROCESSED / "dev_stats_budget.json").read_text())
    supplemental_records = load_supplemental_records()
    return boundaries, metrics, facilities, sites, dev_health, dev_roads, cross_val, dev_budget, supplemental_records
```

Replace with:

```python
    with open(PROCESSED / "dev_stats_roads.csv", newline="", encoding="utf-8") as f:
        dev_roads = list(csv.DictReader(f))
    with open(PROCESSED / "facility_cross_validation.csv", newline="", encoding="utf-8") as f:
        cross_val = list(csv.DictReader(f))
    with open(PROCESSED / "dev_stats_patients_treated.csv", newline="", encoding="utf-8") as f:
        dev_patients = list(csv.DictReader(f))
    with open(PROCESSED / "dev_stats_immunization.csv", newline="", encoding="utf-8") as f:
        dev_immunization = list(csv.DictReader(f))
    with open(PROCESSED / "dev_stats_malaria.csv", newline="", encoding="utf-8") as f:
        dev_malaria = list(csv.DictReader(f))
    dev_budget = json.loads((PROCESSED / "dev_stats_budget.json").read_text())
    supplemental_records = load_supplemental_records()
    return (
        boundaries, metrics, facilities, sites, dev_health, dev_roads, cross_val, dev_budget,
        supplemental_records, dev_patients, dev_immunization, dev_malaria,
    )
```

- [ ] **Step 2: Update `build()`'s call to `load_data()`**

Find:

```python
    boundaries, metrics, facilities, sites, dev_health, dev_roads, cross_val, dev_budget, supplemental_records = load_data()
```

Replace with:

```python
    (
        boundaries, metrics, facilities, sites, dev_health, dev_roads, cross_val, dev_budget,
        supplemental_records, dev_patients, dev_immunization, dev_malaria,
    ) = load_data()
```

- [ ] **Step 3: Add the three `*_rows_html()` functions**

Find:

```python
def cross_val_rows_html(cross_val):
    rows = []
    for r in sorted(cross_val, key=lambda r: r["district"]):
        official = f"{int(r['govt_institutions_official']):,}" if r["govt_institutions_official"] != "" else "&mdash;"
        diff = f"{int(r['difference']):+,}" if r["difference"] != "" else "&mdash;"
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td class=\"num\">{int(r['merged_facility_count']):,}</td>"
            f"<td class=\"num\">{official}</td>"
            f"<td class=\"num\">{diff}</td>"
            f"<td>{r['note']}</td>"
            "</tr>"
        )
    return "\n".join(rows)
```

Replace with:

```python
def cross_val_rows_html(cross_val):
    rows = []
    for r in sorted(cross_val, key=lambda r: r["district"]):
        official = f"{int(r['govt_institutions_official']):,}" if r["govt_institutions_official"] != "" else "&mdash;"
        diff = f"{int(r['difference']):+,}" if r["difference"] != "" else "&mdash;"
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td class=\"num\">{int(r['merged_facility_count']):,}</td>"
            f"<td class=\"num\">{official}</td>"
            f"<td class=\"num\">{diff}</td>"
            f"<td>{r['note']}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def patients_treated_rows_html(dev_patients):
    rows = []
    for r in sorted(dev_patients, key=lambda r: r["district"]):
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td class=\"num\">{int(r['patients_total_2024']):,}</td>"
            f"<td class=\"num\">{int(r['patients_indoor_2024']):,}</td>"
            f"<td class=\"num\">{int(r['patients_outdoor_2024']):,}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def immunization_rows_html(dev_immunization):
    rows = []
    for r in sorted(dev_immunization, key=lambda r: r["district"]):
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td class=\"num\">{int(r['bcg']):,}</td>"
            f"<td class=\"num\">{int(r['opv0']):,}</td>"
            f"<td class=\"num\">{int(r['opv_dpt1']):,}</td>"
            f"<td class=\"num\">{int(r['opv_dpt2']):,}</td>"
            f"<td class=\"num\">{int(r['opv_dpt3']):,}</td>"
            f"<td class=\"num\">{int(r['measles']):,}</td>"
            f"<td class=\"num\">{int(r['tt1']):,}</td>"
            f"<td class=\"num\">{int(r['tt2']):,}</td>"
            f"<td class=\"num\">{int(r['tt3']):,}</td>"
            f"<td class=\"num\">{int(r['tt4']):,}</td>"
            f"<td class=\"num\">{int(r['tt5']):,}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def malaria_rows_html(dev_malaria):
    rows = []
    for r in sorted(dev_malaria, key=lambda r: r["district"]):
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td class=\"num\">{int(r['blood_slides_examined']):,}</td>"
            f"<td class=\"num\">{int(r['malaria_cases']):,}</td>"
            f"<td class=\"num\">{int(r['malaria_cases_treated']):,}</td>"
            "</tr>"
        )
    return "\n".join(rows)
```

- [ ] **Step 4: Insert the three new report subsections after Facility Count Cross-Validation**

Find:

```python
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Merged Facility Count</th><th>Dev Stats Govt. Institutions</th>
<th>Difference</th><th>Note</th></tr></thead>
<tbody>
{cross_val_rows_html(cross_val)}
</tbody>
</table>
</div>
</section>

<section id="terrain-elevation">
```

Replace with:

```python
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Merged Facility Count</th><th>Dev Stats Govt. Institutions</th>
<th>Difference</th><th>Note</th></tr></thead>
<tbody>
{cross_val_rows_html(cross_val)}
</tbody>
</table>
</div>

<h3>Health Service Utilization (Development Statistics 2025)</h3>
<p>District-wise patients treated in 2024 (Table 120 of Development
Statistics 2025), split into indoor (admitted) and outdoor (outpatient) visits. A district with a high outdoor share
relative to indoor may indicate reliance on outpatient-only care rather than real admission capacity - context this
report's own facility/bed counts don't otherwise capture.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Total Patients (2024)</th><th>Indoor</th><th>Outdoor</th></tr></thead>
<tbody>
{patients_treated_rows_html(dev_patients)}
</tbody>
</table>
</div>

<h3>Immunization Coverage (Development Statistics 2025)</h3>
<p>District-wise counts of children immunized by dose, 2023-24 (Table 123 of Development Statistics 2025). These are
<strong>raw counts, not coverage percentages</strong> - the source table publishes no per-district child-population
denominator to compute a rate against, so none is estimated here. Comparing a district's later-round doses (e.g.
OPV round 3, or T.T-3 through T.T-5) against its birth-dose BCG count is still informative as a rough dropout signal,
even without a formal coverage rate.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>BCG</th><th>OPV-0</th><th>OPV/DPT-1</th><th>OPV/DPT-2</th><th>OPV/DPT-3</th>
<th>Measles</th><th>T.T-1</th><th>T.T-2</th><th>T.T-3</th><th>T.T-4</th><th>T.T-5</th></tr></thead>
<tbody>
{immunization_rows_html(dev_immunization)}
</tbody>
</table>
</div>

<h3>Malaria Control Activities (Development Statistics 2025)</h3>
<p>District-wise malaria surveillance and treatment, 2024 (Table 124 of Development Statistics 2025) - blood slides
examined (testing volume), confirmed malaria cases, and cases treated.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Blood Slides Examined</th><th>Malaria Cases</th><th>Cases Treated</th></tr></thead>
<tbody>
{malaria_rows_html(dev_malaria)}
</tbody>
</table>
</div>
</section>

<section id="terrain-elevation">
```

(2024 is a plain literal, matching how the rest of this report states fixed source years elsewhere, e.g. "Figures below are each district's latest available year in that edition (2024 for government institutions/beds/staffing...)" - not something worth a defensive guard around, since it's already baked into the CSV's own column names like `patients_total_2024`.)

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass (the existing report-wording tests in `tests/test_report_wording.py` call `findings_html`/`methodology_html` directly and don't touch `load_data()`/`build()`, so they're unaffected by this change)

- [ ] **Step 6: Commit**

```bash
git add scripts/14_build_html_report.py
git commit -m "feat: add patients-treated/immunization/malaria sections to the HTML report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Full pipeline run and manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 2: Run the affected pipeline stages in order against real data**

Run: `python scripts/17_extract_devstats_health.py && python scripts/12_write_shapefiles.py && python scripts/13_build_qgis_project.py && python scripts/14_build_html_report.py`

(`13_build_qgis_project.py` is included because `12`'s shapefile changes need the `.qgz` project rebuilt to reflect the new fields in QGIS - matching the real dependency chain already confirmed the hard way in the travel-time-routing work's Task 9.)

Expected: four success lines, no traceback.

- [ ] **Step 3: Run the four new/updated verify scripts**

Run: `python tests/verify_devstats_patients.py && python tests/verify_devstats_immunization.py && python tests/verify_devstats_malaria.py && python tests/verify_shapefiles.py`

Expected: all four print `OK: ...`.

- [ ] **Step 4: Spot-check the report renders the three new sections**

Run: `python -c "s = open('report/KP_Healthcare_Plan.html', encoding='utf-8').read(); assert 'Health Service Utilization' in s; assert 'Immunization Coverage' in s; assert 'Malaria Control Activities' in s; print('all three sections present')"`

Expected: `all three sections present`

- [ ] **Step 5: Confirm the new GIS fields in QGIS**

Open (or reload, if already running) `gis/KP_Healthcare_Plan.qgz` in QGIS, open the `KP_Districts` layer's attribute table (right-click the layer → Open Attribute Table), and confirm the 9 new columns (`pat_total`, `pat_indr`, `pat_outdr`, `bcg`, `opv0`, `opv3`, `measles`, `mal_cases`, `mal_trtd`) are present with plausible non-zero values for most districts. Optionally set one (e.g. `mal_cases`) as a graduated-color choropleth field (Layer Properties → Symbology) to visually confirm it renders sensibly across the province - a real map, not just an attribute-table number check.

- [ ] **Step 6: Report findings**

If everything above checks out clean, this task (and the whole plan) is done - no further commit needed beyond what Tasks 1-5 already made. If anything looks wrong (a column entirely zero across every district, a district missing from a new CSV, a QGIS field showing as text instead of numeric), that's a real bug to fix with its own test before considering this complete.
