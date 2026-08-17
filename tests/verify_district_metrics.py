import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "district_metrics.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 25, f"Too few districts in metrics: {len(rows)}"
    for r in rows:
        assert float(r["area_km2"]) > 0, f"Non-positive area: {r}"
        assert int(r["population_2023"]) >= 0, f"Negative population: {r}"
        assert r["terrain"] in ("mountainous", "plains"), f"Bad terrain value: {r}"
        assert int(r["facility_count"]) >= 0
    zero_pop = [r["district"] for r in rows if int(r["population_2023"]) == 0]
    assert not zero_pop, f"Districts with zero population (likely a name-join miss): {zero_pop}"
    print(f"OK: district_metrics.csv covers {len(rows)} districts, no zero-population joins")


if __name__ == "__main__":
    main()
