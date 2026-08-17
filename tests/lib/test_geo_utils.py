import math
import pytest
from shapely.geometry import Polygon
from scripts.lib import geo_utils


def test_haversine_known_distance():
    # Peshawar (34.0151N, 71.5249E) to Islamabad (33.6844N, 73.0479E) ~ 141 km
    d = geo_utils.haversine_km(71.5249, 34.0151, 73.0479, 33.6844)
    assert 135 < d < 148


def test_haversine_zero_for_same_point():
    assert geo_utils.haversine_km(71.5, 34.0, 71.5, 34.0) == pytest.approx(0.0, abs=1e-6)


def test_polygon_area_km2_unit_square_degree_near_equator():
    # A ~1deg x 1deg box near KP's projection center latitude; check against
    # the known equirectangular formula rather than a literal 111x111 to
    # keep the test self-consistent with the implementation's projection.
    box = Polygon([(71.0, 34.0), (72.0, 34.0), (72.0, 35.0), (71.0, 35.0)])
    area = geo_utils.polygon_area_km2(box)
    R = 6371.0
    expected_width_km = math.radians(1.0) * R * math.cos(math.radians(geo_utils.KP_LAT0))
    expected_height_km = math.radians(1.0) * R
    expected = expected_width_km * expected_height_km
    assert area == pytest.approx(expected, rel=0.02)


def test_find_containing_district_inside():
    d1 = {"district": "A", "geometry": Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])}
    d2 = {"district": "B", "geometry": Polygon([(3, 0), (5, 0), (5, 2), (3, 2)])}
    assert geo_utils.find_containing_district(1, 1, [d1, d2]) == "A"


def test_find_containing_district_fallback_nearest():
    d1 = {"district": "A", "geometry": Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])}
    d2 = {"district": "B", "geometry": Polygon([(10, 10), (12, 10), (12, 12), (10, 12)])}
    # point at (2.01, 1) is just outside A, well outside B -> nearest is A
    assert geo_utils.find_containing_district(2.01, 1, [d1, d2]) == "A"
