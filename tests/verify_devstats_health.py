import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "dev_stats_health.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 35, f"Expected all 35 KP districts, got {len(rows)}"

    int_fields = [
        "govt_institutions", "govt_beds", "pvt_hospitals", "pvt_beds",
        "medical_staff", "paramedical_staff", "pvt_practitioners",
    ]
    for r in rows:
        for field in int_fields:
            assert int(r[field]) >= 0, f"{r['district']}.{field} is negative: {r[field]}"
        if r["pop_per_bed"] != "":
            assert int(r["pop_per_bed"]) >= 0, f"{r['district']}.pop_per_bed is negative"

    # Every district should have at least one government health
    # institution - a fully-zero row would indicate a parsing failure
    # for that district, not a real absence of any health facility.
    for r in rows:
        assert int(r["govt_institutions"]) > 0, f"{r['district']} has zero government institutions - likely a parsing gap"

    print(f"OK: dev_stats_health.csv covers {len(rows)} districts")


if __name__ == "__main__":
    main()
