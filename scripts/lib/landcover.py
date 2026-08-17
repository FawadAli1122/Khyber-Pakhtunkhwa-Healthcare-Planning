"""Shared ESA WorldCover 2021 v200 class definitions (value, hex color,
label). Lives here rather than in scripts/13_build_qgis_project.py (its
original location) so it can be imported by other numbered pipeline
scripts too - a script named with a leading digit (e.g. 13_build_...,
24_compute_...) isn't a valid Python module identifier for `from X import
Y`, so shared constants between numbered scripts belong in scripts/lib/,
matching scripts/lib/terrain.py and scripts/lib/geo_utils.py's existing
pattern. Colors are ESA's own official WorldCover palette, used identically
for the QGIS raster layer (scripts/13_build_qgis_project.py) and the report
figure (scripts/14_build_html_report.py) so the same class always reads as
the same color everywhere in this project."""

LANDCOVER_CLASSES = [
    (10, "#006400", "Tree cover"),
    (20, "#ffbb22", "Shrubland"),
    (30, "#ffff4c", "Grassland"),
    (40, "#f096ff", "Cropland"),
    (50, "#fa0000", "Built-up"),
    (60, "#b4b4b4", "Bare / sparse vegetation"),
    (70, "#f0f0f0", "Snow and ice"),
    (80, "#0064c8", "Permanent water bodies"),
    (90, "#0096a0", "Herbaceous wetland"),
    (95, "#00cf75", "Mangroves"),
    (100, "#fae6a0", "Moss and lichen"),
]
