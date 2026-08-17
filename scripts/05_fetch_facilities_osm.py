"""Fetch OpenStreetMap healthcare facility points (hospitals, clinics,
doctors, pharmacies) within KP via the Overpass API, as a supplemental
source to fill gaps in the KPHCC registry (which has no entries for
several tribal districts and skips government facilities that don't need
KPHCC licensing)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.http_utils import make_session

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

# KP's bounding box (south, west, north, east) — a coarse pre-filter;
# Overpass itself has no simple "inside this custom polygon" filter without
# an uploaded area, so results are clipped to the real KP polygon in
# scripts/07_merge_facilities.py using boundaries.json.
KP_BBOX = (31.0, 69.2, 36.9, 74.1)

QUERY_TEMPLATE = """
[out:json][timeout:120];
(
  node["amenity"="hospital"]({bbox});
  node["amenity"="clinic"]({bbox});
  node["amenity"="doctors"]({bbox});
  node["amenity"="pharmacy"]({bbox});
  node["healthcare"]({bbox});
);
out center;
"""


def fetch():
    session = make_session()
    bbox_str = ",".join(str(v) for v in KP_BBOX)
    query = QUERY_TEMPLATE.format(bbox=bbox_str)
    resp = session.post(OVERPASS_URL, data={"data": query}, timeout=180)
    resp.raise_for_status()
    return resp.json()


CATEGORY_TAGS = {
    "hospital": "Hospital",
    "clinic": "Clinic",
    "doctors": "Clinic",
    "pharmacy": "Pharmacy",
}


def parse_elements(data):
    records = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        amenity = tags.get("amenity")
        category = CATEGORY_TAGS.get(amenity, tags.get("healthcare", "Facility").title())
        records.append(
            {
                "name": name,
                "category": category,
                "lat": el["lat"],
                "lon": el["lon"],
                "osm_id": el["id"],
                "osm_type": el["type"],
            }
        )
    return records


def main():
    data = fetch()
    records = parse_elements(data)
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "osm_facilities.json").write_text(json.dumps(records, indent=2))
    print(f"Wrote {len(records)} OSM facility records")


if __name__ == "__main__":
    main()
