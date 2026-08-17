"""Fetch OSM roads (motorway/trunk/primary/secondary/tertiary/
unclassified/residential) within KP's bounding box via Overpass, used for
the HTML report's road-context map and as the routable network for
scripts/16b_compute_travel_time_accessibility.py's travel-time
accessibility metric. The expanded road classes (beyond the original
major-roads-only set) are needed to reach rural facilities that don't sit
on a primary/secondary road - a sparser network would leave most
district-to-facility routes with no path to the mapped network at all
near their endpoints. Query size/timeout is correspondingly larger than a
major-roads-only fetch; if Overpass still times out in practice, splitting
the query by district or road-class batch is the next lever (not yet
needed as of this writing)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.http_utils import make_session

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
KP_BBOX = (31.0, 69.2, 36.9, 74.1)

QUERY_TEMPLATE = """
[out:json][timeout:300];
(
  way["highway"="motorway"]({bbox});
  way["highway"="trunk"]({bbox});
  way["highway"="primary"]({bbox});
  way["highway"="secondary"]({bbox});
  way["highway"="tertiary"]({bbox});
  way["highway"="unclassified"]({bbox});
  way["highway"="residential"]({bbox});
);
out geom;
"""


def fetch():
    session = make_session()
    bbox_str = ",".join(str(v) for v in KP_BBOX)
    query = QUERY_TEMPLATE.format(bbox=bbox_str)
    resp = session.post(OVERPASS_URL, data={"data": query}, timeout=400)
    resp.raise_for_status()
    return resp.json()


def parse_elements(data):
    records = []
    for el in data.get("elements", []):
        if el["type"] != "way" or "geometry" not in el:
            continue
        tags = el.get("tags", {})
        coords = [[pt["lon"], pt["lat"]] for pt in el["geometry"]]
        if len(coords) < 2:
            continue
        records.append(
            {
                "name": tags.get("name", ""),
                "road_class": tags.get("highway", "unknown"),
                "coordinates": coords,
                "osm_id": el["id"],
            }
        )
    return records


def main():
    data = fetch()
    records = parse_elements(data)
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "osm_roads.json").write_text(json.dumps(records))
    print(f"Wrote {len(records)} OSM road segments")


if __name__ == "__main__":
    main()
