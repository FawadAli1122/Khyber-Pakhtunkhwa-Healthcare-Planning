import json
from pathlib import Path

JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "marham_facilities.json"

EXPECTED_DISTRICTS = {
    "Abbottabad", "Bajaur", "Bannu", "Buner", "Charsadda", "Dera Ismail Khan",
    "Hangu", "Haripur", "Kohat", "Malakand", "Mansehra", "Mardan", "Nowshera",
    "Peshawar", "Swabi", "Swat", "Tank", "Lower Dir",
}


def main():
    records = json.loads(JSON_PATH.read_text())
    assert len(records) > 0, "Expected at least some Marham facilities"

    districts_found = {r["district"] for r in records}
    unexpected = districts_found - EXPECTED_DISTRICTS
    assert not unexpected, f"Unexpected district(s) in output: {unexpected} - district_from_marham_slug mapping may be wrong"

    with_coords = sum(1 for r in records if r["has_real_coords"])
    for r in records:
        assert r["name"], f"Empty name found (url={r['url']})"
        assert r["district"] in EXPECTED_DISTRICTS
        assert r["category"] in ("Hospital", "Clinic", "Pharmacy", "Facility", "Other")

    print(f"OK: {len(records)} Marham facilities across {len(districts_found)} districts ({with_coords} with real source coordinates)")


if __name__ == "__main__":
    main()
