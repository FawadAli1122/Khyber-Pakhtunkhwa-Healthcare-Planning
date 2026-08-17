"""Geocode each KPHCC facility's free-text address via OSM Nominatim
(free, rate-limited to >=1 req/sec per its usage policy — enforced by
scripts.lib.http_utils.rate_limited_get). Falls back to the facility's
district centroid (from boundaries.json) when Nominatim finds no match,
flagging geo_precision accordingly so downstream consumers know which
points are approximate."""
import json
import sys
from pathlib import Path

from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.http_utils import make_session, rate_limited_get

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "kphcc_facilities.json"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def load_district_centroids():
    data = json.loads((PROCESSED / "boundaries.json").read_text())
    centroids = {}
    for d in data["districts"]:
        geom = shape(d["geometry"])
        c = geom.centroid
        centroids[d["district"]] = (c.x, c.y)
    return centroids


def geocode_address(session, address, district):
    query = f"{address}, {district}, Khyber Pakhtunkhwa, Pakistan"
    resp = rate_limited_get(
        session,
        NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "pk"},
    )
    results = resp.json()
    if results:
        return float(results[0]["lon"]), float(results[0]["lat"])
    return None


def main():
    records = json.loads(RAW.read_text())
    centroids = load_district_centroids()
    session = make_session()

    resolved = 0
    for i, rec in enumerate(records, start=1):
        coords = None
        try:
            coords = geocode_address(session, rec["address"], rec["district"])
        except RuntimeError:
            coords = None
        if coords:
            rec["lon"], rec["lat"] = coords
            rec["geo_precision"] = "street"
            resolved += 1
        else:
            fallback = centroids.get(rec["district"])
            if fallback is None:
                rec["lon"], rec["lat"], rec["geo_precision"] = None, None, "unresolved"
            else:
                rec["lon"], rec["lat"] = fallback
                rec["geo_precision"] = "district_centroid"
        if i % 25 == 0:
            print(f"  {i}/{len(records)} geocoded ({resolved} street-level so far)")

    PROCESSED.mkdir(parents=True, exist_ok=True)
    (PROCESSED / "kphcc_facilities_geocoded.json").write_text(json.dumps(records, indent=2))
    print(f"Geocoded {resolved}/{len(records)} KPHCC facilities to street level")


if __name__ == "__main__":
    main()
