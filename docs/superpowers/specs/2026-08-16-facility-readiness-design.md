# KP Healthcare Planning — Facility Readiness (WHO SARA Framework)

Status: Approved design, pre-implementation
Date: 2026-08-16
Extends: [2026-08-15-supplemental-facility-data-phase4b-design.md](./2026-08-15-supplemental-facility-data-phase4b-design.md)

## 1. Purpose

The report already states this exact gap in its own Methodology section
(`scripts/14_build_html_report.py`'s SARA bullet): "it has no readiness
data (equipment, medicine stock, diagnostic capacity) at facility level,
since neither the KPHCC registry nor Development Statistics 2025
publishes it." Close that gap by structuring the existing phase 4b
document-upload/AI-extraction feature around WHO's actual SARA
methodology — real domains and tracer items, not free-form categories —
and computing a real SARA-style readiness score once data exists.

## 2. Confirmed During Brainstorming

- **No bulk public dataset exists for KP.** Verified via web search
  (not assumed): SARA assessments in Pakistan exist as one-off PDF
  reports/academic studies (Islamabad Capital Territory 2020, a
  12-district national abortion-care readiness study) — none for KP
  specifically, none structured/downloadable the way Development
  Statistics or Marham.pk were. This is fundamentally unlike the last
  three features (Dev Stats, travel-time routing, Marham) — there is no
  "fetch a real source, parse, merge" path here.
- **User confirmed building this as a framework** — ready to receive
  real data the moment a document is uploaded, expected to start at zero
  facilities assessed, not something that produces rich results
  immediately.
- **Tracer items verified against a real published WHO SARA analysis**
  (not guessed): the General Service Readiness domains and their tracer
  items, per Table 2 of a peer-reviewed SARA methodology paper analyzing
  Burkina Faso's 2014 SARA data (BMC Public Health,
  10.1186/s12889-020-09994-7) for the first four domains, and WHO's own
  standard 14-item core Essential Medicines Availability tracer list
  (not a country-specific adaptation) for the fifth — see §3 for the
  exact list.
- **Architectural constraint that shapes where the SARA reference data
  lives**: `scripts/` (numbered pipeline scripts, including
  `14_build_html_report.py`) cannot import from `server/` (the FastAPI
  admin app) — the report-build script runs standalone, confirmed by
  `scripts/lib/supplemental_records.py`'s own docstring ("living here
  since the report-build script can't import from server/"). `server/`
  already imports from `scripts/lib/` in the other direction (e.g.
  `supplemental_data.py` already imports `normalize_district`). So the
  new SARA reference data and scoring logic go in `scripts/lib/`, not
  `server/`, matching this existing, deliberate layering.
- **No new data store.** `data/processed/supplemental_records.csv` and
  its existing schema (`district, facility, category, label, detail,
  source_document, added_at`) are unchanged. SARA data is just
  supplemental records whose `category`/`label` happen to match a known
  domain/tracer-item name exactly.
- **Not added to the GIS choropleth map in this pass** — a district map
  colored by readiness built from 0-1 assessed facilities would actively
  mislead a reader into thinking it reflects real coverage. Deferred
  explicitly, not silently dropped, until real facility coverage exists.

## 3. The SARA Reference Data

New module: **`scripts/lib/facility_readiness.py`**.

```
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
        "Adult scale", "Child scale", "Thermometer", "Stethoscope",
        "Blood pressure apparatus", "Light source",
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
        "Haemoglobin", "Blood glucose", "Malaria diagnostic capacity",
        "Urine dipstick-protein", "Urine dipstick-glucose",
        "HIV diagnostic capacity", "Urine test for pregnancy",
    ],
    "Essential Medicines": [
        "Amitriptyline", "Amoxicillin", "Atenolol", "Captopril",
        "Ceftriaxone", "Ciprofloxacin", "Co-trimoxazole", "Diazepam",
        "Diclofenac", "Glibenclamide", "Omeprazole", "Paracetamol",
        "Simvastatin", "Salbutamol",
    ],
}
```

43 tracer items across 5 domains, real WHO SARA General Service
Readiness content, not invented.

## 4. AI Extraction Guidance (No Schema Change)

`server/supplemental_data.py`'s `build_extraction_question()` gets
extended: alongside the existing free-form instruction ("category is a
short label you choose... it is not a fixed list"), the prompt lists the
5 domain names and their exact tracer item strings, instructing the AI
to use those exact `category`/`label` values when the document supports
a match, and to fall through to free-form categorization for everything
else. Both paths write to the same `supplemental_records.csv` via the
same `append_records()` — no new file, no new endpoint.

**Presence/absence convention** (a genuine SARA-specific need the
existing free-form schema doesn't address): `detail == "present"` counts
as present; `detail == "absent"` is an explicit, confirmed absence
(still *assessed* — contributes 0 to the score, not silently omitted);
any other non-empty `detail` (a quantity, a note) is treated as present,
matching how the rest of the free-form records already work (a record
existing generally means the fact is true).

## 5. Readiness Score Computation

New function in `scripts/lib/facility_readiness.py`:
`compute_readiness_scores(records)`.

- **Deduplication before scoring**: `supplemental_records.csv` is
  purely additive (`append_records()` never dedupes — confirmed by
  reading the existing phase 4b code), so the same tracer item can
  legitimately appear more than once for the same facility (a second
  document upload, a status that changed between assessments). Before
  scoring, records are deduplicated by `(facility, district, category,
  label)`, keeping only the one with the latest `added_at` — "the most
  recent assessment wins," the same real-world semantics as a facility's
  equipment status changing over time.
- **Per-facility, per-domain score** = (tracer items recorded present) /
  (tracer items *assessed* — present + explicitly absent) within that
  domain, computed after deduplication. A domain with zero assessed
  items for a facility is omitted from that facility's results entirely,
  not scored as 0% — matching real SARA methodology's own convention of
  scoring over what was actually assessed.
- **Per-facility overall score** = mean of whichever domain scores exist
  for that facility (so partial coverage — e.g. only 2 of 5 domains
  assessed — still produces a meaningful number, not one dragged down by
  domains nobody has looked at).
- **District-level aggregation**: mean of per-facility overall scores
  among facilities with at least one assessed item in that district,
  paired with an explicit `facilities_assessed` count so the report can
  state real coverage honestly ("based on 1 facility") rather than
  implying province-wide data that doesn't exist.

## 6. Report Display

New section in `scripts/14_build_html_report.py`, "Facility Readiness
(WHO SARA Framework)," with two states:

- **Zero facilities assessed** (the real starting state): a plain
  explanatory paragraph — framework in place, no readiness documents
  uploaded yet — not an empty, confusing table.
- **Once data exists**: a per-facility table (name, district, each
  assessed domain's score, overall score) plus a district-summary block
  showing the aggregate and its `facilities_assessed` count.

No GIS/shapefile changes in this pass (see §2).

## 7. Admin Panel Hint

A short UI text addition next to the existing document-upload control in
`server/admin_ui.py`, naming the SARA framework and giving 1-2 example
tracer items, so a future admin uploading a document knows this
framework exists and what kind of source material would populate it
(a facility readiness survey, an equipment inventory) — discoverable
without reading source code.

## 8. Testing

Matching this part of the codebase's dominant pytest-with-in-memory-
fixtures convention (unlike the Dev Stats PDF work):

- `compute_readiness_scores`: present/absent/unassessed mixes, multiple
  domains, multiple facilities, district aggregation with the
  `facilities_assessed` count, the "domain with zero assessed items is
  omitted, not zeroed" behavior specifically, and the deduplication rule
  (two records for the same facility/domain/tracer-item with different
  `added_at` timestamps — the later one must win).
- A regression test confirming `build_extraction_question()` still
  produces a prompt that allows free-form categories alongside the new
  SARA guidance (the existing phase 4b behavior must not regress).
- Report-html tests for both the empty-state and populated-state
  rendering of the new section.

## 9. Explicitly Out of Scope

- Any bulk data source — none exists publicly for KP (see §2).
- GIS/shapefile/choropleth integration — deferred until real facility
  coverage exists (see §2, §6).
- Gap-score integration — a real readiness signal could eventually
  inform the composite score, but that's a separate, larger
  methodological decision, matching how the last two features (Dev
  Stats health utilization, Marham) also deliberately excluded gap-score
  changes.
