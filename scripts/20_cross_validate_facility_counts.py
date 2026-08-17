"""Compare the pipeline's merged KPHCC+OSM+Marham facility count per
district against Development Statistics 2025's official government
institution count. These count different things (mine includes private
clinics and pharmacies visible in KPHCC/OSM/Marham; Dev Stats counts only
government institutions) so this is not a "should match" reconciliation -
it's a transparency table surfaced in the report explaining where and how
much the two diverge, per district."""
import csv
from pathlib import Path

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"


def main():
    with open(PROCESSED / "district_metrics.csv", newline="", encoding="utf-8") as f:
        merged_counts = {r["district"]: int(r["facility_count"]) for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_health.csv", newline="", encoding="utf-8") as f:
        official_counts = {r["district"]: int(r["govt_institutions"]) for r in csv.DictReader(f) if r["govt_institutions"]}

    rows = []
    for district in sorted(merged_counts):
        merged = merged_counts[district]
        official = official_counts.get(district)
        if official is None:
            rows.append({"district": district, "merged_facility_count": merged, "govt_institutions_official": "", "difference": "", "note": "No Dev Stats entry for this district"})
            continue
        diff = merged - official
        note = "Merged count includes private facilities not in Dev Stats' government-only tally" if diff > 0 else (
            "Dev Stats shows more government institutions than our merged source data captured" if diff < 0 else "Counts match"
        )
        rows.append({"district": district, "merged_facility_count": merged, "govt_institutions_official": official, "difference": diff, "note": note})

    out_path = PROCESSED / "facility_cross_validation.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "merged_facility_count", "govt_institutions_official", "difference", "note"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote facility_cross_validation.csv for {len(rows)} districts")


if __name__ == "__main__":
    main()
