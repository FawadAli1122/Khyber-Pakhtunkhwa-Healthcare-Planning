# Facility Readiness (WHO SARA Framework) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Structure the existing phase 4b document-upload/AI-extraction feature around WHO's real SARA domains and tracer items, and compute/display a real SARA-style readiness score once data exists — closing the gap the report's own Methodology section already names.

**Architecture:** A new shared module (`scripts/lib/facility_readiness.py`) holds the SARA reference data and scoring logic, importable by both `server/supplemental_data.py` (AI-extraction prompt guidance) and `scripts/14_build_html_report.py` (scoring/display) — matching the existing `scripts/lib/supplemental_records.py` layering (`scripts/` pipeline code cannot import from `server/`; `server/` already imports from `scripts/lib/`). No new data store — `supplemental_records.csv`'s existing schema is reused entirely.

**Tech Stack:** Python, existing `server/ai_client` (unchanged), `html.escape` for report rendering (matching the existing XSS-safety convention in this file).

**Spec:** [docs/superpowers/specs/2026-08-16-facility-readiness-design.md](../specs/2026-08-16-facility-readiness-design.md)

## Global Constraints

- No new CSV file, no schema change to `supplemental_records.csv` (`district, facility, category, label, detail, source_document, added_at`). SARA data is just records whose `category`/`label` happen to exactly match a known domain/tracer-item name.
- `scripts/lib/facility_readiness.py` is the single source of truth for `TRACER_ITEMS` — 5 domains, 43 real WHO SARA tracer items (verified against a published SARA analysis during brainstorming, not guessed). Both `server/supplemental_data.py` and `scripts/14_build_html_report.py` import it from there.
- Presence/absence: `detail.strip().lower() == "absent"` is the only way a record marks an item as confirmed absent (still *assessed*, contributes 0 to the score). Every other case — `"present"`, a quantity, a note, or even an empty string — counts as present, matching how the existing free-form records already treat "a record exists" as "the fact is true."
- Deduplication before scoring: `supplemental_records.csv` is purely additive (`append_records()` never dedupes). Before scoring, group by `(facility, district, category, label)` and keep only the record with the latest `added_at` — "the most recent assessment wins."
- A domain with zero assessed tracer items for a facility is omitted from that facility's results, not scored as 0%. A facility's overall score is the mean of whichever domain scores exist for it.
- No GIS/shapefile changes, no gap-score changes — report-only, matching the spec's explicit exclusions.
- All AI-derived/free-text content in the new report section (facility names, in particular) must be `html.escape()`-d, matching this file's existing XSS-safety convention (see `tests/test_supplemental_data_section.py::test_supplemental_data_rows_html_escapes_untrusted_content`, the established precedent for this exact class of bug).
- Test convention: pytest with in-memory fixtures throughout (this part of the codebase's dominant convention — unlike the Dev Stats PDF work, none of this is real-file/real-network dependent).

---

### Task 1: `scripts/lib/facility_readiness.py` — SARA reference data and scoring

**Files:**
- Create: `scripts/lib/facility_readiness.py`
- Create: `tests/lib/test_facility_readiness.py`

**Interfaces:**
- Produces: `scripts.lib.facility_readiness.TRACER_ITEMS: dict[str, list[str]]` (5 domains, 43 items), `scripts.lib.facility_readiness.compute_readiness_scores(records: list[dict]) -> dict` (shape: `{"facilities": [{"facility": str, "district": str, "domain_scores": dict[str, float], "overall_score": float | None}], "districts": [{"district": str, "mean_score": float, "facilities_assessed": int}]}`). Task 2 consumes `TRACER_ITEMS`; Task 3 consumes `compute_readiness_scores`.

- [ ] **Step 1: Write the failing tests in `tests/lib/test_facility_readiness.py`**

```python
from scripts.lib import facility_readiness


def make_record(district, facility, category, label, detail, added_at="2026-08-16T00:00:00+00:00"):
    return {
        "district": district, "facility": facility, "category": category, "label": label,
        "detail": detail, "source_document": "test.pdf", "added_at": added_at,
    }


def test_tracer_items_has_five_domains_and_43_items():
    assert set(facility_readiness.TRACER_ITEMS.keys()) == {
        "Basic Amenities", "Basic Equipment", "Standard Precautions for Infection Prevention",
        "Diagnostic Capacity", "Essential Medicines",
    }
    total = sum(len(items) for items in facility_readiness.TRACER_ITEMS.values())
    assert total == 43


def test_compute_readiness_scores_ignores_non_tracer_records():
    records = [make_record("Peshawar", "DHQ Hospital", "outbreak", "Cholera", "12 cases")]
    result = facility_readiness.compute_readiness_scores(records)
    assert result["facilities"] == []
    assert result["districts"] == []


def test_compute_readiness_scores_single_facility_single_domain():
    records = [
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "present"),
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Stethoscope", "present"),
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Adult scale", "absent"),
    ]
    result = facility_readiness.compute_readiness_scores(records)
    assert len(result["facilities"]) == 1
    f = result["facilities"][0]
    assert f["facility"] == "DHQ Hospital"
    assert f["district"] == "Peshawar"
    assert f["domain_scores"] == {"Basic Equipment": 2 / 3}
    assert f["overall_score"] == 2 / 3


def test_compute_readiness_scores_domain_with_zero_assessed_items_is_omitted():
    # Only Basic Equipment has any records - Essential Medicines etc. must
    # not appear in domain_scores at all, and must not drag overall_score
    # toward 0.
    records = [make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "present")]
    result = facility_readiness.compute_readiness_scores(records)
    f = result["facilities"][0]
    assert set(f["domain_scores"].keys()) == {"Basic Equipment"}
    assert f["overall_score"] == 1.0


def test_compute_readiness_scores_multi_domain_overall_is_mean_of_domain_scores():
    records = [
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "present"),
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Stethoscope", "absent"),
        make_record("Peshawar", "DHQ Hospital", "Essential Medicines", "Paracetamol", "present"),
    ]
    result = facility_readiness.compute_readiness_scores(records)
    f = result["facilities"][0]
    # Basic Equipment: 1/2 = 0.5, Essential Medicines: 1/1 = 1.0, mean = 0.75
    assert f["domain_scores"] == {"Basic Equipment": 0.5, "Essential Medicines": 1.0}
    assert f["overall_score"] == 0.75


def test_compute_readiness_scores_empty_detail_counts_as_present():
    records = [make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "")]
    result = facility_readiness.compute_readiness_scores(records)
    assert result["facilities"][0]["domain_scores"]["Basic Equipment"] == 1.0


def test_compute_readiness_scores_quantity_detail_counts_as_present():
    records = [make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "3 units")]
    result = facility_readiness.compute_readiness_scores(records)
    assert result["facilities"][0]["domain_scores"]["Basic Equipment"] == 1.0


def test_compute_readiness_scores_dedupes_keeping_latest_added_at():
    records = [
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "absent", added_at="2026-08-01T00:00:00+00:00"),
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "present", added_at="2026-08-16T00:00:00+00:00"),
    ]
    result = facility_readiness.compute_readiness_scores(records)
    # The later record (present) must win, not both counted.
    assert result["facilities"][0]["domain_scores"]["Basic Equipment"] == 1.0


def test_compute_readiness_scores_multiple_facilities_and_district_aggregation():
    records = [
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "present"),
        make_record("Peshawar", "City Clinic", "Basic Equipment", "Thermometer", "absent"),
        make_record("Mardan", "MMC Hospital", "Basic Equipment", "Thermometer", "present"),
    ]
    result = facility_readiness.compute_readiness_scores(records)
    assert len(result["facilities"]) == 3
    districts_by_name = {d["district"]: d for d in result["districts"]}
    assert districts_by_name["Peshawar"]["facilities_assessed"] == 2
    assert districts_by_name["Peshawar"]["mean_score"] == (1.0 + 0.0) / 2
    assert districts_by_name["Mardan"]["facilities_assessed"] == 1
    assert districts_by_name["Mardan"]["mean_score"] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_facility_readiness.py -v`
Expected: FAIL with `ModuleNotFoundError` (`scripts.lib.facility_readiness` doesn't exist yet)

- [ ] **Step 3: Create `scripts/lib/facility_readiness.py`**

```python
"""WHO Service Availability and Readiness Assessment (SARA) reference
data and scoring, shared by server/supplemental_data.py (AI-extraction
prompt guidance) and scripts/14_build_html_report.py (scoring/display).
Lives here, not in server/, because scripts/ pipeline code (including
the report builder, which runs standalone outside the FastAPI app)
cannot import from server/ - the same layering
scripts/lib/supplemental_records.py already established for the same
reason.

TRACER_ITEMS: the 5 real WHO SARA General Service Readiness domains and
their tracer items - the first four domains verified against Table 2 of
a published SARA methodology analysis (Burkina Faso 2014 SARA data, BMC
Public Health 10.1186/s12889-020-09994-7); the fifth (Essential
Medicines) is WHO's own standard 14-item core tracer list, not a
country-specific adaptation. 43 items total, not invented.

No new data store - data/processed/supplemental_records.csv's existing
schema (district, facility, category, label, detail, source_document,
added_at) is reused entirely. A record counts as a SARA tracer item when
its category/label exactly match an entry here; everything else is
ignored by compute_readiness_scores() and continues to work as ordinary
free-form supplemental data. See docs/superpowers/specs/
2026-08-16-facility-readiness-design.md."""

TRACER_ITEMS = {
    "Basic Amenities": [
        "Power (electric or solar device)",
        "Improved water source inside or within the ground of the facility",
        "Room with auditory and visual privacy for patient consultations",
        "Access to adequate sanitation facilities for clients",
        "Communication equipment (phone or SW radio)",
        "Facility has access to computer with E-mail/Internet access",
        "Emergency transportation",
    ],
    "Basic Equipment": [
        "Adult scale",
        "Child scale",
        "Thermometer",
        "Stethoscope",
        "Blood pressure apparatus",
        "Light source",
    ],
    "Standard Precautions for Infection Prevention": [
        "Safe final disposal of sharp materials",
        "Safe final disposal of infectious wastes",
        "Appropriate storage of sharp waste",
        "Appropriate storage of infectious waste",
        "Disinfectant",
        "Single use (standard disposable or auto-disable syringes)",
        "Soap and running water or alcohol based hand rub",
        "Latex gloves",
        "Guidelines for standard precautions",
    ],
    "Diagnostic Capacity": [
        "Haemoglobin",
        "Blood glucose",
        "Malaria diagnostic capacity",
        "Urine dipstick-protein",
        "Urine dipstick-glucose",
        "HIV diagnostic capacity",
        "Urine test for pregnancy",
    ],
    "Essential Medicines": [
        "Amitriptyline",
        "Amoxicillin",
        "Atenolol",
        "Captopril",
        "Ceftriaxone",
        "Ciprofloxacin",
        "Co-trimoxazole",
        "Diazepam",
        "Diclofenac",
        "Glibenclamide",
        "Omeprazole",
        "Paracetamol",
        "Simvastatin",
        "Salbutamol",
    ],
}

# Every (domain, tracer item) pair, for O(1) membership checks.
_KNOWN_TRACER_ITEMS = {(domain, item) for domain, items in TRACER_ITEMS.items() for item in items}


def _is_present(detail):
    """"absent" (case/whitespace-insensitive) is the only way a record
    marks an item as confirmed not-present. Everything else - "present",
    a quantity, a note, or even an empty string - counts as present,
    matching how the rest of the free-form supplemental records already
    treat "a record exists" as "the fact is true"."""
    return (detail or "").strip().lower() != "absent"


def compute_readiness_scores(records):
    """records: supplemental_records.csv rows (any mix of SARA tracer
    items and ordinary free-form facts - non-tracer records are ignored
    here, untouched otherwise). Returns {"facilities": [...],
    "districts": [...]} - see module docstring / spec section 5 for the
    exact scoring rules (per-domain, per-facility overall, district
    aggregation, deduplication, omit-unassessed-domains)."""
    relevant = [r for r in records if (r.get("category"), r.get("label")) in _KNOWN_TRACER_ITEMS]

    # Dedupe by (facility, district, category, label), keeping the
    # record with the latest added_at - supplemental_records.csv is
    # purely additive, so the same tracer item can legitimately appear
    # more than once (a second document upload, a status that changed).
    latest = {}
    for r in relevant:
        key = (r.get("facility", ""), r.get("district", ""), r["category"], r["label"])
        existing = latest.get(key)
        if existing is None or r.get("added_at", "") >= existing.get("added_at", ""):
            latest[key] = r
    deduped = list(latest.values())

    # Group into per-facility, per-domain [present_count, assessed_count].
    facility_domain_counts = {}
    for r in deduped:
        fkey = (r.get("facility", ""), r.get("district", ""))
        domain = r["category"]
        counts = facility_domain_counts.setdefault(fkey, {}).setdefault(domain, [0, 0])
        counts[1] += 1
        if _is_present(r.get("detail")):
            counts[0] += 1

    facilities_out = []
    for (facility, district), domains in facility_domain_counts.items():
        domain_scores = {d: present / assessed for d, (present, assessed) in domains.items() if assessed > 0}
        overall = sum(domain_scores.values()) / len(domain_scores) if domain_scores else None
        facilities_out.append({
            "facility": facility,
            "district": district,
            "domain_scores": domain_scores,
            "overall_score": overall,
        })

    district_scores = {}
    for f in facilities_out:
        if f["overall_score"] is None:
            continue
        district_scores.setdefault(f["district"], []).append(f["overall_score"])
    districts_out = [
        {"district": d, "mean_score": sum(scores) / len(scores), "facilities_assessed": len(scores)}
        for d, scores in district_scores.items()
    ]

    return {"facilities": facilities_out, "districts": districts_out}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_facility_readiness.py -v`
Expected: 10 passed

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/facility_readiness.py tests/lib/test_facility_readiness.py
git commit -m "feat: add WHO SARA reference data and readiness scoring

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Extend AI extraction to recognize SARA tracer items

**Files:**
- Modify: `server/supplemental_data.py`
- Modify: `tests/server/test_supplemental_data.py`

**Interfaces:**
- Consumes: `scripts.lib.facility_readiness.TRACER_ITEMS` (Task 1).
- Produces: `build_extraction_question()`'s returned prompt now also lists SARA domains/tracer items; behavior otherwise unchanged (same function signature, same free-form fallback).

- [ ] **Step 1: Write the failing tests (append to `tests/server/test_supplemental_data.py`)**

```python
def test_build_extraction_question_still_allows_free_form_categories():
    # Regression: the existing phase 4b free-form path must not be
    # removed or narrowed by adding SARA guidance.
    question = supplemental_data.build_extraction_question("", KNOWN_DISTRICTS)
    assert "not a fixed list" in question
    assert "outbreak" in question


def test_build_extraction_question_includes_sara_domains_and_items():
    question = supplemental_data.build_extraction_question("", KNOWN_DISTRICTS)
    assert "Basic Equipment" in question
    assert "Thermometer" in question
    assert "Essential Medicines" in question
    assert "Paracetamol" in question


def test_build_extraction_question_explains_present_absent_convention():
    question = supplemental_data.build_extraction_question("", KNOWN_DISTRICTS)
    assert "present" in question
    assert "absent" in question
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_supplemental_data.py -v -k "extraction_question"`
Expected: `test_build_extraction_question_still_allows_free_form_categories` PASSes already (nothing removed yet); `test_build_extraction_question_includes_sara_domains_and_items` and `test_build_extraction_question_explains_present_absent_convention` FAIL

- [ ] **Step 3: Add the SARA guidance to `build_extraction_question()`**

Find:

```python
from scripts.lib.districts import normalize_district
from server import ai_client
```

Replace with:

```python
from scripts.lib.districts import normalize_district
from scripts.lib.facility_readiness import TRACER_ITEMS
from server import ai_client
```

Find:

```python
def build_extraction_question(instruction, known_districts):
    instruction_line = (
        instruction.strip() if instruction and instruction.strip()
        else "(none given - infer everything from the document itself)"
    )
    districts_list = ", ".join(known_districts)
    return (
        'Extract structured supplemental facility/district records from the '
        'document content above for a Khyber Pakhtunkhwa healthcare planning '
        'report. Respond with ONLY a JSON array (no prose, no markdown code '
        'fence) of objects shaped exactly like: '
        '{"district": "...", "facility": "...", "category": "...", "label": "...", "detail": "..."}. '
        f'"district" MUST be one of these exact names: {districts_list}. '
        '"facility" is the specific hospital/clinic name, or an empty string '
        'if the fact is district-wide (e.g. an outbreak). '
        '"category" is a short label you choose for what kind of fact this is '
        '(equipment, medicine, department, disease_treated, outbreak, or '
        'anything else that fits - it is not a fixed list). '
        '"label" is the short name of the fact. "detail" is a short '
        'elaboration (quantity, status, date, case count, etc). If there is '
        'nothing extractable, respond with an empty JSON array: []. '
        f"Admin's instruction: {instruction_line}"
    )
```

Replace with:

```python
def build_extraction_question(instruction, known_districts):
    instruction_line = (
        instruction.strip() if instruction and instruction.strip()
        else "(none given - infer everything from the document itself)"
    )
    districts_list = ", ".join(known_districts)
    tracer_items_text = "; ".join(
        f'{domain}: {", ".join(items)}' for domain, items in TRACER_ITEMS.items()
    )
    return (
        'Extract structured supplemental facility/district records from the '
        'document content above for a Khyber Pakhtunkhwa healthcare planning '
        'report. Respond with ONLY a JSON array (no prose, no markdown code '
        'fence) of objects shaped exactly like: '
        '{"district": "...", "facility": "...", "category": "...", "label": "...", "detail": "..."}. '
        f'"district" MUST be one of these exact names: {districts_list}. '
        '"facility" is the specific hospital/clinic name, or an empty string '
        'if the fact is district-wide (e.g. an outbreak). '
        '"category" is a short label you choose for what kind of fact this is '
        '(equipment, medicine, department, disease_treated, outbreak, or '
        'anything else that fits - it is not a fixed list) - EXCEPT: if the '
        "document supports it, use the WHO SARA facility-readiness framework "
        'instead - set "category" to one of these exact domain names and '
        '"label" to one of that domain\'s exact tracer item names below, and '
        'set "detail" to exactly "present" or "absent" for whether that '
        f'facility has that item: {tracer_items_text}. '
        '"label" is the short name of the fact (for non-SARA records - use '
        'the exact tracer item name for SARA records, as listed above). '
        '"detail" is a short elaboration (quantity, status, date, case '
        'count, etc, or "present"/"absent" for SARA tracer items). If there '
        'is nothing extractable, respond with an empty JSON array: []. '
        f"Admin's instruction: {instruction_line}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_supplemental_data.py -v`
Expected: all tests pass (existing tests plus the 3 new ones)

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add server/supplemental_data.py tests/server/test_supplemental_data.py
git commit -m "feat: teach the AI extraction prompt WHO SARA domains and tracer items

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Add the Facility Readiness report section

**Files:**
- Modify: `scripts/14_build_html_report.py`
- Create: `tests/test_facility_readiness_section.py`

**Interfaces:**
- Consumes: `scripts.lib.facility_readiness.compute_readiness_scores` (Task 1), `supplemental_records` (already loaded by `load_data()`, no changes needed there).
- Produces: a new `readiness_section_html(readiness)` function and a new `<section id="facility-readiness">` in `build()`'s output, inserted after the existing "Additional Facility & District Information" section.

- [ ] **Step 1: Write the failing tests in `tests/test_facility_readiness_section.py`**

```python
import importlib

report_mod = importlib.import_module("scripts.14_build_html_report")


def test_readiness_section_html_empty_state():
    readiness = {"facilities": [], "districts": []}
    html = report_mod.readiness_section_html(readiness)
    assert "no facility readiness documents have been uploaded yet" in html.lower()
    assert "<table>" not in html


def test_readiness_section_html_populated_state():
    readiness = {
        "facilities": [
            {"facility": "DHQ Hospital", "district": "Peshawar",
             "domain_scores": {"Basic Equipment": 0.5}, "overall_score": 0.5},
        ],
        "districts": [
            {"district": "Peshawar", "mean_score": 0.5, "facilities_assessed": 1},
        ],
    }
    html = report_mod.readiness_section_html(readiness)
    assert "DHQ Hospital" in html
    assert "Peshawar" in html
    assert "Basic Equipment" in html
    assert "50%" in html
    assert "<table>" in html


def test_readiness_section_html_shows_facilities_assessed_count():
    readiness = {
        "facilities": [
            {"facility": "A", "district": "Bannu", "domain_scores": {"Basic Equipment": 1.0}, "overall_score": 1.0},
            {"facility": "B", "district": "Bannu", "domain_scores": {"Basic Equipment": 0.0}, "overall_score": 0.0},
        ],
        "districts": [{"district": "Bannu", "mean_score": 0.5, "facilities_assessed": 2}],
    }
    html = report_mod.readiness_section_html(readiness)
    assert "2" in html


def test_readiness_section_html_escapes_untrusted_facility_name():
    readiness = {
        "facilities": [
            {"facility": "<script>alert(1)</script>", "district": "Peshawar",
             "domain_scores": {"Basic Equipment": 1.0}, "overall_score": 1.0},
        ],
        "districts": [{"district": "Peshawar", "mean_score": 1.0, "facilities_assessed": 1}],
    }
    html = report_mod.readiness_section_html(readiness)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_readiness_section_html_facility_without_name_shows_placeholder():
    readiness = {
        "facilities": [
            {"facility": "", "district": "Peshawar", "domain_scores": {"Basic Equipment": 1.0}, "overall_score": 1.0},
        ],
        "districts": [{"district": "Peshawar", "mean_score": 1.0, "facilities_assessed": 1}],
    }
    html = report_mod.readiness_section_html(readiness)
    assert "&mdash;" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_facility_readiness_section.py -v`
Expected: FAIL with `AttributeError` (`readiness_section_html` doesn't exist yet)

- [ ] **Step 3: Add `readiness_section_html()` and wire it into `build()`**

Find:

```python
def methodology_html():
    return """<section id="methodology-global">
```

Replace with:

```python
def readiness_section_html(readiness):
    """readiness: scripts.lib.facility_readiness.compute_readiness_scores()'s
    return value. Two states, matching the design spec: a plain
    explanatory paragraph when nothing has been assessed yet (the real
    starting state), or a per-facility table plus a district-summary
    table once data exists. No <table> at all in the empty state -
    deliberately not an empty, confusing table."""
    facilities = readiness["facilities"]
    if not facilities:
        return (
            "<p>The WHO Service Availability and Readiness Assessment (SARA) framework is in place, but no "
            "facility readiness documents have been uploaded yet via the admin panel's document upload feature. "
            "Once uploaded, facility-level readiness scores across Basic Amenities, Basic Equipment, Standard "
            "Precautions for Infection Prevention, Diagnostic Capacity, and Essential Medicines will appear "
            "here.</p>"
        )

    facility_rows = []
    for f in sorted(facilities, key=lambda f: (f["district"], f["facility"])):
        domain_cells = "; ".join(
            f"{html.escape(d)}: {v * 100:.0f}%" for d, v in sorted(f["domain_scores"].items())
        )
        overall = f"{f['overall_score'] * 100:.0f}%" if f["overall_score"] is not None else "&mdash;"
        name = html.escape(f["facility"]) if f["facility"] else "&mdash;"
        facility_rows.append(
            "<tr>"
            f"<td class=\"col-name\">{name}</td>"
            f"<td>{html.escape(f['district'])}</td>"
            f"<td>{domain_cells}</td>"
            f"<td class=\"num\">{overall}</td>"
            "</tr>"
        )

    district_rows = []
    for d in sorted(readiness["districts"], key=lambda d: d["district"]):
        district_rows.append(
            "<tr>"
            f"<td class=\"col-name\">{html.escape(d['district'])}</td>"
            f"<td class=\"num\">{d['mean_score'] * 100:.0f}%</td>"
            f"<td class=\"num\">{d['facilities_assessed']}</td>"
            "</tr>"
        )

    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>Facility</th><th>District</th><th>Domain Scores</th><th>Overall</th></tr></thead>"
        f"<tbody>{''.join(facility_rows)}</tbody>"
        "</table></div>"
        "<h3>District Summary</h3>"
        '<div class="table-wrap"><table>'
        "<thead><tr><th>District</th><th>Mean Readiness</th><th>Facilities Assessed</th></tr></thead>"
        f"<tbody>{''.join(district_rows)}</tbody>"
        "</table></div>"
    )


def methodology_html():
    return """<section id="methodology-global">
```

Find:

```python
<section id="supplemental-data">
<h2>Additional Facility &amp; District Information</h2>
<p>Equipment, medicine, departments, diseases treated, outbreak records, and other facts added via the
admin panel's document upload &mdash; not part of the deterministic gap-score model above.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Facility</th><th>Category</th><th>Label</th><th>Detail</th><th>Source</th></tr></thead>
<tbody>
{supplemental_data_rows_html(supplemental_records)}
</tbody>
</table>
</div>
</section>
```

Replace with:

```python
<section id="supplemental-data">
<h2>Additional Facility &amp; District Information</h2>
<p>Equipment, medicine, departments, diseases treated, outbreak records, and other facts added via the
admin panel's document upload &mdash; not part of the deterministic gap-score model above.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Facility</th><th>Category</th><th>Label</th><th>Detail</th><th>Source</th></tr></thead>
<tbody>
{supplemental_data_rows_html(supplemental_records)}
</tbody>
</table>
</div>
</section>

<section id="facility-readiness">
<h2>Facility Readiness (WHO SARA Framework)</h2>
<p>The WHO Service Availability and Readiness Assessment (SARA) framework splits facility adequacy into
<em>availability</em> (infrastructure and staff physically present - the Official Infrastructure Context section
above) and <em>readiness</em> (equipment, medicines, and capacity to actually deliver a service). Readiness scores
below come from documents uploaded via the admin panel's document-upload feature, extracted against SARA's real
tracer items (Basic Amenities, Basic Equipment, Standard Precautions for Infection Prevention, Diagnostic
Capacity, Essential Medicines) - a facility's score is the share of assessed tracer items found present; a domain
nobody has looked at yet is left out of that facility's average rather than counted against it.</p>
{readiness_section_html(readiness)}
</section>
```

- [ ] **Step 4: Compute `readiness` in `build()` and import the new module**

Find:

```python
from scripts.lib.supplemental_records import load_records as load_supplemental_records
```

Replace with:

```python
from scripts.lib.facility_readiness import compute_readiness_scores
from scripts.lib.supplemental_records import load_records as load_supplemental_records
```

Find:

```python
    ) = load_data()
    metrics_by_district = {m["district"]: m for m in metrics}
```

Replace with:

```python
    ) = load_data()
    metrics_by_district = {m["district"]: m for m in metrics}
    readiness = compute_readiness_scores(supplemental_records)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_facility_readiness_section.py -v`
Expected: 5 passed

- [ ] **Step 6: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add scripts/14_build_html_report.py tests/test_facility_readiness_section.py
git commit -m "feat: add Facility Readiness (WHO SARA) section to the HTML report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Admin panel hint

**Files:**
- Modify: `server/admin_ui.py`
- Modify: `tests/server/test_routes.py`

**Interfaces:** none new (text-only change to an existing hint paragraph).

- [ ] **Step 1: Add a test asserting the SARA hint text is present**

Find:

```python
def test_admin_panel_includes_extract_upload_section(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    for hook in ('id="extract-file-input"', 'id="extract-btn"', 'id="extract-result"', "/admin/api/extract"):
        assert hook in panel.text, f"missing hook: {hook}"
```

Replace with:

```python
def test_admin_panel_includes_extract_upload_section(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    for hook in ('id="extract-file-input"', 'id="extract-btn"', 'id="extract-result"', "/admin/api/extract"):
        assert hook in panel.text, f"missing hook: {hook}"


def test_admin_panel_extract_hint_mentions_sara_framework():
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    assert "SARA" in panel.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/server/test_routes.py -v -k sara`
Expected: FAIL (the hint paragraph doesn't mention SARA yet)

- [ ] **Step 3: Extend the hint text in `server/admin_ui.py`**

Find:

```python
  <p class="hint">Upload an Excel, PDF, Word, Text, CSV, or HTML file to preview its extracted text, or add it to the report as supplemental facility/district information (equipment, medicine, departments, diseases treated, outbreaks, or anything else).</p>
```

Replace with:

```python
  <p class="hint">Upload an Excel, PDF, Word, Text, CSV, or HTML file to preview its extracted text, or add it to the report as supplemental facility/district information (equipment, medicine, departments, diseases treated, outbreaks, or anything else). A facility readiness survey or equipment inventory will be recognized against the WHO SARA framework (e.g. "Thermometer: present", "Paracetamol: absent") and scored automatically in the report's Facility Readiness section.</p>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_routes.py -v -k sara`
Expected: 1 passed

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add server/admin_ui.py tests/server/test_routes.py
git commit -m "feat: mention the WHO SARA framework in the admin panel's upload hint

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Full pipeline run and live manual verification

**Files:** none (verification only).

This is an admin-panel-touching feature, so per this project's established cadence it needs manual browser verification against the real running server (not just mocks), matching phase 4b's original verification and every AI-provider-touching feature since.

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 2: Rebuild the report and confirm the empty state renders for real**

Run: `python scripts/14_build_html_report.py`

Expected: no traceback (`data/processed/supplemental_records.csv` is currently empty/absent after the earlier travel-time-routing/Marham sessions' cleanup, so this exercises the real empty-state path, not a mock).

Run:

```bash
python -c "
s = open('report/KP_Healthcare_Plan.html', encoding='utf-8').read()
assert 'Facility Readiness (WHO SARA Framework)' in s
assert 'no facility readiness documents have been uploaded yet' in s.lower()
print('empty state renders correctly')
"
```

Expected: `empty state renders correctly`

- [ ] **Step 3: Start the server and log in**

Run: `python -m server` (matching this project's standard local-server pattern), then log in at `http://127.0.0.1:8420/admin` with whatever admin password is currently set (or complete one-time setup if none is set, per this project's established pattern for a fresh server start).

- [ ] **Step 4: Confirm the SARA hint is visible in the real browser**

Navigate to the admin panel's "Extract Document" section and confirm the hint paragraph mentions the WHO SARA framework, matching Task 4's change.

- [ ] **Step 5: Add one real facility readiness record via the admin panel, using a real AI provider key**

Using whichever AI provider key is currently configured (matching this project's established pattern of live-verifying AI-touching features with a real key, cleaned up afterward), upload a small test document describing a facility's equipment status - for example, a short text file:

```
Test District General Hospital, Peshawar:
- Thermometer: available and working
- Stethoscope: available
- Adult scale: not available, broken for 6 months
```

with the instruction "this is a facility readiness assessment for Test District General Hospital in Peshawar." Click "Add to Report" and confirm the response shows records added under the `Basic Equipment` category (or whichever domain the AI correctly maps the content to), and check whether it correctly distinguishes the "not available" item with `detail: absent`.

- [ ] **Step 6: Confirm the report reflects the new readiness data**

Reload the report (or re-run `python scripts/14_build_html_report.py` if the admin panel's "Add to Report" doesn't already trigger a rebuild - check the admin route's existing behavior for `/admin/api/supplemental-data` to confirm) and check the "Facility Readiness" section now shows the test facility with a real, non-empty domain score and overall score, and the district-summary table shows Peshawar with `facilities_assessed: 1` (or more, if other real data exists by this point).

- [ ] **Step 7: Clean up the test data**

Following this project's established manual-verification cleanup discipline (matching phase 4b's original pattern): remove the test record(s) from `data/processed/supplemental_records.csv`, rebuild the report so the committed file doesn't carry throwaway test content, and confirm via `git status`/`git diff` that the rebuilt report matches what's already committed (no diff) before finishing. If the AI provider key used was a real one the user wants kept, leave it in the OS credential store; if it was added solely for this test, remove it via the admin panel's "Delete" button for that provider.

- [ ] **Step 8: Stop the server**

Stop the `python -m server` process (matching this project's established Windows-specific cleanup: PowerShell `Get-CimInstance Win32_Process` + `Stop-Process`, since Git Bash's `pkill` doesn't reach real Windows processes).

- [ ] **Step 9: Report findings**

If everything above checks out clean, this task (and the whole plan) is done - no further commit needed beyond what Tasks 1-4 already made (Step 7's cleanup should leave the tree clean, matching phase 4b's and phase 4c's precedent of "nothing to commit if cleanup was done correctly"). If anything looks wrong (the AI doesn't recognize the SARA framing at all, the score computation looks wrong against real extracted data, the report doesn't reflect an added record), that's a real bug to fix with its own test before considering this complete.
