"""Per-district and province-wide land-cover composition from
gis/KP_LandCover.tif (ESA WorldCover 2021 v200, fetched by
scripts/23_fetch_landcover.py), feeding a new "Land Use & Land Cover"
section in scripts/14_build_html_report.py. Mirrors
scripts/16_compute_dem_zonal_stats.py's per-district masking pattern
almost exactly, but counts categorical class pixels instead of averaging a
continuous surface - land cover classes must never be interpolated or
averaged across pixels.

Deliberately NOT wired into run_downstream.py (matches
16_compute_dem_zonal_stats.py's own precedent): land cover doesn't change
when an admin applies a population/health override, so there's no need to
recompute it on every downstream rebuild - only on a full run_all.py."""
import csv
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask
from shapely.geometry import shape, mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.geo_utils import EARTH_RADIUS_KM, KP_LAT0
from scripts.lib.landcover import LANDCOVER_CLASSES

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"
LANDCOVER_PATH = GIS_DIR / "KP_LandCover.tif"


def class_slug(label):
    """'Bare / sparse vegetation' -> 'bare_sparse_vegetation'."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return slug


def pixel_area_km2(transform):
    """Pixels are in WGS84 degrees (not meters), so area is computed via the
    same equirectangular approximation scripts/lib/geo_utils.py uses for
    every other area calculation in this project - adequate for a single
    mid-latitude province, not true equal-area accuracy."""
    pixel_width_deg = abs(transform.a)
    pixel_height_deg = abs(transform.e)
    km_per_deg_lon = math.radians(1) * EARTH_RADIUS_KM * math.cos(math.radians(KP_LAT0))
    km_per_deg_lat = math.radians(1) * EARTH_RADIUS_KM
    return (pixel_width_deg * km_per_deg_lon) * (pixel_height_deg * km_per_deg_lat)


def class_pixel_counts(band, nodata):
    """band: 2D numpy array of land-cover class codes. Returns (counts,
    valid_count): counts is {class_value: pixel_count} for every non-nodata
    value actually present, valid_count is the total number of non-nodata
    pixels. Pure function, independently testable without real raster I/O."""
    valid = band[band != nodata] if nodata is not None else band.ravel()
    valid_count = int(valid.size)
    if valid_count == 0:
        return {}, 0
    values, counts_arr = np.unique(valid, return_counts=True)
    counts = {int(v): int(c) for v, c in zip(values, counts_arr)}
    return counts, valid_count


def composition_row(name, counts, valid_count, area_per_pixel_km2, classes=LANDCOVER_CLASSES):
    """name: district name (or a province-wide label). counts/valid_count:
    from class_pixel_counts(). Returns a dict with 'district', 'dominant_class',
    'area_km2', and one '<slug>_pct' column per class in `classes` (0.0 for
    any class absent from this area) - the wide-format row shape written to
    district_landcover.csv."""
    row = {"district": name}
    if valid_count == 0:
        row["dominant_class"] = ""
        row["area_km2"] = 0.0
        for _, _, label in classes:
            row[f"{class_slug(label)}_pct"] = 0.0
        return row
    row["area_km2"] = round(valid_count * area_per_pixel_km2, 2)
    dominant_value = max(counts, key=counts.get)
    row["dominant_class"] = next((label for value, _, label in classes if value == dominant_value), "")
    for value, _, label in classes:
        row[f"{class_slug(label)}_pct"] = round((counts.get(value, 0) / valid_count) * 100, 2)
    return row


def province_composition_rows(total_counts, total_valid, area_per_pixel_km2, classes=LANDCOVER_CLASSES):
    """total_counts/total_valid: summed across every district's
    class_pixel_counts() result. Returns one row per class (in `classes`
    order) with class_value/label/area_km2/pct_area - the long-format shape
    written to landcover_composition.csv."""
    rows = []
    for value, _, label in classes:
        count = total_counts.get(value, 0)
        rows.append(
            {
                "class_value": value,
                "label": label,
                "area_km2": round(count * area_per_pixel_km2, 2),
                "pct_area": round((count / total_valid) * 100, 2) if total_valid else 0.0,
            }
        )
    return rows


def main():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())

    with rasterio.open(LANDCOVER_PATH) as src:
        area_per_pixel_km2 = pixel_area_km2(src.transform)
        nodata = src.nodata

        district_rows = []
        total_counts = {}
        total_valid = 0
        for d in boundaries["districts"]:
            geom = shape(d["geometry"])
            try:
                clipped, _ = mask(src, [mapping(geom)], crop=True, nodata=nodata)
            except ValueError:
                counts, valid_count = {}, 0
            else:
                counts, valid_count = class_pixel_counts(clipped[0], nodata)
            district_rows.append(composition_row(d["district"], counts, valid_count, area_per_pixel_km2))
            for value, count in counts.items():
                total_counts[value] = total_counts.get(value, 0) + count
            total_valid += valid_count

    class_fieldnames = [f"{class_slug(label)}_pct" for _, _, label in LANDCOVER_CLASSES]
    district_out = PROCESSED / "district_landcover.csv"
    with open(district_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "dominant_class", "area_km2"] + class_fieldnames)
        writer.writeheader()
        writer.writerows(district_rows)
    print(f"Wrote district_landcover.csv for {len(district_rows)} districts")

    composition_out = PROCESSED / "landcover_composition.csv"
    with open(composition_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["class_value", "label", "area_km2", "pct_area"])
        writer.writeheader()
        writer.writerows(province_composition_rows(total_counts, total_valid, area_per_pixel_km2))
    print("Wrote landcover_composition.csv")


if __name__ == "__main__":
    main()
