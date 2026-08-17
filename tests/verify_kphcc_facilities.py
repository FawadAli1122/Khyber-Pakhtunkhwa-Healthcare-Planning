import json
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "kphcc_facilities.json"
REQUIRED_KEYS = {"licence_no", "issue_date", "expire_date", "category", "public_private", "name", "address", "district", "beds"}


def main():
    records = json.loads(RAW.read_text())
    assert len(records) >= 100, f"Suspiciously few KPHCC records: {len(records)}"

    for r in records:
        assert REQUIRED_KEYS.issubset(r.keys()), f"Missing keys in record: {r}"
        assert r["name"], f"Empty name in record: {r}"
        assert r["district"], f"Empty district in record: {r}"
        if r["beds"] is not None:
            assert r["beds"] >= 0, f"Negative bed count: {r}"

    districts = {r["district"] for r in records}
    categories = {r["category"] for r in records}
    print(f"OK: {len(records)} KPHCC records across {len(districts)} districts, categories: {sorted(categories)}")


if __name__ == "__main__":
    main()
