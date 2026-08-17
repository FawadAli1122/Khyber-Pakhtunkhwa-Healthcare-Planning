import importlib
import numpy as np

zonal_mod = importlib.import_module("scripts.16_compute_dem_zonal_stats")


def test_flat_surface_has_zero_slope():
    flat = np.full((10, 10), 500.0)
    slope = zonal_mod.compute_slope_degrees(flat, pixel_size_m=30.0)
    assert np.allclose(slope, 0.0, atol=1e-6)


def test_steep_ramp_has_positive_slope():
    # Elevation increases by 30m per 30m pixel along axis 1 -> ~45 degree slope
    ramp = np.tile(np.arange(10) * 30.0, (10, 1))
    slope = zonal_mod.compute_slope_degrees(ramp, pixel_size_m=30.0)
    interior = slope[2:-2, 2:-2]
    assert np.all(interior > 30), f"Expected steep slope, got {interior}"


def test_slope_shape_matches_input():
    arr = np.random.rand(20, 15) * 1000
    slope = zonal_mod.compute_slope_degrees(arr, pixel_size_m=30.0)
    assert slope.shape == arr.shape
