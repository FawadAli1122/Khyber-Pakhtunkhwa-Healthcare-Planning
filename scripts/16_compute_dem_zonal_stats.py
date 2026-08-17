"""Per-district elevation and slope statistics from gis/KP_DEM.tif, used to
replace the hand-classified mountainous/plains terrain flag with a
continuous, data-driven terrain-difficulty score (scripts/08 and
scripts/09). Slope is computed with a simple central-difference gradient
(numpy) rather than a dedicated terrain library, since this environment
doesn't have one installed and a first-derivative gradient is sufficient
for a per-district mean-slope summary statistic (not for pixel-level
terrain analysis)."""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask
from shapely.geometry import shape, mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"
DEM_PATH = GIS_DIR / "KP_DEM.tif"

# Degrees-to-meters conversion at KP's approximate latitude, consistent with
# scripts/lib/geo_utils.py's equirectangular projection.
METERS_PER_DEGREE_LAT = 111320.0


def compute_slope_degrees(elevation, pixel_size_m):
    dy, dx = np.gradient(elevation, pixel_size_m)
    slope_rad = np.arctan(np.sqrt(dx ** 2 + dy ** 2))
    return np.degrees(slope_rad)


def main():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())

    with rasterio.open(DEM_PATH) as src:
        pixel_size_deg = abs(src.transform.a)
        pixel_size_m = pixel_size_deg * METERS_PER_DEGREE_LAT
        nodata = src.nodata

        rows = []
        for d in boundaries["districts"]:
            geom = shape(d["geometry"])
            try:
                clipped, _ = mask(src, [mapping(geom)], crop=True, nodata=nodata)
            except ValueError:
                rows.append({"district": d["district"], "mean_elev_m": "", "min_elev_m": "", "max_elev_m": "", "mean_slope_deg": ""})
                continue
            band = clipped[0]
            valid = band[band != nodata] if nodata is not None else band
            if valid.size == 0:
                rows.append({"district": d["district"], "mean_elev_m": "", "min_elev_m": "", "max_elev_m": "", "mean_slope_deg": ""})
                continue
            band_f = band.astype(np.float64)
            band_nan = np.where(band == nodata, np.nan, band_f) if nodata is not None else band_f
            slope = compute_slope_degrees(band_nan, pixel_size_m)
            valid_slope = slope[~np.isnan(slope)]
            rows.append(
                {
                    "district": d["district"],
                    "mean_elev_m": round(float(valid.mean()), 1),
                    "min_elev_m": round(float(valid.min()), 1),
                    "max_elev_m": round(float(valid.max()), 1),
                    "mean_slope_deg": round(float(valid_slope.mean()), 2) if valid_slope.size else "",
                }
            )

    out_path = PROCESSED / "district_terrain.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "mean_elev_m", "min_elev_m", "max_elev_m", "mean_slope_deg"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote district_terrain.csv for {len(rows)} districts")


if __name__ == "__main__":
    main()
