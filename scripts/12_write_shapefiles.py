"""Assemble every processed data table + boundaries.json into the six
final QGIS-ready shapefiles under gis/, using scripts.lib.shp_writer.

Roads are clipped to the KP province polygon (not just the fetch bounding
box) so the shapefile doesn't carry stray segments from neighboring
provinces/Afghanistan that fall inside the rectangular Overpass bbox."""
import csv
import json
import sys
from pathlib import Path

from shapely.geometry import shape, Point, LineString

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.shp_writer import write_shapefile

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"

PROVINCE_FIELDS = [("name", "C", 50, 0), ("area_km2", "F", 12, 2), ("total_pop", "N", 12, 0)]
DISTRICT_FIELDS = [
    ("district", "C", 50, 0), ("division", "C", 50, 0), ("area_km2", "F", 12, 2),
    ("pop_2023", "N", 12, 0), ("pop_dens", "F", 10, 2), ("terrain", "C", 20, 0),
    ("mean_elev", "F", 10, 1), ("mean_slop", "F", 8, 2), ("terr_diff", "F", 6, 4),
    ("fac_count", "N", 6, 0), ("auth_inst", "N", 6, 0), ("govt_inst", "N", 6, 0), ("govt_beds", "N", 8, 0),
    ("pvt_hosp", "N", 6, 0), ("pvt_beds", "N", 8, 0), ("med_staff", "N", 8, 0),
    ("para_staf", "N", 8, 0), ("pvt_prac", "N", 8, 0), ("pop_pbed", "N", 8, 0),
    ("beds_p1k", "F", 8, 3), ("doc_p1k", "F", 8, 3), ("road_km", "F", 10, 2),
    # Development Statistics 2025, Tables 120/123/124 (report + GIS
    # enrichment only - not gap-score inputs). Immunization is curated to
    # 4 of the 11 raw dose counts extracted (bcg/opv0/opv3/measles) for a
    # focused choropleth field set; the report table shows all 11.
    ("pat_total", "N", 10, 0), ("pat_indr", "N", 8, 0), ("pat_outdr", "N", 10, 0),
    ("bcg", "N", 8, 0), ("opv0", "N", 8, 0), ("opv3", "N", 8, 0), ("measles", "N", 8, 0),
    ("mal_cases", "N", 8, 0), ("mal_trtd", "N", 8, 0),
]
FACILITY_FIELDS = [
    ("name", "C", 120, 0), ("category", "C", 40, 0), ("pub_priv", "C", 10, 0),
    ("beds", "N", 6, 0), ("district", "C", 50, 0), ("source", "C", 10, 0), ("geo_prec", "C", 20, 0),
]
ROAD_FIELDS = [("road_cls", "C", 20, 0), ("name", "C", 80, 0)]
GAP_FIELDS = [
    ("district", "C", 50, 0), ("gap_score", "F", 8, 2), ("need_tier", "C", 10, 0),
    ("pop_2029", "N", 12, 0), ("pop_2031", "N", 12, 0), ("pop_2046", "N", 12, 0),
    ("fac_nd29", "N", 6, 0), ("fac_nd31", "N", 6, 0), ("fac_nd46", "N", 6, 0),
    ("beds_nd29", "N", 8, 0), ("beds_nd31", "N", 8, 0), ("beds_nd46", "N", 8, 0),
]
SITE_FIELDS = [("district", "C", 50, 0), ("priority", "N", 4, 0), ("rationale", "C", 150, 0)]


def write_province(boundaries, district_metrics):
    geom = shape(boundaries["province_geometry"])
    total_pop = sum(int(r["population_2023"]) for r in district_metrics)
    total_area = sum(float(r["area_km2"]) for r in district_metrics)
    record = {"geometry": geom, "name": "Khyber Pakhtunkhwa", "area_km2": round(total_area, 2), "total_pop": total_pop}
    write_shapefile(str(GIS_DIR / "KP_Province_Boundary"), "POLYGON", [record], PROVINCE_FIELDS)
    return geom


def write_districts(boundaries, district_metrics):
    metrics_by_name = {r["district"]: r for r in district_metrics}
    with open(PROCESSED / "dev_stats_health.csv", newline="", encoding="utf-8") as f:
        health_by_name = {r["district"]: r for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_roads.csv", newline="", encoding="utf-8") as f:
        roads_by_name = {r["district"]: r for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_patients_treated.csv", newline="", encoding="utf-8") as f:
        patients_by_name = {r["district"]: r for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_immunization.csv", newline="", encoding="utf-8") as f:
        immunization_by_name = {r["district"]: r for r in csv.DictReader(f)}
    with open(PROCESSED / "dev_stats_malaria.csv", newline="", encoding="utf-8") as f:
        malaria_by_name = {r["district"]: r for r in csv.DictReader(f)}

    records = []
    for d in boundaries["districts"]:
        name = d["district"]
        m = metrics_by_name.get(name, {})
        h = health_by_name.get(name, {})
        rd = roads_by_name.get(name, {})
        pat = patients_by_name.get(name, {})
        imm = immunization_by_name.get(name, {})
        mal = malaria_by_name.get(name, {})
        records.append(
            {
                "geometry": shape(d["geometry"]),
                "district": name,
                "division": d.get("division") or m.get("division") or "",
                "area_km2": float(m.get("area_km2", d.get("area_km2", 0))),
                "pop_2023": int(m.get("population_2023", 0)),
                "pop_dens": float(m.get("pop_density", 0)),
                "terrain": m.get("terrain", ""),
                "mean_elev": float(m.get("mean_elev_m", 0) or 0),
                "mean_slop": float(m.get("mean_slope_deg", 0) or 0),
                "terr_diff": float(m.get("terrain_difficulty", 0) or 0),
                "fac_count": int(m.get("facility_count", 0) or 0),
                "auth_inst": int(m.get("govt_pvt_institutions", 0) or 0),
                "govt_inst": int(h.get("govt_institutions", 0) or 0),
                "govt_beds": int(h.get("govt_beds", 0) or 0),
                "pvt_hosp": int(h.get("pvt_hospitals", 0) or 0),
                "pvt_beds": int(h.get("pvt_beds", 0) or 0),
                "med_staff": int(h.get("medical_staff", 0) or 0),
                "para_staf": int(h.get("paramedical_staff", 0) or 0),
                "pvt_prac": int(h.get("pvt_practitioners", 0) or 0),
                "pop_pbed": int(h.get("pop_per_bed", 0) or 0),
                "beds_p1k": float(m.get("beds_per_1000", 0) or 0),
                "doc_p1k": float(m.get("doctors_per_1000", 0) or 0),
                "road_km": float(rd.get("road_km_total", 0) or 0),
                "pat_total": int(pat.get("patients_total_2024", 0) or 0),
                "pat_indr": int(pat.get("patients_indoor_2024", 0) or 0),
                "pat_outdr": int(pat.get("patients_outdoor_2024", 0) or 0),
                "bcg": int(imm.get("bcg", 0) or 0),
                "opv0": int(imm.get("opv0", 0) or 0),
                "opv3": int(imm.get("opv_dpt3", 0) or 0),
                "measles": int(imm.get("measles", 0) or 0),
                "mal_cases": int(mal.get("malaria_cases", 0) or 0),
                "mal_trtd": int(mal.get("malaria_cases_treated", 0) or 0),
            }
        )
    write_shapefile(str(GIS_DIR / "KP_Districts"), "POLYGON", records, DISTRICT_FIELDS)


def write_facilities():
    records = []
    with open(PROCESSED / "facilities_merged.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            records.append(
                {
                    "geometry": Point(float(r["lon"]), float(r["lat"])),
                    "name": r["name"][:120],
                    "category": r["category"],
                    "pub_priv": r["public_private"],
                    "beds": int(r["beds"]) if r["beds"] else None,
                    "district": r["district"] or "",
                    "source": r["source"],
                    "geo_prec": r["geo_precision"],
                }
            )
    write_shapefile(str(GIS_DIR / "KP_Healthcare_Facilities"), "POINT", records, FACILITY_FIELDS)


def write_roads(province_geom):
    roads = json.loads((RAW / "osm_roads.json").read_text())
    records = []
    for r in roads:
        geom = LineString(r["coordinates"])
        clipped = geom.intersection(province_geom)
        if clipped.is_empty:
            continue
        parts = list(clipped.geoms) if clipped.geom_type == "MultiLineString" else [clipped]
        for part in parts:
            if part.geom_type != "LineString" or len(part.coords) < 2:
                continue
            records.append({"geometry": part, "road_cls": r["road_class"], "name": r["name"][:80]})
    write_shapefile(str(GIS_DIR / "KP_Roads"), "POLYLINE", records, ROAD_FIELDS)


def write_gap_scores(boundaries, district_metrics):
    metrics_by_name = {r["district"]: r for r in district_metrics}
    records = []
    for d in boundaries["districts"]:
        m = metrics_by_name.get(d["district"])
        if not m:
            continue
        records.append(
            {
                "geometry": shape(d["geometry"]),
                "district": d["district"],
                "gap_score": float(m["gap_score"]),
                "need_tier": m["need_tier"],
                "pop_2029": int(m["pop_2029"]),
                "pop_2031": int(m["pop_2031"]),
                "pop_2046": int(m["pop_2046"]),
                "fac_nd29": int(m["fac_nd29"]),
                "fac_nd31": int(m["fac_nd31"]),
                "fac_nd46": int(m["fac_nd46"]),
                "beds_nd29": int(m["beds_nd29"]),
                "beds_nd31": int(m["beds_nd31"]),
                "beds_nd46": int(m["beds_nd46"]),
            }
        )
    write_shapefile(str(GIS_DIR / "KP_District_Gap_Scores"), "POLYGON", records, GAP_FIELDS)


def write_suggested_sites():
    records = []
    with open(PROCESSED / "suggested_sites.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            records.append(
                {
                    "geometry": Point(float(r["lon"]), float(r["lat"])),
                    "district": r["district"],
                    "priority": int(r["priority"]),
                    "rationale": r["rationale"][:150],
                }
            )
    write_shapefile(str(GIS_DIR / "KP_Suggested_New_Sites"), "POINT", records, SITE_FIELDS)


def main():
    GIS_DIR.mkdir(parents=True, exist_ok=True)
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    with open(PROCESSED / "district_metrics.csv", newline="", encoding="utf-8") as f:
        district_metrics = list(csv.DictReader(f))

    province_geom = write_province(boundaries, district_metrics)
    write_districts(boundaries, district_metrics)
    write_facilities()
    write_roads(province_geom)
    write_gap_scores(boundaries, district_metrics)
    write_suggested_sites()
    print("Wrote all 6 shapefile layers to gis/")


if __name__ == "__main__":
    main()
