import importlib

geocode_mod = importlib.import_module("scripts.22_geocode_marham_facilities")


def test_is_within_kp_bounds_accepts_real_kp_point():
    # Peshawar, roughly
    assert geocode_mod.is_within_kp_bounds(71.5, 34.0) is True


def test_is_within_kp_bounds_rejects_wildly_wrong_match():
    # The real bad Nominatim match found during Task 4's live run:
    # "ADC Abbottabad ( Hope Breast Clinic )" resolved to lon=66.6,
    # far outside KP entirely (likely Balochistan/Iran-adjacent).
    assert geocode_mod.is_within_kp_bounds(66.61145323921696, 30.5) is False


def test_is_within_kp_bounds_rejects_point_just_outside_each_edge():
    assert geocode_mod.is_within_kp_bounds(69.0, 34.0) is False  # west of KP_BBOX
    assert geocode_mod.is_within_kp_bounds(74.2, 34.0) is False  # east of KP_BBOX
    assert geocode_mod.is_within_kp_bounds(71.5, 30.9) is False  # south of KP_BBOX
    assert geocode_mod.is_within_kp_bounds(71.5, 37.0) is False  # north of KP_BBOX


def test_resolve_record_trusts_valid_source_coordinates():
    rec = {"has_real_coords": True, "lat": 34.0, "lon": 71.5, "street_address": "", "district": "Abbottabad"}
    lon, lat, precision = geocode_mod.resolve_coordinates(rec, session=None, centroids={}, geocode_fn=None)
    assert (lon, lat, precision) == (71.5, 34.0, "source")


def test_resolve_record_rejects_out_of_bounds_source_coordinates_and_falls_back():
    # The real bad record found during Task 4's live run: has_real_coords
    # is True, but the coordinate itself (27.81, 66.61) is nowhere near
    # KP - Marham's own "precise" data can be wrong, not just the 0,0
    # placeholder case. Must not be silently trusted just because
    # has_real_coords says True.
    rec = {
        "has_real_coords": True, "lat": 27.810106186576643, "lon": 66.61145323921696,
        "street_address": "Eman Plaza, near Shafiq Medical Centre,", "district": "Abbottabad",
    }
    centroids = {"Abbottabad": (73.25, 34.15)}
    lon, lat, precision = geocode_mod.resolve_coordinates(rec, session=None, centroids=centroids, geocode_fn=lambda *a: None)
    assert precision == "district_centroid"
    assert (lon, lat) == (73.25, 34.15)


def test_resolve_record_geocodes_when_no_real_coords():
    rec = {"has_real_coords": False, "lat": None, "lon": None, "street_address": "Main Rd", "district": "Bannu"}
    lon, lat, precision = geocode_mod.resolve_coordinates(
        rec, session=None, centroids={}, geocode_fn=lambda *a: (70.6, 32.9)
    )
    assert (lon, lat, precision) == (70.6, 32.9, "street")


def test_resolve_record_falls_back_to_centroid_when_geocoding_also_out_of_bounds():
    rec = {"has_real_coords": False, "lat": None, "lon": None, "street_address": "Vague Rd", "district": "Bannu"}
    centroids = {"Bannu": (70.6, 32.9)}
    # geocode_fn simulates Nominatim returning something already rejected
    # by resolve_coordinates' own bounds check (None), same as
    # geocode_address() itself would return for an out-of-bounds match.
    lon, lat, precision = geocode_mod.resolve_coordinates(
        rec, session=None, centroids=centroids, geocode_fn=lambda *a: None
    )
    assert (lon, lat, precision) == (70.6, 32.9, "district_centroid")
