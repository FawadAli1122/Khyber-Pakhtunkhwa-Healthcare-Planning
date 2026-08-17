import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "dev_stats_roads.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 35, f"Expected all 35 KP districts, got {len(rows)}"
    for r in rows:
        assert float(r["road_km_total"]) >= 0
        assert float(r["road_km_high_type"]) >= 0
        assert float(r["road_km_low_type"]) >= 0
    print(f"OK: dev_stats_roads.csv covers {len(rows)} districts")


if __name__ == "__main__":
    main()
