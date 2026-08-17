"""Extract district-level road lengths from Development Statistics of
Khyber Pakhtunkhwa 2025 (data/raw/kp_development_statistics_2025.pdf),
Table 201 - "District Wise Road Lengths in Khyber Pakhtunkhwa" (a single
page, 0-based page index 423, with three year-columns: 2021-22, 2022-23,
2023-24; the latest year, 2023-24, is used). Cross-validates the
district-summed total against the table's own "Khyber Pakhtunkhwa"
provincial total row.

Note: the source PDF has a typo on this specific table's own heading -
"Tabel No. 201" instead of "Table No. 201" - so scripts.lib.pdf_tables.
find_table_pages(doc, 201) will NOT find it; the page index below was
located by direct inspection and is hardcoded rather than looked up."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district
from scripts.lib.pdf_tables import extract_table_rows

PDF_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "kp_development_statistics_2025.pdf"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

PAGE_ROAD_LENGTHS = 423  # Table 201 ("Tabel No. 201" typo in the source)


def parse_km(s):
    s = (s or "").replace(",", "").strip()
    if not s or s in ("-", "`"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def main():
    rows = extract_table_rows(str(PDF_PATH), PAGE_ROAD_LENGTHS)

    district_rows = []
    kp_total = None
    for row in rows:
        if not row or not row[0]:
            continue
        label = row[0].replace("\n", " ").strip()
        if not label or "Tabel No." in label or "Table No." in label:
            continue
        if label in ("District", "Districts") or label.startswith("Note:"):
            continue
        clean_label = label.rstrip("*").strip()
        cells = [parse_km(c) for c in row[1:]]
        if len(cells) < 9:
            continue
        if clean_label == "Khyber Pakhtunkhwa":
            kp_total = cells
            continue
        canonical = normalize_district(clean_label)
        district_rows.append((canonical, cells[6], cells[7], cells[8]))  # 2023-24: total, high, low

    assert kp_total is not None, "Could not locate Table 201's Khyber Pakhtunkhwa total row"
    kp_total_2324, kp_high_2324, kp_low_2324 = kp_total[6], kp_total[7], kp_total[8]

    total_sum = sum(r[1] for r in district_rows)
    diff_pct = abs(total_sum - kp_total_2324) / kp_total_2324 * 100
    assert diff_pct < 5, (
        f"District-summed road length ({total_sum:.0f} km) differs from Table 201's own "
        f"Khyber Pakhtunkhwa total ({kp_total_2324:.0f} km) by {diff_pct:.1f}% - inspect "
        "extract_table_rows() output directly before trusting this data."
    )

    out_path = PROCESSED / "dev_stats_roads.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "road_km_total", "road_km_high_type", "road_km_low_type"])
        writer.writeheader()
        for district, total, high, low in sorted(district_rows):
            writer.writerow({
                "district": district,
                "road_km_total": round(total, 1),
                "road_km_high_type": round(high, 1),
                "road_km_low_type": round(low, 1),
            })

    print(
        f"Wrote dev_stats_roads.csv for {len(district_rows)} districts "
        f"(total {total_sum:.0f} km, cross-check target {kp_total_2324:.0f} km)"
    )


if __name__ == "__main__":
    main()
