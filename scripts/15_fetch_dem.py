"""Mosaic Copernicus GLO-30 DEM tiles covering Khyber Pakhtunkhwa and clip
to the province polygon at native ~30m resolution. Tiles are read directly
over HTTPS via GDAL's /vsicurl/ virtual filesystem (rasterio bundles GDAL on
Windows) — no full-tile downloads, only the byte ranges needed for the clip
window are fetched. Source: Copernicus DEM GLO-30 (ESA/Sinergise), public,
no authentication, via the AWS Open Data Registry."""
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
    "/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM.tif"
)

# KP spans ~31.0-36.9N, 69.2-74.1E -> integer tile grid.
LAT_RANGE = range(31, 37)   # 31..36 inclusive
LON_RANGE = range(69, 75)   # 69..74 inclusive


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
        raise RuntimeError("No Copernicus DEM tiles could be opened for KP's bounding box.")
    print(f"Opened {len(datasets)} DEM tiles")
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
    mosaic_path = GIS_DIR / "_dem_mosaic_tmp.tif"
    with rasterio.open(mosaic_path, "w", **mosaic_meta) as dst:
        dst.write(mosaic_array)

    with rasterio.open(mosaic_path) as src:
        nodata = src.nodata if src.nodata is not None else -9999.0
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

    dem_path = GIS_DIR / "KP_DEM.tif"
    with rasterio.open(dem_path, "w", **clipped_meta) as dst:
        dst.write(clipped_array)

    mosaic_path.unlink()

    valid = clipped_array[clipped_array != nodata]
    print(f"Wrote {dem_path}: {clipped_array.shape[1]}x{clipped_array.shape[2]} px, "
          f"elevation range {valid.min():.0f}-{valid.max():.0f} m")


if __name__ == "__main__":
    main()
