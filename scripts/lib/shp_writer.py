"""Minimal shapefile writer built on pyshp for polygon/line/point layers.
pyshp requires exterior rings wound clockwise and holes counter-clockwise
(the Esri shapefile spec's winding rule) — shapely's default winding does
not guarantee this, so every polygon is re-oriented before writing."""
import shapefile  # pyshp
from shapely.geometry.polygon import orient

WGS84_PRJ = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)

_SHAPE_TYPES = {
    "POLYGON": shapefile.POLYGON,
    "POLYLINE": shapefile.POLYLINE,
    "POINT": shapefile.POINT,
}


def write_shapefile(path, geom_type, records, field_defs):
    """path: output path without extension, e.g. "gis/KP_Districts".
    geom_type: "POLYGON" | "POLYLINE" | "POINT".
    records: list of dict with a "geometry" key (shapely geometry) plus one
             key per field_defs name.
    field_defs: list of (name, type_char, size, decimal), e.g.
                [("district", "C", 50, 0), ("pop_2023", "N", 12, 0)].
    Writes path+".shp"/".shx"/".dbf"/".prj" (WGS84)."""
    shp_type = _SHAPE_TYPES[geom_type]
    with shapefile.Writer(path, shapeType=shp_type) as w:
        for name, typ, size, dec in field_defs:
            w.field(name, typ, size, dec)
        for rec in records:
            _write_geom(w, geom_type, rec["geometry"])
            w.record(*[rec.get(name) for name, *_ in field_defs])
    with open(path + ".prj", "w") as f:
        f.write(WGS84_PRJ)


def _write_geom(w, geom_type, geom):
    if geom_type == "POLYGON":
        polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        parts = []
        for poly in polys:
            poly = orient(poly, sign=-1.0)  # exterior CW, holes CCW
            parts.append(list(poly.exterior.coords))
            for interior in poly.interiors:
                parts.append(list(interior.coords))
        w.poly(parts)
    elif geom_type == "POLYLINE":
        lines = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        w.line([list(line.coords) for line in lines])
    elif geom_type == "POINT":
        w.point(geom.x, geom.y)
    else:
        raise ValueError(f"Unsupported geom_type: {geom_type}")
