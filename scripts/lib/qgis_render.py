"""Renders a QGIS project (.qgz) to a PNG for the Telegram bot's /map
command - the map canvas via PyQGIS's headless renderer, plus a legend
panel drawn alongside it with QPainter. Must run through QGIS's own
bundled Python interpreter (Windows:
"C:\\Program Files\\QGIS 4.0.0\\bin\\python-qgis.bat" <this file> <qgz_path> <output_png_path>),
never the project's regular pure-Python environment - it imports the
qgis package, exactly like scripts/13b_build_qgis_project_pyqgis.py,
which is why this lives in scripts/lib/ as a standalone script (invoked
via subprocess) rather than an importable function despite the
directory name. See docs/superpowers/specs/
2026-08-16-telegram-connector-design.md section 10.

The legend entries below are hardcoded to match
scripts/13_build_qgis_project.py's actual layer styling exactly (the
script that builds gis/KP_Healthcare_Plan.qgz in the real pipeline) -
not auto-generated from the project via QgsLayoutItemLegend. That was
tried first and abandoned: QgsLayoutItemLegend's auto-populated model
either truncated every label at the panel edge or, once font/width
adjustments were attempted via QGIS 4.0's (deprecated) legend-styling
API, rendered a completely blank panel - both found live, by sending a
rendered map through the real bot and looking at it, not by any unit
test. A hardcoded QPainter legend is fully within this script's own
control and has no such failure mode, at the cost of needing a manual
update here if a future change restyles those layers.
"""
import sys

from qgis.core import QgsApplication, QgsMapRendererParallelJob, QgsMapSettings, QgsProject, QgsRectangle
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter

MAP_WIDTH = 1600
MAP_HEIGHT = 1200
LEGEND_WIDTH = 420

# Mirrors scripts/13_build_qgis_project.py's actual layer styling exactly
# (see that file's build_qgs_xml() call site) - not read from the .qgz
# file itself.
GAP_SCORE_STOPS = [
    ("0 - 25 (low gap)", "#1a9850"),
    ("25 - 50", "#91cf60"),
    ("50 - 75", "#fee08b"),
    ("75 - 100 (high gap)", "#d73027"),
]
FACILITY_CATEGORIES = [
    ("Hospital", "#e6194b"),
    ("Clinic", "#4363d8"),
    ("Pharmacy", "#3cb44b"),
    ("Facility (other)", "#808080"),
]
ROADS_COLOR = "#8a8a8a"
SITES_COLOR = "#e6194b"


def _render_map_image(layers, extent, crs, width, height):
    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setBackgroundColor(QColor(255, 255, 255))
    settings.setOutputSize(QSize(width, height))
    settings.setExtent(extent)
    settings.setDestinationCrs(crs)
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    return job.renderedImage()


def _draw_legend(painter, x, y, height):
    title_font = QFont("Arial", 18, QFont.Weight.Bold)
    section_font = QFont("Arial", 13, QFont.Weight.Bold)
    label_font = QFont("Arial", 11)
    swatch = 18
    row_h = 28
    cur_y = [y]  # boxed so the nested draw_section can update it

    painter.setFont(title_font)
    painter.setPen(QColor("#000000"))
    painter.drawText(x, cur_y[0], "Legend")
    cur_y[0] += 40

    def draw_section(title, entries, shape):
        painter.setFont(section_font)
        painter.setPen(QColor("#000000"))
        painter.drawText(x, cur_y[0], title)
        cur_y[0] += row_h
        painter.setFont(label_font)
        for label, color in entries:
            top = cur_y[0] - swatch + 4
            if shape == "box":
                painter.setBrush(QColor(color))
                painter.setPen(QColor("#404040"))
                painter.drawRect(x, top, swatch, swatch)
            elif shape == "circle":
                painter.setBrush(QColor(color))
                painter.setPen(QColor("#404040"))
                painter.drawEllipse(x, top, swatch, swatch)
            elif shape == "line":
                painter.setPen(QColor(color))
                mid = top + swatch // 2
                painter.drawLine(x, mid, x + swatch, mid)
            painter.setPen(QColor("#000000"))
            painter.drawText(x + swatch + 10, cur_y[0], label)
            cur_y[0] += row_h
        cur_y[0] += 12

    draw_section("Suggested New Sites", [("New site (priority)", SITES_COLOR)], "circle")
    draw_section("Healthcare Facilities", FACILITY_CATEGORIES, "circle")
    draw_section("Roads", [("Road network", ROADS_COLOR)], "line")
    draw_section("District Gap Score", GAP_SCORE_STOPS, "box")


def render_to_png(qgz_path, output_path, map_width=MAP_WIDTH, map_height=MAP_HEIGHT, legend_width=LEGEND_WIDTH):
    project = QgsProject.instance()
    if not project.read(qgz_path):
        raise RuntimeError(f"Failed to load project: {qgz_path}")

    # project.mapLayers().values() has no guaranteed order - it's a plain
    # dict keyed by layer id, not the project's actual visual stacking.
    # layerTreeRoot().layerOrder() returns layers top-to-bottom exactly as
    # arranged in the Layers panel, which is also the order
    # QgsMapSettings.setLayers() expects (first = drawn on top) - without
    # this, smaller layers (facilities, roads, gap-score choropleth) can
    # end up rendered underneath the full-extent DEM raster and never
    # show at all. Caught by rendering a real project and looking at it,
    # not by any unit test - see docs/superpowers/plans/
    # 2026-08-16-telegram-connector.md Task 5.
    layers = project.layerTreeRoot().layerOrder()
    if not layers:
        raise RuntimeError("Project has no layers to render")

    full_extent = QgsRectangle()
    full_extent.setMinimal()
    for layer in layers:
        full_extent.combineExtentWith(layer.extent())

    map_image = _render_map_image(layers, full_extent, project.crs(), map_width, map_height)

    total_width = map_width + legend_width
    canvas = QImage(total_width, map_height, QImage.Format.Format_ARGB32)
    canvas.fill(QColor(255, 255, 255))

    painter = QPainter(canvas)
    painter.drawImage(0, 0, map_image)
    _draw_legend(painter, map_width + 20, 40, map_height)
    painter.end()

    if not canvas.save(output_path, "PNG"):
        raise RuntimeError(f"Failed to save rendered image to {output_path}")


def main():
    if len(sys.argv) != 3:
        print("Usage: python-qgis.bat qgis_render.py <qgz_path> <output_png_path>", file=sys.stderr)
        sys.exit(2)
    qgz_path, output_path = sys.argv[1], sys.argv[2]

    QgsApplication.setPrefixPath(r"C:\Program Files\QGIS 4.0.0\apps\qgis", True)
    qgs = QgsApplication([], False)
    qgs.initQgis()
    try:
        render_to_png(qgz_path, output_path)
        print(f"Wrote {output_path}")
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
