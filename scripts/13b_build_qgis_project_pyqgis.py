"""Build gis/KP_Healthcare_Plan.qgz using QGIS's own PyQGIS API instead of
hand-authored XML. Must be run through QGIS's bundled Python (Windows:
"C:\\Program Files\\QGIS 4.0.0\\bin\\python-qgis.bat" <this file>), not the
project's regular pure-Python environment - it imports the qgis package.

Why this exists alongside scripts/13_build_qgis_project.py: that script
hand-authors the project XML directly (needed originally since no QGIS
install was available to verify against) and got most of it right, but a
real QGIS 4.0 install exposed two real bugs in it: (1) maplayer entries
need their own <srs> block, not just a project-level one, or CRS comes
back invalid on load; (2) QGIS 4's SimpleFill/SimpleLine symbol layer XML
requires an "enabled" attribute and a newer combined color-value format
("r,g,b,a,colorspec:rn,gn,bn,an") that the hand-authored version didn't
use, so polygon fills and lines silently failed to render (points still
did, since SimpleMarker tolerated the older format) even though the
layers loaded as valid with correct feature counts and CRS. This script
sidesteps that entire class of bug by asking QGIS to serialize its own
renderers, which is always correct for whatever QGIS version generates
it. Prefer running this over scripts/13_build_qgis_project.py whenever a
QGIS install is available; keep the hand-authored version as the fallback
for environments without one.
"""
from qgis.core import (
    QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsCoordinateReferenceSystem, QgsGraduatedSymbolRenderer, QgsCategorizedSymbolRenderer,
    QgsRendererRange, QgsRendererCategory, QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
    QgsSingleSymbolRenderer, QgsSingleBandPseudoColorRenderer, QgsColorRampShader, QgsRasterShader,
)
from qgis.PyQt.QtGui import QColor

GIS_DIR = r"E:\Healthcare System Planning\gis"
QGZ_PATH = GIS_DIR + r"\KP_Healthcare_Plan.qgz"
CRS = QgsCoordinateReferenceSystem("EPSG:4326")


def add_layer(project, filename, name):
    layer = QgsVectorLayer(f"{GIS_DIR}/{filename}", name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Failed to load {filename}: {layer.error().summary()}")
    layer.setCrs(CRS)
    project.addMapLayer(layer)
    return layer


def add_raster_layer(project, filename, name):
    layer = QgsRasterLayer(f"{GIS_DIR}/{filename}", name, "gdal")
    if not layer.isValid():
        raise RuntimeError(f"Failed to load {filename}: {layer.error().summary()}")
    layer.setCrs(CRS)

    stops = [(200, "#1a9850", "200 m"), (1500, "#fee08b", "1500 m"),
             (3500, "#d73027", "3500 m"), (7700, "#ffffff", "7700 m")]
    ramp_items = [QgsColorRampShader.ColorRampItem(value, QColor(color), label) for value, color, label in stops]
    shader_fn = QgsColorRampShader()
    shader_fn.setColorRampType(QgsColorRampShader.Type.Interpolated)
    shader_fn.setColorRampItemList(ramp_items)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(shader_fn)
    layer.setRenderer(QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader))

    project.addMapLayer(layer)
    return layer


def style_single_fill(layer, color, outline_color="#404040", outline_width="0.3"):
    symbol = QgsFillSymbol.createSimple({"color": color, "outline_color": outline_color, "outline_width": outline_width})
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def style_single_line(layer, color="#8a8a8a", width="0.4"):
    symbol = QgsLineSymbol.createSimple({"line_color": color, "line_width": width})
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def style_single_marker(layer, color="#e6194b", size="4", shape="star"):
    symbol = QgsMarkerSymbol.createSimple({"color": color, "size": size, "name": shape})
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def style_graduated(layer, field, stops):
    ranges = []
    for lo, hi, color in stops:
        symbol = QgsFillSymbol.createSimple({"color": color, "outline_color": "#404040", "outline_width": "0.3"})
        ranges.append(QgsRendererRange(lo, hi, symbol, f"{lo:.0f} - {hi:.0f}"))
    layer.setRenderer(QgsGraduatedSymbolRenderer(field, ranges))


def style_categorized(layer, field, categories):
    cats = []
    for value, color in categories:
        symbol = QgsMarkerSymbol.createSimple({"color": color, "size": "2.5"})
        cats.append(QgsRendererCategory(value, symbol, str(value)))
    layer.setRenderer(QgsCategorizedSymbolRenderer(field, cats))


def main():
    QgsApplication.setPrefixPath(r"C:\Program Files\QGIS 4.0.0\apps\qgis", True)
    qgs = QgsApplication([], False)
    qgs.initQgis()

    project = QgsProject.instance()
    project.setCrs(CRS)
    project.setTitle("KP Healthcare System Planning")

    add_raster_layer(project, "KP_DEM.tif", "Elevation (DEM)")

    province = add_layer(project, "KP_Province_Boundary.shp", "KP Province Boundary")
    style_single_fill(province, "255,255,255,0")

    districts = add_layer(project, "KP_Districts.shp", "KP Districts (Population)")
    style_graduated(
        districts, "pop_dens",
        [(0, 100, "#ffffcc"), (100, 300, "#fed976"), (300, 700, "#fd8d3c"), (700, 999999, "#e31a1c")],
    )

    gap = add_layer(project, "KP_District_Gap_Scores.shp", "District Gap Scores")
    style_graduated(
        gap, "gap_score",
        [(0, 25, "#1a9850"), (25, 50, "#91cf60"), (50, 75, "#fee08b"), (75, 100, "#d73027")],
    )

    roads = add_layer(project, "KP_Roads.shp", "Roads")
    style_single_line(roads)

    facilities = add_layer(project, "KP_Healthcare_Facilities.shp", "Healthcare Facilities")
    style_categorized(
        facilities, "category",
        [("Hospital", "#e6194b"), ("Clinic", "#4363d8"), ("Pharmacy", "#3cb44b"), ("Facility", "#808080")],
    )

    sites = add_layer(project, "KP_Suggested_New_Sites.shp", "Suggested New Sites")
    style_single_marker(sites)

    ok = project.write(QGZ_PATH)
    if not ok:
        raise RuntimeError(f"Failed to write project: {project.error()}")
    print(f"Wrote {QGZ_PATH} via PyQGIS ({len(project.mapLayers())} layers)")

    qgs.exitQgis()


if __name__ == "__main__":
    main()
