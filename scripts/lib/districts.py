"""Canonical KP district name normalization. The KPHCC facility registry's
own district filter dropdown contains duplicate/inconsistent entries
(e.g. "Bajaur" and "Bajour"; "Dir Lower" and "Lower Dir"), and boundary
datasets, PBS tables, and OSM tags each spell some district names
differently. This module reconciles all of them to one canonical name per
district so joins across data sources don't silently fragment a district
into two rows."""

ALIASES = {
    "bajour": "Bajaur",
    "bajaur": "Bajaur",
    "dir lower": "Lower Dir",
    "lower dir": "Lower Dir",
    "dir upper": "Upper Dir",
    "upper dir": "Upper Dir",
    "kohistan lower": "Lower Kohistan",
    "lower kohistan": "Lower Kohistan",
    "kohistan upper": "Upper Kohistan",
    "upper kohistan": "Upper Kohistan",
    "chitral upper": "Upper Chitral",
    "upper chitral": "Upper Chitral",
    "chitral lower": "Lower Chitral",
    "lower chitral": "Lower Chitral",
    "d.i. khan": "Dera Ismail Khan",
    "d i khan": "Dera Ismail Khan",
    "d. i. khan": "Dera Ismail Khan",
    "d.i.khan": "Dera Ismail Khan",
    "dera ismail khan": "Dera Ismail Khan",
    "kohistan kolai palas": "Kolai Palas Kohistan",
    "kolai palas kohistan": "Kolai Palas Kohistan",
    "waziristan north": "North Waziristan",
    "north waziristan": "North Waziristan",
    "waziristan south": "South Waziristan",
    "south waziristan": "South Waziristan",
    "batagram": "Battagram",
    "battagram": "Battagram",
    "tor ghar": "Torghar",
    "torghar": "Torghar",
}


def normalize_district(name):
    """Return the canonical district name for any known alias/variant.
    Unknown names pass through stripped but otherwise unchanged (so a
    genuinely new/unlisted district name is preserved, not silently
    mangled)."""
    if not name:
        return name
    key = name.strip().lower()
    return ALIASES.get(key, name.strip())


# Marham.pk's city/district URL slugs (e.g. "tank-city", "bajaur-agency")
# don't match KP's canonical district names directly, and - unlike
# normalize_district()'s other inputs (KPHCC/PBS/OSM text, which already
# comes reasonably capitalized) - these are all-lowercase URL slugs, so
# normalize_district()'s passthrough-if-unknown behavior would return
# them unchanged (wrong case) rather than correctly capitalized. This is
# a separate, deliberately exhaustive mapping - not every KP district has
# a Marham listing at all (a real, documented coverage gap - see
# docs/superpowers/specs/2026-08-16-marham-facilities-design.md section
# 2), and district_from_marham_slug() raises KeyError for any slug
# outside this verified set rather than guessing.
MARHAM_DISTRICT_SLUGS = [
    "abbottabad", "bajaur-agency", "bannu", "buner", "charsadda",
    "dera-ismail-khan", "hangu", "haripur", "kohat", "malakand", "dargai",
    "mansehra", "mardan", "nowshera", "peshawar", "swabi", "swat",
    "tank-city", "timergara",
]

_MARHAM_SLUG_TO_DISTRICT = {
    "abbottabad": "Abbottabad",
    "bajaur-agency": "Bajaur",
    "bannu": "Bannu",
    "buner": "Buner",
    "charsadda": "Charsadda",
    "dera-ismail-khan": "Dera Ismail Khan",
    "hangu": "Hangu",
    "haripur": "Haripur",
    "kohat": "Kohat",
    "malakand": "Malakand",
    "dargai": "Malakand",  # Dargai is a real, separate top-level listing (13 facilities) within Malakand district
    "mansehra": "Mansehra",
    "mardan": "Mardan",
    "nowshera": "Nowshera",
    "peshawar": "Peshawar",
    "swabi": "Swabi",
    "swat": "Swat",
    "tank-city": "Tank",
    "timergara": "Lower Dir",  # Timergara is Lower Dir's main town and Marham's only listing for that district
}


def district_from_marham_slug(slug):
    """Return the canonical KP district name for a Marham.pk city/district
    URL slug. Raises KeyError for a slug not in the known, verified
    covered set - deliberately, since this is a one-directional lookup
    where a silently-wrong district name is worse than a loud failure."""
    return _MARHAM_SLUG_TO_DISTRICT[slug]
