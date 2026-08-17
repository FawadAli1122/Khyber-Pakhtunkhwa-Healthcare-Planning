import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "facilities_merged.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 100, f"Suspiciously few merged facilities: {len(rows)}"
    sources = {r["source"] for r in rows}
    assert sources == {"KPHCC", "OSM"}, f"Unexpected sources: {sources}"
    for r in rows:
        assert r["name"], f"Empty name: {r}"
        lat, lon = float(r["lat"]), float(r["lon"])
        assert -75 < lon < 76 and 30 < lat < 38, f"Coordinate out of bounds: {r}"
    print(f"OK: {len(rows)} merged facilities from sources {sources}")


if __name__ == "__main__":
    main()
