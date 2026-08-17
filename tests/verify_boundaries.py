"""Sanity checks on the fetched boundary data. Run after
scripts/01_fetch_boundaries.py — not a unit test, since the exact district
count/geometry depends on the live external dataset fetched."""
import json
from pathlib import Path
from shapely.geometry import shape

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed" / "boundaries.json"


def main():
    data = json.loads(PROCESSED.read_text())
    districts = data["districts"]
    assert 25 <= len(districts) <= 45, f"Unexpected district count: {len(districts)}"

    names = [d["district"] for d in districts]
    assert len(names) == len(set(names)), f"Duplicate district names after normalization: {names}"

    for d in districts:
        geom = shape(d["geometry"])
        assert geom.is_valid, f"Invalid geometry for {d['district']}"
        assert d["area_km2"] > 0, f"Non-positive area for {d['district']}"

    total_area = sum(d["area_km2"] for d in districts)
    # KP's total area (incl. merged tribal districts) is ~101,741 km^2 published
    # figure; allow a wide tolerance since simplified/clipped boundary datasets
    # vary.
    assert 60000 <= total_area <= 140000, f"Total KP area implausible: {total_area}"

    province_geom = shape(data["province_geometry"])
    assert province_geom.is_valid, "Province dissolve geometry invalid"

    print(f"OK: {len(districts)} districts, total area {total_area:.0f} km^2")


if __name__ == "__main__":
    main()
