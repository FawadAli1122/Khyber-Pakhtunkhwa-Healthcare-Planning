"""Build the JSON payload embedded in the interactive dashboard: one
compact record per district combining its gap score/population density
(for the choropleth fill) with a simplified SVG path for its boundary
geometry. Pure data transform - no HTML/JS/matplotlib here, so it's
testable without rendering anything.
"""
from shapely.geometry import shape

# KP's districts span roughly this lon/lat box (data/processed/boundaries.json).
# A fixed box - rather than deriving min/max per render - keeps the choropleth's
# scale stable across rebuilds even if a future district edit shifts the data's
# bounding box slightly.
LON_MIN, LON_MAX = 69.3, 74.2
LAT_MIN, LAT_MAX = 31.5, 36.9
SVG_WIDTH = 620
SVG_HEIGHT = 740

# Degrees, not meters - district polygons don't need survey precision for a
# ~600px map, and this keeps the embedded JSON payload small.
SIMPLIFY_TOLERANCE_DEG = 0.005


def project(lon, lat):
    """Linear lon/lat -> SVG pixel coords. Y is flipped: SVG grows downward,
    latitude grows upward."""
    x = (lon - LON_MIN) / (LON_MAX - LON_MIN) * SVG_WIDTH
    y = (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * SVG_HEIGHT
    return x, y


def polygon_to_svg_path(geom):
    """Convert a shapely Polygon/MultiPolygon into one SVG <path> `d` string
    (one subpath per polygon part; interior rings/holes are not drawn - none
    of KP's district polygons have holes worth rendering at dashboard scale).
    """
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    parts = []
    for poly in polys:
        simplified = poly.simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
        coords = list(simplified.exterior.coords)
        if coords and coords[0] == coords[-1]:
            coords = coords[:-1]  # drop the duplicate closing point; "Z" re-closes it
        if len(coords) < 3:
            continue
        points = [project(lon, lat) for lon, lat in coords]
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points) + " Z"
        parts.append(d)
    return " ".join(parts)


def build_dashboard_payload(boundaries, metrics):
    """boundaries: parsed data/processed/boundaries.json
    ({"districts": [{"district": ..., "geometry": <geojson>}, ...]}).
    metrics: list of dict rows from data/processed/district_metrics.csv
    (csv.DictReader output).
    Returns a JSON-serializable dict for the "#dashboard-data" script block.
    """
    metrics_by_district = {m["district"]: m for m in metrics}
    records = []
    for d in boundaries["districts"]:
        m = metrics_by_district.get(d["district"])
        if m is None:
            continue
        records.append(
            {
                "district": d["district"],
                "gap_score": round(float(m["gap_score"]), 1),
                "pop_density": round(float(m["pop_density"]), 1),
                "need_tier": m["need_tier"],
                "path": polygon_to_svg_path(shape(d["geometry"])),
            }
        )
    return {"districts": records}
