import shapefile  # pyshp
from shapely.geometry import Polygon, LineString, Point
from scripts.lib import shp_writer


def test_write_polygon_shapefile(tmp_path):
    path = str(tmp_path / "test_poly")
    square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    records = [{"geometry": square, "name": "Square", "count": 5}]
    field_defs = [("name", "C", 40, 0), ("count", "N", 6, 0)]
    shp_writer.write_shapefile(path, "POLYGON", records, field_defs)

    r = shapefile.Reader(path)
    assert len(r.shapes()) == 1
    assert r.shapes()[0].shapeType == shapefile.POLYGON
    rec = r.record(0)
    assert rec["name"] == "Square"
    assert rec["count"] == 5
    with open(path + ".prj") as f:
        assert "GCS_WGS_1984" in f.read()


def test_write_point_shapefile(tmp_path):
    path = str(tmp_path / "test_pts")
    records = [{"geometry": Point(71.5, 34.0), "name": "Facility A"}]
    field_defs = [("name", "C", 60, 0)]
    shp_writer.write_shapefile(path, "POINT", records, field_defs)
    r = shapefile.Reader(path)
    assert len(r.shapes()) == 1
    assert r.shapes()[0].points == [(71.5, 34.0)]


def test_write_line_shapefile(tmp_path):
    path = str(tmp_path / "test_lines")
    line = LineString([(71.0, 34.0), (71.5, 34.5)])
    records = [{"geometry": line, "name": "Road A"}]
    field_defs = [("name", "C", 40, 0)]
    shp_writer.write_shapefile(path, "POLYLINE", records, field_defs)
    r = shapefile.Reader(path)
    assert len(r.shapes()) == 1
    assert r.shapes()[0].shapeType == shapefile.POLYLINE
