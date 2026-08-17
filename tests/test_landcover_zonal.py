import importlib

import numpy as np

zonal_mod = importlib.import_module("scripts.24_compute_landcover_zonal_stats")

TEST_CLASSES = [
    (10, "#006400", "Tree cover"),
    (50, "#fa0000", "Built-up"),
    (80, "#0064c8", "Permanent water bodies"),
]


def test_class_slug_handles_slashes_and_spaces():
    assert zonal_mod.class_slug("Bare / sparse vegetation") == "bare_sparse_vegetation"
    assert zonal_mod.class_slug("Built-up") == "built_up"
    assert zonal_mod.class_slug("Tree cover") == "tree_cover"


def test_class_pixel_counts_excludes_nodata():
    band = np.array([[10, 10, 0], [50, 0, 80]])
    counts, valid_count = zonal_mod.class_pixel_counts(band, nodata=0)
    assert counts == {10: 2, 50: 1, 80: 1}
    assert valid_count == 4


def test_class_pixel_counts_handles_no_nodata_value():
    band = np.array([[10, 50], [50, 80]])
    counts, valid_count = zonal_mod.class_pixel_counts(band, nodata=None)
    assert counts == {10: 1, 50: 2, 80: 1}
    assert valid_count == 4


def test_class_pixel_counts_all_nodata_returns_empty():
    band = np.array([[0, 0], [0, 0]])
    counts, valid_count = zonal_mod.class_pixel_counts(band, nodata=0)
    assert counts == {}
    assert valid_count == 0


def test_composition_row_computes_percentages_and_area():
    counts = {10: 3, 50: 1}  # 3 Tree cover, 1 Built-up, 0 Permanent water
    row = zonal_mod.composition_row("TestDistrict", counts, valid_count=4, area_per_pixel_km2=2.0, classes=TEST_CLASSES)
    assert row["district"] == "TestDistrict"
    assert row["dominant_class"] == "Tree cover"
    assert row["area_km2"] == 8.0  # 4 pixels * 2.0 km2
    assert row["tree_cover_pct"] == 75.0
    assert row["built_up_pct"] == 25.0
    assert row["permanent_water_bodies_pct"] == 0.0


def test_composition_row_handles_zero_valid_pixels():
    row = zonal_mod.composition_row("EmptyDistrict", {}, valid_count=0, area_per_pixel_km2=2.0, classes=TEST_CLASSES)
    assert row["district"] == "EmptyDistrict"
    assert row["dominant_class"] == ""
    assert row["area_km2"] == 0.0
    assert row["tree_cover_pct"] == 0.0
    assert row["built_up_pct"] == 0.0
    assert row["permanent_water_bodies_pct"] == 0.0


def test_province_composition_rows_covers_every_class_in_order():
    total_counts = {10: 6, 50: 2}  # Permanent water bodies absent entirely
    rows = zonal_mod.province_composition_rows(total_counts, total_valid=8, area_per_pixel_km2=1.0, classes=TEST_CLASSES)
    assert [r["label"] for r in rows] == ["Tree cover", "Built-up", "Permanent water bodies"]
    assert rows[0]["area_km2"] == 6.0
    assert rows[0]["pct_area"] == 75.0
    assert rows[2]["area_km2"] == 0.0
    assert rows[2]["pct_area"] == 0.0


def test_province_composition_rows_handles_zero_total_valid():
    rows = zonal_mod.province_composition_rows({}, total_valid=0, area_per_pixel_km2=1.0, classes=TEST_CLASSES)
    assert all(r["pct_area"] == 0.0 for r in rows)


def test_pixel_area_km2_is_positive_and_reasonable_for_10m_pixels():
    # ESA WorldCover is ~10m resolution -> roughly 0.0001 km2/pixel; the
    # source raster stores degrees, not meters, so this exercises the real
    # equirectangular conversion, not a fixed constant.
    from types import SimpleNamespace
    transform = SimpleNamespace(a=0.0000892857, e=-0.0000892857)  # ~10m at KP's latitude
    area = zonal_mod.pixel_area_km2(transform)
    assert 0.00005 < area < 0.00015
