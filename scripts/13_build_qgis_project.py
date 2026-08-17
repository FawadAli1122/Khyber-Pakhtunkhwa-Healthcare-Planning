"""Hand-author a QGIS project (.qgz) that loads all 6 gis/*.shp layers
plus the KP_DEM.tif elevation raster, with baked-in styling: singleband
pseudocolor elevation ramp on the DEM, graduated population choropleth on
KP_Districts, graduated red-yellow-green on KP_District_Gap_Scores.gap_score,
categorized symbology on KP_Healthcare_Facilities.category, plain line
style for KP_Roads, and a distinct marker for KP_Suggested_New_Sites.

This is authored directly against the documented QGIS project XML schema.
It was originally written without a QGIS install available to verify
against; a real QGIS 4.0 install was later used to open the generated
project and catch a real bug (see SRS_XML below) — see
scripts/load_and_style.py for a PyQGIS fallback that reconstructs the same
result programmatically if this file ever drifts from a future QGIS
version's schema again.
"""
import sys
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.landcover import LANDCOVER_CLASSES  # noqa: E402

GIS_DIR = Path(__file__).resolve().parent.parent / "gis"
PROJECT_NAME = "KP_Healthcare_Plan"

# Every <maplayer> needs its own <srs> block - QGIS does NOT fall back to
# reading a shapefile's .prj sidecar for a layer listed in a project file
# without one. Omitting this (an earlier bug in this script) left every
# layer's CRS blank/invalid when opened in real QGIS: extents still looked
# right individually, but the map renderer couldn't reproject vector
# layers against each other or the raster, so choropleth polygons, roads,
# and the province outline silently failed to draw - only point markers
# (rendered through a different code path) showed up, scattered relative
# to the DEM. Confirmed fixed by opening the regenerated project in a real
# QGIS 4.0 install. WKT/proj4 values below are EPSG:4326 (WGS 84), the CRS
# every layer in this pipeline already uses (scripts/lib/shp_writer.py).
SRS_XML = """<srs>
        <spatialrefsys nativeFormat="Wkt">
          <wkt>GEOGCRS["WGS 84",ENSEMBLE["World Geodetic System 1984 ensemble",MEMBER["World Geodetic System 1984 (Transit)"],MEMBER["World Geodetic System 1984 (G730)"],MEMBER["World Geodetic System 1984 (G873)"],MEMBER["World Geodetic System 1984 (G1150)"],MEMBER["World Geodetic System 1984 (G1674)"],MEMBER["World Geodetic System 1984 (G1762)"],MEMBER["World Geodetic System 1984 (G2139)"],MEMBER["World Geodetic System 1984 (G2296)"],ELLIPSOID["WGS 84",6378137,298.257223563,LENGTHUNIT["metre",1]],ENSEMBLEACCURACY[2.0]],PRIMEM["Greenwich",0,ANGLEUNIT["degree",0.0174532925199433]],CS[ellipsoidal,2],AXIS["geodetic latitude (Lat)",north,ORDER[1],ANGLEUNIT["degree",0.0174532925199433]],AXIS["geodetic longitude (Lon)",east,ORDER[2],ANGLEUNIT["degree",0.0174532925199433]],USAGE[SCOPE["Horizontal component of 3D system."],AREA["World."],BBOX[-90,-180,90,180]],ID["EPSG",4326]]</wkt>
          <proj4>+proj=longlat +datum=WGS84 +no_defs</proj4>
          <srsid>3452</srsid>
          <srid>4326</srid>
          <authid>EPSG:4326</authid>
          <description>WGS 84</description>
          <projectionacronym>longlat</projectionacronym>
          <ellipsoidacronym>EPSG:7030</ellipsoidacronym>
          <geographicflag>true</geographicflag>
        </spatialrefsys>
      </srs>"""

LAYERS = [
    {"id": "dem", "file": "KP_DEM.tif", "name": "Elevation (DEM)", "geom": "Raster"},
    {"id": "landcover", "file": "KP_LandCover.tif", "name": "Land Cover (ESA WorldCover 2021)", "geom": "Raster"},
    {"id": "province", "file": "KP_Province_Boundary.shp", "name": "KP Province Boundary", "geom": "Polygon"},
    {"id": "districts", "file": "KP_Districts.shp", "name": "KP Districts (Population)", "geom": "Polygon"},
    {"id": "gapscores", "file": "KP_District_Gap_Scores.shp", "name": "District Gap Scores", "geom": "Polygon"},
    {"id": "roads", "file": "KP_Roads.shp", "name": "Roads", "geom": "Line"},
    {"id": "facilities", "file": "KP_Healthcare_Facilities.shp", "name": "Healthcare Facilities", "geom": "Point"},
    {"id": "sites", "file": "KP_Suggested_New_Sites.shp", "name": "Suggested New Sites", "geom": "Point"},
]


def _layer_id(name):
    return f"{name}_{uuid.uuid4().hex[:8]}"


def _simple_polygon_layer_xml(layer, layer_id, color, outline="#404040"):
    return f"""
    <maplayer type="vector" geometry="Polygon">
      <id>{layer_id}</id>
      <datasource>./{layer['file']}</datasource>
      <layername>{layer['name']}</layername>
      {SRS_XML}
      <provider>ogr</provider>
      <renderer-v2 type="singleSymbol">
        <symbols>
          <symbol type="fill" name="0">
            <layer class="SimpleFill">
              <Option type="Map">
                <Option type="QString" name="color" value="{color}"/>
                <Option type="QString" name="outline_color" value="{outline}"/>
                <Option type="QString" name="outline_width" value="0.3"/>
              </Option>
            </layer>
          </symbol>
        </symbols>
      </renderer-v2>
    </maplayer>"""


def _graduated_polygon_layer_xml(layer, layer_id, field, ramp_stops):
    ranges = "\n".join(
        f'''        <range lower="{lo}" upper="{hi}" label="{lo:.0f} - {hi:.0f}" symbol="{i}"/>'''
        for i, (lo, hi, _color) in enumerate(ramp_stops)
    )
    symbols = "\n".join(
        f'''        <symbol type="fill" name="{i}">
          <layer class="SimpleFill">
            <Option type="Map">
              <Option type="QString" name="color" value="{color}"/>
              <Option type="QString" name="outline_color" value="#404040"/>
              <Option type="QString" name="outline_width" value="0.3"/>
            </Option>
          </layer>
        </symbol>'''
        for i, (_lo, _hi, color) in enumerate(ramp_stops)
    )
    return f"""
    <maplayer type="vector" geometry="Polygon">
      <id>{layer_id}</id>
      <datasource>./{layer['file']}</datasource>
      <layername>{layer['name']}</layername>
      {SRS_XML}
      <provider>ogr</provider>
      <renderer-v2 type="graduatedSymbol" attr="{field}">
        <ranges>
{ranges}
        </ranges>
        <symbols>
{symbols}
        </symbols>
      </renderer-v2>
    </maplayer>"""


def _categorized_point_layer_xml(layer, layer_id, field, categories):
    cats_xml = "\n".join(
        f'''        <category value="{value}" symbol="{i}" label="{value}"/>'''
        for i, (value, _color) in enumerate(categories)
    )
    symbols = "\n".join(
        f'''        <symbol type="marker" name="{i}">
          <layer class="SimpleMarker">
            <Option type="Map">
              <Option type="QString" name="color" value="{color}"/>
              <Option type="QString" name="size" value="2.5"/>
            </Option>
          </layer>
        </symbol>'''
        for i, (_value, color) in enumerate(categories)
    )
    return f"""
    <maplayer type="vector" geometry="Point">
      <id>{layer_id}</id>
      <datasource>./{layer['file']}</datasource>
      <layername>{layer['name']}</layername>
      {SRS_XML}
      <provider>ogr</provider>
      <renderer-v2 type="categorizedSymbol" attr="{field}">
        <categories>
{cats_xml}
        </categories>
        <symbols>
{symbols}
        </symbols>
      </renderer-v2>
    </maplayer>"""


def _line_layer_xml(layer, layer_id, color="#8a8a8a"):
    return f"""
    <maplayer type="vector" geometry="Line">
      <id>{layer_id}</id>
      <datasource>./{layer['file']}</datasource>
      <layername>{layer['name']}</layername>
      {SRS_XML}
      <provider>ogr</provider>
      <renderer-v2 type="singleSymbol">
        <symbols>
          <symbol type="line" name="0">
            <layer class="SimpleLine">
              <Option type="Map">
                <Option type="QString" name="line_color" value="{color}"/>
                <Option type="QString" name="line_width" value="0.4"/>
              </Option>
            </layer>
          </symbol>
        </symbols>
      </renderer-v2>
    </maplayer>"""


def _point_marker_layer_xml(layer, layer_id, color="#e6194b"):
    return f"""
    <maplayer type="vector" geometry="Point">
      <id>{layer_id}</id>
      <datasource>./{layer['file']}</datasource>
      <layername>{layer['name']}</layername>
      {SRS_XML}
      <provider>ogr</provider>
      <renderer-v2 type="singleSymbol">
        <symbols>
          <symbol type="marker" name="0">
            <layer class="SimpleMarker">
              <Option type="Map">
                <Option type="QString" name="color" value="{color}"/>
                <Option type="QString" name="size" value="4"/>
                <Option type="QString" name="name" value="star"/>
              </Option>
            </layer>
          </symbol>
        </symbols>
      </renderer-v2>
    </maplayer>"""


def _raster_layer_xml(layer_id, filename, layer_name):
    return f"""
    <maplayer type="raster">
      <id>{layer_id}</id>
      <datasource>./{filename}</datasource>
      <layername>{layer_name}</layername>
      {SRS_XML}
      <provider>gdal</provider>
      <pipe>
        <rasterrenderer type="singlebandpseudocolor" band="1">
          <rastershader>
            <colorrampshader colorRampType="INTERPOLATED">
              <item value="200" color="#1a9850" label="200 m"/>
              <item value="1500" color="#fee08b" label="1500 m"/>
              <item value="3500" color="#d73027" label="3500 m"/>
              <item value="7700" color="#ffffff" label="7700 m"/>
            </colorrampshader>
          </rastershader>
        </rasterrenderer>
      </pipe>
    </maplayer>"""


def _paletted_raster_layer_xml(layer_id, filename, layer_name, class_colors):
    """class_colors: list of (value, color, label) tuples for a
    singlebandpseudocolor renderer with an EXACT (not interpolated) color
    ramp - correct for discrete class-code rasters like land cover,
    unlike the DEM's continuous elevation gradient above."""
    items = "\n".join(
        f'              <item value="{value}" color="{color}" label="{label}"/>'
        for value, color, label in class_colors
    )
    return f"""
    <maplayer type="raster">
      <id>{layer_id}</id>
      <datasource>./{filename}</datasource>
      <layername>{layer_name}</layername>
      {SRS_XML}
      <provider>gdal</provider>
      <pipe>
        <rasterrenderer type="singlebandpseudocolor" band="1">
          <rastershader>
            <colorrampshader colorRampType="EXACT">
{items}
            </colorrampshader>
          </rastershader>
        </rasterrenderer>
      </pipe>
    </maplayer>"""


def build_qgs_xml():
    ids = {l["id"]: _layer_id(l["id"]) for l in LAYERS}
    by_id = {l["id"]: l for l in LAYERS}

    layers_xml = [
        _raster_layer_xml(ids["dem"], by_id["dem"]["file"], by_id["dem"]["name"]),
        _paletted_raster_layer_xml(
            ids["landcover"], by_id["landcover"]["file"], by_id["landcover"]["name"], LANDCOVER_CLASSES,
        ),
        _simple_polygon_layer_xml(by_id["province"], ids["province"], color="255,255,255,0"),
        _graduated_polygon_layer_xml(
            by_id["districts"], ids["districts"], "pop_dens",
            [(0, 100, "#ffffcc"), (100, 300, "#fed976"), (300, 700, "#fd8d3c"), (700, 999999, "#e31a1c")],
        ),
        _graduated_polygon_layer_xml(
            by_id["gapscores"], ids["gapscores"], "gap_score",
            [(0, 25, "#1a9850"), (25, 50, "#91cf60"), (50, 75, "#fee08b"), (75, 100, "#d73027")],
        ),
        _line_layer_xml(by_id["roads"], ids["roads"]),
        _categorized_point_layer_xml(
            by_id["facilities"], ids["facilities"], "category",
            [("Hospital", "#e6194b"), ("Clinic", "#4363d8"), ("Pharmacy", "#3cb44b"), ("Facility", "#808080")],
        ),
        _point_marker_layer_xml(by_id["sites"], ids["sites"]),
    ]

    layer_tree_entries = "\n".join(
        f'      <layer-tree-layer id="{ids[l["id"]]}" name="{l["name"]}"/>' for l in reversed(LAYERS)
    )
    layer_order_entries = "\n".join(f'      <layer id="{ids[l["id"]]}"/>' for l in LAYERS)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<qgis projectname="{PROJECT_NAME}" version="3.34.0">
  <homePath path=""/>
  <title>KP Healthcare System Planning</title>
  <projectCrs>
    <spatialrefsys>
      <authid>EPSG:4326</authid>
    </spatialrefsys>
  </projectCrs>
  <layer-tree-group>
{layer_tree_entries}
  </layer-tree-group>
  <projectlayers>
{"".join(layers_xml)}
  </projectlayers>
  <layerorder>
{layer_order_entries}
  </layerorder>
</qgis>
"""


def main():
    qgs_content = build_qgs_xml()
    qgz_path = GIS_DIR / f"{PROJECT_NAME}.qgz"
    with zipfile.ZipFile(qgz_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{PROJECT_NAME}.qgs", qgs_content)
    print(f"Wrote {qgz_path}")


if __name__ == "__main__":
    main()
