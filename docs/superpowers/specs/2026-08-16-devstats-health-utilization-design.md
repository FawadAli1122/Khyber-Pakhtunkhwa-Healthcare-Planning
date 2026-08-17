# KP Healthcare Planning — Dev Stats Health Utilization & Disease Burden (Report + GIS Enrichment)

Status: Approved design, pre-implementation
Date: 2026-08-16
Extends: [2026-08-14-devstats-dem-horizons-extension-design.md](./2026-08-14-devstats-dem-horizons-extension-design.md)

## 1. Purpose

Development Statistics of Khyber Pakhtunkhwa 2025's Health chapter (Table
No. 111-124, 14 tables total) is only partially used today — 5 of the 14
tables feed `scripts/17_extract_devstats_health.py`
(`data/processed/dev_stats_health.csv`). Extract three more of the
remaining nine and surface them in the report and the QGIS map: **Table
120** (Patients Treated), **Table 123** (District Immunization), and
**Table 124** (District Malaria Control). These were identified as the
highest-value additions — real district-level utilization and
disease-burden signals nothing in the current model captures — versus
the other six unused tables, which are either provincial-only (111, 122),
institute-level rather than district-level (118), or redundant with
tables already extracted (113 vs. 112, 116 vs. 117, 121 is
narrower/lower-priority than 120/123/124).

## 2. Confirmed During Brainstorming

- **Scope: report + GIS enrichment only.** These three tables do **not**
  feed `08_compute_district_metrics.py` or the gap-score `WEIGHTS` in
  `09_gap_score_and_clusters.py`. Folding new data into an already-tuned,
  documented composite score is a separate, larger methodological
  decision explicitly deferred, not attempted here.
- The complete, verified 14-table Health chapter list (source: the PDF's
  own "List of Tables" contents page, fitz page 11 — not guessed, not
  inferred from data pages, which don't carry title text) is in §3.
  5 tables (112, 114, 115, 117, 119) are already extracted; 3 (120, 123,
  124) are added by this spec; 6 (111, 113, 116, 118, 121, 122) are
  explicitly out of scope for now (see §7).

## 3. The Complete Health Chapter (Table No. 111-124)

Verified directly against `data/raw/kp_development_statistics_2025.pdf`'s
contents page (fitz page 11) and cross-checked against each table's own
page header. Printed page 196 = the chapter's Explanatory Note; the
chapter ends at printed page 225 (Table 124); printed page 226 begins the
next chapter (Social Welfare, Table 125).

| Table | Title | Printed page | Status |
|---|---|---|---|
| 111 | Government Health Institutions & Bed Strength in KP | 197 | Not used (provincial-only 3-yr trend) |
| 112 | District Wise Govt Health Institutions & Bed Strength | 198 | Used (`govt_institutions`, `govt_beds`) |
| 113 | District Wise Hospitals & Bed Strength | 201 | Not used (narrower subset of 112) |
| 114 | District Wise Registered Private Hospitals & Bed Capacity | 204 | Used (`pvt_hospitals`, `pvt_beds` est.) |
| 115 | District Wise Population Per Govt Hospital Bed | 206 | Used (`pop_per_bed`) |
| 116 | District Wise Medical & Paramedical Staff Posted | 207 | Not used (earlier-year duplicate of 117) |
| 117 | District Wise Medical & Paramedical Staff Posted | 210 | Used (`medical_staff`, `paramedical_staff`) |
| 118 | Staff in Medical Teaching Institutes | 213 | Not used (institute-level, not district) |
| 119 | District Wise Registered Private Medical Practitioners | 216 | Used (`pvt_practitioners`) |
| **120** | **District Wise Number of Patients Treated** | **217** | **Added by this spec** |
| 121 | District Wise Distribution of ORS | 218 | Not used (lower priority than 120/123/124) |
| 122 | Expanded Programme on Immunization (provincial) | 219 | Not used (provincial-only) |
| **123** | **District Wise Expanded Programme on Immunization** | **220** | **Added by this spec** |
| **124** | **District Wise Malaria Control Activities** | **223** | **Added by this spec** |

## 4. Extraction

Extends `scripts/17_extract_devstats_health.py` with three new
extraction functions, each writing its own output CSV — matching this
project's existing convention where `18_extract_devstats_roads.py` and
`19_extract_devstats_budget.py` are separate single-purpose files reading
the same source PDF, rather than one file handling every domain.

Fitz page indices below were read directly via
`scripts.lib.pdf_tables.extract_table_rows()` during brainstorming, not
guessed — each candidate "latest year" page was confirmed by its own
`(20XX-YY)` header text before being selected.

**Table 120 → `data/processed/dev_stats_patients_treated.csv`**
Single page (fitz 247) holding all three years in one grid (`District |
2022 Total/Indoor/Outdoor | 2023 ... | 2024 ...`). Take the 2024 columns
(indices 7-9 of each data row).
Columns: `district, patients_total_2024, patients_indoor_2024,
patients_outdoor_2024`.

**Table 123 → `data/processed/dev_stats_immunization.csv`**
Three yearly repeats at fitz pages 250/251/252, confirmed via each page's
own `(2021-22)`/`(2022-23)`/`(2023-24)` header — page 252 is latest.
Columns: `district, bcg, opv0, opv_dpt1, opv_dpt2, opv_dpt3, measles,
tt1, tt2, tt3, tt4, tt5` (all 11 raw dose counts — full transparency,
matching how the existing Official Infrastructure Context table already
shows all 8 institution types rather than a curated subset). **Raw
counts, not coverage percentages** — Table 123 publishes no per-district
child-population denominator to compute a rate against, so none is
invented.

**Table 124 → `data/processed/dev_stats_malaria.csv`**
Three yearly repeats at fitz pages 253/254/255, confirmed via the "2024"
year label directly in page 255's own table content (this page's text
extraction happens to include the table title inline, unlike the other
two).
Columns: `district, blood_slides_examined, malaria_cases,
malaria_cases_treated`.

All three follow the existing script's row-parsing conventions
(`iter_district_rows`/`parse_int`, district-name normalization via
`scripts.lib.districts.normalize_district`, cross-validation against the
same table's own "Khyber Pakhtunkhwa" provincial total row where
applicable).

## 5. Report Enrichment

Three new subsections inside the existing `<section
id="infrastructure-context">`, immediately after "Facility Count
Cross-Validation," matching that section's established style exactly —
`<h3>` heading, a short paragraph documenting the source table
number/year and any caveat, then a `<table>` rendered by a new
`*_rows_html()` function following `infra_context_rows_html()`'s pattern:

- **`<h3>Health Service Utilization (Development Statistics 2025)</h3>`**
  — Table 120, per-district Total/Indoor/Outdoor patients treated (2024),
  with a note that the indoor/outdoor split can hint at real admission
  capacity versus outpatient-only reliance.
- **`<h3>Immunization Coverage (Development Statistics 2025)</h3>`** —
  Table 123, all 11 raw dose counts per district, explicitly labeled as
  raw counts rather than coverage rates (see §4's caveat).
- **`<h3>Malaria Control Activities (Development Statistics 2025)</h3>`**
  — Table 124, per-district blood slides examined / cases / cases
  treated (2024).

`load_data()` gains three new `csv.DictReader` loads, threaded through
`build()` the same way `dev_health`/`dev_roads` already are.

## 6. GIS/QGIS Layer

These join onto the existing `KP_Districts` layer
(`12_write_shapefiles.py`'s `write_district()`), which already merges
`district_metrics.csv` + `dev_stats_health.csv` + `dev_stats_roads.csv`
by district name — the same join pattern extends directly, no new
shapefile layer needed. For the map specifically (not the report, which
shows everything), fields are curated to what's useful as a
choropleth/attribute, respecting the DBF 10-character field-name limit
already used throughout this schema (`para_staf`, `pop_pbed`, etc.):

| New DBF field | Source | Meaning |
|---|---|---|
| `pat_total`, `pat_indr`, `pat_outdr` | Table 120 | Patients treated 2024 (total/indoor/outdoor) |
| `bcg`, `opv0`, `opv3`, `measles` | Table 123 | The four headline EPI doses (birth-dose BCG, OPV-0, third-round OPV as the dropout comparator, Measles) — not all 11; the report table keeps full transparency separately |
| `mal_cases`, `mal_trtd` | Table 124 | Malaria cases / cases treated 2024 |

9 new fields total, usable in QGIS exactly like `gap_score`/`terr_diff`
today — pick any one as a choropleth field, or open the attribute table
to see them all per district.

## 7. Testing

Matching this exact class of PDF-dependent script's existing convention:
`17_extract_devstats_health.py` has **zero pytest unit tests today** —
it's verified only via `tests/verify_devstats_health.py`, a plain script
run against the real generated CSV (post-pipeline-run) doing sanity
assertions. This spec follows that same pattern rather than introducing
inconsistent mocked pytest coverage for this one file:

- **`tests/verify_devstats_patients.py`** — 35 districts; for every row,
  `patients_total_2024 == patients_indoor_2024 + patients_outdoor_2024`
  (a real internal-consistency check the source table's own structure
  guarantees); all fields non-negative.
- **`tests/verify_devstats_immunization.py`** — 35 districts; all 11
  dose fields non-negative; `bcg > 0` for every district (a zero count
  indicates a parsing gap, not a real absence — mirroring
  `verify_devstats_health.py`'s existing `govt_institutions > 0` check).
- **`tests/verify_devstats_malaria.py`** — 35 districts; all fields
  non-negative. Not asserting `malaria_cases_treated == malaria_cases`
  even though every sampled row during brainstorming showed them equal —
  that equality needs checking across all 35 real rows during
  implementation before being hard-coded as an invariant.

`tests/verify_shapefiles.py`'s `EXPECTED_FIELDS["KP_Districts"]` set gets
the 9 new field names added, matching its existing field-presence-check
pattern exactly (already read and confirmed during brainstorming, not
assumed).

## 8. Explicitly Out of Scope

- **Gap-score integration.** No `WEIGHTS` changes, no new composite-score
  terms. A future decision, not this one.
- **The other six unused Health-chapter tables** (111, 113, 116, 118,
  121, 122) — provincial-only, institute-level, or redundant with
  already-extracted tables, per §3's status column. Not a permanent
  exclusion, just not part of this pass.
- **Computing an immunization coverage rate.** Table 123 has no
  per-district child-population denominator; inventing one (e.g. from a
  crude birth-rate assumption) would be a real methodological addition
  requiring its own justification, not a byproduct of this extraction
  pass.
