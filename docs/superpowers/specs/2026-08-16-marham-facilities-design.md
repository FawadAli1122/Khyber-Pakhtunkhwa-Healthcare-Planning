# KP Healthcare Planning — Marham.pk Facility Data (Third Facility Source)

Status: Approved design, pre-implementation
Date: 2026-08-16
Extends: [2026-08-14-kp-healthcare-gis-planning-design.md](./2026-08-14-kp-healthcare-gis-planning-design.md)

## 1. Purpose

Add Marham.pk (a commercial healthcare-booking directory) as a third
facility data source alongside KPHCC (official registry) and OSM
(crowd-sourced), to genuinely new facilities the other two sources miss.

## 2. Confirmed During Brainstorming — Real Constraints Found

These were verified directly against the live site during brainstorming,
not assumed:

- **Coverage is partial and skewed away from where it would help most.**
  Marham only lists facilities in **~19 of KP's 35 districts**
  (confirmed slugs: `abbottabad`, `bajaur-agency`, `bannu`, `buner`,
  `charsadda`, `dera-ismail-khan`, `hangu`, `haripur`, `kohat`,
  `malakand`, `mansehra`, `mardan`, `nowshera`, `peshawar`, `swabi`,
  `swat`, `tank-city` — plus `timergara`, likely representing Lower Dir,
  to confirm at implementation time). It has **zero listings** for
  Battagram, Karak, Khyber, Kolai Palas Kohistan, Kurram, Lakki Marwat,
  both Chitral districts, both Kohistan districts, Mohmand, both
  Waziristan districts, Orakzai, Shangla, and Torghar — precisely the
  remote/Critical-tier districts the gap-score model most needs better
  data for. This is being built anyway per explicit user confirmation
  after this limitation was raised; it is not being hidden or minimized
  anywhere downstream (the report's own facility-count cross-validation
  style honesty extends here too — see §6).
- **`robots.txt` explicitly allows `ClaudeBot`** (`User-agent: ClaudeBot
  / Allow: /`), overriding its otherwise-strict general rules (which
  disallow all query-string paths, `/*?*`, among others). The user
  confirmed proceeding with full `?page=N` pagination on this basis
  after the identity nuance (a dedicated named-crawler allowance vs. a
  generic research fetch) was explicitly raised.
- **Coordinates are sometimes real, sometimes placeholder.** Each
  listing's embedded `schema.org` JSON-LD (`<script
  type="application/ld+json">`, `@type: "Hospital"`) has a `geo` field —
  roughly half the sampled facilities carry genuine precise
  `latitude`/`longitude`; the rest have a literal `0, 0` placeholder,
  easily detected and requiring the existing Nominatim geocoding fallback
  (§4).
- **`medicalSpecialty` is useless boilerplate** — every single sampled
  facility has the identical string `"Multi-Speciality (M, u & more)"`,
  regardless of what kind of facility it actually is (verified across
  hospitals, clinics, pharmacies, and labs alike). No category signal
  exists in the source data; category must be inferred from the facility
  name (§3).
- **Pagination is a sliding, overlapping window, not clean pages.**
  Verified precisely on Bannu: page 1 returns 10 facilities, page 2
  returns 10 (4 repeated from page 1, 6 new), page 3 returns 6 (4
  repeated from page 2, 2 new), page 4 returns 0. Deduplicating by the
  `url` field across all pages yields exactly 18 unique facilities —
  matching the page's own "18 Best Hospitals in Bannu" header text
  exactly. This header count is reliably extractable via regex on every
  district page checked (Bannu 18, Peshawar 503, Mardan 89, Tank City 1)
  and is used as a cross-validation check (§3), matching this project's
  established "assert against the source's own stated total" pattern
  (`17_extract_devstats_health.py`).
- **Real scale**: Peshawar alone is ~503 facilities (~84 paginated
  requests at the observed ~6-new-facilities-per-page rate). A full run
  across all ~19 covered districts, at the existing project-wide ≥1.1s
  rate-limit, will plausibly take 10+ minutes — accepted as a normal
  batch-pipeline cost, matching prior multi-minute stages in this
  project (DEM fetch, the travel-time-routing computation).

## 3. Extraction

New script: **`scripts/21_fetch_facilities_marham.py`** (next free
number after `20_cross_validate_facility_counts.py`).

For each of the ~19 covered district slugs:

1. Fetch `/hospitals/{slug}` (page 1), then `?page=2`, `?page=3`, ...,
   via `scripts.lib.http_utils.rate_limited_get` (same ≥1.1s discipline
   used throughout this project).
2. Parse every `<script type="application/ld+json">` block on each page
   (via `BeautifulSoup` + `json.loads` per block — confirmed to parse
   cleanly as a JSON list of objects, `@type: "Hospital"` entries mixed
   with other schema types on the same page).
3. Deduplicate by the `url` field into a running per-district set.
4. Stop paginating once a page contributes zero facilities not already
   in that running set.
5. Cross-validate: extract the "N Best Hospitals in {City}" header count
   via regex; assert it equals the deduplicated total, the same style of
   check `17_extract_devstats_health.py` already uses against Dev
   Stats' own provincial total row.

Each raw record:

```json
{
  "name": "Bukhari Medical and Surgical Complex",
  "url": "https://www.marham.pk/hospitals/bannu/bukhari-medical-and-surgical-complex/kpk",
  "telephone": "03130918921",
  "street_address": "D I Khan Rd, near Bannu Woollen Mills Ltd",
  "district": "Bannu",
  "lat": 32.98092479495509,
  "lon": 70.618048230688,
  "has_real_coords": true,
  "category": "Facility"
}
```

- `district`: the slug mapped to KP's canonical district name via new
  entries in `scripts/lib/districts.py`'s existing `ALIASES` table (e.g.
  `"bajaur-agency"` → `"Bajaur"`, `"tank-city"` → `"Tank"`,
  `"dera-ismail-khan"` → `"Dera Ismail Khan"`) — extending the existing
  module rather than a parallel one.
- `has_real_coords`: `False` when `geo.latitude`/`geo.longitude` are the
  literal `0, 0` placeholder; `True` otherwise. Drives §4's geocoding
  skip logic.
- `category`: a documented keyword heuristic on `name` (case-insensitive
  substring match, first match wins): `"hospital"` → `Hospital`;
  `"clinic"` → `Clinic`; `"medical store"` or `"pharmacy"` → `Pharmacy`;
  `"lab"` or `"laboratory"` → `Facility`; else → `Other`. This mirrors
  `14_build_html_report.py`'s existing `map_category()` in spirit (raw
  source data reduced to the four display categories), keyword-based
  instead of a lookup table since Marham supplies no raw category field
  at all (see §2's `medicalSpecialty` finding).

Output: `data/raw/marham_facilities.json` — matching
`03_fetch_facilities_kphcc.py`'s raw-then-geocode convention (see §4),
not `05_fetch_facilities_osm.py`'s single-step pattern (OSM already
carries real coordinates for every point; Marham only carries them for
about half).

## 4. Geocoding Fallback

New script: **`scripts/22_geocode_marham_facilities.py`**.

For every record where `has_real_coords` is `False`, reuses
`04_geocode_kphcc_facilities.py`'s `geocode_address(session, address,
district)` pattern verbatim in spirit: same Nominatim query shape
(`f"{address}, {district}, Khyber Pakhtunkhwa, Pakistan"`), same
district-centroid fallback when Nominatim finds no match, same
`rate_limited_get` politeness. Records where `has_real_coords` is `True`
pass through unchanged — no Nominatim call spent on data Marham already
supplied precisely.

`geo_precision` values (extending the existing `"street"` /
`"district_centroid"` convention from `04`):
- `"source"` — Marham supplied real coordinates directly (new value,
  since neither KPHCC nor OSM geocoding ever hits this case — OSM points
  are always source-precise, KPHCC is always geocoded).
- `"street"` — resolved via Nominatim (existing meaning, unchanged).
- `"district_centroid"` — Nominatim found nothing, fell back to the
  district's centroid (existing meaning, unchanged).

Output: `data/processed/marham_facilities_geocoded.json`, mirroring
`data/processed/kphcc_facilities_geocoded.json`'s naming and shape.

## 5. Merge Into `facilities_merged.csv`

Extends `07_merge_facilities.py`'s `merge(kphcc, osm, districts)` to
`merge(kphcc, osm, marham, districts)`. KPHCC stays primary (unchanged
— still the official, most-trusted source). Marham records get the same
province-boundary filter and `find_containing_district()` treatment OSM
records already get (a safety check even though Marham's own `district`
field is already normalized, the same defensive posture the existing
code takes toward OSM's bbox-overfetch).

**Duplicate flagging widens from a single KPHCC-vs-OSM pair to
pairwise-across-all-three**: `dedup_key()`'s existing name-normalization
logic is unchanged (already source-agnostic); only the comparison loop
widens so a Marham record gets checked against *both* KPHCC and OSM
records (not just KPHCC), and vice versa for OSM against Marham — so a
Marham entry duplicating an OSM entry that itself was never in KPHCC
still gets correctly flagged, rather than being silently double-counted
as two independent "new" facilities. `is_duplicate_of` still records the
matched record's name; `source` gains a third value, `"Marham"`.

## 6. Report / GIS Impact

No new report section and no new shapefile fields — Marham records flow
into the *existing* `facilities_merged.csv` → `KP_Healthcare_Facilities`
shapefile pipeline exactly like KPHCC/OSM records already do (same
`FACILITY_FIELDS` schema in `12_write_shapefiles.py`, including a
`source` field that already accepts arbitrary short strings — `"Marham"`
fits without a schema change). The one narrative correction needed: the
report's "Facility Count Cross-Validation" section text currently frames
the comparison as "merged KPHCC+OSM" — that phrase gets updated to
"merged KPHCC+OSM+Marham" wherever it appears, since the underlying
merged count now genuinely includes a third source. §2's coverage
limitation (19 of 35 districts) is not separately called out in the
report — the existing Facility Count Cross-Validation section will
naturally show unchanged/zero-Marham-contribution differences for the 16
uncovered districts, which is the accurate, honest reflection of the
real situation without needing new prose to explain it.

## 7. Testing

Following this project's dominant pytest convention (unlike the Dev
Stats PDF extraction, this data isn't real-file-dependent — parsing
functions take already-fetched HTML/JSON as input, cleanly testable with
in-memory fixtures, no live site access needed in the test suite):

- **Pagination/dedup**: given synthetic multi-page JSON-LD fixtures
  matching the real observed sliding-window overlap shape, assert the
  correct deduplicated set and the correct "stop when a page contributes
  nothing new" behavior.
- **JSON-LD parsing**: given a raw HTML fragment with an embedded
  `application/ld+json` script tag (built from the real shape observed
  during brainstorming, not guessed), assert the correct record fields
  extracted, including the `has_real_coords` `0,0`-vs-real distinction.
- **Category heuristic**: table-driven tests for each keyword rule and
  the `Other` fallback.
- **Cross-validation header regex**: given sample header strings
  (`"18 Best Hospitals in Bannu"`, `"503 Best Hospitals in Peshawar"`,
  `"1 Best Hospitals in Tank City"`), assert correct count extraction.
- **Merge extension**: extends `tests/test_merge_facilities.py`'s
  existing in-memory-fixture style to a three-source case, confirming
  the widened pairwise-duplicate-check doesn't regress the existing
  KPHCC-vs-OSM behavior those tests already cover.
- **Live orchestration**: `tests/verify_marham_facilities.py`, matching
  `verify_devstats_health.py`'s pattern — run manually against real
  output, sanity-checking district coverage and coordinate presence.

## 8. Explicitly Out of Scope

- The 16 KP districts Marham doesn't cover at all (§2) — not
  addressable by this source; a different approach would be needed for
  those, not attempted here.
- Fetching each facility's own detail page (richer service/doctor
  listings behind the `url` field) — the listing page's JSON-LD already
  has what this project's facility schema needs (name, address,
  category, coordinates); going deeper is a separate decision.
- Any change to the gap-score model — this is the same "more accurate
  facility map" contribution KPHCC/OSM already make, not a new weighted
  input.
