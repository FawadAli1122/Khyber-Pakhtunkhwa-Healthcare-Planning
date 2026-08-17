"""Extract the Health sector's ADP (Annual Development Programme) budget
allocation from Development Statistics of Khyber Pakhtunkhwa 2025,
Tables 188 (FY2025-26 planned allocations, page 404) and 189 (FY2024-25,
page 405) - provincial-level figures used for report narrative context
only (not a per-district shapefile attribute). Figures are in Rs.
Million. Also captures each year's all-sector provincial total so the
report can state Health's share of the total development budget."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.pdf_tables import extract_table_rows

PDF_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "kp_development_statistics_2025.pdf"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

PAGE_ADP_FY2025_26 = 404  # Table 188
PAGE_ADP_FY2024_25 = 405  # Table 189


def parse_amount(s):
    s = (s or "").replace(",", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def extract_year(page_idx):
    """Rows on these pages are [S#, Sector, KP, MA, AIP, Total]; the
    provincial total row has no S# so its label lands in column 0
    ("Khyber Pakhtunkhwa") instead of column 1 like every sector row."""
    rows = extract_table_rows(str(PDF_PATH), page_idx)
    provincial_total = None
    health = None
    for row in rows:
        if not row or len(row) < 6:
            continue
        if (row[0] or "").strip() == "Khyber Pakhtunkhwa":
            provincial_total = [parse_amount(c) for c in row[2:6]]
            continue
        label = (row[1] or "").strip()
        if label.lower() == "health":
            health = [parse_amount(c) for c in row[2:6]]
    return health, provincial_total


def main():
    health_2526, total_2526 = extract_year(PAGE_ADP_FY2025_26)
    health_2425, total_2425 = extract_year(PAGE_ADP_FY2024_25)
    assert health_2526 and total_2526, "Could not find Health sector / provincial total row in Table 188 (FY2025-26)"
    assert health_2425 and total_2425, "Could not find Health sector / provincial total row in Table 189 (FY2024-25)"

    def make_entry(health, total):
        kp, ma, aip, ttotal = health
        _, _, _, provincial_total = total
        return {
            "kp": kp, "ma": ma, "aip": aip, "total": ttotal,
            "provincial_total": provincial_total,
            "share_pct": round(ttotal / provincial_total * 100, 2) if provincial_total else None,
        }

    out = {
        "fy2024_25": make_entry(health_2425, total_2425),
        "fy2025_26": make_entry(health_2526, total_2526),
    }
    (PROCESSED / "dev_stats_budget.json").write_text(json.dumps(out, indent=2))
    print(
        f"Wrote dev_stats_budget.json: FY24-25 Health total Rs.{out['fy2024_25']['total']:.0f}M "
        f"({out['fy2024_25']['share_pct']}% of ADP), "
        f"FY25-26 Rs.{out['fy2025_26']['total']:.0f}M ({out['fy2025_26']['share_pct']}% of ADP)"
    )


if __name__ == "__main__":
    main()
