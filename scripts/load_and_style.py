"""PyQGIS console fallback: run this from QGIS's own Python Console
(Plugins > Python Console) if gis/KP_Healthcare_Plan.qgz has any
compatibility issue when opened directly in your QGIS version. It
reconstructs the same 6 vector layers plus the KP_DEM.tif elevation
raster with the same styling programmatically against the live QGIS API,
which sidesteps any hand-authored-XML version mismatch.

Usage inside QGIS's Python Console:
    exec(open(r"E:/Healthcare System Planning/scripts/load_and_style.py").read())
"""
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, QgsGraduatedSymbolRenderer, QgsCategorizedSymbolRenderer,
    QgsRendererRange, QgsRendererCategory, QgsSymbol, QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
    QgsSingleBandPseudoColorRenderer, QgsColorRampShader, QgsRasterShader,
)
from qgis.PyQt.QtGui import QColor

GIS_DIR = r"E:/Healthcare System Planning/gis"


def add_layer(filename, name):
    layer = QgsVectorLayer(f"{GIS_DIR}/{filename}", name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Failed to load {filename}")
    QgsProject.instance().addMapLayer(layer)
    return layer


def add_raster_layer(filename, name):
    layer = QgsRasterLayer(f"{GIS_DIR}/{filename}", name, "gdal")
    if not layer.isValid():
        raise RuntimeError(f"Failed to load {filename}")

    stops = [(200, "#1a9850", "200 m"), (1500, "#fee08b", "1500 m"),
             (3500, "#d73027", "3500 m"), (7700, "#ffffff", "7700 m")]
    ramp_items = [QgsColorRampShader.ColorRampItem(value, QColor(color), label) for value, color, label in stops]
    shader_fn = QgsColorRampShader()
    shader_fn.setColorRampType(QgsColorRampShader.Interpolated)
    shader_fn.setColorRampItemList(ramp_items)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(shader_fn)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    layer.setRenderer(renderer)

    QgsProject.instance().addMapLayer(layer)
    return layer


def style_graduated(layer, field, stops):
    ranges = []
    for lo, hi, color in stops:
        symbol = QgsFillSymbol.createSimple({"color": color, "outline_color": "#404040", "outline_width": "0.3"})
        ranges.append(QgsRendererRange(lo, hi, symbol, f"{lo:.0f} - {hi:.0f}"))
    layer.setRenderer(QgsGraduatedSymbolRenderer(field, ranges))
    layer.triggerRepaint()


def style_categorized(layer, field, categories):
    cats = []
    for value, color in categories:
        symbol = QgsMarkerSymbol.createSimple({"color": color, "size": "2.5"})
        cats.append(QgsRendererCategory(value, symbol, str(value)))
    layer.setRenderer(QgsCategorizedSymbolRenderer(field, cats))
    layer.triggerRepaint()


def main():
    add_raster_layer("KP_DEM.tif", "Elevation (DEM)")

    add_layer("KP_Province_Boundary.shp", "KP Province Boundary")

    districts = add_layer("KP_Districts.shp", "KP Districts (Population)")
    style_graduated(
        districts, "pop_dens",
        [(0, 100, "#ffffcc"), (100, 300, "#fed976"), (300, 700, "#fd8d3c"), (700, 999999, "#e31a1c")],
    )

    gap = add_layer("KP_District_Gap_Scores.shp", "District Gap Scores")
    style_graduated(
        gap, "gap_score",
        [(0, 25, "#1a9850"), (25, 50, "#91cf60"), (50, 75, "#fee08b"), (75, 100, "#d73027")],
    )

    add_layer("KP_Roads.shp", "Roads")

    facilities = add_layer("KP_Healthcare_Facilities.shp", "Healthcare Facilities")
    style_categorized(
        facilities, "category",
        [("Hospital", "#e6194b"), ("Clinic", "#4363d8"), ("Pharmacy", "#3cb44b"), ("Facility", "#808080")],
    )

    add_layer("KP_Suggested_New_Sites.shp", "Suggested New Sites")

    print("Loaded and styled all 7 KP healthcare planning layers (6 vector + DEM raster).")


if __name__ == "__main__":
    main()
