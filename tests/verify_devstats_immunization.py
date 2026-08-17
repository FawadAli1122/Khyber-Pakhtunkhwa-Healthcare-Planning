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
