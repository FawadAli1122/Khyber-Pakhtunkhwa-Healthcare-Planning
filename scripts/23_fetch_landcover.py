"""Mosaic ESA WorldCover 2021 (v200) land-cover tiles covering Khyber
Pakhtunkhwa and clip to the province polygon at native 10m resolution.
Tiles are read directly over HTTPS via GDAL's /vsicurl/ virtual
filesystem (rasterio bundles GDAL on Windows) - no full-tile downloads,
only the byte ranges needed for the clip window are fetched. Source: ESA
WorldCover 2021 v200 (ESA/VITO), public, no authentication, via the AWS
Open Data Registry. Mirrors scripts/15_fetch_dem.py's exact pattern -
see that script's own docstring for why this technique is used. See
docs/superpowers/specs/2026-08-16-landcover-integration-design.md.

Pixel values are discrete land-cover class codes (10=Tree cover,
20=Shrubland, 30=Grassland, 40=Cropland, 50=Built-up, 60=Bare/sparse
vegetation, 70=Snow and ice, 80=Permanent water bodies, 90=Herbaceous
wetland, 95=Mangroves, 100=Moss and lichen), not a continuous field like
the DEM - nodata is 0 (ESA's own convention; no valid class code is 0).
"""
import json
import sys
from pathlib import Path

import rasterio
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.errors import RasterioIOError
from shapely.geometry import shape, mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"

TILE_URL_TEMPLATE = (
    "/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
    "v200/2021/map/ESA_WorldCover_10m_2021_v200_N{lat:02d}E{lon:03d}_Map.tif"
)

# KP spans ~31.0-36.9N, 69.2-74.1E -> ESA WorldCover's 3-degree tile grid,
# named by each tile's south-west corner. Verified reachable (HTTP 200)
# for all 6 combinations below at spec-writing time.
LAT_RANGE = (30, 33, 36)
LON_RANGE = (69, 72)


def open_available_tiles():
    datasets = []
    for lat in LAT_RANGE:
        for lon in LON_RANGE:
            url = TILE_URL_TEMPLATE.format(lat=lat, lon=lon)
            try:
                ds = rasterio.open(url)
                datasets.append(ds)
            except RasterioIOError:
                continue  # tile doesn't exist at this coordinate (shouldn't happen inside KP's bbox, but don't hard-fail)
    if not datasets:
        raise RuntimeError("No ESA WorldCover tiles could be opened for KP's bounding box.")
    print(f"Opened {len(datasets)} land-cover tiles")
    return datasets


def main():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    province_geom = shape(boundaries["province_geometry"])

    datasets = open_available_tiles()
    mosaic_array, mosaic_transform = merge(datasets, bounds=province_geom.bounds)
    mosaic_meta = datasets[0].meta.copy()
    mosaic_meta.update(
        {
            "height": mosaic_array.shape[1],
            "width": mosaic_array.shape[2],
            "transform": mosaic_transform,
            "compress": "deflate",
        }
    )
    for ds in datasets:
        ds.close()

    GIS_DIR.mkdir(parents=True, exist_ok=True)
    mosaic_path = GIS_DIR / "_landcover_mosaic_tmp.tif"
    with rasterio.open(mosaic_path, "w", **mosaic_meta) as dst:
        dst.write(mosaic_array)

    with rasterio.open(mosaic_path) as src:
        nodata = 0
        clipped_array, clipped_transform = mask(src, [mapping(province_geom)], crop=True, nodata=nodata)
        clipped_meta = src.meta.copy()
        clipped_meta.update(
            {
                "height": clipped_array.shape[1],
                "width": clipped_array.shape[2],
                "transform": clipped_transform,
                "compress": "deflate",
                "nodata": nodata,
            }
        )

    landcover_path = GIS_DIR / "KP_LandCover.tif"
    with rasterio.open(landcover_path, "w", **clipped_meta) as dst:
        dst.write(clipped_array)

    mosaic_path.unlink()

    valid = clipped_array[clipped_array != nodata]
    classes = sorted(set(valid.tolist())) if valid.size else []
    print(f"Wrote {landcover_path}: {clipped_array.shape[1]}x{clipped_array.shape[2]} px, "
          f"classes present: {classes}")


if __name__ == "__main__":
    main()
