import pytest

from scripts.lib import districts


def test_district_from_marham_slug_maps_simple_names_with_correct_case():
    assert districts.district_from_marham_slug("abbottabad") == "Abbottabad"
    assert districts.district_from_marham_slug("buner") == "Buner"
    assert districts.district_from_marham_slug("peshawar") == "Peshawar"


def test_district_from_marham_slug_maps_renamed_slugs():
    assert districts.district_from_marham_slug("bajaur-agency") == "Bajaur"
    assert districts.district_from_marham_slug("tank-city") == "Tank"
    assert districts.district_from_marham_slug("dera-ismail-khan") == "Dera Ismail Khan"


def test_district_from_marham_slug_maps_both_malakand_slugs():
    assert districts.district_from_marham_slug("malakand") == "Malakand"
    assert districts.district_from_marham_slug("dargai") == "Malakand"


def test_district_from_marham_slug_maps_timergara_to_lower_dir():
    assert districts.district_from_marham_slug("timergara") == "Lower Dir"


def test_district_from_marham_slug_unknown_slug_raises():
    with pytest.raises(KeyError):
        districts.district_from_marham_slug("not-a-real-slug")


def test_marham_district_slugs_has_no_duplicates():
    assert len(districts.MARHAM_DISTRICT_SLUGS) == len(set(districts.MARHAM_DISTRICT_SLUGS))
    # 19 slugs map to 18 distinct districts - Malakand alone has two
    # (malakand + dargai, both verified as real, separate listings).
    assert len(districts.MARHAM_DISTRICT_SLUGS) == 19


def test_marham_district_slugs_map_to_18_distinct_districts():
    mapped = {districts.district_from_marham_slug(s) for s in districts.MARHAM_DISTRICT_SLUGS}
    assert len(mapped) == 18
