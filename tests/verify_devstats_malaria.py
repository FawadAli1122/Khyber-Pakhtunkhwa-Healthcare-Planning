import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "dev_stats_malaria.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 35, f"Expected all 35 KP districts, got {len(rows)}"

    for r in rows:
        # A regression of Task 1's title-row filter fix would surface
        # here first, as a garbage district name pulled from the page's
        # merged title text rather than a real KP district.
        assert "Table No." not in r["district"] and "DISTRICT WISE" not in r["district"].upper(), (
            f"Garbage district name found: {r['district']!r} - the title-row filter fix likely regressed"
        )
        assert int(r["blood_slides_examined"]) >= 0
        assert int(r["malaria_cases"]) >= 0
        assert int(r["malaria_cases_treated"]) >= 0

    print(f"OK: dev_stats_malaria.csv covers {len(rows)} districts")


if __name__ == "__main__":
    main()
