"""ML-based new-facility site suggestion: for the worst-scoring districts,
run a population-weighted KMeans over OSM settlement points to find
population centers, then rank those centers by distance from the nearest
existing facility (farthest-from-care first). This approximates a
maximum-coverage facility-location heuristic without a full optimization
solver — documented as a simplified heuristic in the HTML report."""
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.geo_utils import haversine_km
from scripts.lib.http_utils import make_session

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
KP_BBOX = (31.0, 69.2, 36.9, 74.1)
TOP_N_DISTRICTS = 10
SITES_PER_DISTRICT = 1

EXCLUDED_LANDCOVER_CLASSES = {
    70: "Snow and ice",
    80: "Permanent water bodies",
    90: "Herbaceous wetland",
}


def _adjust_for_landcover(lon, lat, cluster_idx, labels, settlements, sample_landcover):
    """Returns (lon, lat, adjustment_note). adjustment_note is None when
    the original centroid's land cover is fine as-is. A KMeans cluster
    centroid is a synthetic geometric point - it can land in a river or
    snowfield even though the real settlements feeding that cluster are
    legitimate, inhabited (and therefore buildable) locations, so this
    falls back to the highest-population real settlement in the same
    cluster whose own land cover is allowed, rather than searching for
    the nearest valid raster pixel with no settlement backing it."""
    excluded_label = EXCLUDED_LANDCOVER_CLASSES.get(sample_landcover(lon, lat))
    if excluded_label is None:
        return lon, lat, None

    cluster_settlements = sorted(
        (s for s, label in zip(settlements, labels) if label == cluster_idx),
        key=lambda s: s.get("population", 1), reverse=True,
    )
    for s in cluster_settlements:
        if sample_landcover(s["lon"], s["lat"]) not in EXCLUDED_LANDCOVER_CLASSES:
            return s["lon"], s["lat"], f"adjusted from a nearby cluster centroid falling in {excluded_label}"

    return lon, lat, (
        f"cluster centroid falls in {excluded_label} and no member settlement offered a clear "
        "alternative - manual site verification recommended"
    )


def _make_landcover_sampler():
    landcover_path = GIS_DIR / "KP_LandCover.tif"
    if not landcover_path.exists():
        return None
    import rasterio
    ds = rasterio.open(landcover_path)

    def sample(lon, lat):
        try:
            row, col = ds.index(lon, lat)
            value = ds.read(1, window=((row, row + 1), (col, col + 1)))[0, 0]
        except IndexError:
            return None  # outside the raster's extent - treat as unknown, never excluded
        return int(value)

    return sample


def pick_candidate_sites(settlements, existing_facilities, n_sites, sample_landcover=None):
    if not settlements:
        return []
    n_sites = min(n_sites, len(settlements))
    coords = np.array([[s["lon"], s["lat"]] for s in settlements])
    weights = np.array([max(s.get("population", 1), 1) for s in settlements])

    km = KMeans(n_clusters=n_sites, n_init=10, random_state=42)
    km.fit(coords, sample_weight=weights)
    centers = km.cluster_centers_
    labels = km.labels_

    scored_centers = []
    for cluster_idx, (lon, lat) in enumerate(centers):
        adjustment_note = None
        if sample_landcover is not None:
            lon, lat, adjustment_note = _adjust_for_landcover(
                lon, lat, cluster_idx, labels, settlements, sample_landcover
            )
        if existing_facilities:
            nearest_km = min(haversine_km(lon, lat, f["lon"], f["lat"]) for f in existing_facilities)
        else:
            nearest_km = float("inf")
        scored_centers.append((nearest_km, lon, lat, adjustment_note))

    scored_centers.sort(key=lambda t: t[0], reverse=True)  # farthest-from-care first
    results = []
    for nearest_km, lon, lat, adjustment_note in scored_centers:
        base = "Nearby settlement" if adjustment_note else "Population-weighted settlement cluster centroid"
        if nearest_km == float("inf"):
            rationale = f"{base}; no existing facility currently mapped in this district"
        else:
            rationale = f"{base}, ~{nearest_km:.1f} km from nearest existing facility"
        if adjustment_note:
            rationale += f" ({adjustment_note})"
        results.append({"lat": round(lat, 5), "lon": round(lon, 5), "rationale": rationale})
    return results


def fetch_settlements():
    session = make_session()
    bbox_str = ",".join(str(v) for v in KP_BBOX)
    query = f"""
    [out:json][timeout:180];
    (
      node["place"="town"]({bbox_str});
      node["place"="village"]({bbox_str});
    );
    out;
    """
    last_exc = None
    for attempt in range(3):
        try:
            resp = session.post(OVERPASS_URL, data={"data": query}, timeout=240)
            resp.raise_for_status()
            break
        except Exception as exc:  # noqa: BLE001 - Overpass' public instance is occasionally overloaded
            last_exc = exc
            time.sleep(10 * (attempt + 1))
    else:
        raise RuntimeError(f"Overpass settlements query failed after 3 attempts") from last_exc
    data = resp.json()
    records = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        pop = tags.get("population")
        records.append(
            {
                "lat": el["lat"],
                "lon": el["lon"],
                "population": int(pop) if pop and str(pop).isdigit() else 300,  # small-village default
            }
        )
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "osm_settlements.json").write_text(json.dumps(records))
    return records


def load_facilities_by_district():
    by_district = {}
    with open(PROCESSED / "facilities_merged.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["is_duplicate_of"]:
                continue
            by_district.setdefault(r["district"], []).append({"lat": float(r["lat"]), "lon": float(r["lon"])})
    return by_district


def load_settlements_by_district(settlements, boundaries):
    from shapely.geometry import Point, shape
    from scripts.lib.geo_utils import find_containing_district

    districts = [{"district": d["district"], "geometry": shape(d["geometry"])} for d in boundaries["districts"]]
    province_geom = shape(boundaries["province_geometry"])
    by_district = {}
    for s in settlements:
        # Same bbox-overfetch issue as scripts/07_merge_facilities.py:
        # the Overpass query's rectangular bbox also returns settlements
        # in neighboring Afghanistan/Punjab/Islamabad. Without this check,
        # find_containing_district()'s nearest-district fallback would
        # silently attribute them to whichever KP district is closest,
        # and a K-means cluster centroid built from those contaminated
        # points can land outside the district - or outside KP entirely.
        if not province_geom.contains(Point(s["lon"], s["lat"])):
            continue
        district = find_containing_district(s["lon"], s["lat"], districts)
        by_district.setdefault(district, []).append(s)
    return by_district


def main():
    with open(PROCESSED / "district_metrics.csv", newline="", encoding="utf-8") as f:
        metrics = list(csv.DictReader(f))
    metrics.sort(key=lambda r: float(r["gap_score"]), reverse=True)
    top_districts = metrics[:TOP_N_DISTRICTS]

    settlements = fetch_settlements()
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    settlements_by_district = load_settlements_by_district(settlements, boundaries)
    facilities_by_district = load_facilities_by_district()
    sample_landcover = _make_landcover_sampler()

    out_rows = []
    for priority, row in enumerate(top_districts, start=1):
        district = row["district"]
        district_settlements = settlements_by_district.get(district, [])
        district_facilities = facilities_by_district.get(district, [])
        sites = pick_candidate_sites(
            district_settlements, district_facilities, SITES_PER_DISTRICT, sample_landcover=sample_landcover,
        )
        for site in sites:
            out_rows.append(
                {
                    "district": district,
                    "priority": priority,
                    "lat": site["lat"],
                    "lon": site["lon"],
                    "rationale": site["rationale"],
                }
            )

    out_path = PROCESSED / "suggested_sites.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "priority", "lat", "lon", "rationale"])
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"Wrote {len(out_rows)} suggested new sites across {len(top_districts)} priority districts")


if __name__ == "__main__":
    main()
