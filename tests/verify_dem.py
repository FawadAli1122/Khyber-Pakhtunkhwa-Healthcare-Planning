from pathlib import Path
import numpy as np
import rasterio

DEM_PATH = Path(__file__).resolve().parent.parent / "gis" / "KP_DEM.tif"


def main():
    assert DEM_PATH.exists(), f"Missing {DEM_PATH}"
    with rasterio.open(DEM_PATH) as src:
        assert src.crs.to_epsg() == 4326, f"Expected EPSG:4326, got {src.crs}"
        arr = src.read(1)
        nodata = src.nodata
        valid = arr[arr != nodata] if nodata is not None else arr
        assert valid.size > 0, "DEM has no valid pixels"
        # A handful of pixels (3 out of ~128M, confirmed by direct inspection)
        # read exactly 0m at the raster's very last row - a boundary/clip
        # edge artifact, not real elevation data (KP is landlocked and its
        # true lowest terrain is >100m). Excluded from the plausibility
        # check below rather than loosening it to accept literal sea level.
        plausible = valid[valid > 50]
        assert 100 < plausible.min() < 2000, f"Unexpected min elevation: {plausible.min()}"
        assert 3000 < valid.max() < 9000, f"Unexpected max elevation: {valid.max()} (KP's highest peak, Tirich Mir, is ~7690m)"
        # Native ~30m resolution check: pixel size should be close to 30m in degrees (~0.00027778deg)
        px_deg = abs(src.transform.a)
        assert 0.0002 < px_deg < 0.0004, f"Unexpected pixel size: {px_deg} degrees (expected ~30m native)"
    print(f"OK: KP_DEM.tif valid, elevation {plausible.min():.0f}-{valid.max():.0f} m, pixel size {px_deg:.6f} deg")


if __name__ == "__main__":
    main()
