# Marham.pk Facility Data (Third Facility Source) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Marham.pk as a third facility data source (alongside KPHCC and OSM) feeding `facilities_merged.csv`, `KP_Healthcare_Facilities`, and the gap-score model's facility count — for the ~17-19 KP districts Marham actually covers.

**Architecture:** Two new numbered scripts (fetch, then geocode) mirroring `03_fetch_facilities_kphcc.py`/`04_geocode_kphcc_facilities.py`'s existing split, feeding an extended three-source `07_merge_facilities.py`. No new shapefile fields (facilities already flow generically through `write_facilities()`); no gap-score weight changes.

**Tech Stack:** Python, `requests` (via `scripts.lib.http_utils`), `BeautifulSoup`, `json`, existing Nominatim geocoding pattern.

**Spec:** [docs/superpowers/specs/2026-08-16-marham-facilities-design.md](../specs/2026-08-16-marham-facilities-design.md)

## Global Constraints

- No gap-score changes. `08_compute_district_metrics.py`/`09_gap_score_and_clusters.py` are not touched.
- **Final, verified district-slug mapping** (resolved during plan-writing, superseding the spec's "~19, to confirm" hedge): 18 slugs covering **17 distinct KP districts** — `malakand` and `dargai` both map to Malakand (Dargai confirmed as its own real listing, 2 facilities, not a duplicate/redirect of the main Malakand page); `timergara` maps to Lower Dir (confirmed 13 facilities — this actually **adds** Lower Dir to Marham's coverage, which the spec's district list didn't include). Full list: `abbottabad`, `bajaur-agency`, `bannu`, `buner`, `charsadda`, `dera-ismail-khan`, `hangu`, `haripur`, `kohat`, `malakand`, `dargai`, `mansehra`, `mardan`, `nowshera`, `peshawar`, `swabi`, `swat`, `tank-city`, `timergara`.
- **Marham's district field is trusted directly** (like KPHCC's self-reported district), not geometrically re-derived via `find_containing_district()` (like OSM, which has no self-reported district at all) — a small, deliberate simplification from the spec's "safety check" framing, since Marham's slug-derived district assignment is the data provider's own assertion, the same trust level already given to KPHCC.
- Multiple slugs can map to the same district (Malakand). Facilities are deduplicated by the `url` field **per district** (pooled across all of that district's slugs), not per slug — so a hypothetical overlap between `malakand` and `dargai` listings would collapse correctly rather than double-count.
- `has_real_coords` distinguishes Marham's own precise coordinates (~50% of listings, verified) from the literal `0, 0` placeholder (the other ~50%, needing the Nominatim fallback) — this drives a new `geo_precision` value, `"source"`, alongside the existing `"street"`/`"district_centroid"`.
- `merge()`'s widened pairwise duplicate-check must not change existing KPHCC-vs-OSM test behavior — verified algorithmically during planning (see Task 5) to be an exact behavioral superset, not a rewrite.
- Every `KPHCC/OSM` or `KPHCC+OSM` mention in the report and `20_cross_validate_facility_counts.py` gets corrected to include Marham — all 6 occurrences project-wide were grepped and read in full during planning (matching the lesson from the travel-time-routing work, where an initial pass caught only 4 of 6 similar stale mentions), not just the one the spec's §6 called out.
- Test convention: the pure parsing/category/pagination-stop logic gets real pytest unit tests with in-memory fixtures (this project's dominant convention — Marham's data isn't real-file-dependent like the Dev Stats PDF, so there's no reason to deviate the way that file does). The live-fetch orchestration gets a `verify_marham_facilities.py`-style script, matching `verify_devstats_health.py`'s pattern, run manually against real output.
- Politeness: every Marham page fetch and every Nominatim geocoding call goes through `scripts.lib.http_utils.rate_limited_get` (≥1.1s between requests, already enforced project-wide) — no bespoke `requests.get` calls anywhere in this feature.

---

### Task 1: `scripts/lib/districts.py` — Marham slug-to-district mapping

**Files:**
- Modify: `scripts/lib/districts.py`
- Create: `tests/lib/test_marham_districts.py`

**Interfaces:**
- Produces: `scripts.lib.districts.MARHAM_DISTRICT_SLUGS: list[str]` (the 18 slugs), `scripts.lib.districts.district_from_marham_slug(slug: str) -> str` (raises `KeyError` for an unknown slug). Task 2's parsing code and Task 3's orchestration consume both.

This is a deliberately separate lookup from `normalize_district()` — Marham's URL slugs are all-lowercase (`"buner"`, `"abbottabad"`), and `normalize_district()`'s passthrough-if-unknown behavior would return them unchanged (wrong case) rather than correctly capitalized, since that function was designed for already-reasonably-capitalized KPHCC/PBS/OSM text, not lowercase URL slugs. A silently-wrong-cased district name is worse than a loud `KeyError` for a slug outside the known, verified set.

- [ ] **Step 1: Write the failing tests in `tests/lib/test_marham_districts.py`**

```python
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
    assert len(districts.MARHAM_DISTRICT_SLUGS) == 18
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_marham_districts.py -v`
Expected: FAIL with `AttributeError` (`district_from_marham_slug`/`MARHAM_DISTRICT_SLUGS` don't exist yet)

- [ ] **Step 3: Add the mapping to `scripts/lib/districts.py`**

Find (the end of the file):

```python
def normalize_district(name):
    """Return the canonical district name for any known alias/variant.
    Unknown names pass through stripped but otherwise unchanged (so a
    genuinely new/unlisted district name is preserved, not silently
    mangled)."""
    if not name:
        return name
    key = name.strip().lower()
    return ALIASES.get(key, name.strip())
```

Replace with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_marham_districts.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass (pure addition, `normalize_district` itself unchanged)

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/districts.py tests/lib/test_marham_districts.py
git commit -m "feat: add Marham.pk district-slug mapping to scripts/lib/districts.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `scripts/21_fetch_facilities_marham.py` — parsing logic

**Files:**
- Create: `scripts/21_fetch_facilities_marham.py`
- Create: `tests/test_fetch_facilities_marham.py`

**Interfaces:**
- Produces: `scripts.21_fetch_facilities_marham.infer_category(name: str) -> str`, `parse_hospital_entries(html: str) -> list[dict]` (each dict: `name, url, telephone, street_address, lat, lon, has_real_coords`), `extract_header_count(html: str) -> int | None`. Task 3 consumes all three for the live orchestration.

- [ ] **Step 1: Write the failing tests in `tests/test_fetch_facilities_marham.py`**

```python
import importlib

marham_mod = importlib.import_module("scripts.21_fetch_facilities_marham")


def test_infer_category_hospital():
    assert marham_mod.infer_category("Combined Military Hospital") == "Hospital"
    assert marham_mod.infer_category("Dhq Hospital") == "Hospital"


def test_infer_category_clinic():
    assert marham_mod.infer_category("Specialist Psychiatry Clinic") == "Clinic"


def test_infer_category_pharmacy():
    assert marham_mod.infer_category("Zafran Medical store") == "Pharmacy"
    assert marham_mod.infer_category("City Pharmacy") == "Pharmacy"


def test_infer_category_facility_for_labs():
    assert marham_mod.infer_category("Doctors Lab") == "Facility"
    assert marham_mod.infer_category("Zeeshan Medical Store and Azam laboratory") == "Pharmacy"  # "medical store" checked before "laboratory"


def test_infer_category_other_fallback():
    assert marham_mod.infer_category("Abaseen Center") == "Other"


HOSPITAL_JSON_LD_HTML = """
<html><body>
<script type="application/ld+json">
[
  {
    "@context": "https://schema.org/",
    "@type": "Hospital",
    "medicalSpecialty": "Multi-Speciality (M, u & more)",
    "name": "Bukhari Medical and Surgical Complex",
    "url": "https://www.marham.pk/hospitals/bannu/bukhari-medical-and-surgical-complex/kpk",
    "telephone": "03130918921",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "D I Khan Rd, near Bannu Woollen Mills Ltd",
      "addressLocality": "KPK",
      "addressRegion": "Bannu",
      "addressCountry": "Pakistan",
      "postalCode": "54000"
    },
    "geo": {"@type": "GeoCoordinates", "latitude": 32.98092479495509, "longitude": 70.618048230688}
  },
  {
    "@context": "https://schema.org/",
    "@type": "Hospital",
    "name": "Combined Military Hospital",
    "url": "https://www.marham.pk/hospitals/bannu/combined-military-hospital/bannu-cantonment",
    "telephone": "03339988128",
    "address": {"@type": "PostalAddress", "streetAddress": "Cantt", "addressLocality": "Bannu Cantonment"},
    "geo": {"@type": "GeoCoordinates", "latitude": 0, "longitude": 0}
  },
  {
    "@context": "https://schema.org/",
    "@type": "Pharmacy",
    "name": "Some Other Schema Type - Not A Hospital",
    "url": "https://www.marham.pk/pharmacies/bannu/irrelevant"
  }
]
</script>
</body></html>
"""


def test_parse_hospital_entries_extracts_only_hospital_type():
    entries = marham_mod.parse_hospital_entries(HOSPITAL_JSON_LD_HTML)
    assert len(entries) == 2  # the "Pharmacy" @type entry is excluded
    names = {e["name"] for e in entries}
    assert names == {"Bukhari Medical and Surgical Complex", "Combined Military Hospital"}


def test_parse_hospital_entries_detects_real_coordinates():
    entries = marham_mod.parse_hospital_entries(HOSPITAL_JSON_LD_HTML)
    by_name = {e["name"]: e for e in entries}
    real = by_name["Bukhari Medical and Surgical Complex"]
    assert real["has_real_coords"] is True
    assert real["lat"] == 32.98092479495509
    assert real["lon"] == 70.618048230688


def test_parse_hospital_entries_treats_zero_zero_as_placeholder():
    entries = marham_mod.parse_hospital_entries(HOSPITAL_JSON_LD_HTML)
    by_name = {e["name"]: e for e in entries}
    placeholder = by_name["Combined Military Hospital"]
    assert placeholder["has_real_coords"] is False
    assert placeholder["lat"] is None
    assert placeholder["lon"] is None


def test_parse_hospital_entries_extracts_url_and_address_fields():
    entries = marham_mod.parse_hospital_entries(HOSPITAL_JSON_LD_HTML)
    by_name = {e["name"]: e for e in entries}
    real = by_name["Bukhari Medical and Surgical Complex"]
    assert real["url"] == "https://www.marham.pk/hospitals/bannu/bukhari-medical-and-surgical-complex/kpk"
    assert real["telephone"] == "03130918921"
    assert real["street_address"] == "D I Khan Rd, near Bannu Woollen Mills Ltd"


def test_parse_hospital_entries_no_json_ld_returns_empty():
    assert marham_mod.parse_hospital_entries("<html><body>no listings here</body></html>") == []


def test_extract_header_count_finds_the_number():
    assert marham_mod.extract_header_count("<title>18 Best Hospitals in Bannu | Marham</title>") == 18
    assert marham_mod.extract_header_count("<h1>503 Best Hospitals in Peshawar</h1>") == 503
    assert marham_mod.extract_header_count("<title>1 Best Hospitals in Tank City | Marham</title>") == 1


def test_extract_header_count_returns_none_when_absent():
    assert marham_mod.extract_header_count("<html><body>nothing relevant</body></html>") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_facilities_marham.py -v`
Expected: FAIL with `ModuleNotFoundError` (`scripts.21_fetch_facilities_marham` doesn't exist yet)

- [ ] **Step 3: Create `scripts/21_fetch_facilities_marham.py` (parsing functions only — pagination/main() is Task 3)**

```python
"""Fetch healthcare facility listings from Marham.pk (a commercial
booking/directory platform) for the KP districts it actually covers - a
real, documented coverage gap (see
docs/superpowers/specs/2026-08-16-marham-facilities-design.md section 2):
Marham lists facilities in only 17 of KP's 35 districts, skewed toward
already-accessible, already-well-mapped areas rather than the remote
districts that most need better facility data. Used as a third facility
source alongside KPHCC (official registry) and OSM (crowd-sourced) - see
scripts/07_merge_facilities.py.

robots.txt (https://www.marham.pk/robots.txt) explicitly allows
ClaudeBot (User-agent: ClaudeBot / Allow: /), overriding its otherwise
strict rules (which disallow all query-string paths, needed here for
pagination). Every request goes through
scripts.lib.http_utils.rate_limited_get's existing >=1.1s politeness
discipline.

Each listing page embeds schema.org JSON-LD (<script
type="application/ld+json">, @type: "Hospital") with real per-facility
data - name, phone, structured address, and sometimes (verified ~50% of
sampled facilities) genuine precise coordinates; the rest carry a literal
0,0 placeholder needing the Nominatim geocoding fallback in
scripts/22_geocode_marham_facilities.py. The medicalSpecialty field is
useless boilerplate (verified identical - "Multi-Speciality (M, u & more)"
- across every sampled facility regardless of actual type), so category
is inferred from the facility name instead."""
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import MARHAM_DISTRICT_SLUGS, district_from_marham_slug
from scripts.lib.http_utils import make_session, rate_limited_get

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
BASE_URL = "https://www.marham.pk/hospitals"

# Keyword -> display category, first match wins (checked in this exact
# order - "medical store" is checked before "laboratory" so e.g. "Zeeshan
# Medical Store and Azam laboratory" resolves to Pharmacy, matching what
# the business actually primarily is).
CATEGORY_KEYWORDS = [
    ("hospital", "Hospital"),
    ("clinic", "Clinic"),
    ("medical store", "Pharmacy"),
    ("pharmacy", "Pharmacy"),
    ("laboratory", "Facility"),
    ("lab", "Facility"),
]


def infer_category(name):
    """Marham gives no usable raw category field (medicalSpecialty is
    identical boilerplate for every facility - see module docstring), so
    category is inferred from the facility name via this documented
    keyword heuristic, matching this project's existing "transparent,
    simplified heuristic" style."""
    lower = name.lower()
    for keyword, category in CATEGORY_KEYWORDS:
        if keyword in lower:
            return category
    return "Other"


def parse_hospital_entries(html):
    """Parse every schema.org Hospital JSON-LD entry embedded in a
    listing page. Returns a list of dicts with name/url/telephone/
    street_address/lat/lon/has_real_coords - district and category are
    added by the caller, since this function doesn't know which slug the
    page came from."""
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (TypeError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "Hospital":
                continue
            geo = item.get("geo") or {}
            lat, lon = geo.get("latitude"), geo.get("longitude")
            has_real_coords = bool(lat) and bool(lon)  # the 0,0 placeholder is falsy on both
            address = item.get("address") or {}
            entries.append(
                {
                    "name": item.get("name", ""),
                    "url": item.get("url", ""),
                    "telephone": item.get("telephone", ""),
                    "street_address": address.get("streetAddress", ""),
                    "lat": lat if has_real_coords else None,
                    "lon": lon if has_real_coords else None,
                    "has_real_coords": has_real_coords,
                }
            )
    return entries


def extract_header_count(html):
    """Return the integer count from the page's own "N Best Hospitals in
    {City}" text (verified present in both <title> and page headers), or
    None if not found. Used as a cross-validation check against the
    deduplicated total in Task 3, matching
    scripts/17_extract_devstats_health.py's "assert against the source's
    own stated total" convention."""
    m = re.search(r"(\d+)\s+Best Hospitals?\s+in\s+", html, re.IGNORECASE)
    return int(m.group(1)) if m else None


if __name__ == "__main__":
    print("scripts/21_fetch_facilities_marham.py's main() is added in a later plan task.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch_facilities_marham.py -v`
Expected: 11 passed

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add scripts/21_fetch_facilities_marham.py tests/test_fetch_facilities_marham.py
git commit -m "feat: add Marham.pk JSON-LD parsing and category inference

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `scripts/21_fetch_facilities_marham.py` — pagination, orchestration, live run

**Files:**
- Modify: `scripts/21_fetch_facilities_marham.py`
- Modify: `tests/test_fetch_facilities_marham.py`
- Create: `tests/verify_marham_facilities.py`

**Interfaces:**
- Consumes: `parse_hospital_entries`, `extract_header_count`, `infer_category` (Task 2); `MARHAM_DISTRICT_SLUGS`, `district_from_marham_slug` (Task 1); `scripts.lib.http_utils.make_session`/`rate_limited_get` (existing).
- Produces: `data/raw/marham_facilities.json` (list of dicts: `name, url, telephone, street_address, district, lat, lon, has_real_coords, category`), consumed by Task 4.

- [ ] **Step 1: Write the failing tests (append to `tests/test_fetch_facilities_marham.py`)**

```python
def test_fetch_district_facilities_dedupes_across_sliding_pagination():
    # Mirrors the real observed Bannu shape: page 1 returns facilities
    # A-J, page 2 returns G-J (repeated) + K-P (new), page 3 returns
    # nothing new -> pagination stops, 16 unique facilities total.
    def make_page(names, page_num):
        entries = ",\n".join(
            f'{{"@type": "Hospital", "name": "{n}", "url": "https://www.marham.pk/hospitals/test/{n.lower()}", '
            f'"telephone": "0000", "address": {{"streetAddress": "Some Rd"}}, '
            f'"geo": {{"latitude": 0, "longitude": 0}}}}'
            for n in names
        )
        return f'<html><body><title>16 Best Hospitals in Test</title><script type="application/ld+json">[{entries}]</script></body></html>'

    names_a_to_j = [chr(ord("A") + i) for i in range(10)]
    names_g_to_p = [chr(ord("A") + i) for i in range(6, 16)]
    pages = {
        1: make_page(names_a_to_j, 1),
        2: make_page(names_g_to_p, 2),
        3: make_page([], 3),
    }

    calls = []

    class FakeSession:
        pass

    def fake_rate_limited_get(session, url, params=None, **kwargs):
        page_num = (params or {}).get("page", 1)
        calls.append(page_num)

        class FakeResp:
            text = pages[page_num]

        return FakeResp()

    entries, header_count = marham_mod.fetch_district_facilities(FakeSession(), "test", get_fn=fake_rate_limited_get)
    assert len(entries) == 16
    assert header_count == 16
    assert calls == [1, 2, 3]  # stopped after page 3 contributed nothing new


def test_fetch_district_facilities_single_page_district():
    def fake_rate_limited_get(session, url, params=None, **kwargs):
        class FakeResp:
            text = (
                '<html><body><title>1 Best Hospitals in Tank City</title>'
                '<script type="application/ld+json">'
                '[{"@type": "Hospital", "name": "Only Clinic", "url": "https://www.marham.pk/hospitals/tank-city/only", '
                '"telephone": "0000", "address": {"streetAddress": "Main Rd"}, '
                '"geo": {"latitude": 0, "longitude": 0}}]'
                "</script></body></html>"
            )

        return FakeResp()

    entries, header_count = marham_mod.fetch_district_facilities(None, "tank-city", get_fn=fake_rate_limited_get)
    assert len(entries) == 1
    assert header_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_facilities_marham.py -v`
Expected: the 2 new tests FAIL with `AttributeError` (`fetch_district_facilities` doesn't exist yet); the 11 existing tests still PASS

- [ ] **Step 3: Add pagination orchestration and `main()`**

Find:

```python
def extract_header_count(html):
    """Return the integer count from the page's own "N Best Hospitals in
    {City}" text (verified present in both <title> and page headers), or
    None if not found. Used as a cross-validation check against the
    deduplicated total in Task 3, matching
    scripts/17_extract_devstats_health.py's "assert against the source's
    own stated total" convention."""
    m = re.search(r"(\d+)\s+Best Hospitals?\s+in\s+", html, re.IGNORECASE)
    return int(m.group(1)) if m else None


if __name__ == "__main__":
    print("scripts/21_fetch_facilities_marham.py's main() is added in a later plan task.")
```

Replace with:

```python
def extract_header_count(html):
    """Return the integer count from the page's own "N Best Hospitals in
    {City}" text (verified present in both <title> and page headers), or
    None if not found. Used as a cross-validation check against the
    deduplicated total in Task 3, matching
    scripts/17_extract_devstats_health.py's "assert against the source's
    own stated total" convention."""
    m = re.search(r"(\d+)\s+Best Hospitals?\s+in\s+", html, re.IGNORECASE)
    return int(m.group(1)) if m else None


def fetch_district_facilities(session, slug, get_fn=rate_limited_get):
    """Fetch every page for one Marham city/district slug, paginating
    until a page contributes no new facilities. Pagination is a sliding,
    overlapping window, not clean non-overlapping pages (verified during
    design: Bannu's page 2 repeats 4 of page 1's 10 entries before adding
    6 new ones) - deduplicating by the url field across all pages is
    required, not optional. get_fn defaults to the real
    rate_limited_get, overridable in tests to avoid live HTTP calls.
    Returns (list of deduplicated entry dicts, header_count or None)."""
    seen = {}
    header_count = None
    page = 1
    while True:
        resp = get_fn(session, f"{BASE_URL}/{slug}", params={"page": page} if page > 1 else None)
        if header_count is None:
            header_count = extract_header_count(resp.text)
        new_count = 0
        for entry in parse_hospital_entries(resp.text):
            if entry["url"] not in seen:
                seen[entry["url"]] = entry
                new_count += 1
        if new_count == 0:
            break
        page += 1
    return list(seen.values()), header_count


def main():
    session = make_session()
    district_entries = {}  # district name -> {url: entry}
    validation_issues = []

    for slug in MARHAM_DISTRICT_SLUGS:
        district = district_from_marham_slug(slug)
        entries, header_count = fetch_district_facilities(session, slug)
        bucket = district_entries.setdefault(district, {})
        for entry in entries:
            # Dedupe across slugs mapping to the same district (malakand + dargai)
            bucket[entry["url"]] = entry
        if header_count is not None and len(entries) != header_count:
            validation_issues.append(f"{slug}: extracted {len(entries)} but page header says {header_count}")
        header_note = f" (header said {header_count})" if header_count is not None else ""
        print(f"  {slug} -> {district}: {len(entries)} facilities{header_note}")

    if validation_issues:
        raise AssertionError("Marham extraction count mismatch:\n" + "\n".join(validation_issues))

    records = []
    for district, bucket in district_entries.items():
        for entry in bucket.values():
            records.append(
                {
                    "name": entry["name"],
                    "url": entry["url"],
                    "telephone": entry["telephone"],
                    "street_address": entry["street_address"],
                    "district": district,
                    "lat": entry["lat"],
                    "lon": entry["lon"],
                    "has_real_coords": entry["has_real_coords"],
                    "category": infer_category(entry["name"]),
                }
            )

    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "marham_facilities.json").write_text(json.dumps(records, indent=2))
    print(f"Wrote {len(records)} Marham facility records across {len(district_entries)} districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch_facilities_marham.py -v`
Expected: 13 passed

- [ ] **Step 5: Write `tests/verify_marham_facilities.py`**

```python
import json
from pathlib import Path

JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "marham_facilities.json"

EXPECTED_DISTRICTS = {
    "Abbottabad", "Bajaur", "Bannu", "Buner", "Charsadda", "Dera Ismail Khan",
    "Hangu", "Haripur", "Kohat", "Malakand", "Mansehra", "Mardan", "Nowshera",
    "Peshawar", "Swabi", "Swat", "Tank", "Lower Dir",
}


def main():
    records = json.loads(JSON_PATH.read_text())
    assert len(records) > 0, "Expected at least some Marham facilities"

    districts_found = {r["district"] for r in records}
    unexpected = districts_found - EXPECTED_DISTRICTS
    assert not unexpected, f"Unexpected district(s) in output: {unexpected} - district_from_marham_slug mapping may be wrong"

    with_coords = sum(1 for r in records if r["has_real_coords"])
    for r in records:
        assert r["name"], f"Empty name found (url={r['url']})"
        assert r["district"] in EXPECTED_DISTRICTS
        assert r["category"] in ("Hospital", "Clinic", "Pharmacy", "Facility", "Other")

    print(f"OK: {len(records)} Marham facilities across {len(districts_found)} districts ({with_coords} with real source coordinates)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the live fetch against the real site**

Run: `python scripts/21_fetch_facilities_marham.py`
Expected: 18 `"  {slug} -> {district}: N facilities (header said N)"` lines (one per slug), each with matching extracted/header counts, then `Wrote N Marham facility records across 17 districts`. This will take real time (Peshawar alone is ~84 paginated requests at the ≥1.1s rate limit) — expect several minutes, not seconds.

If any slug's line shows a count mismatch, the script raises `AssertionError` before writing output — stop and inspect that slug's actual page content directly (e.g. `python -c "import requests; print(requests.get('https://www.marham.pk/hospitals/<slug>', headers={'User-Agent': 'ClaudeBot/1.0'}).text)"`) rather than adjusting the assertion to accept the mismatch.

Run: `python tests/verify_marham_facilities.py`
Expected: `OK: N Marham facilities across 17 districts (M with real source coordinates)`

- [ ] **Step 7: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add scripts/21_fetch_facilities_marham.py tests/test_fetch_facilities_marham.py tests/verify_marham_facilities.py data/raw/marham_facilities.json
git commit -m "feat: add Marham.pk pagination, orchestration, and live fetch

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `scripts/22_geocode_marham_facilities.py`

**Files:**
- Create: `scripts/22_geocode_marham_facilities.py`
- Create: `tests/verify_marham_geocoded.py`

**Interfaces:**
- Consumes: `data/raw/marham_facilities.json` (Task 3).
- Produces: `data/processed/marham_facilities_geocoded.json` (same records, each with `lon`, `lat`, `geo_precision` filled in — `"source"` for Marham's own coordinates, `"street"` for Nominatim hits, `"district_centroid"` for the fallback), consumed by Task 5.

This mirrors `04_geocode_kphcc_facilities.py` closely — same Nominatim query shape, same district-centroid fallback, same `rate_limited_get` politeness — with one addition: records where `has_real_coords` is already `True` skip Nominatim entirely (no wasted geocoding calls on data Marham already supplied precisely).

- [ ] **Step 1: Create `scripts/22_geocode_marham_facilities.py`**

```python
"""Geocode Marham.pk facility records that lack real coordinates (the
~50% carrying a 0,0 placeholder - see scripts/21_fetch_facilities_marham.py)
via OSM Nominatim, mirroring scripts/04_geocode_kphcc_facilities.py's
existing pattern exactly (same query shape, same district-centroid
fallback, same rate_limited_get politeness). Records that already have
real Marham-supplied coordinates skip Nominatim entirely - no geocoding
call is spent on data already precise."""
import json
import sys
from pathlib import Path

from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.http_utils import make_session, rate_limited_get

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "marham_facilities.json"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def load_district_centroids():
    data = json.loads((PROCESSED / "boundaries.json").read_text())
    centroids = {}
    for d in data["districts"]:
        geom = shape(d["geometry"])
        c = geom.centroid
        centroids[d["district"]] = (c.x, c.y)
    return centroids


def geocode_address(session, address, district):
    query = f"{address}, {district}, Khyber Pakhtunkhwa, Pakistan"
    resp = rate_limited_get(
        session,
        NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "pk"},
    )
    results = resp.json()
    if results:
        return float(results[0]["lon"]), float(results[0]["lat"])
    return None


def main():
    records = json.loads(RAW.read_text())
    centroids = load_district_centroids()
    session = make_session()

    from_source = 0
    from_nominatim = 0
    for i, rec in enumerate(records, start=1):
        if rec["has_real_coords"]:
            rec["geo_precision"] = "source"
            from_source += 1
        else:
            coords = None
            try:
                coords = geocode_address(session, rec["street_address"], rec["district"])
            except RuntimeError:
                coords = None
            if coords:
                rec["lon"], rec["lat"] = coords
                rec["geo_precision"] = "street"
                from_nominatim += 1
            else:
                fallback = centroids.get(rec["district"])
                if fallback is None:
                    rec["lon"], rec["lat"], rec["geo_precision"] = None, None, "unresolved"
                else:
                    rec["lon"], rec["lat"] = fallback
                    rec["geo_precision"] = "district_centroid"
        if i % 25 == 0:
            print(f"  {i}/{len(records)} processed")

    PROCESSED.mkdir(parents=True, exist_ok=True)
    (PROCESSED / "marham_facilities_geocoded.json").write_text(json.dumps(records, indent=2))
    print(f"Geocoded Marham facilities: {from_source} from source, {from_nominatim} via Nominatim, {len(records)} total")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write `tests/verify_marham_geocoded.py`**

```python
import json
from pathlib import Path

JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "marham_facilities_geocoded.json"


def main():
    records = json.loads(JSON_PATH.read_text())
    assert len(records) > 0

    resolved = [r for r in records if r["geo_precision"] != "unresolved"]
    for r in resolved:
        assert r["lon"] is not None and r["lat"] is not None, f"{r['name']}: resolved but missing coordinates"
        assert 68 <= r["lon"] <= 76, f"{r['name']}: lon {r['lon']} outside KP's plausible range"
        assert 30 <= r["lat"] <= 38, f"{r['name']}: lat {r['lat']} outside KP's plausible range"
        assert r["geo_precision"] in ("source", "street", "district_centroid")

    by_precision = {}
    for r in records:
        by_precision[r["geo_precision"]] = by_precision.get(r["geo_precision"], 0) + 1
    print(f"OK: {len(records)} Marham facilities geocoded - {by_precision}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run against the real fetched output**

Run: `python scripts/22_geocode_marham_facilities.py`
Expected: periodic progress lines, ending with `Geocoded Marham facilities: N from source, M via Nominatim, T total`. This will take real time — roughly one Nominatim call per non-source-coordinate record at ≥1.1s each.

Run: `python tests/verify_marham_geocoded.py`
Expected: `OK: T Marham facilities geocoded - {...}`

- [ ] **Step 4: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add scripts/22_geocode_marham_facilities.py tests/verify_marham_geocoded.py data/processed/marham_facilities_geocoded.json
git commit -m "feat: geocode Marham.pk facilities lacking source coordinates

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Extend `07_merge_facilities.py` to three sources

**Files:**
- Modify: `scripts/07_merge_facilities.py`
- Modify: `tests/test_merge_facilities.py`

**Interfaces:**
- Consumes: `data/processed/marham_facilities_geocoded.json` (Task 4).
- Produces: `merge(kphcc, osm, marham, districts)` (signature widened from `merge(kphcc, osm, districts)`), `facilities_merged.csv` now includes `source == "Marham"` rows.

**Correctness note (verified by hand-tracing during planning, not assumed):** the widened duplicate-check loop below is an exact behavioral superset of the existing KPHCC-vs-OSM logic when `marham=[]` — records are still appended in KPHCC-then-OSM-then-Marham order, so for any OSM record, `records[:i]` (everything appended before it) contains exactly the KPHCC records plus any earlier OSM records, and the `other["source"] == rec["source"]` skip excludes those earlier OSM records, leaving exactly `kphcc_records` — identical to today's behavior. For a Marham record, `records[:i]` additionally includes every OSM record (added earlier), so Marham correctly gets checked against both KPHCC and OSM without needing a second pass.

- [ ] **Step 1: Update the existing tests' `merge()` calls to pass an empty `marham` list**

Find:

```python
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
    merged = merge_mod.merge(kphcc, osm, districts)
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
    merged = merge_mod.merge(kphcc, osm, districts)
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
    merged = merge_mod.merge([], osm, districts)
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
    merged = merge_mod.merge([], osm, districts)
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
    merged = merge_mod.merge(kphcc, [], districts)
    assert merged == []
```

Replace with:

```python
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
    merged = merge_mod.merge(kphcc, osm, [], districts)
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
    merged = merge_mod.merge(kphcc, osm, [], districts)
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
    merged = merge_mod.merge([], osm, [], districts)
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
    merged = merge_mod.merge([], osm, [], districts)
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
    merged = merge_mod.merge(kphcc, [], [], districts)
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
    merged = merge_mod.merge([], [], marham, districts)
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
    merged = merge_mod.merge([], osm, marham, districts)
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
    merged = merge_mod.merge(kphcc, [], marham, districts)
    assert len(merged) == 2
    assert all(r["is_duplicate_of"] is None for r in merged)
```

- [ ] **Step 2: Run tests to verify the new ones fail and the others still pass**

Run: `pytest tests/test_merge_facilities.py -v`
Expected: the 3 new tests FAIL with `TypeError` (`merge()` doesn't accept a 4th positional arg yet); the 5 existing tests FAIL too, since they now pass 4 args to a 3-arg function — confirming the signature change is real and required

- [ ] **Step 3: Widen `merge()` to three sources**

Find:

```python
def merge(kphcc, osm, districts):
    province_geom = unary_union([d["geometry"] for d in districts]) if districts else None
    records = []
    for r in kphcc:
        if r.get("lat") is None or r.get("lon") is None:
            continue  # unresolved geocode with no district-centroid fallback available
        records.append(
            {
                "name": r["name"],
                "category": r["category"],
                "public_private": r.get("public_private", ""),
                "beds": r.get("beds"),
                "district": normalize_district(r["district"]),
                "lat": r["lat"],
                "lon": r["lon"],
                "source": "KPHCC",
                "geo_precision": r["geo_precision"],
                "is_duplicate_of": None,
            }
        )

    for r in osm:
        if province_geom is not None and not province_geom.contains(Point(r["lon"], r["lat"])):
            continue  # outside KP entirely - a neighboring-region facility caught by the bbox fetch, not a KP one
        district = find_containing_district(r["lon"], r["lat"], districts) if districts else None
        records.append(
            {
                "name": r["name"],
                "category": r["category"],
                "public_private": "",
                "beds": None,
                "district": district,
                "lat": r["lat"],
                "lon": r["lon"],
                "source": "OSM",
                "geo_precision": "osm_native",
                "is_duplicate_of": None,
            }
        )

    # Flag duplicates: KPHCC records are primary; any OSM record with the
    # same dedup_key within DUPLICATE_DISTANCE_KM of a KPHCC record is
    # flagged (not removed).
    kphcc_records = [r for r in records if r["source"] == "KPHCC"]
    for rec in records:
        if rec["source"] != "OSM":
            continue
        key = dedup_key(rec["name"])
        for k in kphcc_records:
            if dedup_key(k["name"]) != key:
                continue
            dist = haversine_km(rec["lon"], rec["lat"], k["lon"], k["lat"])
            if dist <= DUPLICATE_DISTANCE_KM:
                rec["is_duplicate_of"] = k["name"]
                break

    return records
```

Replace with:

```python
def merge(kphcc, osm, marham, districts):
    province_geom = unary_union([d["geometry"] for d in districts]) if districts else None
    records = []
    for r in kphcc:
        if r.get("lat") is None or r.get("lon") is None:
            continue  # unresolved geocode with no district-centroid fallback available
        records.append(
            {
                "name": r["name"],
                "category": r["category"],
                "public_private": r.get("public_private", ""),
                "beds": r.get("beds"),
                "district": normalize_district(r["district"]),
                "lat": r["lat"],
                "lon": r["lon"],
                "source": "KPHCC",
                "geo_precision": r["geo_precision"],
                "is_duplicate_of": None,
            }
        )

    for r in osm:
        if province_geom is not None and not province_geom.contains(Point(r["lon"], r["lat"])):
            continue  # outside KP entirely - a neighboring-region facility caught by the bbox fetch, not a KP one
        district = find_containing_district(r["lon"], r["lat"], districts) if districts else None
        records.append(
            {
                "name": r["name"],
                "category": r["category"],
                "public_private": "",
                "beds": None,
                "district": district,
                "lat": r["lat"],
                "lon": r["lon"],
                "source": "OSM",
                "geo_precision": "osm_native",
                "is_duplicate_of": None,
            }
        )

    for r in marham:
        if r.get("lat") is None or r.get("lon") is None:
            continue  # unresolved geocode with no district-centroid fallback available
        if province_geom is not None and not province_geom.contains(Point(r["lon"], r["lat"])):
            continue  # same safety net OSM gets, even though Marham's own district field is normally trusted directly
        records.append(
            {
                "name": r["name"],
                "category": r["category"],
                "public_private": "",
                "beds": None,
                "district": r["district"],  # trusted directly, like KPHCC - Marham's own slug-derived assignment, not geometrically re-derived
                "lat": r["lat"],
                "lon": r["lon"],
                "source": "Marham",
                "geo_precision": r["geo_precision"],
                "is_duplicate_of": None,
            }
        )

    # Flag duplicates: KPHCC records are always primary (never flagged as
    # a duplicate of anything). Every OSM and Marham record is checked
    # against every OTHER already-appended record from a DIFFERENT
    # source (not just against KPHCC) - so a Marham entry duplicating an
    # OSM entry that itself was never in KPHCC still gets correctly
    # flagged, not double-counted as two independent "new" facilities.
    # Records are appended in KPHCC-then-OSM-then-Marham order, so for
    # any OSM record this reproduces the exact original KPHCC-only
    # comparison (see this task's plan-level correctness note); for a
    # Marham record it additionally reaches every earlier-appended OSM
    # record.
    for i, rec in enumerate(records):
        if rec["source"] == "KPHCC":
            continue
        key = dedup_key(rec["name"])
        for other in records[:i]:
            if other["source"] == rec["source"]:
                continue  # only cross-source duplicates are flagged, matching the original OSM-vs-KPHCC-only behavior
            if dedup_key(other["name"]) != key:
                continue
            dist = haversine_km(rec["lon"], rec["lat"], other["lon"], other["lat"])
            if dist <= DUPLICATE_DISTANCE_KM:
                rec["is_duplicate_of"] = other["name"]
                break

    return records
```

- [ ] **Step 4: Update `main()` to read the Marham file**

Find:

```python
def main():
    kphcc = json.loads((PROCESSED / "kphcc_facilities_geocoded.json").read_text())
    osm = json.loads((RAW / "osm_facilities.json").read_text())
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    districts = [{"district": d["district"], "geometry": shape(d["geometry"])} for d in boundaries["districts"]]

    merged = merge(kphcc, osm, districts)
```

Replace with:

```python
def main():
    kphcc = json.loads((PROCESSED / "kphcc_facilities_geocoded.json").read_text())
    osm = json.loads((RAW / "osm_facilities.json").read_text())
    marham = json.loads((PROCESSED / "marham_facilities_geocoded.json").read_text())
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    districts = [{"district": d["district"], "geometry": shape(d["geometry"])} for d in boundaries["districts"]]

    merged = merge(kphcc, osm, marham, districts)
```

- [ ] **Step 5: Update the module docstring**

Find:

```python
"""Merge KPHCC (official, geocoded) and OSM facility points into one
deduplicated table. A record is flagged (not dropped) as a likely duplicate
of another when they share a normalized name and are within ~500m of each
other — the KPHCC record is kept as primary in that case since it's the
official source, and the OSM record's `is_duplicate_of` is set to the
KPHCC record's name so both stay auditable in the output.

The Overpass fetch (scripts/05_fetch_facilities_osm.py) queries a
bounding box around KP, which necessarily also pulls in facilities from
neighboring Islamabad/Punjab/Afghanistan that fall inside that rectangle
but outside KP itself. Those are dropped here (not district-assigned):
find_containing_district()'s "nearest district" fallback is meant for a
genuine KP facility sitting just outside its own district polygon (e.g.
imprecise geocoding near a shared border), not for silently relabeling
an Islamabad hospital as being in Haripur because Haripur's centroid
happens to be the closest KP district. KPHCC's own registry needs no such
filter - checked empirically to contain zero out-of-province points."""
```

Replace with:

```python
"""Merge KPHCC (official, geocoded), OSM (crowd-sourced), and Marham.pk
(commercial directory) facility points into one deduplicated table. A
record is flagged (not dropped) as a likely duplicate of another when
they share a normalized name and are within ~500m of each other, checked
pairwise across every source pair — KPHCC is always primary (the
official source) and never itself flagged; OSM and Marham each get
checked against every other already-processed record from a different
source, so a Marham entry duplicating an OSM entry that itself isn't in
KPHCC still gets correctly flagged, not double-counted. `is_duplicate_of`
records the matched record's name so all sources stay auditable in the
output. Marham only covers 17 of KP's 35 districts (a real, documented
coverage gap — see
docs/superpowers/specs/2026-08-16-marham-facilities-design.md section 2)
— absent from the other 18 is expected, not a bug.

The Overpass fetch (scripts/05_fetch_facilities_osm.py) queries a
bounding box around KP, which necessarily also pulls in facilities from
neighboring Islamabad/Punjab/Afghanistan that fall inside that rectangle
but outside KP itself. Those are dropped here (not district-assigned):
find_containing_district()'s "nearest district" fallback is meant for a
genuine KP facility sitting just outside its own district polygon (e.g.
imprecise geocoding near a shared border), not for silently relabeling
an Islamabad hospital as being in Haripur because Haripur's centroid
happens to be the closest KP district. KPHCC's own registry needs no such
filter - checked empirically to contain zero out-of-province points.
Marham's district field is trusted directly (like KPHCC's self-reported
district), not geometrically re-derived - it's the data provider's own
assertion of which city page a listing belongs to, not the same kind of
degrade a bounding-box overfetch produces."""
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_merge_facilities.py -v`
Expected: 8 passed (5 existing + 3 new)

- [ ] **Step 7: Run the merge against real data and the full test suite**

Run: `python scripts/07_merge_facilities.py`
Expected: `Wrote N merged facility records (M flagged as likely duplicates)` — N should be noticeably higher than before this task (KPHCC+OSM count plus however many genuinely new Marham facilities were found).

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add scripts/07_merge_facilities.py tests/test_merge_facilities.py data/processed/facilities_merged.csv
git commit -m "feat: merge Marham.pk as a third facility source (KPHCC/OSM/Marham)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Correct "KPHCC/OSM" narrative text to include Marham

**Files:**
- Modify: `scripts/20_cross_validate_facility_counts.py`
- Modify: `scripts/14_build_html_report.py`

**Interfaces:** none (text-only changes).

All 6 occurrences of `KPHCC/OSM` or `KPHCC+OSM` project-wide were grepped and read in full during planning — not just the one location the spec's §6 called out, matching the lesson from the travel-time-routing work (an initial pass there caught only 4 of 6 similar stale mentions before a fuller grep caught the rest).

- [ ] **Step 1: Correct `20_cross_validate_facility_counts.py`'s docstring**

Find:

```python
"""Compare the pipeline's merged KPHCC+OSM facility count per district
against Development Statistics 2025's official government institution
count. These count different things (mine includes private clinics and
pharmacies visible in KPHCC/OSM; Dev Stats counts only government
institutions) so this is not a "should match" reconciliation - it's a
transparency table surfaced in the report explaining where and how much
the two diverge, per district."""
```

Replace with:

```python
"""Compare the pipeline's merged KPHCC+OSM+Marham facility count per
district against Development Statistics 2025's official government
institution count. These count different things (mine includes private
clinics and pharmacies visible in KPHCC/OSM/Marham; Dev Stats counts only
government institutions) so this is not a "should match" reconciliation -
it's a transparency table surfaced in the report explaining where and how
much the two diverge, per district."""
```

- [ ] **Step 2: Correct the "Official Infrastructure Context" intro paragraph**

Find:

```python
  is the primary source for the institution, bed, and doctor terms (55% of the total weight combined), used in
  preference to this project's own KPHCC/OSM facility mapping wherever the two overlap. The one input Dev Stats
  cannot supply is distance, since it publishes district totals rather than site coordinates &mdash; accessibility
```

Replace with:

```python
  is the primary source for the institution, bed, and doctor terms (55% of the total weight combined), used in
  preference to this project's own KPHCC/OSM/Marham facility mapping wherever the two overlap. The one input Dev Stats
  cannot supply is distance, since it publishes district totals rather than site coordinates &mdash; accessibility
```

- [ ] **Step 3: Correct the Dev Stats explanatory paragraph**

Find:

```python
its health-sector tables give official government institution counts, private-hospital bed-capacity brackets,
staffing, and district road lengths independent of this report's own KPHCC/OSM facility mapping. Figures below
```

Replace with:

```python
its health-sector tables give official government institution counts, private-hospital bed-capacity brackets,
staffing, and district road lengths independent of this report's own KPHCC/OSM/Marham facility mapping. Figures below
```

- [ ] **Step 4: Correct the "Facility Count Cross-Validation" section text**

Find:

```python
<h3>Facility Count Cross-Validation</h3>
<p>This report's own merged KPHCC+OSM facility count and Development Statistics' official government institution
count measure <strong>different things</strong> and are not expected to match: the merged count includes private
clinics and pharmacies visible to KPHCC/OSM that Dev Stats' government-only tally excludes, while Dev Stats counts
```

Replace with:

```python
<h3>Facility Count Cross-Validation</h3>
<p>This report's own merged KPHCC+OSM+Marham facility count and Development Statistics' official government institution
count measure <strong>different things</strong> and are not expected to match: the merged count includes private
clinics and pharmacies visible to KPHCC/OSM/Marham that Dev Stats' government-only tally excludes, while Dev Stats counts
```

- [ ] **Step 5: Correct the District Data section intro**

Find:

```python
of Statistics' official publication, used throughout this analysis in preference to this project's own KPHCC/OSM
facility mapping. "Institutions" is Dev Stats' own count of government health institutions (all 8 types &mdash;
```

Replace with:

```python
of Statistics' official publication, used throughout this analysis in preference to this project's own KPHCC/OSM/Marham
facility mapping. "Institutions" is Dev Stats' own count of government health institutions (all 8 types &mdash;
```

- [ ] **Step 6: Correct the remaining District Data mention**

Find:

```python
site coordinates, so the mapped KPHCC/OSM registry remains the source for travel-time accessibility and the
facility-distribution map above &mdash; see Official Infrastructure Context and Facility Count Cross-Validation
```

Replace with:

```python
site coordinates, so the mapped KPHCC/OSM/Marham registry remains the source for travel-time accessibility and the
facility-distribution map above &mdash; see Official Infrastructure Context and Facility Count Cross-Validation
```

- [ ] **Step 7: Confirm no stale mentions remain**

Run: `grep -n "KPHCC+OSM[^/+]\|KPHCC/OSM[^/]" scripts/14_build_html_report.py scripts/20_cross_validate_facility_counts.py`
Expected: no output (every occurrence now reads `KPHCC+OSM+Marham` or `KPHCC/OSM/Marham`, not the bare two-source form)

- [ ] **Step 8: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass (no test asserts on the exact old two-source wording — confirmed by the passing test suite itself, not assumed)

- [ ] **Step 9: Commit**

```bash
git add scripts/20_cross_validate_facility_counts.py scripts/14_build_html_report.py
git commit -m "fix: correct KPHCC/OSM narrative text to include Marham as a third source

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Wire the new stages into `run_all.py`

**Files:**
- Modify: `scripts/run_all.py`

**Interfaces:** none (pipeline orchestration only).

- [ ] **Step 1: Insert the two new stages before `07_merge_facilities.py`**

Find:

```python
    "03_fetch_facilities_kphcc.py",
    "04_geocode_kphcc_facilities.py",
    "05_fetch_facilities_osm.py",
    "06_fetch_roads_osm.py",
    "07_merge_facilities.py",
```

Replace with:

```python
    "03_fetch_facilities_kphcc.py",
    "04_geocode_kphcc_facilities.py",
    "05_fetch_facilities_osm.py",
    "06_fetch_roads_osm.py",
    "21_fetch_facilities_marham.py",          # independent - only needs the district-slug mapping, not boundaries.json
    "22_geocode_marham_facilities.py",        # needs 21 (raw fetch) + boundaries.json (01, for centroid fallback)
    "07_merge_facilities.py",                 # needs 03/04 (KPHCC) + 05 (OSM) + 21/22 (Marham) + boundaries.json
```

- [ ] **Step 2: Commit**

```bash
git add scripts/run_all.py
git commit -m "feat: wire Marham.pk fetch/geocode stages into run_all.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Full pipeline run and manual verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 2: Run the full downstream chain against real, already-refreshed Marham data**

Tasks 1-7 already ran the live Marham fetch/geocode/merge against real data (Tasks 3, 4, 5's own verification steps). This task runs everything downstream of the merged facility count to confirm it flows through cleanly end to end.

Run: `python scripts/08_compute_district_metrics.py && python scripts/09_gap_score_and_clusters.py && python scripts/10_forecast_demand.py && python scripts/11_suggest_new_sites.py && python scripts/20_cross_validate_facility_counts.py && python scripts/12_write_shapefiles.py && python scripts/13_build_qgis_project.py && python scripts/14_build_html_report.py`

Expected: eight success lines, no traceback. (This is the same full post-facility-merge chain the travel-time-routing and Dev Stats health plans already established is required — `14`'s forecast-year columns depend on `10` having run, `12`/`13` need the refreshed shapefiles, matching the real dependency order in `run_all.py`, not just the stages this feature directly touches.)

- [ ] **Step 3: Spot-check the report reflects the new facility count**

Run:

```bash
python -c "
import csv
with open('data/processed/facilities_merged.csv', newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
by_source = {}
for r in rows:
    by_source[r['source']] = by_source.get(r['source'], 0) + 1
print('facilities by source:', by_source)
marham_dupes = sum(1 for r in rows if r['source'] == 'Marham' and r['is_duplicate_of'])
marham_total = by_source.get('Marham', 0)
print(f'Marham: {marham_total} total, {marham_dupes} flagged as duplicates of another source')
"
```

Expected: a `'Marham'` entry in `by_source` with a real count, and a real (not 0, not equal to the total) number of flagged duplicates — some overlap with KPHCC/OSM is expected in well-covered districts like Peshawar, but not total overlap (Marham should also be contributing some genuinely new facilities, or this feature added nothing real).

- [ ] **Step 4: Confirm the new facilities render in QGIS**

Reload `gis/KP_Healthcare_Plan.qgz` in QGIS (or relaunch it), open `KP_Healthcare_Facilities`'s attribute table, and filter/sort by the `source` field to confirm `"Marham"` rows are present with real coordinates and district assignments — the same live confirmation approach already used earlier in this project for the travel-time-routing and Dev Stats features.

- [ ] **Step 5: Report findings**

If everything above checks out clean, this task (and the whole plan) is done — no further commit needed beyond what Tasks 1-7 already made, plus whatever refreshed `report/KP_Healthcare_Plan.html` / `gis/*` outputs Step 2 produced (commit those as a final "chore: refresh pipeline outputs" commit, matching the pattern already established in the travel-time-routing and Dev Stats health plans). If anything looks wrong (Marham showing 0 facilities, every single one flagged as a duplicate, coordinates outside KP's bounds), that's a real bug to fix with its own test before considering this complete.
