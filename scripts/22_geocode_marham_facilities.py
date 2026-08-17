"""Geocode Marham.pk facility records that lack real coordinates (the
~47% carrying a 0,0 placeholder - see scripts/21_fetch_facilities_marham.py)
via OSM Nominatim, mirroring scripts/04_geocode_kphcc_facilities.py's
existing pattern exactly (same query shape, same district-centroid
fallback, same rate_limited_get politeness). Records that already have
real Marham-supplied coordinates skip Nominatim entirely - no geocoding
call is spent on data already precise."""
import json
import sys
from pathlib import Path

from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.http_utils import make_session, rate_limited_get

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "marham_facilities.json"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Same bounding box used by scripts/05_fetch_facilities_osm.py and
# scripts/06_fetch_roads_osm.py (south, west, north, east). Found needed
# during Task 4's live run: Nominatim's fuzzy address matching sometimes
# returns a wildly wrong location for an ambiguous facility-name-like
# "street address" (e.g. "ADC Abbottabad ( Hope Breast Clinic )" matched
# to lon=66.6, nowhere near KP) - a result this far outside the province
# is treated the same as "no match found", falling back to the district
# centroid, rather than accepted as a real geocode.
KP_BBOX = (31.0, 69.2, 36.9, 74.1)


def is_within_kp_bounds(lon, lat):
    south, west, north, east = KP_BBOX
    return west <= lon <= east and south <= lat <= north


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
        lon, lat = float(results[0]["lon"]), float(results[0]["lat"])
        if is_within_kp_bounds(lon, lat):
            return lon, lat
        # A wildly wrong fuzzy match (see KP_BBOX comment above) - treat
        # as no match rather than accept an obviously incorrect point.
        return None
    return None


def resolve_coordinates(rec, session, centroids, geocode_fn=geocode_address):
    """Returns (lon, lat, geo_precision) for one Marham record. Trusts
    Marham's own coordinates only if they're both present AND within
    KP's bounds - has_real_coords=True does not by itself guarantee the
    point is anywhere near KP (see KP_BBOX comment above; found via a
    real record during Task 4's live run). Falls through to geocode_fn
    (Nominatim by default, overridable for testing) when source
    coordinates are missing or implausible, then to the district
    centroid when neither works, then "unresolved" if even that fails."""
    if rec["has_real_coords"] and is_within_kp_bounds(rec["lon"], rec["lat"]):
        return rec["lon"], rec["lat"], "source"

    coords = None
    try:
        coords = geocode_fn(session, rec["street_address"], rec["district"])
    except RuntimeError:
        coords = None
    if coords:
        return coords[0], coords[1], "street"

    fallback = centroids.get(rec["district"])
    if fallback is None:
        return None, None, "unresolved"
    return fallback[0], fallback[1], "district_centroid"


def main():
    records = json.loads(RAW.read_text())
    centroids = load_district_centroids()
    session = make_session()

    counts = {"source": 0, "street": 0, "district_centroid": 0, "unresolved": 0}
    for i, rec in enumerate(records, start=1):
        lon, lat, precision = resolve_coordinates(rec, session, centroids)
        rec["lon"], rec["lat"], rec["geo_precision"] = lon, lat, precision
        counts[precision] += 1
        if i % 25 == 0:
            print(f"  {i}/{len(records)} processed")

    PROCESSED.mkdir(parents=True, exist_ok=True)
    (PROCESSED / "marham_facilities_geocoded.json").write_text(json.dumps(records, indent=2))
    print(
        f"Geocoded Marham facilities: {counts['source']} from source, {counts['street']} via Nominatim, "
        f"{counts['district_centroid']} via district centroid, {counts['unresolved']} unresolved, "
        f"{len(records)} total"
    )


if __name__ == "__main__":
    main()
