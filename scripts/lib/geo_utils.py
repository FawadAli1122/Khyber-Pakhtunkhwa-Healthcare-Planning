"""Geometry helpers: projected area/distance math and point-in-polygon
district assignment. All output shapefiles stay in WGS84 degrees; this
module is only used internally for area_km2 / distance_km computations,
never for the geometry that gets written to disk."""
import math
from shapely.geometry import Point
from shapely.ops import transform

# KP roughly spans 31.0-36.9N, 69.2-74.1E; center latitude for the
# equirectangular projection used in area/distance calculations below.
KP_LAT0 = 34.0
EARTH_RADIUS_KM = 6371.0


def project_xy(lon, lat):
    """Equirectangular projection to meters, centered near KP. Adequate for
    area/distance math over a single mid-latitude province; not for
    anything requiring true equal-area accuracy at continental scale."""
    R = EARTH_RADIUS_KM * 1000.0
    x = math.radians(lon) * R * math.cos(math.radians(KP_LAT0))
    y = math.radians(lat) * R
    return x, y


def to_projected(geom):
    return transform(lambda x, y, z=None: project_xy(x, y), geom)


def polygon_area_km2(geom):
    return to_projected(geom).area / 1_000_000.0


def haversine_km(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def find_containing_district(lon, lat, districts):
    """districts: list of {"district": str, "geometry": shapely Polygon/MultiPolygon}.
    Returns the containing district's name, or the nearest district's name
    (by centroid distance) if the point falls outside every polygon."""
    pt = Point(lon, lat)
    for d in districts:
        if d["geometry"].contains(pt):
            return d["district"]
    best, best_dist = None, float("inf")
    for d in districts:
        c = d["geometry"].centroid
        dist = haversine_km(lon, lat, c.x, c.y)
        if dist < best_dist:
            best, best_dist = d["district"], dist
    return best
