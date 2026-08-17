"""Merge KPHCC (official, geocoded), OSM (crowd-sourced), Marham.pk
(commercial directory), and Bot (Telegram /addpoint - see
docs/superpowers/specs/2026-08-16-telegram-connector-design.md) facility
points into one deduplicated table. A record is flagged (not dropped) as
a likely duplicate of another when they share a normalized name and are
within ~500m of each other, checked pairwise across every source pair —
KPHCC is always primary (the official source) and never itself flagged;
OSM, Marham, and Bot each get checked against every other already-
processed record from a different source, so a Marham entry duplicating
an OSM entry that itself isn't in KPHCC still gets correctly flagged, not
double-counted. `is_duplicate_of` records the matched record's name so
all sources stay auditable in the output. Marham only covers 18 of KP's
35 districts (a real, documented coverage gap — see
docs/superpowers/specs/2026-08-16-marham-facilities-design.md section 2)
— absent from the other 17 is expected, not a bug.

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
import csv
import json
import re
import sys
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib import local_db
from scripts.lib.districts import normalize_district
from scripts.lib.geo_utils import find_containing_district, haversine_km

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
DUPLICATE_DISTANCE_KM = 0.5


def dedup_key(name):
    key = name.lower()
    key = re.sub(r"\bdr\.?\b", "", key)
    key = re.sub(r"[^a-z0-9]+", " ", key)
    return " ".join(key.split())


def merge(kphcc, osm, marham, bot, districts):
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

    for r in bot:
        if r.get("lat") in (None, "") or r.get("lon") in (None, ""):
            continue  # unresolved - shouldn't happen (/addpoint always collects a real location), but matches the same guard KPHCC/Marham use
        lat, lon = float(r["lat"]), float(r["lon"])
        if province_geom is not None and not province_geom.contains(Point(lon, lat)):
            continue  # same safety net OSM/Marham get - /addpoint already validates this before writing, but never trust a single validation point
        records.append(
            {
                "name": r["name"],
                "category": r["category"],
                "public_private": "",
                "beds": None,
                "district": normalize_district(r["district"]),  # already resolved+validated at /addpoint time, like Marham's own district field
                "lat": lat,
                "lon": lon,
                "source": "Bot",
                "geo_precision": "bot_shared_location",
                "is_duplicate_of": None,
            }
        )

    # Flag duplicates: KPHCC records are always primary (never flagged as
    # a duplicate of anything). Every OSM, Marham, and Bot record is
    # checked against every OTHER already-appended record from a
    # DIFFERENT source (not just against KPHCC) - so a Marham entry
    # duplicating an OSM entry that itself was never in KPHCC still gets
    # correctly flagged, not double-counted as two independent "new"
    # facilities. Records are appended in KPHCC-then-OSM-then-Marham-
    # then-Bot order, so for any OSM record this reproduces the exact
    # original KPHCC-only comparison; for a Marham record it additionally
    # reaches every earlier-appended OSM record; for a Bot record it
    # reaches every earlier-appended KPHCC/OSM/Marham record.
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


def main():
    kphcc = json.loads((PROCESSED / "kphcc_facilities_geocoded.json").read_text())
    osm = json.loads((RAW / "osm_facilities.json").read_text())
    marham = json.loads((PROCESSED / "marham_facilities_geocoded.json").read_text())
    bot = local_db.fetch_all("bot_facilities", order_by="added_at")
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    districts = [{"district": d["district"], "geometry": shape(d["geometry"])} for d in boundaries["districts"]]

    merged = merge(kphcc, osm, marham, bot, districts)

    out_path = PROCESSED / "facilities_merged.csv"
    fieldnames = ["name", "category", "public_private", "beds", "district", "lat", "lon", "source", "geo_precision", "is_duplicate_of"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged)

    dupes = sum(1 for r in merged if r["is_duplicate_of"])
    print(f"Wrote {len(merged)} merged facility records ({dupes} flagged as likely duplicates)")


if __name__ == "__main__":
    main()
