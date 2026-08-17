"""Compute each district's accessibility_min: network- and terrain-
adjusted travel time (minutes) to the nearest KP facility, searched
globally across all of KP rather than restricted to the district's own
mapped facilities (see docs/superpowers/specs/2026-08-15-travel-time-
routing-design.md section 3a). Routes over the OSM road network
(data/raw/osm_roads.json, scripts/06_fetch_roads_osm.py), with road speed
derated by the DEM-derived terrain_difficulty score
(scripts/lib/terrain.py) for whichever district each road segment sits
in, and a straight-line "last mile" leg connecting facilities/district
centroids to the mapped network. Falls back to a straight-line estimate
where the road network doesn't connect a district to any facility at all
(scripts/lib/routing.py handles both the routing and the fallback).
Writes data/processed/district_travel_time.csv, consumed by
scripts/08_compute_district_metrics.py."""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask as rasterio_mask
from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib import routing
from scripts.lib.geo_utils import haversine_km
from scripts.lib.terrain import compute_terrain_difficulty

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"

BUILT_UP_CLASS = 50


def _mean_coordinate(landcover_array, transform, target_class=BUILT_UP_CLASS):
    """landcover_array: 2D numpy array of land-cover class codes (already
    clipped to a district). Returns (lon, lat) of the mean coordinate of
    all pixels equal to target_class, or None if there are none - pure
    function, independently testable without real raster I/O. See
    docs/superpowers/specs/2026-08-16-landcover-accessibility-design.md."""
    rows, cols = np.where(landcover_array == target_class)
    if len(rows) == 0:
        return None
    xs, ys = rasterio.transform.xy(transform, rows, cols)
    return float(np.mean(xs)), float(np.mean(ys))


def _built_up_weighted_point(geom, landcover_ds):
    """geom: shapely district polygon. landcover_ds: open rasterio dataset
    for KP_LandCover.tif. Returns (lon, lat) or None if the district has
    no Built-up pixels (or the geometry doesn't overlap the raster at
    all)."""
    try:
        clipped, transform = rasterio_mask(landcover_ds, [mapping(geom)], crop=True, nodata=0)
    except ValueError:
        return None  # geometry doesn't overlap the raster at all
    return _mean_coordinate(clipped[0], transform)


def build_districts_with_centroids(boundaries, landcover_path=None):
    """boundaries: parsed boundaries.json dict. landcover_path: optional
    Path to KP_LandCover.tif - when given and it exists, each district's
    routing origin is the mean coordinate of its Built-up land-cover
    pixels (where people actually live) instead of the plain geometric
    centroid, falling back to the geometric centroid for a district with
    no Built-up pixels mapped. Returns {"district", "geometry",
    "centroid_lon", "centroid_lat", "centroid_shift_km", "point_source"}
    per district - the shape scripts.lib.routing.compute_district_accessibility
    expects (plus the two new fields)."""
    landcover_ds = None
    if landcover_path is not None and landcover_path.exists():
        landcover_ds = rasterio.open(landcover_path)

    out = []
    for d in boundaries["districts"]:
        geom = shape(d["geometry"])
        geometric_centroid = geom.centroid

        built_up_point = _built_up_weighted_point(geom, landcover_ds) if landcover_ds is not None else None
        if built_up_point is not None:
            lon, lat = built_up_point
            point_source = "built_up_weighted"
            shift_km = haversine_km(lon, lat, geometric_centroid.x, geometric_centroid.y)
        else:
            lon, lat = geometric_centroid.x, geometric_centroid.y
            point_source = "geometric_centroid"
            shift_km = 0.0

        out.append({
            "district": d["district"],
            "geometry": geom,
            "centroid_lon": lon,
            "centroid_lat": lat,
            "centroid_shift_km": round(shift_km, 2),
            "point_source": point_source,
        })

    if landcover_ds is not None:
        landcover_ds.close()
    return out


def load_facilities(rows):
    """rows: csv.DictReader rows from data/processed/facilities_merged.csv.
    Returns [{"lon": float, "lat": float}, ...] for every non-duplicate
    facility in all of KP (global search, not restricted to the same
    district - see module docstring)."""
    return [
        {"lon": float(r["lon"]), "lat": float(r["lat"])}
        for r in rows
        if not r["is_duplicate_of"]
    ]


def build_terrain_by_district(terrain_rows):
    """terrain_rows: csv.DictReader rows from
    data/processed/district_terrain.csv. Returns {district_name:
    terrain_difficulty} via the shared scripts.lib.terrain function."""
    scored = compute_terrain_difficulty(list(terrain_rows))
    return {r["district"]: r["terrain_difficulty"] for r in scored}


def main():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    districts = build_districts_with_centroids(boundaries, landcover_path=GIS_DIR / "KP_LandCover.tif")

    road_records = json.loads((RAW / "osm_roads.json").read_text())

    with open(PROCESSED / "facilities_merged.csv", newline="", encoding="utf-8") as f:
        facilities = load_facilities(csv.DictReader(f))

    with open(PROCESSED / "district_terrain.csv", newline="", encoding="utf-8") as f:
        terrain_by_district = build_terrain_by_district(list(csv.DictReader(f)))

    accessibility = routing.compute_district_accessibility(road_records, facilities, districts, terrain_by_district)

    out_path = PROCESSED / "district_travel_time.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "accessibility_min", "centroid_shift_km", "point_source"])
        writer.writeheader()
        for d in districts:
            value = accessibility[d["district"]]
            writer.writerow({
                "district": d["district"],
                "accessibility_min": value if value is not None else "",
                "centroid_shift_km": d["centroid_shift_km"],
                "point_source": d["point_source"],
            })
    print(f"Wrote district_travel_time.csv for {len(districts)} districts")


if __name__ == "__main__":
    main()
