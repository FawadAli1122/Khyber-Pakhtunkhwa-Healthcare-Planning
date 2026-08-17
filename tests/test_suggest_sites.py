import importlib

sites_mod = importlib.import_module("scripts.11_suggest_new_sites")


def test_load_settlements_by_district_drops_settlements_outside_province():
    boundaries = {
        "province_geometry": {
            "type": "Polygon",
            "coordinates": [[[73.0, 34.0], [73.5, 34.0], [73.5, 34.5], [73.0, 34.5], [73.0, 34.0]]],
        },
        "districts": [
            {
                "district": "Abbottabad",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[73.0, 34.0], [73.5, 34.0], [73.5, 34.5], [73.0, 34.5], [73.0, 34.0]]],
                },
            }
        ],
    }
    settlements = [
        {"lat": 34.2, "lon": 73.2, "population": 500},  # inside the province/district
        {"lat": 30.0, "lon": 75.0, "population": 500},  # nowhere near it (e.g. Islamabad-area bbox overfetch)
    ]
    by_district = sites_mod.load_settlements_by_district(settlements, boundaries)
    all_kept = [s for group in by_district.values() for s in group]
    assert len(all_kept) == 1
    assert all_kept[0]["lon"] == 73.2


def test_pick_candidate_sites_favors_underserved_cluster():
    # Two settlement clusters: one near an existing facility, one far away.
    settlements = (
        [{"lat": 34.00 + i * 0.001, "lon": 71.00 + i * 0.001, "population": 500} for i in range(5)]
        + [{"lat": 35.00 + i * 0.001, "lon": 72.00 + i * 0.001, "population": 500} for i in range(5)]
    )
    existing_facilities = [{"lat": 34.00, "lon": 71.00}]  # sits right in the first cluster
    sites = sites_mod.pick_candidate_sites(settlements, existing_facilities, n_sites=1)
    assert len(sites) == 1
    # The suggested site should land near the underserved (far) cluster, not the served one.
    assert sites[0]["lat"] > 34.5


def test_pick_candidate_sites_respects_n_sites_cap():
    settlements = [{"lat": 34.0 + i * 0.01, "lon": 71.0 + i * 0.01, "population": 100} for i in range(20)]
    sites = sites_mod.pick_candidate_sites(settlements, [], n_sites=3)
    assert len(sites) == 3


def test_pick_candidate_sites_empty_settlements_returns_empty():
    assert sites_mod.pick_candidate_sites([], [], n_sites=3) == []


def test_adjust_for_landcover_leaves_valid_centroid_unchanged():
    settlements = [{"lat": 34.0, "lon": 71.0, "population": 500}]
    labels = [0]
    lon, lat, note = sites_mod._adjust_for_landcover(
        71.5, 34.5, 0, labels, settlements, sample_landcover=lambda lon, lat: 40  # Cropland - allowed
    )
    assert (lon, lat, note) == (71.5, 34.5, None)


def test_adjust_for_landcover_falls_back_to_highest_population_settlement():
    settlements = [
        {"lat": 34.0, "lon": 71.0, "population": 500},
        {"lat": 34.1, "lon": 71.1, "population": 5000},
    ]
    labels = [0, 0]

    def sample_landcover(lon, lat):
        if (lon, lat) == (71.5, 34.5):
            return 80  # Permanent water bodies - excluded
        return 40  # Cropland - allowed for both real settlements

    lon, lat, note = sites_mod._adjust_for_landcover(71.5, 34.5, 0, labels, settlements, sample_landcover)
    assert (lon, lat) == (71.1, 34.1)  # the higher-population settlement
    assert "Permanent water bodies" in note


def test_adjust_for_landcover_skips_settlements_that_are_also_excluded():
    settlements = [
        {"lat": 34.0, "lon": 71.0, "population": 5000},  # higher population but also excluded
        {"lat": 34.1, "lon": 71.1, "population": 500},   # lower population but allowed
    ]
    labels = [0, 0]

    def sample_landcover(lon, lat):
        if (lon, lat) == (71.5, 34.5):
            return 70  # Snow and ice - excluded
        if (lon, lat) == (71.0, 34.0):
            return 90  # Herbaceous wetland - also excluded
        return 40  # Cropland - allowed

    lon, lat, note = sites_mod._adjust_for_landcover(71.5, 34.5, 0, labels, settlements, sample_landcover)
    assert (lon, lat) == (71.1, 34.1)  # skipped the excluded one, used the allowed one
    assert "Snow and ice" in note


def test_adjust_for_landcover_keeps_centroid_when_all_settlements_excluded():
    settlements = [{"lat": 34.0, "lon": 71.0, "population": 500}]
    labels = [0]

    def sample_landcover(lon, lat):
        return 80  # Permanent water bodies - excluded everywhere in this fixture

    lon, lat, note = sites_mod._adjust_for_landcover(71.5, 34.5, 0, labels, settlements, sample_landcover)
    assert (lon, lat) == (71.5, 34.5)  # kept the original centroid
    assert "manual site verification" in note


def test_pick_candidate_sites_without_sample_landcover_is_unchanged():
    # Backward compatibility: every pre-existing caller/test omits
    # sample_landcover entirely.
    settlements = [{"lat": 34.0 + i * 0.01, "lon": 71.0 + i * 0.01, "population": 100} for i in range(20)]
    sites = sites_mod.pick_candidate_sites(settlements, [], n_sites=3)
    assert len(sites) == 3


def test_pick_candidate_sites_applies_landcover_adjustment():
    settlements = [
        {"lat": 34.00, "lon": 71.00, "population": 500},
        {"lat": 34.001, "lon": 71.001, "population": 500},
    ]

    def sample_landcover(lon, lat):
        return 80  # Permanent water bodies - excluded, forces a fallback to a real settlement

    sites = sites_mod.pick_candidate_sites(settlements, [], n_sites=1, sample_landcover=sample_landcover)
    assert len(sites) == 1
    assert "Permanent water bodies" in sites[0]["rationale"]
