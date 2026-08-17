import json
from pathlib import Path

JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "marham_facilities_geocoded.json"


def main():
    records = json.loads(JSON_PATH.read_text())
    assert len(records) > 0

    resolved = [r for r in records if r["geo_precision"] != "unresolved"]
    for r in resolved:
        assert r["lon"] is not None and r["lat"] is not None, f"{r['name']}: resolved but missing coordinates"
        assert 68 <= r["lon"] <= 76, f"{r['name']}: lon {r['lon']} outside KP's plausible range"
        assert 30 <= r["lat"] <= 38, f"{r['name']}: lat {r['lat']} outside KP's plausible range"
        assert r["geo_precision"] in ("source", "street", "district_centroid")

    by_precision = {}
    for r in records:
        by_precision[r["geo_precision"]] = by_precision.get(r["geo_precision"], 0) + 1
    print(f"OK: {len(records)} Marham facilities geocoded - {by_precision}")


if __name__ == "__main__":
    main()
