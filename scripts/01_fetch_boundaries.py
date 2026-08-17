"""Fetch KP province + district boundaries from HDX/OCHA Pakistan COD-AB
(primary), dissolve districts into a province polygon, and write
data/processed/boundaries.json.

Resolved via HDX's CKAN API (package_show for dataset "cod-ab-pak") rather
than a hardcoded direct-download URL, since HDX resource URLs embed
dataset/resource UUIDs that aren't stable across dataset updates. The
resource named "pak_admin_boundaries.geojson.zip" ships one GeoJSON file
per admin level; this script extracts pak_admin2.geojson (district level)
and filters to adm1_name == "Khyber Pakhtunkhwa".

If HDX is unreachable or its schema has changed beyond what
KP_DISTRICT_FIELD/KP_PROVINCE_FIELD expect, this raises with a clear
message rather than silently producing an empty/wrong boundary set — do
not catch that exception and fall back to fabricated geometry.
"""
import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path

from shapely.geometry import shape, mapping
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district
from scripts.lib.geo_utils import polygon_area_km2
from scripts.lib.http_utils import make_session

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

CKAN_PACKAGE_SHOW_URL = "https://data.humdata.org/api/3/action/package_show"
HDX_DATASET_ID = "cod-ab-pak"
GEOJSON_ZIP_RESOURCE_NAME = "pak_admin_boundaries.geojson.zip"
ADMIN2_MEMBER_NAME = "pak_admin2.geojson"

PROVINCE_FIELD = "adm1_name"
DISTRICT_FIELD = "adm2_name"
AREA_FIELD = "area_sqkm"
KP_NAMES = {"khyber pakhtunkhwa"}


def find_geojson_zip_url(session):
    resp = session.get(CKAN_PACKAGE_SHOW_URL, params={"id": HDX_DATASET_ID}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    resources = data["result"]["resources"]
    for r in resources:
        if r.get("name") == GEOJSON_ZIP_RESOURCE_NAME:
            return r["url"]
    raise RuntimeError(
        f"Could not find resource '{GEOJSON_ZIP_RESOURCE_NAME}' in HDX dataset "
        f"'{HDX_DATASET_ID}'. Available resources: {[r.get('name') for r in resources]}"
    )


def fetch_admin2_geojson(session):
    zip_url = find_geojson_zip_url(session)
    resp = session.get(zip_url, timeout=120)
    resp.raise_for_status()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        if ADMIN2_MEMBER_NAME not in zf.namelist():
            raise RuntimeError(
                f"'{ADMIN2_MEMBER_NAME}' not found in {zip_url}. Contents: {zf.namelist()}"
            )
        zf.extract(ADMIN2_MEMBER_NAME, RAW_DIR)
    return json.loads((RAW_DIR / ADMIN2_MEMBER_NAME).read_text(encoding="utf-8")), zip_url


def extract_kp_districts(geojson):
    features = geojson["features"]
    if not features or PROVINCE_FIELD not in features[0]["properties"] or DISTRICT_FIELD not in features[0]["properties"]:
        raise RuntimeError(
            f"Expected properties '{PROVINCE_FIELD}'/'{DISTRICT_FIELD}' not found in "
            f"{features[0]['properties'].keys() if features else 'no features'}"
        )
    kp_districts = []
    for feat in features:
        province_name = str(feat["properties"].get(PROVINCE_FIELD, "")).strip().lower()
        if province_name not in KP_NAMES:
            continue
        geom = shape(feat["geometry"])
        district_name = normalize_district(str(feat["properties"][DISTRICT_FIELD]))
        source_area = feat["properties"].get(AREA_FIELD)
        kp_districts.append(
            {"district": district_name, "division": None, "geometry": geom, "source_area_km2": source_area}
        )
    if not kp_districts:
        raise RuntimeError("No features matched a KP province name — check KP_NAMES/PROVINCE_FIELD.")
    return kp_districts


def main():
    session = make_session()
    geojson, source_url = fetch_admin2_geojson(session)
    kp_districts = extract_kp_districts(geojson)

    province_geom = unary_union([d["geometry"] for d in kp_districts])

    out = {
        "source": source_url,
        "districts": [
            {
                "district": d["district"],
                "division": d["division"],
                "geometry": mapping(d["geometry"]),
                "area_km2": round(d["source_area_km2"], 2)
                if d["source_area_km2"] is not None
                else round(polygon_area_km2(d["geometry"]), 2),
            }
            for d in kp_districts
        ],
        "province_geometry": mapping(province_geom),
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (PROCESSED_DIR / "boundaries.json").write_text(json.dumps(out))
    print(f"Wrote {len(kp_districts)} KP districts from {source_url}")


if __name__ == "__main__":
    main()
