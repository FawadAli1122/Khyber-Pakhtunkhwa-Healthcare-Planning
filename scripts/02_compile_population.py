"""Compile KP district population from the Pakistan Bureau of Statistics'
official Digital Census 2023 dashboard API (census23.pbos.gov.pk), rather
than a secondary/manually-transcribed source. The dashboard's
`/Index/GetData` endpoint returns the exact same per-district figures shown
on the live public dashboard, including PBS's own pre-computed annual
growth rate (`agr`) — used directly here rather than re-deriving growth
from a 2017 comparison figure, since for districts created/reorganized
after 2017 (e.g. Kolai Palas Kohistan) PBS's own boundary-adjusted `agr` is
more reliable than a naive population17-vs-population2023 ratio.

Data source, confirmed live during implementation:
  https://census23.pbos.gov.pk/Index/GetData  (POST code=<district code>, level=3)
Raw per-district responses cached under data/raw/census_districts/*.json
(fetched via scripts/lib/districts.py-normalized codes enumerated from the
dashboard's own dropdown API, /Dynamic/GetDropDownData).
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
RAW_DISTRICTS_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "census_districts"
BOUNDARIES = PROCESSED / "boundaries.json"
POPULATION_CSV = PROCESSED / "kp_district_population_2023.csv"
SOURCE_URL = "https://census23.pbos.gov.pk/Index/GetData (POST code=<district>, level=3)"


def clean_district_name(raw_ds):
    """'BANNU DISTRICT' -> 'Bannu'; 'MALAKAND PROTECTED AREA' -> 'Malakand'."""
    name = raw_ds.strip()
    if name.upper() == "MALAKAND PROTECTED AREA":
        name = "Malakand"
    elif name.upper().endswith(" DISTRICT"):
        name = name[: -len(" DISTRICT")]
    return normalize_district(name.title())


def load_boundary_district_names():
    data = json.loads(BOUNDARIES.read_text())
    return {d["district"] for d in data["districts"]}


def compile_from_raw():
    rows = []
    for f in sorted(RAW_DISTRICTS_DIR.glob("*.json")):
        payload = json.loads(f.read_text(encoding="utf-8"))
        pop = payload["Population"]
        district = clean_district_name(pop["ds"])
        division = pop["dv"].strip().title().replace(" Division", "") + " Division"
        rows.append(
            {
                "district": district,
                "division": division,
                "population_2023": int(pop["population"]),
                "population_prior": int(pop["population17"]) if pop.get("population17") else "",
                "prior_census_year": 2017 if pop.get("population17") else "",
                "growth_rate_pct": round(float(pop["agr"]), 4) if pop.get("agr") is not None else "",
                "source_url": SOURCE_URL,
            }
        )
    rows.sort(key=lambda r: r["district"])
    return rows


def main():
    rows = compile_from_raw()
    boundary_names = load_boundary_district_names()
    csv_names = {r["district"] for r in rows}

    missing = boundary_names - csv_names
    extra = csv_names - boundary_names
    if missing:
        raise RuntimeError(f"Population data missing districts present in boundaries.json: {sorted(missing)}")
    if extra:
        raise RuntimeError(f"Population data has districts not in boundaries.json: {sorted(extra)}")

    fieldnames = ["district", "division", "population_2023", "population_prior", "prior_census_year", "growth_rate_pct", "source_url"]
    with open(POPULATION_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} district population rows, all matched to boundaries.json")


if __name__ == "__main__":
    main()
