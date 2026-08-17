from scripts.lib import districts


def test_known_aliases_normalize():
    assert districts.normalize_district("Bajour") == "Bajaur"
    assert districts.normalize_district("Dir Lower") == "Lower Dir"
    assert districts.normalize_district("Lower Dir") == "Lower Dir"
    assert districts.normalize_district("Dir Upper") == "Upper Dir"
    assert districts.normalize_district("D.I. Khan") == "Dera Ismail Khan"
    assert districts.normalize_district("Waziristan North") == "North Waziristan"
    assert districts.normalize_district("Batagram") == "Battagram"
    assert districts.normalize_district("Tor Ghar") == "Torghar"
    assert districts.normalize_district("D. I. Khan") == "Dera Ismail Khan"


def test_case_and_whitespace_insensitive():
    assert districts.normalize_district("  bajour  ") == "Bajaur"
    assert districts.normalize_district("DIR LOWER") == "Lower Dir"


def test_unknown_name_passthrough_stripped():
    assert districts.normalize_district("  Peshawar ") == "Peshawar"


def test_none_or_empty_passthrough():
    assert districts.normalize_district("") == ""
    assert districts.normalize_district(None) is None
