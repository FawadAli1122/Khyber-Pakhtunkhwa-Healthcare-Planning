import importlib

import numpy as np
import pytest
from rasterio.transform import Affine

travel_time_mod = importlib.import_module("scripts.16b_compute_travel_time_accessibility")


def test_mean_coordinate_returns_none_when_class_absent():
    array = np.array([[10, 10], [20, 20]])
    transform = Affine(0.01, 0, 71.0, 0, -0.01, 34.0)
    assert travel_time_mod._mean_coordinate(array, transform, target_class=50) is None


def test_mean_coordinate_averages_matching_pixel_coordinates():
    # A 2x2 raster where only the top-left pixel (row=0, col=0) is
    # Built-up (50) - its center should be the single returned coordinate.
    array = np.array([[50, 10], [20, 30]])
    transform = Affine(0.01, 0, 71.0, 0, -0.01, 34.0)
    lon, lat = travel_time_mod._mean_coordinate(array, transform, target_class=50)
    assert lon == pytest.approx(71.005)
    assert lat == pytest.approx(33.995)


def test_mean_coordinate_averages_multiple_matching_pixels():
    array = np.array([[50, 50], [20, 30]])
    transform = Affine(0.01, 0, 71.0, 0, -0.01, 34.0)
    lon, lat = travel_time_mod._mean_coordinate(array, transform, target_class=50)
    # Mean of the two top-row pixel centers: (71.005, 33.995) and (71.015, 33.995)
    assert lon == pytest.approx(71.01)
    assert lat == pytest.approx(33.995)


def test_build_districts_with_centroids_falls_back_without_landcover_path():
    # Backward compatibility: the existing call site/test with no
    # landcover_path keeps behaving exactly as before.
    boundaries = {
        "districts": [
            {
                "district": "TestDistrict",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[71.0, 34.0], [71.2, 34.0], [71.2, 34.2], [71.0, 34.2], [71.0, 34.0]]],
                },
            }
        ]
    }
    result = travel_time_mod.build_districts_with_centroids(boundaries)
    assert result[0]["centroid_lon"] == pytest.approx(71.1)
    assert result[0]["centroid_lat"] == pytest.approx(34.1)
    assert result[0]["centroid_shift_km"] == 0.0
    assert result[0]["point_source"] == "geometric_centroid"


def test_build_districts_with_centroids_falls_back_when_landcover_file_missing(tmp_path):
    boundaries = {
        "districts": [
            {
                "district": "TestDistrict",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[71.0, 34.0], [71.2, 34.0], [71.2, 34.2], [71.0, 34.2], [71.0, 34.0]]],
                },
            }
        ]
    }
    result = travel_time_mod.build_districts_with_centroids(boundaries, landcover_path=tmp_path / "does_not_exist.tif")
    assert result[0]["point_source"] == "geometric_centroid"


def test_build_districts_with_centroids_computes_geometric_centroid():
    boundaries = {
        "districts": [
            {
                "district": "TestDistrict",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[71.0, 34.0], [71.2, 34.0], [71.2, 34.2], [71.0, 34.2], [71.0, 34.0]]],
                },
            }
        ]
    }
    result = travel_time_mod.build_districts_with_centroids(boundaries)
    assert len(result) == 1
    assert result[0]["district"] == "TestDistrict"
    assert result[0]["centroid_lon"] == pytest.approx(71.1)
    assert result[0]["centroid_lat"] == pytest.approx(34.1)


def test_load_facilities_drops_duplicates_and_keeps_lon_lat():
    rows = [
        {"lon": "71.5", "lat": "34.5", "is_duplicate_of": ""},
        {"lon": "71.6", "lat": "34.6", "is_duplicate_of": "Some Other Hospital"},
    ]
    facilities = travel_time_mod.load_facilities(rows)
    assert facilities == [{"lon": 71.5, "lat": 34.5}]


def test_build_terrain_by_district_returns_name_to_difficulty_map():
    terrain_rows = [
        {"district": "A", "mean_elev_m": 200, "mean_slope_deg": 1},
        {"district": "B", "mean_elev_m": 4000, "mean_slope_deg": 25},
    ]
    result = travel_time_mod.build_terrain_by_district(terrain_rows)
    assert set(result.keys()) == {"A", "B"}
    assert result["A"] < result["B"]
