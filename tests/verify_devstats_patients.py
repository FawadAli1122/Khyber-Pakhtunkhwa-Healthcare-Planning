import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "dev_stats_patients_treated.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 35, f"Expected all 35 KP districts, got {len(rows)}"

    for r in rows:
        total = int(r["patients_total_2024"])
        indoor = int(r["patients_indoor_2024"])
        outdoor = int(r["patients_outdoor_2024"])
        assert total >= 0 and indoor >= 0 and outdoor >= 0, f"{r['district']}: negative value found"
        assert total == indoor + outdoor, (
            f"{r['district']}: total ({total}) != indoor ({indoor}) + outdoor ({outdoor}) - "
            "the source table's own structure guarantees this, so a mismatch means a parsing error"
        )

    print(f"OK: dev_stats_patients_treated.csv covers {len(rows)} districts")


if __name__ == "__main__":
    main()
