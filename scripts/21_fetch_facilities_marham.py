"""Fetch healthcare facility listings from Marham.pk (a commercial
booking/directory platform) for the KP districts it actually covers - a
real, documented coverage gap (see
docs/superpowers/specs/2026-08-16-marham-facilities-design.md section 2):
Marham lists facilities in only 18 of KP's 35 districts, skewed toward
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
    deduplicated total, matching
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


def count_mismatch_message(slug, extracted_count, header_count, tolerance_pct=5):
    """Returns None if extracted_count is within tolerance_pct of
    header_count (or header_count is unavailable), else a descriptive
    message. A small mismatch is a real, observed quirk of Marham's own
    site (verified on Peshawar: pagination genuinely stops after a page
    returns zero new entries, at 499 unique facilities, while the page's
    own header still says 503 - a stale/inaccurate header count on their
    end, not a pagination bug on ours). Tolerated the same way
    scripts/17_extract_devstats_health.py tolerates small cross-
    validation gaps against its own source's stated total (< 5%); a
    larger gap still indicates a real extraction bug and is not
    silenced."""
    if not header_count:
        return None
    diff_pct = abs(extracted_count - header_count) / header_count * 100
    if diff_pct >= tolerance_pct:
        return f"{slug}: extracted {extracted_count} but page header says {header_count} ({diff_pct:.1f}% diff)"
    return None


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
        issue = count_mismatch_message(slug, len(entries), header_count)
        header_note = ""
        if issue:
            validation_issues.append(issue)
        elif header_count is not None and len(entries) != header_count:
            header_note = f" (header said {header_count}, within tolerance)"
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
