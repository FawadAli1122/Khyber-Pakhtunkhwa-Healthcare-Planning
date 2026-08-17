"""Extract district-level government/private health institution, bed,
staffing, and practitioner counts from Development Statistics of Khyber
Pakhtunkhwa 2025 (data/raw/kp_development_statistics_2025.pdf) - the
current edition (published mid-2025), whose tables span data through 2024
(government institutions/beds/staff), 2023-24 (private practitioners,
road lengths) and 2025-26 (planned ADP allocations). A 2024 edition also
exists in this repo (data/raw/kp_development_statistics_2024.pdf, data
through 2023) and was the only one located during initial web research,
but the 2025 edition was found locally and is one year more current
throughout, so it is used as the authoritative source here.

Cross-validates the extracted district sum against the same table's own
"Khyber Pakhtunkhwa" provincial total row, so a parsing error surfaces as
a failed assertion rather than silently-wrong numbers.

Table map (2025 edition; page indices are 0-based fitz/pdfplumber page
indices, confirmed by direct inspection of extract_table_rows() output -
the 2025 edition renumbers the health tables +7 versus the 2024 edition
since 7 additional energy tables were inserted ahead of the health
chapter):

  Table 112 (page 230, last of a same-table 3-year run 228-230) -
      district government institutions & beds, all 8 institution types
      (Hospitals, Dispensaries, RHCs, TB Clinics, MCH Centres, Health Sub
      Centres, BHUs, Leprosy Clinics), latest year (2024), single page,
      no tehsil sub-rows in this edition.
  Table 114 (page 234) - district private hospitals by bed-size bracket
      (Upto 15 / 16-29 / 30-49 / 50-99 / 100-299 / Above 300 / Total).
      No exact private bed *count* is published anywhere in this edition
      (Table 115's "Hospitals Beds" column is government-only, matching
      Table 112's government beds figure) - pvt_beds is therefore
      estimated from these bed-size brackets using bracket midpoints
      (see PVT_BED_BRACKET_MIDPOINTS below), which is clearly documented
      wherever this figure is surfaced downstream.
  Table 115 (page 236) - district population per government hospital
      bed, 3-year columns (1.1.2022, 1.1.2023, 1.1.2024); latest = last
      3 columns.
  Table 117 (page 242, last of a 3-year run 240-242) - district medical &
      paramedical staff "Posted" (chosen over the very similarly-titled
      Table 116 "Actually Posted" because 117's per-district totals are
      consistently higher across every staff category, suggesting
      broader facility coverage rather than a narrower subset).
  Table 119 (page 246) - district registered private practitioners,
      3-year columns (2021-22, 2022-23, 2023-24); latest = last column.

Row-label quirks handled: some district labels carry a trailing footnote
asterisk (e.g. "Buner *" in the road-length table's pattern, and
elsewhere); "D.I.Khan" and "Kohistan Kolai Palas" needed extra aliases in
scripts/lib/districts.py to normalize correctly.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district
from scripts.lib.pdf_tables import extract_table_rows

PDF_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "kp_development_statistics_2025.pdf"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

PAGE_GOVT_INSTITUTIONS = 230   # Table 112, latest (2024) year page
PAGE_PVT_HOSPITALS = 234       # Table 114
PAGE_POP_PER_BED = 236         # Table 115
PAGE_STAFF_POSTED = 242        # Table 117, latest (2024) year page
PAGE_PVT_PRACTITIONERS = 246   # Table 119
PAGE_PATIENTS_TREATED = 247    # Table 120, single page holds all 3 years in one grid
PAGE_IMMUNIZATION = 252        # Table 123, latest (2023-24) of a 3-year run at pages 250-252
PAGE_MALARIA = 255             # Table 124, latest (2024) of a 3-year run at pages 253-255

# Conservative midpoint (floor, for the open-ended top bracket) used to
# turn Table 114's private-hospital bed-size bracket counts into an
# estimated total private bed count, since no exact figure is published.
PVT_BED_BRACKET_MIDPOINTS = [8, 22, 40, 75, 200, 300]


def parse_int(s):
    s = (s or "").replace(",", "").strip()
    if not s or s in ("-", "`"):
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


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


def get_kp_total_row(page_index):
    """Return the numeric cells of the "Khyber Pakhtunkhwa" provincial
    total row on a page, for cross-validation against a district sum."""
    rows = extract_table_rows(str(PDF_PATH), page_index)
    for row in rows:
        if row and row[0] and row[0].replace("\n", " ").strip() == "Khyber Pakhtunkhwa":
            return [parse_int(c) for c in row[1:]]
    return None


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

    def ensure(district):
        return district_totals.setdefault(
            district,
            {
                "govt_institutions": 0,
                "govt_beds": 0,
                "pvt_hospitals": 0,
                "pvt_beds": 0,
                "medical_staff": 0,
                "paramedical_staff": 0,
                "pvt_practitioners": 0,
                "pop_per_bed": "",
            },
        )

    # --- Table 112: government institutions & beds, all 8 types -------
    inst_sum = 0
    beds_sum = 0
    for district, cells in iter_district_rows(PAGE_GOVT_INSTITUTIONS):
        if len(cells) < 12:
            continue
        institutions = cells[0] + cells[2] + cells[4] + cells[6] + cells[8] + cells[9] + cells[10] + cells[11]
        beds = cells[1] + cells[3] + cells[5] + cells[7]
        rec = ensure(district)
        rec["govt_institutions"] += institutions
        rec["govt_beds"] += beds
        inst_sum += institutions
        beds_sum += beds

    kp_total = get_kp_total_row(PAGE_GOVT_INSTITUTIONS)
    assert kp_total and len(kp_total) >= 12, "Could not read Table 112's Khyber Pakhtunkhwa total row"
    kp_institutions = kp_total[0] + kp_total[2] + kp_total[4] + kp_total[6] + kp_total[8] + kp_total[9] + kp_total[10] + kp_total[11]
    kp_beds = kp_total[1] + kp_total[3] + kp_total[5] + kp_total[7]
    inst_diff_pct = abs(inst_sum - kp_institutions) / kp_institutions * 100
    beds_diff_pct = abs(beds_sum - kp_beds) / kp_beds * 100
    assert inst_diff_pct < 5, (
        f"District-summed government institutions ({inst_sum}) differs from Table 112's own "
        f"Khyber Pakhtunkhwa total ({kp_institutions}) by {inst_diff_pct:.1f}% - the column "
        "mapping is likely wrong; inspect extract_table_rows() output directly."
    )
    assert beds_diff_pct < 5, (
        f"District-summed government beds ({beds_sum}) differs from Table 112's own "
        f"Khyber Pakhtunkhwa total ({kp_beds}) by {beds_diff_pct:.1f}% - the column mapping "
        "is likely wrong; inspect extract_table_rows() output directly."
    )

    # --- Table 114: private hospitals by bed-size bracket --------------
    for district, cells in iter_district_rows(PAGE_PVT_HOSPITALS):
        if len(cells) < 7:
            continue
        rec = ensure(district)
        rec["pvt_hospitals"] += cells[6]
        rec["pvt_beds"] += sum(n * m for n, m in zip(cells[:6], PVT_BED_BRACKET_MIDPOINTS))

    # --- Table 115: population per government hospital bed, latest year -
    # "-" in the source means undefined (zero beds in that district), not
    # a literal zero pop-per-bed - kept blank rather than coerced to 0 so
    # downstream consumers don't read it as "excellent access".
    for district, cells in iter_district_rows_raw(PAGE_POP_PER_BED):
        if len(cells) < 9:
            continue
        rec = ensure(district)
        raw = cells[8]
        rec["pop_per_bed"] = parse_int(raw) if raw not in ("-", "", "`") else ""

    # --- Table 117: medical & paramedical staff posted, latest year ----
    for district, cells in iter_district_rows(PAGE_STAFF_POSTED):
        if len(cells) < 8:
            continue
        rec = ensure(district)
        rec["medical_staff"] += cells[0] + cells[1] + cells[2]
        rec["paramedical_staff"] += cells[3] + cells[4] + cells[5] + cells[6] + cells[7]

    # --- Table 119: registered private practitioners, latest year ------
    for district, cells in iter_district_rows(PAGE_PVT_PRACTITIONERS):
        if len(cells) < 3:
            continue
        rec = ensure(district)
        rec["pvt_practitioners"] += cells[2]

    rows_out = []
    for district, vals in sorted(district_totals.items()):
        rows_out.append({"district": district, **vals})

    out_path = PROCESSED / "dev_stats_health.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "district", "govt_institutions", "govt_beds", "pvt_hospitals", "pvt_beds",
                "medical_staff", "paramedical_staff", "pvt_practitioners", "pop_per_bed",
            ],
        )
        writer.writeheader()
        writer.writerows(rows_out)

    print(
        f"Wrote dev_stats_health.csv for {len(rows_out)} districts "
        f"(provincial total {inst_sum} institutions / {beds_sum} govt beds; "
        f"cross-check target {kp_institutions} / {kp_beds})"
    )


if __name__ == "__main__":
    main()
    main_patients_treated()
    main_immunization()
    main_malaria()
