"""Compute per-district metrics feeding the gap-score analysis: area,
population density, a network- and terrain-adjusted travel-time
accessibility metric (nearest facility to the district's population
centroid, routed over the OSM road network and derated by DEM-derived
terrain difficulty - see scripts/16b_compute_travel_time_accessibility.py
and docs/superpowers/specs/2026-08-15-travel-time-routing-design.md), a
DEM-derived continuous terrain difficulty score
(data/processed/district_terrain.csv, built by
scripts/16_compute_dem_zonal_stats.py from the Copernicus GLO-30 DEM,
scored via scripts/lib/terrain.py) in place of the earlier hand-classified
mountainous/plains flag, and institution/bed/doctor counts sourced from
Development Statistics of KP 2025 (data/processed/dev_stats_health.csv),
the official KP Bureau of Statistics publication, used as the primary
source for facility-density, bed-capacity, and staffing figures
throughout the gap-score model and report in preference to the merged
KPHCC/OSM facility registry - which is kept only for the two things Dev
Stats cannot provide (it publishes district totals, not site locations):
accessibility routing and the facility-distribution map."""
import csv
import json
import sys
from pathlib import Path

from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district
from scripts.lib.geo_utils import polygon_area_km2
from scripts.lib.terrain import compute_terrain_difficulty, terrain_label

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"


def load_terrain():
    with open(PROCESSED / "district_terrain.csv", newline="", encoding="utf-8") as f:
        return {normalize_district(r["district"]): r for r in csv.DictReader(f)}


def load_travel_time():
    with open(PROCESSED / "district_travel_time.csv", newline="", encoding="utf-8") as f:
        return {normalize_district(r["district"]): r for r in csv.DictReader(f)}


def load_population():
    with open(PROCESSED / "kp_district_population_2023.csv", newline="", encoding="utf-8") as f:
        return {normalize_district(r["district"]): r for r in csv.DictReader(f)}


def load_facilities_by_district():
    by_district = {}
    with open(PROCESSED / "facilities_merged.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["is_duplicate_of"]:
                continue  # don't double-count a facility present in both sources
            by_district.setdefault(r["district"], []).append(
                {"lon": float(r["lon"]), "lat": float(r["lat"]), "beds": r["beds"]}
            )
    return by_district


def load_dev_stats_health():
    with open(PROCESSED / "dev_stats_health.csv", newline="", encoding="utf-8") as f:
        return {normalize_district(r["district"]): r for r in csv.DictReader(f)}


def main():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    population = load_population()
    facilities_by_district = load_facilities_by_district()
    terrain = load_terrain()
    travel_time = load_travel_time()
    dev_health = load_dev_stats_health()

    rows = []
    for d in boundaries["districts"]:
        name = d["district"]
        geom = shape(d["geometry"])
        area_km2 = d.get("area_km2") or round(polygon_area_km2(geom), 2)
        pop_row = population.get(name)
        pop_2023 = int(pop_row["population_2023"]) if pop_row else 0
        pop_density = round(pop_2023 / area_km2, 2) if area_km2 else 0.0

        facilities = facilities_by_district.get(name, [])

        travel_row = travel_time.get(name)
        accessibility_min = (
            float(travel_row["accessibility_min"])
            if travel_row and travel_row["accessibility_min"] != ""
            else None
        )
        centroid_shift_km = (
            float(travel_row["centroid_shift_km"])
            if travel_row and travel_row.get("centroid_shift_km") not in (None, "")
            else 0.0
        )

        terrain_row = terrain.get(name)
        mean_elev_m = float(terrain_row["mean_elev_m"]) if terrain_row and terrain_row["mean_elev_m"] != "" else 0.0
        mean_slope_deg = float(terrain_row["mean_slope_deg"]) if terrain_row and terrain_row["mean_slope_deg"] != "" else 0.0

        health_row = dev_health.get(name)
        govt_institutions = int(health_row["govt_institutions"]) if health_row and health_row["govt_institutions"] != "" else 0
        pvt_hospitals = int(health_row["pvt_hospitals"]) if health_row and health_row["pvt_hospitals"] != "" else 0
        govt_beds = int(health_row["govt_beds"]) if health_row and health_row["govt_beds"] != "" else 0
        pvt_beds = int(health_row["pvt_beds"]) if health_row and health_row["pvt_beds"] != "" else 0
        medical_staff = int(health_row["medical_staff"]) if health_row and health_row["medical_staff"] != "" else 0
        beds_per_1000 = round(((govt_beds + pvt_beds) / pop_2023) * 1000, 3) if pop_2023 else 0.0
        doctors_per_1000 = round((medical_staff / pop_2023) * 1000, 3) if pop_2023 else 0.0

        rows.append(
            {
                "district": name,
                "division": d.get("division") or (pop_row["division"] if pop_row else ""),
                "area_km2": area_km2,
                "population_2023": pop_2023,
                "pop_density": pop_density,
                "mean_elev_m": mean_elev_m,
                "mean_slope_deg": mean_slope_deg,
                # Dev Stats' own official institution count (all 8 government
                # institution types - Hospitals/Dispensaries/RHCs/TB Clinics/
                # MCH Centres/Health Sub Centres/BHUs/Leprosy Clinics - plus
                # registered private hospitals). This is the authoritative,
                # government-published figure and is what drives the gap
                # score's facility-density term (scripts/09). facility_count
                # below (mapped KPHCC+OSM locations) is kept separately since
                # it's still the only source with real coordinates, needed
                # for accessibility routing and the facility-distribution map -
                # Dev Stats publishes district totals only, no site locations.
                "govt_pvt_institutions": govt_institutions + pvt_hospitals,
                "facility_count": len(facilities),
                "beds_per_1000": beds_per_1000,
                "doctors_per_1000": doctors_per_1000,
                "accessibility_min": accessibility_min if accessibility_min is not None else "",
                "centroid_shift_km": centroid_shift_km,
            }
        )

    # Scale terrain difficulty relative to the full province, then derive
    # the mountainous/plains label from the continuous score.
    rows = compute_terrain_difficulty(rows)
    for r in rows:
        r["terrain"] = terrain_label(r["terrain_difficulty"])

    out_path = PROCESSED / "district_metrics.csv"
    fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote district_metrics.csv for {len(rows)} districts")


if __name__ == "__main__":
    main()
