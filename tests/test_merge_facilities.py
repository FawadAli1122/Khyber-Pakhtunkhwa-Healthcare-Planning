import importlib
from shapely.geometry import Polygon

merge_mod = importlib.import_module("scripts.07_merge_facilities")
# scripts/07_merge_facilities.py starts with a digit, so it can't be
# imported with a normal dotted import; importlib.import_module works
# around that Python restriction.


def test_dedup_key_normalizes_case_and_punctuation():
    assert merge_mod.dedup_key("Dr. Shahid Masroor Clinic") == merge_mod.dedup_key("dr shahid masroor clinic")


def test_merge_flags_close_same_name_as_duplicate():
    districts = [{
        "district": "Abbottabad",
        "geometry": Polygon([(73.0, 34.0), (73.5, 34.0), (73.5, 34.5), (73.0, 34.5)]),
    }]
    kphcc = [{
        "name": "City Hospital", "category": "Hospital", "public_private": "Private",
        "beds": 40, "district": "Abbottabad", "lon": 73.2, "lat": 34.2, "geo_precision": "street",
    }]
    osm = [{
        "name": "City Hospital", "category": "Hospital", "lon": 73.2001, "lat": 34.2001,
        "osm_id": 1, "osm_type": "node",
    }]
    merged = merge_mod.merge(kphcc, osm, [], [], districts)
    assert len(merged) == 2
    sources = {r["source"] for r in merged}
    assert sources == {"KPHCC", "OSM"}
    dup_flags = [r["is_duplicate_of"] for r in merged]
    assert any(d is not None for d in dup_flags), "Expected one record flagged as a duplicate of the other"


def test_merge_keeps_distinct_facilities_separate():
    districts = [{
        "district": "Abbottabad",
        "geometry": Polygon([(73.0, 34.0), (73.5, 34.0), (73.5, 34.5), (73.0, 34.5)]),
    }]
    kphcc = [{
        "name": "City Hospital", "category": "Hospital", "public_private": "Private",
        "beds": 40, "district": "Abbottabad", "lon": 73.1, "lat": 34.1, "geo_precision": "street",
    }]
    osm = [{
        "name": "Green Valley Clinic", "category": "Clinic", "lon": 73.4, "lat": 34.4,
        "osm_id": 2, "osm_type": "node",
    }]
    merged = merge_mod.merge(kphcc, osm, [], [], districts)
    assert len(merged) == 2
    assert all(r["is_duplicate_of"] is None for r in merged)


def test_merge_drops_osm_records_outside_kp_entirely():
    districts = [{
        "district": "Abbottabad",
        "geometry": Polygon([(73.0, 34.0), (73.5, 34.0), (73.5, 34.5), (73.0, 34.5)]),
    }]
    osm = [{
        # Nowhere near the district polygon above (e.g. an Islamabad-area
        # facility caught by the Overpass bounding-box query) - must be
        # dropped, not force-assigned to the "nearest" KP district.
        "name": "Far Away Clinic", "category": "Clinic", "lon": 75.0, "lat": 30.0,
        "osm_id": 3, "osm_type": "node",
    }]
    merged = merge_mod.merge([], osm, [], [], districts)
    assert merged == []


def test_merge_keeps_osm_records_inside_any_provided_district():
    districts = [
        {"district": "Abbottabad", "geometry": Polygon([(73.0, 34.0), (73.5, 34.0), (73.5, 34.5), (73.0, 34.5)])},
        {"district": "Haripur", "geometry": Polygon([(72.5, 34.0), (73.0, 34.0), (73.0, 34.5), (72.5, 34.5)])},
    ]
    osm = [{
        # Inside Haripur's polygon, not Abbottabad's - still well within
        # the two districts' combined footprint, so it must be kept (not
        # confused with a facility genuinely outside KP altogether).
        "name": "Haripur Clinic", "category": "Clinic", "lon": 72.75, "lat": 34.25,
        "osm_id": 4, "osm_type": "node",
    }]
    merged = merge_mod.merge([], osm, [], [], districts)
    assert len(merged) == 1
    assert merged[0]["district"] == "Haripur"


def test_merge_skips_kphcc_records_with_no_coordinates():
    districts = [{
        "district": "Abbottabad",
        "geometry": Polygon([(73.0, 34.0), (73.5, 34.0), (73.5, 34.5), (73.0, 34.5)]),
    }]
    kphcc = [{
        "name": "Unresolved Clinic", "category": "Clinic", "public_private": "Private",
        "beds": None, "district": "Abbottabad", "lon": None, "lat": None, "geo_precision": "unresolved",
    }]
    merged = merge_mod.merge(kphcc, [], [], [], districts)
    assert merged == []


def test_merge_adds_marham_records_with_own_source_label():
    districts = [{
        "district": "Bannu",
        "geometry": Polygon([(70.0, 32.0), (71.0, 32.0), (71.0, 33.0), (70.0, 33.0)]),
    }]
    marham = [{
        "name": "Bukhari Medical and Surgical Complex", "category": "Facility",
        "district": "Bannu", "lon": 70.6, "lat": 32.9, "geo_precision": "source",
    }]
    merged = merge_mod.merge([], [], marham, [], districts)
    assert len(merged) == 1
    assert merged[0]["source"] == "Marham"
    assert merged[0]["is_duplicate_of"] is None


def test_merge_flags_marham_duplicate_of_osm_even_without_kphcc():
    # Neither source is in KPHCC - Marham must still be checked against
    # OSM, not just against KPHCC, or this facility would be silently
    # double-counted as two independent "new" facilities.
    districts = [{
        "district": "Bannu",
        "geometry": Polygon([(70.0, 32.0), (71.0, 32.0), (71.0, 33.0), (70.0, 33.0)]),
    }]
    osm = [{
        "name": "City Hospital", "category": "Hospital", "lon": 70.5, "lat": 32.5,
        "osm_id": 10, "osm_type": "node",
    }]
    marham = [{
        "name": "City Hospital", "category": "Hospital",
        "district": "Bannu", "lon": 70.5001, "lat": 32.5001, "geo_precision": "source",
    }]
    merged = merge_mod.merge([], osm, marham, [], districts)
    assert len(merged) == 2
    marham_rec = next(r for r in merged if r["source"] == "Marham")
    assert marham_rec["is_duplicate_of"] == "City Hospital"


def test_merge_marham_distinct_from_kphcc_and_osm_not_flagged():
    districts = [{
        "district": "Bannu",
        "geometry": Polygon([(70.0, 32.0), (71.0, 32.0), (71.0, 33.0), (70.0, 33.0)]),
    }]
    kphcc = [{
        "name": "DHQ Hospital", "category": "Hospital", "public_private": "Public",
        "beds": 100, "district": "Bannu", "lon": 70.3, "lat": 32.3, "geo_precision": "street",
    }]
    marham = [{
        "name": "Totally Different Clinic", "category": "Clinic",
        "district": "Bannu", "lon": 70.7, "lat": 32.7, "geo_precision": "source",
    }]
    merged = merge_mod.merge(kphcc, [], marham, [], districts)
    assert len(merged) == 2
    assert all(r["is_duplicate_of"] is None for r in merged)


def test_merge_adds_bot_records_with_own_source_label():
    districts = [{
        "district": "Bannu",
        "geometry": Polygon([(70.0, 32.0), (71.0, 32.0), (71.0, 33.0), (70.0, 33.0)]),
    }]
    bot = [{
        "name": "Field Clinic", "category": "Clinic", "district": "Bannu",
        "lat": "32.5", "lon": "70.5",
    }]
    merged = merge_mod.merge([], [], [], bot, districts)
    assert len(merged) == 1
    assert merged[0]["source"] == "Bot"
    assert merged[0]["is_duplicate_of"] is None
    assert merged[0]["lat"] == 32.5
    assert merged[0]["lon"] == 70.5


def test_merge_flags_bot_duplicate_of_existing_kphcc_record():
    districts = [{
        "district": "Bannu",
        "geometry": Polygon([(70.0, 32.0), (71.0, 32.0), (71.0, 33.0), (70.0, 33.0)]),
    }]
    kphcc = [{
        "name": "City Hospital", "category": "Hospital", "public_private": "Public",
        "beds": 50, "district": "Bannu", "lon": 70.5, "lat": 32.5, "geo_precision": "street",
    }]
    bot = [{
        "name": "City Hospital", "category": "Hospital", "district": "Bannu",
        "lat": "32.5001", "lon": "70.5001",
    }]
    merged = merge_mod.merge(kphcc, [], [], bot, districts)
    assert len(merged) == 2
    bot_rec = next(r for r in merged if r["source"] == "Bot")
    assert bot_rec["is_duplicate_of"] == "City Hospital"


def test_merge_drops_bot_records_outside_kp_entirely():
    districts = [{
        "district": "Abbottabad",
        "geometry": Polygon([(73.0, 34.0), (73.5, 34.0), (73.5, 34.5), (73.0, 34.5)]),
    }]
    bot = [{
        # Same safety-net case as the existing OSM out-of-KP test - a
        # bot-submitted point that somehow ended up outside the real KP
        # province polygon must be dropped, not force-assigned to the
        # nearest district, even though /addpoint already validates this
        # before writing the record (defense in depth).
        "name": "Far Away Clinic", "category": "Clinic", "district": "Abbottabad",
        "lat": "30.0", "lon": "75.0",
    }]
    merged = merge_mod.merge([], [], [], bot, districts)
    assert merged == []
