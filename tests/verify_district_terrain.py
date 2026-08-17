import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "district_terrain.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 25, f"Too few districts: {len(rows)}"
    by_district = {r["district"]: r for r in rows}
    missing_elev = [r["district"] for r in rows if r["mean_elev_m"] == ""]
    assert not missing_elev, f"Districts with no elevation data: {missing_elev}"
    # Sanity: a known high-elevation district should be well above a known low-elevation one.
    chitral = by_district.get("Upper Chitral")
    peshawar = by_district.get("Peshawar")
    assert chitral and peshawar, "Expected both Upper Chitral and Peshawar in the terrain data"
    assert float(chitral["mean_elev_m"]) > float(peshawar["mean_elev_m"]), (
        f"Expected Upper Chitral ({chitral['mean_elev_m']}m) to be higher than Peshawar ({peshawar['mean_elev_m']}m)"
    )
    print(f"OK: district_terrain.csv covers {len(rows)} districts; "
          f"Upper Chitral {chitral['mean_elev_m']}m > Peshawar {peshawar['mean_elev_m']}m")


if __name__ == "__main__":
    main()
