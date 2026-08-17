# KP Healthcare System Planning — GIS + AI Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python pipeline that produces real KP province/district/healthcare-facility/road shapefiles, an AI-based facility-access gap analysis, a styled QGIS project, and a comprehensive HTML planning report.

**Architecture:** Numbered pipeline scripts under `scripts/`, each reading the previous stage's output from `data/raw/` or `data/processed/` and writing its own output, backed by a small shared `scripts/lib/` package of pure, unit-testable helpers (shapefile writing, geometry math, district name normalization, polite HTTP). Final artifacts land in `gis/` (shapefiles + `.qgz`), `report/` (HTML).

**Tech Stack:** Python 3.12, `shapely`, `pyshp`, `requests`, `beautifulsoup4`/`lxml`, `matplotlib`, `scikit-learn`, `pytest`. No geopandas/fiona/pyogrio.

## Global Constraints

- Do not use `geopandas`, `fiona`, or `pyogrio` anywhere — confirmed broken in this environment (numpy2/pyarrow ABI conflict). Use `shapely` + `pyshp` instead.
- All output shapefiles are WGS84 (EPSG:4326); area/distance math is done in a projected space via `scripts/lib/geo_utils.py`, never in raw degrees.
- DBF field names are hard-capped at 10 characters — every `field_defs` tuple in this plan already respects that; do not rename fields to something longer.
- Nominatim (OSM geocoder) usage policy requires ≥1 request/second and a descriptive `User-Agent` — always go through `scripts/lib/http_utils.rate_limited_get`.
- Every fetch/scrape stage caches its raw output under `data/raw/` so re-running the pipeline doesn't re-hit external services unless the cache is deleted.
- Because boundaries, population, and facility counts come from live external sources, "tests" for data-acquisition tasks are schema/sanity assertions (field presence, count-in-range, no-negative-numbers, sums roughly matching a cited published total) rather than exact hardcoded values — exact values aren't known until the real fetch runs. Pure-logic library code (shapefile writer, geometry math, name normalization, scoring formulas) gets real `pytest` unit tests with exact expected values.
- Report and QGIS project must state plainly that this is a planning aid from open/official data, not official government policy, and must disclose: straight-line accessibility is a proxy (no routing engine available), and facility geocoding precision varies (exact/street/district-centroid).

---

## File Structure

```
scripts/
  lib/
    __init__.py
    shp_writer.py        # pyshp-based shapefile writer (Task 1)
    geo_utils.py          # projected area/distance/point-in-polygon (Task 1)
    districts.py          # district name normalization (Task 2)
    http_utils.py          # polite session + rate-limited GET (Task 1)
  01_fetch_boundaries.py
  02_compile_population.py
  03_fetch_facilities_kphcc.py
  04_geocode_kphcc_facilities.py
  05_fetch_facilities_osm.py
  06_fetch_roads_osm.py
  07_merge_facilities.py
  08_compute_district_metrics.py
  09_gap_score_and_clusters.py
  10_forecast_demand.py
  11_suggest_new_sites.py
  12_write_shapefiles.py
  13_build_qgis_project.py
  load_and_style.py         # PyQGIS console fallback
  14_build_html_report.py
tests/
  lib/
    test_shp_writer.py
    test_geo_utils.py
    test_districts.py
  test_gap_scoring.py
  test_forecasting.py
  verify_boundaries.py
  verify_population.py
  verify_kphcc_facilities.py
  verify_merged_facilities.py
  verify_district_metrics.py
  verify_shapefiles.py
data/
  raw/
  processed/
gis/
report/
```

---

### Task 1: Shared geometry/shapefile/HTTP library

**Files:**
- Create: `scripts/lib/__init__.py` (empty)
- Create: `scripts/lib/shp_writer.py`
- Create: `scripts/lib/geo_utils.py`
- Create: `scripts/lib/http_utils.py`
- Test: `tests/lib/test_shp_writer.py`
- Test: `tests/lib/test_geo_utils.py`

**Interfaces:**
- Produces: `shp_writer.write_shapefile(path: str, geom_type: str, records: list[dict], field_defs: list[tuple]) -> None` — `geom_type` is one of `"POLYGON"`, `"POLYLINE"`, `"POINT"`; each record dict has key `"geometry"` (a `shapely` geometry) plus one key per field name in `field_defs`; `field_defs` entries are `(name: str, type_char: "C"|"N"|"F", size: int, decimal: int)`.
- Produces: `geo_utils.project_xy(lon: float, lat: float) -> tuple[float, float]`
- Produces: `geo_utils.to_projected(geom) -> shapely geometry` (equirectangular meters, centered on KP)
- Produces: `geo_utils.polygon_area_km2(geom) -> float`
- Produces: `geo_utils.haversine_km(lon1, lat1, lon2, lat2) -> float`
- Produces: `geo_utils.find_containing_district(lon, lat, districts: list[dict]) -> str` — `districts` items have `"district"` and `"geometry"` keys; returns nearest district name if the point falls outside all polygons (boundary simplification tolerance).
- Produces: `http_utils.make_session() -> requests.Session`
- Produces: `http_utils.rate_limited_get(session, url, params=None, min_interval=1.1, max_retries=3) -> requests.Response`

- [ ] **Step 1: Install pyshp**

Run: `pip install pyshp`
Expected: installs successfully (pure Python, no numpy dependency).

- [ ] **Step 2: Write failing tests for geo_utils**

```python
# tests/lib/test_geo_utils.py
import math
import pytest
from shapely.geometry import Polygon
from scripts.lib import geo_utils

def test_haversine_known_distance():
    # Peshawar (34.0151N, 71.5249E) to Islamabad (33.6844N, 73.0479E) ~ 141 km
    d = geo_utils.haversine_km(71.5249, 34.0151, 73.0479, 33.6844)
    assert 135 < d < 148

def test_haversine_zero_for_same_point():
    assert geo_utils.haversine_km(71.5, 34.0, 71.5, 34.0) == pytest.approx(0.0, abs=1e-6)

def test_polygon_area_km2_unit_square_degree_near_equator():
    # A ~1deg x 1deg box near the equator is roughly 111km x 111km => ~12321 km^2.
    # geo_utils centers its projection on KP's latitude, so use a box actually
    # near that latitude and check against the known equirectangular formula
    # rather than a literal 111x111 to keep the test self-consistent with the
    # implementation's chosen projection.
    box = Polygon([(71.0, 34.0), (72.0, 34.0), (72.0, 35.0), (71.0, 35.0)])
    area = geo_utils.polygon_area_km2(box)
    R = 6371.0
    expected_width_km = math.radians(1.0) * R * math.cos(math.radians(geo_utils.KP_LAT0))
    expected_height_km = math.radians(1.0) * R
    expected = expected_width_km * expected_height_km
    assert area == pytest.approx(expected, rel=0.02)

def test_find_containing_district_inside():
    d1 = {"district": "A", "geometry": Polygon([(0,0),(2,0),(2,2),(0,2)])}
    d2 = {"district": "B", "geometry": Polygon([(3,0),(5,0),(5,2),(3,2)])}
    assert geo_utils.find_containing_district(1, 1, [d1, d2]) == "A"

def test_find_containing_district_fallback_nearest():
    d1 = {"district": "A", "geometry": Polygon([(0,0),(2,0),(2,2),(0,2)])}
    d2 = {"district": "B", "geometry": Polygon([(10,10),(12,10),(12,12),(10,12)])}
    # point at (2.01, 1) is just outside A, well outside B -> nearest is A
    assert geo_utils.find_containing_district(2.01, 1, [d1, d2]) == "A"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/lib/test_geo_utils.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.lib.geo_utils'`

- [ ] **Step 4: Implement geo_utils.py**

```python
# scripts/lib/geo_utils.py
"""Geometry helpers: projected area/distance math and point-in-polygon
district assignment. All output shapefiles stay in WGS84 degrees; this
module is only used internally for area_km2 / distance_km computations,
never for the geometry that gets written to disk."""
import math
from shapely.geometry import Point
from shapely.ops import transform

# KP roughly spans 31.0-36.9N, 69.2-74.1E; center latitude for the
# equirectangular projection used in area/distance calculations below.
KP_LAT0 = 34.0
EARTH_RADIUS_KM = 6371.0


def project_xy(lon, lat):
    """Equirectangular projection to meters, centered near KP. Adequate for
    area/distance math over a single mid-latitude province; not for
    anything requiring true equal-area accuracy at continental scale."""
    R = EARTH_RADIUS_KM * 1000.0
    x = math.radians(lon) * R * math.cos(math.radians(KP_LAT0))
    y = math.radians(lat) * R
    return x, y


def to_projected(geom):
    return transform(lambda x, y, z=None: project_xy(x, y), geom)


def polygon_area_km2(geom):
    return to_projected(geom).area / 1_000_000.0


def haversine_km(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def find_containing_district(lon, lat, districts):
    """districts: list of {"district": str, "geometry": shapely Polygon/MultiPolygon}.
    Returns the containing district's name, or the nearest district's name
    (by centroid distance) if the point falls outside every polygon."""
    pt = Point(lon, lat)
    for d in districts:
        if d["geometry"].contains(pt):
            return d["district"]
    best, best_dist = None, float("inf")
    for d in districts:
        c = d["geometry"].centroid
        dist = haversine_km(lon, lat, c.x, c.y)
        if dist < best_dist:
            best, best_dist = d["district"], dist
    return best
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/lib/test_geo_utils.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Write failing test for shp_writer**

```python
# tests/lib/test_shp_writer.py
import shapefile  # pyshp
from shapely.geometry import Polygon, LineString, Point
from scripts.lib import shp_writer

def test_write_polygon_shapefile(tmp_path):
    path = str(tmp_path / "test_poly")
    square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    records = [{"geometry": square, "name": "Square", "count": 5}]
    field_defs = [("name", "C", 40, 0), ("count", "N", 6, 0)]
    shp_writer.write_shapefile(path, "POLYGON", records, field_defs)

    r = shapefile.Reader(path)
    assert len(r.shapes()) == 1
    assert r.shapes()[0].shapeType == shapefile.POLYGON
    rec = r.record(0)
    assert rec["name"] == "Square"
    assert rec["count"] == 5
    with open(path + ".prj") as f:
        assert "GCS_WGS_1984" in f.read()

def test_write_point_shapefile(tmp_path):
    path = str(tmp_path / "test_pts")
    records = [{"geometry": Point(71.5, 34.0), "name": "Facility A"}]
    field_defs = [("name", "C", 60, 0)]
    shp_writer.write_shapefile(path, "POINT", records, field_defs)
    r = shapefile.Reader(path)
    assert len(r.shapes()) == 1
    assert r.shapes()[0].points == [(71.5, 34.0)]

def test_write_line_shapefile(tmp_path):
    path = str(tmp_path / "test_lines")
    line = LineString([(71.0, 34.0), (71.5, 34.5)])
    records = [{"geometry": line, "name": "Road A"}]
    field_defs = [("name", "C", 40, 0)]
    shp_writer.write_shapefile(path, "POLYLINE", records, field_defs)
    r = shapefile.Reader(path)
    assert len(r.shapes()) == 1
    assert r.shapes()[0].shapeType == shapefile.POLYLINE
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/lib/test_shp_writer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.lib.shp_writer'`

- [ ] **Step 8: Implement shp_writer.py**

```python
# scripts/lib/shp_writer.py
"""Minimal shapefile writer built on pyshp for polygon/line/point layers.
pyshp requires exterior rings wound clockwise and holes counter-clockwise
(the Esri shapefile spec's winding rule) — shapely's default winding does
not guarantee this, so every polygon is re-oriented before writing."""
import shapefile  # pyshp
from shapely.geometry.polygon import orient

WGS84_PRJ = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)

_SHAPE_TYPES = {
    "POLYGON": shapefile.POLYGON,
    "POLYLINE": shapefile.POLYLINE,
    "POINT": shapefile.POINT,
}


def write_shapefile(path, geom_type, records, field_defs):
    """path: output path without extension, e.g. "gis/KP_Districts".
    geom_type: "POLYGON" | "POLYLINE" | "POINT".
    records: list of dict with a "geometry" key (shapely geometry) plus one
             key per field_defs name.
    field_defs: list of (name, type_char, size, decimal), e.g.
                [("district", "C", 50, 0), ("pop_2023", "N", 12, 0)].
    Writes path+".shp"/".shx"/".dbf"/".prj" (WGS84)."""
    shp_type = _SHAPE_TYPES[geom_type]
    with shapefile.Writer(path, shapeType=shp_type) as w:
        for name, typ, size, dec in field_defs:
            w.field(name, typ, size, dec)
        for rec in records:
            _write_geom(w, geom_type, rec["geometry"])
            w.record(*[rec.get(name) for name, *_ in field_defs])
    with open(path + ".prj", "w") as f:
        f.write(WGS84_PRJ)


def _write_geom(w, geom_type, geom):
    if geom_type == "POLYGON":
        polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        parts = []
        for poly in polys:
            poly = orient(poly, sign=-1.0)  # exterior CW, holes CCW
            parts.append(list(poly.exterior.coords))
            for interior in poly.interiors:
                parts.append(list(interior.coords))
        w.poly(parts)
    elif geom_type == "POLYLINE":
        lines = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        w.line([list(line.coords) for line in lines])
    elif geom_type == "POINT":
        w.point(geom.x, geom.y)
    else:
        raise ValueError(f"Unsupported geom_type: {geom_type}")
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/lib/test_shp_writer.py -v`
Expected: PASS (3 passed)

- [ ] **Step 10: Implement http_utils.py (no test — thin I/O wrapper, exercised by later stages' verify scripts)**

```python
# scripts/lib/http_utils.py
"""Polite HTTP helpers: a session with a descriptive User-Agent, and a
rate-limited GET for services with usage policies (e.g. OSM Nominatim
requires >=1 second between requests)."""
import time
import requests

USER_AGENT = "KP-Healthcare-GIS-Planning/1.0 (open-data planning research)"


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def rate_limited_get(session, url, params=None, min_interval=1.1, max_retries=3):
    last_call = getattr(rate_limited_get, "_last_call", 0.0)
    wait = min_interval - (time.time() - last_call)
    if wait > 0:
        time.sleep(wait)
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = session.get(url, params=params, timeout=30)
            rate_limited_get._last_call = time.time()
            if resp.status_code == 200:
                return resp
            last_exc = RuntimeError(f"HTTP {resp.status_code} for {url}")
        except requests.RequestException as exc:
            last_exc = exc
        time.sleep(2 ** attempt)
    rate_limited_get._last_call = time.time()
    raise RuntimeError(f"Failed to GET {url} after {max_retries} attempts") from last_exc
```

- [ ] **Step 11: Commit**

```bash
git add scripts/lib tests/lib
git commit -m "feat: add shared geo/shapefile/http library with unit tests

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: District name normalization

**Files:**
- Create: `scripts/lib/districts.py`
- Test: `tests/lib/test_districts.py`

**Interfaces:**
- Consumes: nothing (pure lookup table)
- Produces: `districts.normalize_district(name: str) -> str`

- [ ] **Step 1: Write failing tests**

```python
# tests/lib/test_districts.py
from scripts.lib import districts

def test_known_aliases_normalize():
    assert districts.normalize_district("Bajour") == "Bajaur"
    assert districts.normalize_district("Dir Lower") == "Lower Dir"
    assert districts.normalize_district("Lower Dir") == "Lower Dir"
    assert districts.normalize_district("Dir Upper") == "Upper Dir"
    assert districts.normalize_district("D.I. Khan") == "Dera Ismail Khan"
    assert districts.normalize_district("Waziristan North") == "North Waziristan"

def test_case_and_whitespace_insensitive():
    assert districts.normalize_district("  bajour  ") == "Bajaur"
    assert districts.normalize_district("DIR LOWER") == "Lower Dir"

def test_unknown_name_passthrough_stripped():
    assert districts.normalize_district("  Peshawar ") == "Peshawar"

def test_none_or_empty_passthrough():
    assert districts.normalize_district("") == ""
    assert districts.normalize_district(None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_districts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.lib.districts'`

- [ ] **Step 3: Implement districts.py**

```python
# scripts/lib/districts.py
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
    "dera ismail khan": "Dera Ismail Khan",
    "waziristan north": "North Waziristan",
    "north waziristan": "North Waziristan",
    "waziristan south": "South Waziristan",
    "south waziristan": "South Waziristan",
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_districts.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/districts.py tests/lib/test_districts.py
git commit -m "feat: add KP district name normalization

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Fetch KP administrative boundaries

**Files:**
- Create: `scripts/01_fetch_boundaries.py`
- Test: `tests/verify_boundaries.py`

**Interfaces:**
- Consumes: `scripts.lib.districts.normalize_district`, `scripts.lib.geo_utils.polygon_area_km2`
- Produces: `data/processed/boundaries.json` with shape:
  ```json
  {
    "source": "<dataset name/url actually used>",
    "districts": [
      {"district": "Peshawar", "division": "Peshawar", "geometry": {"type": "Polygon", "coordinates": [...]}}
    ],
    "province_geometry": {"type": "MultiPolygon", "coordinates": [...]}
  }
  ```
  (`geometry` values are GeoJSON, loadable via `shapely.geometry.shape`.)

- [ ] **Step 1: Write the fetch/parse script**

```python
# scripts/01_fetch_boundaries.py
"""Fetch KP province + district boundaries from HDX/OCHA Pakistan COD-AB
(primary) or GADM level-2 (fallback), dissolve districts into a province
polygon, and write data/processed/boundaries.json.

HDX dataset pages change their direct-download URL periodically; this
script tries a short list of known-good COD-AB/GADM endpoints in order and
uses the first one that returns valid, parseable admin-2 polygons for
Pakistan with a "KP"/"Khyber Pakhtunkhwa" province field. If every
candidate URL fails, it raises with a clear message rather than silently
producing an empty/wrong boundary set — do not catch that exception and
fall back to fabricated geometry.
"""
import json
import sys
from pathlib import Path

from shapely.geometry import shape, mapping
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district
from scripts.lib.geo_utils import polygon_area_km2
from scripts.lib.http_utils import make_session

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

# Tried in order; each must be a GeoJSON FeatureCollection of Pakistan
# admin-2 (district) polygons with a province/state name field.
CANDIDATE_SOURCES = [
    # HDX Pakistan COD-AB admin-2 GeoJSON (OCHA ROAP)
    "https://data.humdata.org/dataset/cod-ab-pak/resource/download/pak_admbnda_adm2_ocha_pco_gaul_20220106.geojson",
    # GADM Pakistan level-2 GeoJSON fallback
    "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_PAK_2.json.zip",
]

PROVINCE_FIELD_CANDIDATES = ["ADM1_EN", "NAME_1", "province", "PROVINCE"]
DISTRICT_FIELD_CANDIDATES = ["ADM2_EN", "NAME_2", "district", "DISTRICT"]
KP_NAMES = {"khyber pakhtunkhwa", "kp", "nwfp"}


def _find_field(props, candidates):
    for c in candidates:
        if c in props:
            return c
    return None


def fetch_geojson(session):
    last_exc = None
    for url in CANDIDATE_SOURCES:
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            if url.endswith(".zip"):
                # GADM fallback ships as a zipped GeoJSON; skip zip handling
                # here and let a later attempt/manual download supply it if
                # this path is ever actually needed.
                continue
            data = resp.json()
            if data.get("type") == "FeatureCollection" and data.get("features"):
                RAW_DIR.mkdir(parents=True, exist_ok=True)
                (RAW_DIR / "pk_admin2_boundaries.geojson").write_text(
                    json.dumps(data)
                )
                return data, url
        except Exception as exc:  # noqa: BLE001 - trying multiple sources deliberately
            last_exc = exc
            continue
    raise RuntimeError(
        "Could not fetch a usable Pakistan admin-2 boundary GeoJSON from any "
        f"candidate source: {CANDIDATE_SOURCES}. Last error: {last_exc}"
    )


def extract_kp_districts(geojson):
    features = geojson["features"]
    province_field = _find_field(features[0]["properties"], PROVINCE_FIELD_CANDIDATES)
    district_field = _find_field(features[0]["properties"], DISTRICT_FIELD_CANDIDATES)
    if not province_field or not district_field:
        raise RuntimeError(
            f"Could not find province/district property names in {features[0]['properties'].keys()}"
        )
    kp_districts = []
    for feat in features:
        province_name = str(feat["properties"].get(province_field, "")).strip().lower()
        if province_name not in KP_NAMES:
            continue
        geom = shape(feat["geometry"])
        district_name = normalize_district(str(feat["properties"][district_field]))
        kp_districts.append({"district": district_name, "division": None, "geometry": geom})
    if not kp_districts:
        raise RuntimeError("No features matched a KP province name — check KP_NAMES/province_field.")
    return kp_districts


def main():
    session = make_session()
    geojson, source_url = fetch_geojson(session)
    kp_districts = extract_kp_districts(geojson)

    province_geom = unary_union([d["geometry"] for d in kp_districts])

    out = {
        "source": source_url,
        "districts": [
            {
                "district": d["district"],
                "division": d["division"],
                "geometry": mapping(d["geometry"]),
                "area_km2": round(polygon_area_km2(d["geometry"]), 2),
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
```

- [ ] **Step 2: Run the script against the real internet**

Run: `python scripts/01_fetch_boundaries.py`
Expected: prints `Wrote N KP districts from <url>` with N in the 25-40 range. If both candidate sources fail (site restructure, URL rot), update `CANDIDATE_SOURCES` with a currently-working HDX/GADM download URL found via web search before proceeding — do not hand-draw placeholder geometry.

- [ ] **Step 3: Write verification checks**

```python
# tests/verify_boundaries.py
"""Sanity checks on the fetched boundary data. Run after
scripts/01_fetch_boundaries.py — not a unit test, since the exact district
count/geometry depends on the live external dataset fetched."""
import json
from pathlib import Path
from shapely.geometry import shape

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed" / "boundaries.json"


def main():
    data = json.loads(PROCESSED.read_text())
    districts = data["districts"]
    assert 25 <= len(districts) <= 45, f"Unexpected district count: {len(districts)}"

    names = [d["district"] for d in districts]
    assert len(names) == len(set(names)), f"Duplicate district names after normalization: {names}"

    for d in districts:
        geom = shape(d["geometry"])
        assert geom.is_valid, f"Invalid geometry for {d['district']}"
        assert d["area_km2"] > 0, f"Non-positive area for {d['district']}"

    total_area = sum(d["area_km2"] for d in districts)
    # KP's total area (incl. merged tribal districts) is ~101,741 km^2 published
    # figure; allow a wide tolerance since simplified/clipped boundary datasets
    # vary.
    assert 60000 <= total_area <= 140000, f"Total KP area implausible: {total_area}"

    province_geom = shape(data["province_geometry"])
    assert province_geom.is_valid, "Province dissolve geometry invalid"

    print(f"OK: {len(districts)} districts, total area {total_area:.0f} km^2")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run verification**

Run: `python tests/verify_boundaries.py`
Expected: `OK: N districts, total area NNNNN km^2` with no `AssertionError`. If the area assertion fails, inspect whether the fetched dataset includes/excludes the merged tribal districts and adjust the tolerance with a comment explaining why, rather than silently loosening it without explanation.

- [ ] **Step 5: Commit**

```bash
git add scripts/01_fetch_boundaries.py tests/verify_boundaries.py data/processed/boundaries.json data/raw/pk_admin2_boundaries.geojson
git commit -m "feat: fetch and validate KP province/district boundaries

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Compile KP district population (PBS 2023 census)

**Files:**
- Create: `scripts/02_compile_population.py`
- Test: `tests/verify_population.py`

**Interfaces:**
- Consumes: `data/processed/boundaries.json` (district list to match against)
- Produces: `data/processed/kp_district_population_2023.csv` with columns:
  `district,division,population_2023,population_prior,prior_census_year,growth_rate_pct,source_url`

- [ ] **Step 1: Research and write the population CSV**

This task is primarily data compilation, not algorithmic code. The
implementer must:
1. Use web search to find the PBS 2023 Digital Census (7th census)
   district-wise population results for KP (PBS press releases / official
   tables, or a secondary source that explicitly cites PBS 2023 figures per
   district — record whichever URL was actually used per row).
2. For `population_prior`, use the district's 1998 or 2017 census figure if
   findable per-district; otherwise leave blank for that row and note it —
   do not fabricate a number.
3. Compute `growth_rate_pct` as a compound annual growth rate between
   `prior_census_year` and 2023 where both figures exist:
   `((population_2023 / population_prior) ** (1 / (2023 - prior_census_year)) - 1) * 100`.
   Where `population_prior` is missing, leave `growth_rate_pct` blank — the
   forecasting task (Task 10) falls back to the KP provincial average growth
   rate for any row with a blank value.
4. Write one row per district name exactly matching (after
   `scripts.lib.districts.normalize_district`) the district list in
   `data/processed/boundaries.json` — reconcile any mismatch by adding the
   correct alias to `scripts/lib/districts.py` (Task 2) rather than editing
   the CSV to a different spelling.

```python
# scripts/02_compile_population.py
"""Load boundaries.json's district list and confirm the population CSV
(hand-compiled from PBS 2023 census sources per the process in Step 1)
covers every district by canonical name. Run this after the CSV has been
written/updated."""
import csv
import json
from pathlib import Path

from scripts.lib.districts import normalize_district

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
BOUNDARIES = PROCESSED / "boundaries.json"
POPULATION_CSV = PROCESSED / "kp_district_population_2023.csv"


def load_boundary_district_names():
    data = json.loads(BOUNDARIES.read_text())
    return {d["district"] for d in data["districts"]}


def load_population_district_names():
    with open(POPULATION_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    names = {normalize_district(r["district"]) for r in rows}
    return rows, names


def main():
    boundary_names = load_boundary_district_names()
    rows, population_names = load_population_district_names()

    missing = boundary_names - population_names
    extra = population_names - boundary_names
    if missing:
        raise RuntimeError(f"Population CSV missing districts: {sorted(missing)}")
    if extra:
        raise RuntimeError(f"Population CSV has districts not in boundaries: {sorted(extra)}")

    for r in rows:
        pop = int(r["population_2023"])
        assert pop > 0, f"Non-positive population for {r['district']}"
        assert r["source_url"], f"Missing source_url for {r['district']}"

    print(f"OK: population data covers all {len(boundary_names)} boundary districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it to verify it currently fails (CSV not yet written)**

Run: `python scripts/02_compile_population.py`
Expected: `FileNotFoundError` for `kp_district_population_2023.csv` — confirms the check runs before the data exists.

- [ ] **Step 3: Write the CSV** (per the research process in Step 1), save to `data/processed/kp_district_population_2023.csv`.

- [ ] **Step 4: Run the script again to verify it passes**

Run: `python scripts/02_compile_population.py`
Expected: `OK: population data covers all N boundary districts`. If it raises on missing/extra districts, reconcile via `scripts/lib/districts.py` aliases (preferred) or a CSV correction, then re-run.

- [ ] **Step 5: Write and run the sum-sanity check**

```python
# tests/verify_population.py
"""Cross-check the compiled population CSV against the officially published
KP provincial total (independent of the per-district research in Task 4),
to catch gross transcription errors."""
import csv
from pathlib import Path

POPULATION_CSV = Path(__file__).resolve().parent.parent / "data" / "processed" / "kp_district_population_2023.csv"

# KP's published 2023 Digital Census provincial total population. Update
# this constant (with a comment citing the source) if the implementer finds
# a differently-scoped official figure (e.g. with/without merged tribal
# districts) during Task 4 research.
KP_PROVINCIAL_TOTAL_2023 = 40_856_097  # PBS 2023 Digital Census, KP province total

def main():
    with open(POPULATION_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    total = sum(int(r["population_2023"]) for r in rows)
    diff_pct = abs(total - KP_PROVINCIAL_TOTAL_2023) / KP_PROVINCIAL_TOTAL_2023 * 100
    assert diff_pct < 5, (
        f"Summed district populations ({total}) differ from the published "
        f"provincial total ({KP_PROVINCIAL_TOTAL_2023}) by {diff_pct:.1f}% "
        "- check for a missed/duplicated district or a transcription error."
    )
    print(f"OK: district sum {total} within {diff_pct:.1f}% of provincial total")


if __name__ == "__main__":
    main()
```

Run: `python tests/verify_population.py`
Expected: `OK: district sum ... within N% of provincial total`. If the constant `KP_PROVINCIAL_TOTAL_2023` looks wrong once real search results are in hand, update it in this file with a source comment — never adjust the tolerance instead of fixing the constant.

- [ ] **Step 6: Commit**

```bash
git add scripts/02_compile_population.py tests/verify_population.py data/processed/kp_district_population_2023.csv
git commit -m "feat: compile and validate KP 2023 census district population

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Scrape KPHCC licensed facilities registry

**Files:**
- Create: `scripts/03_fetch_facilities_kphcc.py`
- Test: `tests/verify_kphcc_facilities.py`

**Interfaces:**
- Consumes: `scripts.lib.http_utils.make_session`, `scripts.lib.districts.normalize_district`
- Produces: `data/raw/kphcc_facilities.json`, a list of dicts:
  `{"licence_no", "issue_date", "expire_date", "category", "public_private", "name", "address", "district", "beds"}`
  (`beds` is `int` or `None`)

- [ ] **Step 1: Write the scraper**

```python
# scripts/03_fetch_facilities_kphcc.py
"""Scrape the KP Health Care Commission's public 'Licensed Health Care
Establishment' registry (https://hcc.kp.gov.pk/licensed-hces/), which is
plain server-rendered HTML paginated via a `?page=N` query param (confirmed
during design: ~28 pages / ~280 records, no JS/API needed). Writes
data/raw/kphcc_facilities.json.

Note: as of the design pass, this registry has zero entries for several
newly-merged tribal districts (Bannu, D.I. Khan, Kurram, Waziristan,
Orakzai, Tank, etc.) — that's a genuine coverage gap in KP's licensing
rollout, not a scraper bug. Do not treat an empty result for those
districts as an error.
"""
import json
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.lib.districts import normalize_district
from scripts.lib.http_utils import make_session

BASE_URL = "https://hcc.kp.gov.pk/licensed-hces/"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
REQUEST_DELAY_SECONDS = 1.0


def parse_beds(text):
    text = text.strip()
    if not text or not text.isdigit():
        return None
    return int(text)


def parse_table(html):
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("table tr")
    records = []
    for row in rows:
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) != 9:
            continue  # header row or malformed row
        licence_no, issue_date, expire_date, category, pub_priv, name, address, district, beds = cells
        records.append(
            {
                "licence_no": licence_no,
                "issue_date": issue_date,
                "expire_date": expire_date,
                "category": category,
                "public_private": pub_priv,
                "name": name,
                "address": address,
                "district": normalize_district(district),
                "beds": parse_beds(beds),
            }
        )
    return records


def get_total_pages(html):
    soup = BeautifulSoup(html, "lxml")
    page_links = soup.select("a, button")
    numbers = [int(a.get_text(strip=True)) for a in page_links if a.get_text(strip=True).isdigit()]
    return max(numbers) if numbers else 1


def fetch_all():
    session = make_session()
    resp = session.get(BASE_URL, params={"search": "", "district": "", "category": "", "date": ""}, timeout=30)
    resp.raise_for_status()
    total_pages = get_total_pages(resp.text)
    all_records = parse_table(resp.text)

    for page in range(2, total_pages + 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        resp = session.get(BASE_URL, params={"page": page}, timeout=30)
        resp.raise_for_status()
        all_records.extend(parse_table(resp.text))

    return all_records


def main():
    records = fetch_all()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "kphcc_facilities.json").write_text(json.dumps(records, indent=2))
    print(f"Wrote {len(records)} KPHCC facility records")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the real site**

Run: `python scripts/03_fetch_facilities_kphcc.py`
Expected: `Wrote N KPHCC facility records` with N roughly in the 200-400 range (280 confirmed during design; the live site may have grown/shrunk since). If `get_total_pages` returns 1 unexpectedly, inspect the page's pagination markup (`tests/verify_kphcc_facilities.py` in the next step will catch a too-low count either way).

- [ ] **Step 3: Write and run verification**

```python
# tests/verify_kphcc_facilities.py
import json
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "kphcc_facilities.json"
REQUIRED_KEYS = {"licence_no", "issue_date", "expire_date", "category", "public_private", "name", "address", "district", "beds"}


def main():
    records = json.loads(RAW.read_text())
    assert len(records) >= 100, f"Suspiciously few KPHCC records: {len(records)}"

    for r in records:
        assert REQUIRED_KEYS.issubset(r.keys()), f"Missing keys in record: {r}"
        assert r["name"], f"Empty name in record: {r}"
        assert r["district"], f"Empty district in record: {r}"
        if r["beds"] is not None:
            assert r["beds"] >= 0, f"Negative bed count: {r}"

    districts = {r["district"] for r in records}
    print(f"OK: {len(records)} KPHCC records across {len(districts)} districts")


if __name__ == "__main__":
    main()
```

Run: `python tests/verify_kphcc_facilities.py`
Expected: `OK: N KPHCC records across M districts`

- [ ] **Step 4: Commit**

```bash
git add scripts/03_fetch_facilities_kphcc.py tests/verify_kphcc_facilities.py data/raw/kphcc_facilities.json
git commit -m "feat: scrape and validate KPHCC licensed facilities registry

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Geocode KPHCC facility addresses

**Files:**
- Create: `scripts/04_geocode_kphcc_facilities.py`

**Interfaces:**
- Consumes: `data/raw/kphcc_facilities.json`, `data/processed/boundaries.json`, `scripts.lib.http_utils.{make_session,rate_limited_get}`, `scripts.lib.geo_utils.find_containing_district`
- Produces: `data/processed/kphcc_facilities_geocoded.json`, same records as input plus `lat`, `lon`, `geo_precision` (`"street"` for a Nominatim match, `"district_centroid"` for a fallback).

- [ ] **Step 1: Write the geocoder**

```python
# scripts/04_geocode_kphcc_facilities.py
"""Geocode each KPHCC facility's free-text address via OSM Nominatim
(free, rate-limited to >=1 req/sec per its usage policy — enforced by
scripts.lib.http_utils.rate_limited_get). Falls back to the facility's
district centroid (from boundaries.json) when Nominatim finds no match,
flagging geo_precision accordingly so downstream consumers know which
points are approximate."""
import json
from pathlib import Path

from shapely.geometry import shape

from scripts.lib.geo_utils import find_containing_district
from scripts.lib.http_utils import make_session, rate_limited_get

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "kphcc_facilities.json"
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

    for rec in records:
        coords = None
        try:
            coords = geocode_address(session, rec["address"], rec["district"])
        except RuntimeError:
            coords = None
        if coords:
            rec["lon"], rec["lat"] = coords
            rec["geo_precision"] = "street"
        else:
            fallback = centroids.get(rec["district"])
            if fallback is None:
                rec["lon"], rec["lat"], rec["geo_precision"] = None, None, "unresolved"
            else:
                rec["lon"], rec["lat"] = fallback
                rec["geo_precision"] = "district_centroid"

    PROCESSED.mkdir(parents=True, exist_ok=True)
    (PROCESSED / "kphcc_facilities_geocoded.json").write_text(json.dumps(records, indent=2))
    resolved = sum(1 for r in records if r["geo_precision"] != "unresolved")
    print(f"Geocoded {resolved}/{len(records)} KPHCC facilities")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it (this takes several minutes — ~1 request/second across all records)**

Run: `python scripts/04_geocode_kphcc_facilities.py`
Expected: `Geocoded N/M KPHCC facilities` with N/M > 0.9 (a handful of `unresolved` — districts with no boundary entry, none expected — is acceptable; if `unresolved` is common, check that district names in `kphcc_facilities.json` match `boundaries.json` after normalization).

- [ ] **Step 3: Verify**

```python
# add to tests/verify_kphcc_facilities.py, or run inline:
python -c "
import json
records = json.loads(open('data/processed/kphcc_facilities_geocoded.json').read())
assert len(records) > 0
for r in records:
    assert r['geo_precision'] in ('street', 'district_centroid', 'unresolved')
    if r['geo_precision'] != 'unresolved':
        assert -75 < r['lon'] < 76 and 30 < r['lat'] < 38, f\"Coordinate out of Pakistan bounding box: {r}\"
resolved = sum(1 for r in records if r['geo_precision'] != 'unresolved')
print(f'OK: {resolved}/{len(records)} resolved, coords within Pakistan bounds')
"
```

Expected: `OK: N/M resolved, coords within Pakistan bounds`

- [ ] **Step 4: Commit**

```bash
git add scripts/04_geocode_kphcc_facilities.py data/processed/kphcc_facilities_geocoded.json
git commit -m "feat: geocode KPHCC facility addresses via Nominatim with district-centroid fallback

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Fetch OSM healthcare facilities

**Files:**
- Create: `scripts/05_fetch_facilities_osm.py`

**Interfaces:**
- Consumes: `data/processed/boundaries.json` (province geometry, for the Overpass area filter)
- Produces: `data/raw/osm_facilities.json`, a list of dicts:
  `{"name", "category", "lat", "lon", "osm_id", "osm_type"}`

- [ ] **Step 1: Write the Overpass fetch**

```python
# scripts/05_fetch_facilities_osm.py
"""Fetch OpenStreetMap healthcare facility points (hospitals, clinics,
doctors, pharmacies) within KP via the Overpass API, as a supplemental
source to fill gaps in the KPHCC registry (which has no entries for
several tribal districts and skips government facilities that don't need
KPHCC licensing)."""
import json
from pathlib import Path

from scripts.lib.http_utils import make_session

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

# KP's bounding box (south, west, north, east) — a coarse pre-filter;
# Overpass itself has no simple "inside this custom polygon" filter without
# an uploaded area, so results are clipped to the real KP polygon in
# scripts/07_merge_facilities.py using boundaries.json.
KP_BBOX = (31.0, 69.2, 36.9, 74.1)

QUERY_TEMPLATE = """
[out:json][timeout:120];
(
  node["amenity"="hospital"]({bbox});
  node["amenity"="clinic"]({bbox});
  node["amenity"="doctors"]({bbox});
  node["amenity"="pharmacy"]({bbox});
  node["healthcare"]({bbox});
);
out center;
"""


def fetch():
    session = make_session()
    bbox_str = ",".join(str(v) for v in KP_BBOX)
    query = QUERY_TEMPLATE.format(bbox=bbox_str)
    resp = session.post(OVERPASS_URL, data={"data": query}, timeout=180)
    resp.raise_for_status()
    return resp.json()


CATEGORY_TAGS = {
    "hospital": "Hospital",
    "clinic": "Clinic",
    "doctors": "Clinic",
    "pharmacy": "Pharmacy",
}


def parse_elements(data):
    records = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        amenity = tags.get("amenity")
        category = CATEGORY_TAGS.get(amenity, tags.get("healthcare", "Facility").title())
        records.append(
            {
                "name": name,
                "category": category,
                "lat": el["lat"],
                "lon": el["lon"],
                "osm_id": el["id"],
                "osm_type": el["type"],
            }
        )
    return records


def main():
    data = fetch()
    records = parse_elements(data)
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "osm_facilities.json").write_text(json.dumps(records, indent=2))
    print(f"Wrote {len(records)} OSM facility records")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `python scripts/05_fetch_facilities_osm.py`
Expected: `Wrote N OSM facility records`, N in the low hundreds to low thousands (coverage is uneven — dense in Peshawar/Abbottabad, sparse elsewhere, as flagged during design). If the Overpass public instance times out or rate-limits, retry once after a short wait; if it persistently fails, note the outage in the run log rather than silently writing an empty file.

- [ ] **Step 3: Verify**

Run:
```bash
python -c "
import json
records = json.loads(open('data/raw/osm_facilities.json').read())
assert len(records) > 20, f'Suspiciously few OSM records: {len(records)}'
for r in records:
    assert -75 < r['lon'] < 76 and 30 < r['lat'] < 38
    assert r['name']
print(f'OK: {len(records)} OSM facility records, coords in bounds')
"
```
Expected: `OK: N OSM facility records, coords in bounds`

- [ ] **Step 4: Commit**

```bash
git add scripts/05_fetch_facilities_osm.py data/raw/osm_facilities.json
git commit -m "feat: fetch OSM healthcare facility points for KP

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Fetch OSM major roads

**Files:**
- Create: `scripts/06_fetch_roads_osm.py`

**Interfaces:**
- Consumes: nothing beyond the KP bounding box constant (duplicated from Task 7 for script independence)
- Produces: `data/raw/osm_roads.json`, a list of dicts: `{"name", "road_class", "coordinates": [[lon, lat], ...], "osm_id"}`

- [ ] **Step 1: Write the fetch**

```python
# scripts/06_fetch_roads_osm.py
"""Fetch major OSM roads (motorway/trunk/primary/secondary) within KP's
bounding box via Overpass, used as an accessibility-proxy layer and for
the HTML report's context maps. Full routing/travel-time is out of scope
(no routing engine available) — straight-line distance is the documented
accessibility proxy used in scripts/08_compute_district_metrics.py."""
import json
from pathlib import Path

from scripts.lib.http_utils import make_session

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
KP_BBOX = (31.0, 69.2, 36.9, 74.1)

QUERY_TEMPLATE = """
[out:json][timeout:180];
(
  way["highway"="motorway"]({bbox});
  way["highway"="trunk"]({bbox});
  way["highway"="primary"]({bbox});
  way["highway"="secondary"]({bbox});
);
out geom;
"""


def fetch():
    session = make_session()
    bbox_str = ",".join(str(v) for v in KP_BBOX)
    query = QUERY_TEMPLATE.format(bbox=bbox_str)
    resp = session.post(OVERPASS_URL, data={"data": query}, timeout=240)
    resp.raise_for_status()
    return resp.json()


def parse_elements(data):
    records = []
    for el in data.get("elements", []):
        if el["type"] != "way" or "geometry" not in el:
            continue
        tags = el.get("tags", {})
        coords = [[pt["lon"], pt["lat"]] for pt in el["geometry"]]
        if len(coords) < 2:
            continue
        records.append(
            {
                "name": tags.get("name", ""),
                "road_class": tags.get("highway", "unknown"),
                "coordinates": coords,
                "osm_id": el["id"],
            }
        )
    return records


def main():
    data = fetch()
    records = parse_elements(data)
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "osm_roads.json").write_text(json.dumps(records))
    print(f"Wrote {len(records)} OSM road segments")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `python scripts/06_fetch_roads_osm.py`
Expected: `Wrote N OSM road segments`, N at least in the low hundreds.

- [ ] **Step 3: Verify**

Run:
```bash
python -c "
import json
records = json.loads(open('data/raw/osm_roads.json').read())
assert len(records) > 20, f'Suspiciously few road segments: {len(records)}'
for r in records:
    assert len(r['coordinates']) >= 2
    for lon, lat in r['coordinates']:
        assert -75 < lon < 76 and 30 < lat < 38
print(f'OK: {len(records)} road segments, coords in bounds')
"
```
Expected: `OK: N road segments, coords in bounds`

- [ ] **Step 4: Commit**

```bash
git add scripts/06_fetch_roads_osm.py data/raw/osm_roads.json
git commit -m "feat: fetch OSM major road network for KP

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Merge and deduplicate facilities

**Files:**
- Create: `scripts/07_merge_facilities.py`
- Test: `tests/test_merge_facilities.py`
- Test: `tests/verify_merged_facilities.py`

**Interfaces:**
- Consumes: `data/processed/kphcc_facilities_geocoded.json`, `data/raw/osm_facilities.json`, `data/processed/boundaries.json`, `scripts.lib.geo_utils.{haversine_km,find_containing_district}`, `scripts.lib.districts.normalize_district`
- Produces:
  - `scripts/07_merge_facilities.dedup_key(name: str) -> str` (importable, unit-tested)
  - `scripts/07_merge_facilities.merge(kphcc: list[dict], osm: list[dict], districts: list[dict]) -> list[dict]` (importable, unit-tested) — output records: `{name, category, public_private, beds, district, lat, lon, source, geo_precision, is_duplicate_of}` (`is_duplicate_of` is `None` or another record's `name`)
  - `data/processed/facilities_merged.csv`

- [ ] **Step 1: Write failing tests for the merge/dedup logic**

```python
# tests/test_merge_facilities.py
import importlib
merge_mod = importlib.import_module("scripts.07_merge_facilities")
# scripts/07_merge_facilities.py starts with a digit, so it can't be
# imported with a normal dotted import; see Step 3's importlib.import_module
# workaround note.

def test_dedup_key_normalizes_case_and_punctuation():
    assert merge_mod.dedup_key("Dr. Shahid Masroor Clinic") == merge_mod.dedup_key("dr shahid masroor clinic")

def test_merge_flags_close_same_name_as_duplicate():
    districts = [{"district": "Abbottabad", "geometry": __import__("shapely.geometry", fromlist=["Polygon"]).Polygon(
        [(73.0, 34.0), (73.5, 34.0), (73.5, 34.5), (73.0, 34.5)]
    )}]
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
    districts = [{"district": "Abbottabad", "geometry": __import__("shapely.geometry", fromlist=["Polygon"]).Polygon(
        [(73.0, 34.0), (73.5, 34.0), (73.5, 34.5), (73.0, 34.5)]
    )}]
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_merge_facilities.py -v`
Expected: FAIL with `ModuleNotFoundError` (module doesn't exist yet).

- [ ] **Step 3: Implement the merge script**

```python
# scripts/07_merge_facilities.py
"""Merge KPHCC (official, geocoded) and OSM facility points into one
deduplicated table. A record is flagged (not dropped) as a likely duplicate
of another when they share a normalized name and are within ~500m of each
other — the KPHCC record is kept as primary in that case since it's the
official source, and the OSM record's `is_duplicate_of` is set to the
KPHCC record's name so both stay auditable in the output."""
import csv
import json
import re
from pathlib import Path

from shapely.geometry import shape

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


def merge(kphcc, osm, districts):
    records = []
    for r in kphcc:
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


def main():
    kphcc = json.loads((PROCESSED / "kphcc_facilities_geocoded.json").read_text())
    osm = json.loads((RAW / "osm_facilities.json").read_text())
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    districts = [{"district": d["district"], "geometry": shape(d["geometry"])} for d in boundaries["districts"]]

    merged = merge(kphcc, osm, districts)

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_merge_facilities.py -v`
Expected: PASS (3 passed). Note: because the filename starts with a digit, `tests/test_merge_facilities.py` must import it via `importlib.import_module("scripts.07_merge_facilities")` as shown in Step 1 — a plain `import scripts.07_merge_facilities` is a `SyntaxError` in Python.

- [ ] **Step 5: Run the real merge and verify output**

Run: `python scripts/07_merge_facilities.py`
Expected: `Wrote N merged facility records (M flagged as likely duplicates)`

```python
# tests/verify_merged_facilities.py
import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "facilities_merged.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 100, f"Suspiciously few merged facilities: {len(rows)}"
    sources = {r["source"] for r in rows}
    assert sources == {"KPHCC", "OSM"}, f"Unexpected sources: {sources}"
    for r in rows:
        assert r["name"], f"Empty name: {r}"
        lat, lon = float(r["lat"]), float(r["lon"])
        assert -75 < lon < 76 and 30 < lat < 38, f"Coordinate out of bounds: {r}"
    print(f"OK: {len(rows)} merged facilities from sources {sources}")


if __name__ == "__main__":
    main()
```

Run: `python tests/verify_merged_facilities.py`
Expected: `OK: N merged facilities from sources {'KPHCC', 'OSM'}`

- [ ] **Step 6: Commit**

```bash
git add scripts/07_merge_facilities.py tests/test_merge_facilities.py tests/verify_merged_facilities.py data/processed/facilities_merged.csv
git commit -m "feat: merge and deduplicate KPHCC + OSM facility data

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: Compute per-district metrics

**Files:**
- Create: `scripts/08_compute_district_metrics.py`
- Test: `tests/test_district_metrics.py`
- Test: `tests/verify_district_metrics.py`

**Interfaces:**
- Consumes: `data/processed/boundaries.json`, `data/processed/kp_district_population_2023.csv`, `data/processed/facilities_merged.csv`, `scripts.lib.geo_utils.{polygon_area_km2,haversine_km}`
- Produces:
  - `scripts/08_compute_district_metrics.classify_terrain(district_name: str) -> str` (`"mountainous"` or `"plains"`, importable/unit-tested)
  - `scripts/08_compute_district_metrics.nearest_facility_km(centroid_lon, centroid_lat, facilities: list[dict]) -> float` (importable/unit-tested)
  - `data/processed/district_metrics.csv` with columns: `district,division,area_km2,population_2023,pop_density,terrain,facility_count,beds_per_1000,accessibility_km`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_district_metrics.py
import importlib
metrics_mod = importlib.import_module("scripts.08_compute_district_metrics")

def test_classify_terrain_known_mountainous_district():
    assert metrics_mod.classify_terrain("Chitral") == "mountainous"
    assert metrics_mod.classify_terrain("Upper Chitral") == "mountainous"
    assert metrics_mod.classify_terrain("Swat") == "mountainous"

def test_classify_terrain_known_plains_district():
    assert metrics_mod.classify_terrain("Peshawar") == "plains"
    assert metrics_mod.classify_terrain("Mardan") == "plains"

def test_classify_terrain_unknown_defaults_to_plains():
    assert metrics_mod.classify_terrain("Some New District") == "plains"

def test_nearest_facility_km_finds_closest():
    facilities = [
        {"lon": 71.0, "lat": 34.0},
        {"lon": 71.5, "lat": 34.5},
    ]
    # centroid closer to the second facility
    d = metrics_mod.nearest_facility_km(71.45, 34.45, facilities)
    from scripts.lib.geo_utils import haversine_km
    assert d == haversine_km(71.45, 34.45, 71.5, 34.5)

def test_nearest_facility_km_empty_list_returns_none():
    assert metrics_mod.nearest_facility_km(71.0, 34.0, []) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_district_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# scripts/08_compute_district_metrics.py
"""Compute per-district metrics feeding the gap-score analysis: area,
population density, facility density, beds per 1,000 population, a
straight-line accessibility proxy (nearest facility to the district's
population centroid — no routing engine available in this environment, so
this is documented as a proxy, not a real travel time), and a hand-classified
terrain flag."""
import csv
import json
from pathlib import Path

from shapely.geometry import shape

from scripts.lib.districts import normalize_district
from scripts.lib.geo_utils import haversine_km, polygon_area_km2

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

# Hand-classified from known KP geography: districts substantially in the
# Hindu Kush / Himalayan foothills or otherwise mountainous terrain that
# materially affects road access to health facilities.
MOUNTAINOUS_DISTRICTS = {
    "Chitral", "Upper Chitral", "Lower Chitral", "Swat", "Upper Dir", "Lower Dir",
    "Shangla", "Kohistan", "Upper Kohistan", "Lower Kohistan", "Battagram",
    "Buner", "Torghar", "Mansehra", "Abbottabad", "Bajaur", "Dir",
}


def classify_terrain(district_name):
    return "mountainous" if district_name in MOUNTAINOUS_DISTRICTS else "plains"


def nearest_facility_km(centroid_lon, centroid_lat, facilities):
    if not facilities:
        return None
    return min(
        haversine_km(centroid_lon, centroid_lat, f["lon"], f["lat"]) for f in facilities
    )


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


def main():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    population = load_population()
    facilities_by_district = load_facilities_by_district()

    rows = []
    for d in boundaries["districts"]:
        name = d["district"]
        geom = shape(d["geometry"])
        area_km2 = d.get("area_km2") or round(polygon_area_km2(geom), 2)
        pop_row = population.get(name)
        pop_2023 = int(pop_row["population_2023"]) if pop_row else 0
        pop_density = round(pop_2023 / area_km2, 2) if area_km2 else 0.0

        facilities = facilities_by_district.get(name, [])
        beds_total = sum(int(f["beds"]) for f in facilities if f["beds"])
        beds_per_1000 = round((beds_total / pop_2023) * 1000, 3) if pop_2023 else 0.0

        centroid = geom.centroid
        accessibility_km = nearest_facility_km(centroid.x, centroid.y, facilities)

        rows.append(
            {
                "district": name,
                "division": d.get("division") or "",
                "area_km2": area_km2,
                "population_2023": pop_2023,
                "pop_density": pop_density,
                "terrain": classify_terrain(name),
                "facility_count": len(facilities),
                "beds_per_1000": beds_per_1000,
                "accessibility_km": round(accessibility_km, 2) if accessibility_km is not None else "",
            }
        )

    out_path = PROCESSED / "district_metrics.csv"
    fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote district_metrics.csv for {len(rows)} districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_district_metrics.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the real computation and verify**

Run: `python scripts/08_compute_district_metrics.py`

```python
# tests/verify_district_metrics.py
import csv
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "district_metrics.csv"


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 25, f"Too few districts in metrics: {len(rows)}"
    for r in rows:
        assert float(r["area_km2"]) > 0, f"Non-positive area: {r}"
        assert int(r["population_2023"]) >= 0, f"Negative population: {r}"
        assert r["terrain"] in ("mountainous", "plains"), f"Bad terrain value: {r}"
        assert int(r["facility_count"]) >= 0
    zero_pop = [r["district"] for r in rows if int(r["population_2023"]) == 0]
    assert not zero_pop, f"Districts with zero population (likely a name-join miss): {zero_pop}"
    print(f"OK: district_metrics.csv covers {len(rows)} districts, no zero-population joins")


if __name__ == "__main__":
    main()
```

Run: `python tests/verify_district_metrics.py`
Expected: `OK: district_metrics.csv covers N districts, no zero-population joins`. A non-empty `zero_pop` list means a district-name mismatch between `boundaries.json` and the population CSV — fix via `scripts/lib/districts.py`, not by patching this script.

- [ ] **Step 6: Commit**

```bash
git add scripts/08_compute_district_metrics.py tests/test_district_metrics.py tests/verify_district_metrics.py data/processed/district_metrics.csv
git commit -m "feat: compute per-district population/facility/accessibility metrics

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: Gap score and need-tier clustering

**Files:**
- Create: `scripts/09_gap_score_and_clusters.py`
- Test: `tests/test_gap_scoring.py`

**Interfaces:**
- Consumes: `data/processed/district_metrics.csv`
- Produces:
  - `scripts/09_gap_score_and_clusters.compute_gap_scores(rows: list[dict]) -> list[dict]` (adds `gap_score` float 0-100 to each row, importable/unit-tested)
  - `scripts/09_gap_score_and_clusters.assign_need_tiers(rows: list[dict]) -> list[dict]` (adds `need_tier` in `{"Critical","High","Moderate","Low"}`, importable/unit-tested)
  - Overwrites `data/processed/district_metrics.csv` with `gap_score` and `need_tier` columns added.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gap_scoring.py
import importlib
gap_mod = importlib.import_module("scripts.09_gap_score_and_clusters")

def make_row(district, pop_density, facility_count, area_km2, accessibility_km, terrain):
    return {
        "district": district, "pop_density": pop_density, "facility_count": facility_count,
        "area_km2": area_km2, "accessibility_km": accessibility_km, "terrain": terrain,
        "population_2023": pop_density * area_km2,
    }

def test_higher_density_lower_facilities_scores_higher_gap():
    rows = [
        make_row("Underserved", pop_density=2000, facility_count=1, area_km2=100, accessibility_km=40, terrain="mountainous"),
        make_row("WellServed", pop_density=200, facility_count=50, area_km2=100, accessibility_km=2, terrain="plains"),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    by_name = {r["district"]: r["gap_score"] for r in scored}
    assert by_name["Underserved"] > by_name["WellServed"]

def test_gap_scores_are_bounded_0_100():
    rows = [
        make_row("A", 2000, 1, 100, 40, "mountainous"),
        make_row("B", 200, 50, 100, 2, "plains"),
        make_row("C", 800, 10, 100, 15, "plains"),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    for r in scored:
        assert 0 <= r["gap_score"] <= 100

def test_assign_need_tiers_labels_highest_score_critical():
    rows = [
        make_row("A", 2000, 1, 100, 40, "mountainous"),
        make_row("B", 200, 50, 100, 2, "plains"),
        make_row("C", 800, 10, 100, 15, "plains"),
        make_row("D", 900, 8, 100, 18, "mountainous"),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    tiered = gap_mod.assign_need_tiers(scored)
    by_name = {r["district"]: r for r in tiered}
    assert set(r["need_tier"] for r in tiered).issubset({"Critical", "High", "Moderate", "Low"})
    highest_gap_district = max(tiered, key=lambda r: r["gap_score"])["district"]
    assert by_name[highest_gap_district]["need_tier"] in ("Critical", "High")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_gap_scoring.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# scripts/09_gap_score_and_clusters.py
"""Composite facility-access gap score per district (0-100, higher =
more underserved) and a KMeans need-tier clustering on the same feature
set. Weighting/method is documented in plain language in the HTML report
(scripts/14_build_html_report.py) — this is a transparent weighted score
plus an unsupervised grouping, not a black-box model."""
import csv
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import MinMaxScaler

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

# Feature weights for the composite gap score. Each feature is min-max
# normalized to [0,1] first so weights are comparable; terrain adds a flat
# penalty since mountainous terrain independently worsens real access
# beyond what straight-line distance captures.
WEIGHTS = {
    "pop_density": 0.30,       # more people per km^2 with few facilities -> higher need
    "inverse_facility_density": 0.30,  # facilities per capita, inverted
    "accessibility_km": 0.25,  # distance to nearest facility
    "terrain_penalty": 0.15,   # flat bump for mountainous districts
}


def _feature_matrix(rows):
    pop_density = np.array([float(r["pop_density"]) for r in rows]).reshape(-1, 1)
    facility_count = np.array([max(float(r["facility_count"]), 0.0) for r in rows])
    population = np.array([float(r["population_2023"]) for r in rows])
    facility_density = np.divide(
        facility_count, population, out=np.zeros_like(facility_count), where=population > 0
    )
    inverse_facility_density = (-facility_density).reshape(-1, 1)  # more facilities -> lower gap
    accessibility = np.array(
        [float(r["accessibility_km"]) if r["accessibility_km"] not in ("", None) else 0.0 for r in rows]
    ).reshape(-1, 1)
    terrain_penalty = np.array([1.0 if r["terrain"] == "mountainous" else 0.0 for r in rows]).reshape(-1, 1)

    scaler = MinMaxScaler()
    pop_density_n = scaler.fit_transform(pop_density)
    inv_fac_n = MinMaxScaler().fit_transform(inverse_facility_density)
    access_n = MinMaxScaler().fit_transform(accessibility)
    # terrain_penalty is already 0/1, no scaling needed

    return np.hstack([pop_density_n, inv_fac_n, access_n, terrain_penalty])


def compute_gap_scores(rows):
    features = _feature_matrix(rows)
    weights = np.array(
        [WEIGHTS["pop_density"], WEIGHTS["inverse_facility_density"], WEIGHTS["accessibility_km"], WEIGHTS["terrain_penalty"]]
    )
    raw_scores = features @ weights  # weighted sum, already in ~[0,1] since each feature column is in [0,1]
    # Re-normalize to a clean 0-100 scale across this district set.
    lo, hi = raw_scores.min(), raw_scores.max()
    scaled = (raw_scores - lo) / (hi - lo) * 100 if hi > lo else np.zeros_like(raw_scores)
    out = []
    for row, score in zip(rows, scaled):
        row = dict(row)
        row["gap_score"] = round(float(score), 2)
        out.append(row)
    return out


def assign_need_tiers(rows, n_clusters=4):
    scores = np.array([[r["gap_score"]] for r in rows])
    n_clusters = min(n_clusters, len(rows))
    km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
    labels = km.fit_predict(scores)
    # Order cluster centers ascending -> map to tier names so "highest mean
    # gap_score cluster" is always "Critical" regardless of KMeans' arbitrary
    # label numbering.
    centers = km.cluster_centers_.flatten()
    order = np.argsort(centers)  # ascending gap score
    tier_names = ["Low", "Moderate", "High", "Critical"][-n_clusters:]
    label_to_tier = {label: tier_names[rank] for rank, label in enumerate(order)}

    out = []
    for row, label in zip(rows, labels):
        row = dict(row)
        row["need_tier"] = label_to_tier[label]
        out.append(row)
    return out


def main():
    csv_path = PROCESSED / "district_metrics.csv"
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    scored = compute_gap_scores(rows)
    tiered = assign_need_tiers(scored)

    fieldnames = list(tiered[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(tiered)
    print(f"Updated district_metrics.csv with gap_score/need_tier for {len(tiered)} districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gap_scoring.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Install scikit-learn dependency check and run for real**

Run: `python scripts/09_gap_score_and_clusters.py`
Expected: `Updated district_metrics.csv with gap_score/need_tier for N districts`

Run:
```bash
python -c "
import csv
rows = list(csv.DictReader(open('data/processed/district_metrics.csv', newline='', encoding='utf-8')))
assert all(0 <= float(r['gap_score']) <= 100 for r in rows)
assert all(r['need_tier'] in ('Critical','High','Moderate','Low') for r in rows)
print('OK: gap_score bounded, need_tier assigned for', len(rows), 'districts')
"
```
Expected: `OK: gap_score bounded, need_tier assigned for N districts`

- [ ] **Step 6: Commit**

```bash
git add scripts/09_gap_score_and_clusters.py tests/test_gap_scoring.py data/processed/district_metrics.csv
git commit -m "feat: compute weighted gap score and KMeans need-tier clustering

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12: Demand forecasting (2030/2035)

**Files:**
- Create: `scripts/10_forecast_demand.py`
- Test: `tests/test_forecasting.py`

**Interfaces:**
- Consumes: `data/processed/district_metrics.csv`, `data/processed/kp_district_population_2023.csv`
- Produces:
  - `scripts/10_forecast_demand.project_population(pop_2023: float, growth_rate_pct: float, years_ahead: int) -> float` (importable/unit-tested)
  - `scripts/10_forecast_demand.facilities_needed(population: float, per_facility_population: int = 30000) -> int` (importable/unit-tested)
  - Overwrites `data/processed/district_metrics.csv` adding `pop_2030,pop_2035,fac_nd30,fac_nd35`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_forecasting.py
import importlib
forecast_mod = importlib.import_module("scripts.10_forecast_demand")

def test_project_population_zero_growth_unchanged():
    assert forecast_mod.project_population(100000, 0.0, 5) == 100000

def test_project_population_positive_growth_increases():
    # 2% annual growth over 7 years (2023 -> 2030)
    result = forecast_mod.project_population(100000, 2.0, 7)
    expected = 100000 * (1.02 ** 7)
    assert abs(result - expected) < 0.01

def test_facilities_needed_rounds_up_for_partial_population():
    # 65,000 people at 1 facility per 30,000 -> ceil(65000/30000) = 3
    assert forecast_mod.facilities_needed(65000, per_facility_population=30000) == 3

def test_facilities_needed_zero_population():
    assert forecast_mod.facilities_needed(0, per_facility_population=30000) == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_forecasting.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# scripts/10_forecast_demand.py
"""Project district population to 2030/2035 using each district's compound
annual growth rate (falling back to the KP provincial average where a
district's own rate is unavailable — see scripts/02_compile_population.py),
then estimate facilities needed at each horizon against a simplified
Pakistan health-facility population norm (1 basic facility per ~30,000
population, approximating the BHU/RHC tier — documented as a simplification
in the HTML report, not an official MoH standard)."""
import csv
import math
from pathlib import Path

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
CENSUS_YEAR = 2023
DEFAULT_PER_FACILITY_POPULATION = 30000


def project_population(pop_current, growth_rate_pct, years_ahead):
    return pop_current * ((1 + growth_rate_pct / 100.0) ** years_ahead)


def facilities_needed(population, per_facility_population=DEFAULT_PER_FACILITY_POPULATION):
    if population <= 0:
        return 0
    return math.ceil(population / per_facility_population)


def load_growth_rates():
    with open(PROCESSED / "kp_district_population_2023.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rates = {}
    known = []
    for r in rows:
        if r["growth_rate_pct"]:
            rate = float(r["growth_rate_pct"])
            rates[r["district"]] = rate
            known.append(rate)
    provincial_avg = sum(known) / len(known) if known else 2.4  # KP long-run avg fallback
    return rates, provincial_avg


def main():
    rates, provincial_avg = load_growth_rates()
    csv_path = PROCESSED / "district_metrics.csv"
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        pop_2023 = float(row["population_2023"])
        rate = rates.get(row["district"], provincial_avg)
        pop_2030 = project_population(pop_2023, rate, 2030 - CENSUS_YEAR)
        pop_2035 = project_population(pop_2023, rate, 2035 - CENSUS_YEAR)
        current_facilities = int(row["facility_count"])

        needed_2030 = facilities_needed(pop_2030)
        needed_2035 = facilities_needed(pop_2035)

        row["pop_2030"] = round(pop_2030)
        row["pop_2035"] = round(pop_2035)
        row["fac_nd30"] = max(needed_2030 - current_facilities, 0)
        row["fac_nd35"] = max(needed_2035 - current_facilities, 0)

    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Updated district_metrics.csv with 2030/2035 forecasts for {len(rows)} districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_forecasting.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run for real and verify**

Run: `python scripts/10_forecast_demand.py`

Run:
```bash
python -c "
import csv
rows = list(csv.DictReader(open('data/processed/district_metrics.csv', newline='', encoding='utf-8')))
for r in rows:
    assert int(r['pop_2030']) >= int(r['population_2023']), r
    assert int(r['pop_2035']) >= int(r['pop_2030']), r
    assert int(r['fac_nd30']) >= 0 and int(r['fac_nd35']) >= 0, r
print('OK: forecasts monotonic and non-negative for', len(rows), 'districts')
"
```
Expected: `OK: forecasts monotonic and non-negative for N districts`

- [ ] **Step 6: Commit**

```bash
git add scripts/10_forecast_demand.py tests/test_forecasting.py data/processed/district_metrics.csv
git commit -m "feat: forecast 2030/2035 population and facilities needed per district

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 13: ML-based new-site suggestions

**Files:**
- Create: `scripts/11_suggest_new_sites.py`
- Test: `tests/test_suggest_sites.py`

**Interfaces:**
- Consumes: `data/processed/district_metrics.csv`, `data/processed/facilities_merged.csv`, `data/processed/boundaries.json`, an OSM settlement fetch (added in this task), `scripts.lib.geo_utils.haversine_km`
- Produces:
  - `scripts/11_suggest_new_sites.pick_candidate_sites(settlements: list[dict], existing_facilities: list[dict], n_sites: int) -> list[dict]` (importable/unit-tested) — returns up to `n_sites` dicts `{"lat","lon","rationale"}`, chosen as the centroids of a population-weighted KMeans over settlement points, ranked by distance from the nearest existing facility (farthest first).
  - `data/processed/suggested_sites.csv` with columns `district,priority,lat,lon,rationale` for the top 10 highest-`gap_score` districts.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_suggest_sites.py
import importlib
sites_mod = importlib.import_module("scripts.11_suggest_new_sites")

def test_pick_candidate_sites_favors_underserved_cluster():
    # Two settlement clusters: one near an existing facility, one far away.
    settlements = (
        [{"lat": 34.00 + i * 0.001, "lon": 71.00 + i * 0.001, "population": 500} for i in range(5)]
        + [{"lat": 35.00 + i * 0.001, "lon": 72.00 + i * 0.001, "population": 500} for i in range(5)]
    )
    existing_facilities = [{"lat": 34.00, "lon": 71.00}]  # sits right in the first cluster
    sites = sites_mod.pick_candidate_sites(settlements, existing_facilities, n_sites=1)
    assert len(sites) == 1
    # The suggested site should land near the underserved (far) cluster, not the served one.
    assert sites[0]["lat"] > 34.5

def test_pick_candidate_sites_respects_n_sites_cap():
    settlements = [{"lat": 34.0 + i * 0.01, "lon": 71.0 + i * 0.01, "population": 100} for i in range(20)]
    sites = sites_mod.pick_candidate_sites(settlements, [], n_sites=3)
    assert len(sites) == 3

def test_pick_candidate_sites_empty_settlements_returns_empty():
    assert sites_mod.pick_candidate_sites([], [], n_sites=3) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_suggest_sites.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# scripts/11_suggest_new_sites.py
"""ML-based new-facility site suggestion: for the worst-scoring districts,
run a population-weighted KMeans over OSM settlement points to find
population centers, then rank those centers by distance from the nearest
existing facility (farthest-from-care first). This approximates a
maximum-coverage facility-location heuristic without a full optimization
solver — documented as a simplified heuristic in the HTML report."""
import csv
import json
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans

from scripts.lib.geo_utils import haversine_km
from scripts.lib.http_utils import make_session

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
KP_BBOX = (31.0, 69.2, 36.9, 74.1)
TOP_N_DISTRICTS = 10
SITES_PER_DISTRICT = 1


def pick_candidate_sites(settlements, existing_facilities, n_sites):
    if not settlements:
        return []
    n_sites = min(n_sites, len(settlements))
    coords = np.array([[s["lon"], s["lat"]] for s in settlements])
    weights = np.array([max(s.get("population", 1), 1) for s in settlements])

    km = KMeans(n_clusters=n_sites, n_init=10, random_state=42)
    km.fit(coords, sample_weight=weights)
    centers = km.cluster_centers_

    scored_centers = []
    for lon, lat in centers:
        if existing_facilities:
            nearest_km = min(haversine_km(lon, lat, f["lon"], f["lat"]) for f in existing_facilities)
        else:
            nearest_km = float("inf")
        scored_centers.append((nearest_km, lon, lat))

    scored_centers.sort(key=lambda t: t[0], reverse=True)  # farthest-from-care first
    return [
        {
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "rationale": f"Population-weighted settlement cluster centroid, ~{dist:.1f} km from nearest existing facility",
        }
        for dist, lon, lat in scored_centers
    ]


def fetch_settlements():
    session = make_session()
    bbox_str = ",".join(str(v) for v in KP_BBOX)
    query = f"""
    [out:json][timeout:120];
    (
      node["place"="town"]({bbox_str});
      node["place"="village"]({bbox_str});
    );
    out;
    """
    resp = session.post(OVERPASS_URL, data={"data": query}, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    records = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        pop = tags.get("population")
        records.append(
            {
                "lat": el["lat"],
                "lon": el["lon"],
                "population": int(pop) if pop and str(pop).isdigit() else 300,  # small-village default
            }
        )
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "osm_settlements.json").write_text(json.dumps(records))
    return records


def load_facilities_by_district():
    by_district = {}
    with open(PROCESSED / "facilities_merged.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["is_duplicate_of"]:
                continue
            by_district.setdefault(r["district"], []).append({"lat": float(r["lat"]), "lon": float(r["lon"])})
    return by_district


def load_settlements_by_district(settlements, boundaries):
    from shapely.geometry import shape
    from scripts.lib.geo_utils import find_containing_district

    districts = [{"district": d["district"], "geometry": shape(d["geometry"])} for d in boundaries["districts"]]
    by_district = {}
    for s in settlements:
        district = find_containing_district(s["lon"], s["lat"], districts)
        by_district.setdefault(district, []).append(s)
    return by_district


def main():
    with open(PROCESSED / "district_metrics.csv", newline="", encoding="utf-8") as f:
        metrics = list(csv.DictReader(f))
    metrics.sort(key=lambda r: float(r["gap_score"]), reverse=True)
    top_districts = metrics[:TOP_N_DISTRICTS]

    settlements = fetch_settlements()
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    settlements_by_district = load_settlements_by_district(settlements, boundaries)
    facilities_by_district = load_facilities_by_district()

    out_rows = []
    for priority, row in enumerate(top_districts, start=1):
        district = row["district"]
        district_settlements = settlements_by_district.get(district, [])
        district_facilities = facilities_by_district.get(district, [])
        sites = pick_candidate_sites(district_settlements, district_facilities, SITES_PER_DISTRICT)
        for site in sites:
            out_rows.append(
                {
                    "district": district,
                    "priority": priority,
                    "lat": site["lat"],
                    "lon": site["lon"],
                    "rationale": site["rationale"],
                }
            )

    out_path = PROCESSED / "suggested_sites.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "priority", "lat", "lon", "rationale"])
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"Wrote {len(out_rows)} suggested new sites across {len(top_districts)} priority districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_suggest_sites.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run for real and verify**

Run: `python scripts/11_suggest_new_sites.py`
Expected: `Wrote N suggested new sites across M priority districts` (N up to 10, some districts may yield 0 if they have no OSM settlement points — acceptable, note it in the report rather than forcing a site).

Run:
```bash
python -c "
import csv
rows = list(csv.DictReader(open('data/processed/suggested_sites.csv', newline='', encoding='utf-8')))
for r in rows:
    lat, lon = float(r['lat']), float(r['lon'])
    assert -75 < lon < 76 and 30 < lat < 38, r
    assert r['rationale']
print('OK:', len(rows), 'suggested sites, coords in bounds')
"
```
Expected: `OK: N suggested sites, coords in bounds`

- [ ] **Step 6: Commit**

```bash
git add scripts/11_suggest_new_sites.py tests/test_suggest_sites.py data/processed/suggested_sites.csv data/raw/osm_settlements.json
git commit -m "feat: ML-based new healthcare facility site suggestions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 14: Write all output shapefiles

**Files:**
- Create: `scripts/12_write_shapefiles.py`
- Test: `tests/verify_shapefiles.py`

**Interfaces:**
- Consumes: `data/processed/boundaries.json`, `data/processed/district_metrics.csv`, `data/processed/facilities_merged.csv`, `data/raw/osm_roads.json`, `data/processed/suggested_sites.csv`, `scripts.lib.shp_writer.write_shapefile`
- Produces: `gis/KP_Province_Boundary.shp`, `gis/KP_Districts.shp`, `gis/KP_Healthcare_Facilities.shp`, `gis/KP_Roads.shp`, `gis/KP_District_Gap_Scores.shp`, `gis/KP_Suggested_New_Sites.shp` (each with matching `.shx/.dbf/.prj`)

- [ ] **Step 1: Implement**

```python
# scripts/12_write_shapefiles.py
"""Assemble every processed data table + boundaries.json into the six
final QGIS-ready shapefiles under gis/, using scripts.lib.shp_writer."""
import csv
import json
from pathlib import Path

from shapely.geometry import shape, Point, MultiLineString, LineString

from scripts.lib.shp_writer import write_shapefile

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"

PROVINCE_FIELDS = [("name", "C", 50, 0), ("area_km2", "F", 12, 2), ("total_pop", "N", 12, 0)]
DISTRICT_FIELDS = [
    ("district", "C", 50, 0), ("division", "C", 50, 0), ("area_km2", "F", 12, 2),
    ("pop_2023", "N", 12, 0), ("pop_dens", "F", 10, 2), ("terrain", "C", 20, 0),
]
FACILITY_FIELDS = [
    ("name", "C", 120, 0), ("category", "C", 40, 0), ("pub_priv", "C", 10, 0),
    ("beds", "N", 6, 0), ("district", "C", 50, 0), ("source", "C", 10, 0), ("geo_prec", "C", 20, 0),
]
ROAD_FIELDS = [("road_cls", "C", 20, 0), ("name", "C", 80, 0)]
GAP_FIELDS = [
    ("district", "C", 50, 0), ("gap_score", "F", 8, 2), ("need_tier", "C", 10, 0),
    ("pop_2030", "N", 12, 0), ("pop_2035", "N", 12, 0), ("fac_nd30", "N", 6, 0), ("fac_nd35", "N", 6, 0),
]
SITE_FIELDS = [("district", "C", 50, 0), ("priority", "N", 4, 0), ("rationale", "C", 150, 0)]


def write_province(boundaries, district_metrics):
    geom = shape(boundaries["province_geometry"])
    total_pop = sum(int(r["population_2023"]) for r in district_metrics)
    total_area = sum(float(r["area_km2"]) for r in district_metrics)
    record = {"geometry": geom, "name": "Khyber Pakhtunkhwa", "area_km2": round(total_area, 2), "total_pop": total_pop}
    write_shapefile(str(GIS_DIR / "KP_Province_Boundary"), "POLYGON", [record], PROVINCE_FIELDS)


def write_districts(boundaries, district_metrics):
    metrics_by_name = {r["district"]: r for r in district_metrics}
    records = []
    for d in boundaries["districts"]:
        m = metrics_by_name.get(d["district"], {})
        records.append(
            {
                "geometry": shape(d["geometry"]),
                "district": d["district"],
                "division": d.get("division") or "",
                "area_km2": float(m.get("area_km2", d.get("area_km2", 0))),
                "pop_2023": int(m.get("population_2023", 0)),
                "pop_dens": float(m.get("pop_density", 0)),
                "terrain": m.get("terrain", ""),
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


def write_roads():
    roads = json.loads((RAW / "osm_roads.json").read_text())
    records = []
    for r in roads:
        geom = LineString(r["coordinates"])
        records.append({"geometry": geom, "road_cls": r["road_class"], "name": r["name"][:80]})
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
                "pop_2030": int(m["pop_2030"]),
                "pop_2035": int(m["pop_2035"]),
                "fac_nd30": int(m["fac_nd30"]),
                "fac_nd35": int(m["fac_nd35"]),
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

    write_province(boundaries, district_metrics)
    write_districts(boundaries, district_metrics)
    write_facilities()
    write_roads()
    write_gap_scores(boundaries, district_metrics)
    write_suggested_sites()
    print("Wrote all 6 shapefile layers to gis/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `python scripts/12_write_shapefiles.py`
Expected: `Wrote all 6 shapefile layers to gis/`

- [ ] **Step 3: Write and run verification (round-trip read every shapefile)**

```python
# tests/verify_shapefiles.py
from pathlib import Path
import shapefile

GIS_DIR = Path(__file__).resolve().parent.parent / "gis"
EXPECTED = {
    "KP_Province_Boundary": 1,
    "KP_Districts": 25,   # lower bound
    "KP_Healthcare_Facilities": 100,
    "KP_Roads": 20,
    "KP_District_Gap_Scores": 25,
    "KP_Suggested_New_Sites": 1,
}


def main():
    for layer, min_count in EXPECTED.items():
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            path = GIS_DIR / f"{layer}{ext}"
            assert path.exists(), f"Missing {path}"
        r = shapefile.Reader(str(GIS_DIR / layer))
        shapes = r.shapes()
        assert len(shapes) >= min_count, f"{layer}: expected >= {min_count} features, got {len(shapes)}"
        for shp in shapes:
            assert shp.points or shp.shapeType == shapefile.NULL, f"{layer}: empty geometry found"
        print(f"OK: {layer} has {len(shapes)} features")


if __name__ == "__main__":
    main()
```

Run: `python tests/verify_shapefiles.py`
Expected: `OK: <layer> has N features` for each of the 6 layers, no `AssertionError`.

- [ ] **Step 4: Commit**

```bash
git add scripts/12_write_shapefiles.py tests/verify_shapefiles.py gis/
git commit -m "feat: write all 6 KP healthcare planning shapefiles

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 15: QGIS project file + PyQGIS fallback script

**Files:**
- Create: `scripts/13_build_qgis_project.py`
- Create: `scripts/load_and_style.py`

**Interfaces:**
- Consumes: the 6 shapefiles written in Task 14
- Produces: `gis/KP_Healthcare_Plan.qgz` (a zipped `.qgs` XML project, QGIS's native `.qgz` container format — `zipfile` with a single `.qgs` member plus an empty `.qgz` marker as QGIS itself produces), `scripts/load_and_style.py` (standalone PyQGIS console script)

- [ ] **Step 1: Implement the QGIS project XML builder**

```python
# scripts/13_build_qgis_project.py
"""Hand-author a QGIS 3.x project (.qgz) that loads all 6 gis/*.shp layers
with baked-in styling: graduated population choropleth on KP_Districts,
graduated red-yellow-green on KP_District_Gap_Scores.gap_score,
categorized symbology on KP_Healthcare_Facilities.category, plain line
style for KP_Roads, and a distinct marker for KP_Suggested_New_Sites.

This is authored directly against the documented QGIS project XML schema
since no QGIS install is available in this environment to generate/verify
it interactively — see scripts/load_and_style.py for a PyQGIS fallback that
reconstructs the same result if this file has any version-compatibility
issue when opened in the user's actual QGIS install.
"""
import uuid
import zipfile
from pathlib import Path

GIS_DIR = Path(__file__).resolve().parent.parent / "gis"
PROJECT_NAME = "KP_Healthcare_Plan"

LAYERS = [
    {"id": "province", "file": "KP_Province_Boundary.shp", "name": "KP Province Boundary", "geom": "Polygon"},
    {"id": "districts", "file": "KP_Districts.shp", "name": "KP Districts (Population)", "geom": "Polygon"},
    {"id": "gapscores", "file": "KP_District_Gap_Scores.shp", "name": "District Gap Scores", "geom": "Polygon"},
    {"id": "roads", "file": "KP_Roads.shp", "name": "Roads", "geom": "Line"},
    {"id": "facilities", "file": "KP_Healthcare_Facilities.shp", "name": "Healthcare Facilities", "geom": "Point"},
    {"id": "sites", "file": "KP_Suggested_New_Sites.shp", "name": "Suggested New Sites", "geom": "Point"},
]


def _layer_id(name):
    return f"{name}_{uuid.uuid4().hex[:8]}"


def _simple_polygon_layer_xml(layer, layer_id, color, outline="#404040"):
    return f"""
    <maplayer type="vector" geometry="Polygon">
      <id>{layer_id}</id>
      <datasource>./{layer['file']}</datasource>
      <layername>{layer['name']}</layername>
      <provider>ogr</provider>
      <renderer-v2 type="singleSymbol">
        <symbols>
          <symbol type="fill" name="0">
            <layer class="SimpleFill">
              <Option type="Map">
                <Option type="QString" name="color" value="{color}"/>
                <Option type="QString" name="outline_color" value="{outline}"/>
                <Option type="QString" name="outline_width" value="0.3"/>
              </Option>
            </layer>
          </symbol>
        </symbols>
      </renderer-v2>
    </maplayer>"""


def _graduated_polygon_layer_xml(layer, layer_id, field, ramp_stops):
    ranges = "\n".join(
        f'''        <range lower="{lo}" upper="{hi}" label="{lo:.0f} - {hi:.0f}" symbol="{i}"/>'''
        for i, (lo, hi, _color) in enumerate(ramp_stops)
    )
    symbols = "\n".join(
        f'''        <symbol type="fill" name="{i}">
          <layer class="SimpleFill">
            <Option type="Map">
              <Option type="QString" name="color" value="{color}"/>
              <Option type="QString" name="outline_color" value="#404040"/>
              <Option type="QString" name="outline_width" value="0.3"/>
            </Option>
          </layer>
        </symbol>'''
        for i, (_lo, _hi, color) in enumerate(ramp_stops)
    )
    return f"""
    <maplayer type="vector" geometry="Polygon">
      <id>{layer_id}</id>
      <datasource>./{layer['file']}</datasource>
      <layername>{layer['name']}</layername>
      <provider>ogr</provider>
      <renderer-v2 type="graduatedSymbol" attr="{field}">
        <ranges>
{ranges}
        </ranges>
        <symbols>
{symbols}
        </symbols>
      </renderer-v2>
    </maplayer>"""


def _categorized_point_layer_xml(layer, layer_id, field, categories):
    cats_xml = "\n".join(
        f'''        <category value="{value}" symbol="{i}" label="{value}"/>'''
        for i, (value, _color) in enumerate(categories)
    )
    symbols = "\n".join(
        f'''        <symbol type="marker" name="{i}">
          <layer class="SimpleMarker">
            <Option type="Map">
              <Option type="QString" name="color" value="{color}"/>
              <Option type="QString" name="size" value="2.5"/>
            </Option>
          </layer>
        </symbol>'''
        for i, (_value, color) in enumerate(categories)
    )
    return f"""
    <maplayer type="vector" geometry="Point">
      <id>{layer_id}</id>
      <datasource>./{layer['file']}</datasource>
      <layername>{layer['name']}</layername>
      <provider>ogr</provider>
      <renderer-v2 type="categorizedSymbol" attr="{field}">
        <categories>
{cats_xml}
        </categories>
        <symbols>
{symbols}
        </symbols>
      </renderer-v2>
    </maplayer>"""


def _line_layer_xml(layer, layer_id, color="#8a8a8a"):
    return f"""
    <maplayer type="vector" geometry="Line">
      <id>{layer_id}</id>
      <datasource>./{layer['file']}</datasource>
      <layername>{layer['name']}</layername>
      <provider>ogr</provider>
      <renderer-v2 type="singleSymbol">
        <symbols>
          <symbol type="line" name="0">
            <layer class="SimpleLine">
              <Option type="Map">
                <Option type="QString" name="line_color" value="{color}"/>
                <Option type="QString" name="line_width" value="0.4"/>
              </Option>
            </layer>
          </symbol>
        </symbols>
      </renderer-v2>
    </maplayer>"""


def _point_marker_layer_xml(layer, layer_id, color="#e6194b"):
    return f"""
    <maplayer type="vector" geometry="Point">
      <id>{layer_id}</id>
      <datasource>./{layer['file']}</datasource>
      <layername>{layer['name']}</layername>
      <provider>ogr</provider>
      <renderer-v2 type="singleSymbol">
        <symbols>
          <symbol type="marker" name="0">
            <layer class="SimpleMarker">
              <Option type="Map">
                <Option type="QString" name="color" value="{color}"/>
                <Option type="QString" name="size" value="4"/>
                <Option type="QString" name="name" value="star"/>
              </Option>
            </layer>
          </symbol>
        </symbols>
      </renderer-v2>
    </maplayer>"""


def build_qgs_xml():
    ids = {l["id"]: _layer_id(l["id"]) for l in LAYERS}
    by_id = {l["id"]: l for l in LAYERS}

    layers_xml = [
        _simple_polygon_layer_xml(by_id["province"], ids["province"], color="255,255,255,0"),
        _graduated_polygon_layer_xml(
            by_id["districts"], ids["districts"], "pop_dens",
            [(0, 100, "#ffffcc"), (100, 300, "#fed976"), (300, 700, "#fd8d3c"), (700, 999999, "#e31a1c")],
        ),
        _graduated_polygon_layer_xml(
            by_id["gapscores"], ids["gapscores"], "gap_score",
            [(0, 25, "#1a9850"), (25, 50, "#91cf60"), (50, 75, "#fee08b"), (75, 100, "#d73027")],
        ),
        _line_layer_xml(by_id["roads"], ids["roads"]),
        _categorized_point_layer_xml(
            by_id["facilities"], ids["facilities"], "category",
            [("Hospital", "#e6194b"), ("Clinic", "#4363d8"), ("Pharmacy", "#3cb44b"), ("Facility", "#808080")],
        ),
        _point_marker_layer_xml(by_id["sites"], ids["sites"]),
    ]

    layer_tree_entries = "\n".join(
        f'      <layer-tree-layer id="{ids[l["id"]]}" name="{l["name"]}"/>' for l in reversed(LAYERS)
    )
    layer_order_entries = "\n".join(f'      <layer id="{ids[l["id"]]}"/>' for l in LAYERS)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<qgis projectname="{PROJECT_NAME}" version="3.34.0">
  <homePath path=""/>
  <title>KP Healthcare System Planning</title>
  <projectCrs>
    <spatialrefsys>
      <authid>EPSG:4326</authid>
    </spatialrefsys>
  </projectCrs>
  <layer-tree-group>
{layer_tree_entries}
  </layer-tree-group>
  <projectlayers>
{"".join(layers_xml)}
  </projectlayers>
  <layerorder>
{layer_order_entries}
  </layerorder>
</qgis>
"""


def main():
    qgs_content = build_qgs_xml()
    qgz_path = GIS_DIR / f"{PROJECT_NAME}.qgz"
    with zipfile.ZipFile(qgz_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{PROJECT_NAME}.qgs", qgs_content)
    print(f"Wrote {qgz_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and sanity-check the zip/XML structure**

Run: `python scripts/13_build_qgis_project.py`
Expected: `Wrote gis/KP_Healthcare_Plan.qgz`

Run:
```bash
python -c "
import zipfile
import xml.etree.ElementTree as ET
with zipfile.ZipFile('gis/KP_Healthcare_Plan.qgz') as zf:
    names = zf.namelist()
    assert any(n.endswith('.qgs') for n in names), names
    qgs = [n for n in names if n.endswith('.qgs')][0]
    content = zf.read(qgs)
    root = ET.fromstring(content)  # raises if not well-formed XML
    layer_count = len(root.findall('.//maplayer'))
    assert layer_count == 6, f'Expected 6 maplayer entries, got {layer_count}'
print('OK: .qgz contains well-formed .qgs XML with 6 layers')
"
```
Expected: `OK: .qgz contains well-formed .qgs XML with 6 layers`. This confirms the file is a valid zip containing well-formed XML with the right layer count — it does **not** confirm QGIS itself will render every styling option correctly, since no QGIS install exists here. That risk is exactly why Step 3's fallback script exists.

- [ ] **Step 3: Write the PyQGIS fallback script**

```python
# scripts/load_and_style.py
"""PyQGIS console fallback: run this from QGIS's own Python Console
(Plugins > Python Console) if gis/KP_Healthcare_Plan.qgz has any
compatibility issue when opened directly in your QGIS version. It
reconstructs the same 6 layers with the same styling programmatically
against the live QGIS API, which sidesteps any hand-authored-XML
version mismatch.

Usage inside QGIS's Python Console:
    exec(open(r"E:/Healthcare System Planning/scripts/load_and_style.py").read())
"""
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsGraduatedSymbolRenderer, QgsCategorizedSymbolRenderer,
    QgsRendererRange, QgsRendererCategory, QgsSymbol, QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
)

GIS_DIR = r"E:/Healthcare System Planning/gis"


def add_layer(filename, name):
    layer = QgsVectorLayer(f"{GIS_DIR}/{filename}", name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Failed to load {filename}")
    QgsProject.instance().addMapLayer(layer)
    return layer


def style_graduated(layer, field, stops):
    ranges = []
    for lo, hi, color in stops:
        symbol = QgsFillSymbol.createSimple({"color": color, "outline_color": "#404040", "outline_width": "0.3"})
        ranges.append(QgsRendererRange(lo, hi, symbol, f"{lo:.0f} - {hi:.0f}"))
    layer.setRenderer(QgsGraduatedSymbolRenderer(field, ranges))
    layer.triggerRepaint()


def style_categorized(layer, field, categories):
    cats = []
    for value, color in categories:
        symbol = QgsMarkerSymbol.createSimple({"color": color, "size": "2.5"})
        cats.append(QgsRendererCategory(value, symbol, str(value)))
    layer.setRenderer(QgsCategorizedSymbolRenderer(field, cats))
    layer.triggerRepaint()


def main():
    add_layer("KP_Province_Boundary.shp", "KP Province Boundary")

    districts = add_layer("KP_Districts.shp", "KP Districts (Population)")
    style_graduated(
        districts, "pop_dens",
        [(0, 100, "#ffffcc"), (100, 300, "#fed976"), (300, 700, "#fd8d3c"), (700, 999999, "#e31a1c")],
    )

    gap = add_layer("KP_District_Gap_Scores.shp", "District Gap Scores")
    style_graduated(
        gap, "gap_score",
        [(0, 25, "#1a9850"), (25, 50, "#91cf60"), (50, 75, "#fee08b"), (75, 100, "#d73027")],
    )

    add_layer("KP_Roads.shp", "Roads")

    facilities = add_layer("KP_Healthcare_Facilities.shp", "Healthcare Facilities")
    style_categorized(
        facilities, "category",
        [("Hospital", "#e6194b"), ("Clinic", "#4363d8"), ("Pharmacy", "#3cb44b"), ("Facility", "#808080")],
    )

    add_layer("KP_Suggested_New_Sites.shp", "Suggested New Sites")

    print("Loaded and styled all 6 KP healthcare planning layers.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Commit**

```bash
git add scripts/13_build_qgis_project.py scripts/load_and_style.py gis/KP_Healthcare_Plan.qgz
git commit -m "feat: build QGIS project file with styling + PyQGIS fallback script

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 16: HTML planning report

**Files:**
- Create: `scripts/14_build_html_report.py`
- Test: manual visual check (Step 3)

**Interfaces:**
- Consumes: `data/processed/boundaries.json`, `data/processed/district_metrics.csv`, `data/processed/facilities_merged.csv`, `data/processed/suggested_sites.csv`, `data/processed/kp_district_population_2023.csv`
- Produces: `report/KP_Healthcare_Plan.html` (self-contained, embedded base64 PNG maps, no external asset references)

- [ ] **Step 1: Implement the report builder**

```python
# scripts/14_build_html_report.py
"""Render report/KP_Healthcare_Plan.html: a self-contained planning report
with embedded static maps (matplotlib, plotted directly from shapely
geometries — no geopandas), data tables, transparent methodology
explanation, ranked findings, and phased recommendations. Every figure is
base64-embedded so the file has zero external dependencies."""
import base64
import csv
import io
import json
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from shapely.geometry import shape

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
REPORT_DIR = Path(__file__).resolve().parent.parent / "report"


def fig_to_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _plot_polygon(ax, geom, **kwargs):
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for poly in polys:
        ax.add_patch(MplPolygon(list(poly.exterior.coords), closed=True, **kwargs))


def render_population_map(boundaries, metrics_by_district):
    fig, ax = plt.subplots(figsize=(7, 8))
    densities = [float(m["pop_density"]) for m in metrics_by_district.values()]
    lo, hi = min(densities), max(densities)
    for d in boundaries["districts"]:
        m = metrics_by_district.get(d["district"])
        density = float(m["pop_density"]) if m else 0
        t = (density - lo) / (hi - lo) if hi > lo else 0
        color = plt.cm.YlOrRd(0.15 + 0.8 * t)
        _plot_polygon(ax, shape(d["geometry"]), facecolor=color, edgecolor="#404040", linewidth=0.4)
    ax.set_title("KP District Population Density (2023 Census)")
    ax.set_aspect("equal")
    ax.axis("off")
    return fig_to_base64(fig)


def render_gap_score_map(boundaries, metrics_by_district):
    fig, ax = plt.subplots(figsize=(7, 8))
    for d in boundaries["districts"]:
        m = metrics_by_district.get(d["district"])
        score = float(m["gap_score"]) if m else 0
        color = plt.cm.RdYlGn_r(score / 100.0)
        _plot_polygon(ax, shape(d["geometry"]), facecolor=color, edgecolor="#404040", linewidth=0.4)
    ax.set_title("Healthcare Access Gap Score by District")
    ax.set_aspect("equal")
    ax.axis("off")
    return fig_to_base64(fig)


def render_facility_map(boundaries, facilities):
    fig, ax = plt.subplots(figsize=(7, 8))
    for d in boundaries["districts"]:
        _plot_polygon(ax, shape(d["geometry"]), facecolor="#f0f0f0", edgecolor="#c0c0c0", linewidth=0.3)
    by_cat = {}
    for f in facilities:
        by_cat.setdefault(f["category"], []).append(f)
    colors = {"Hospital": "#e6194b", "Clinic": "#4363d8", "Pharmacy": "#3cb44b"}
    for cat, pts in by_cat.items():
        xs = [float(p["lon"]) for p in pts]
        ys = [float(p["lat"]) for p in pts]
        ax.scatter(xs, ys, s=6, label=cat, color=colors.get(cat, "#808080"), alpha=0.7)
    ax.legend(loc="lower left", fontsize=8)
    ax.set_title("Healthcare Facility Distribution")
    ax.set_aspect("equal")
    ax.axis("off")
    return fig_to_base64(fig)


def load_data():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    with open(PROCESSED / "district_metrics.csv", newline="", encoding="utf-8") as f:
        metrics = list(csv.DictReader(f))
    with open(PROCESSED / "facilities_merged.csv", newline="", encoding="utf-8") as f:
        facilities = list(csv.DictReader(f))
    with open(PROCESSED / "suggested_sites.csv", newline="", encoding="utf-8") as f:
        sites = list(csv.DictReader(f))
    return boundaries, metrics, facilities, sites


def district_rows_html(metrics):
    rows = []
    for m in sorted(metrics, key=lambda r: float(r["gap_score"]), reverse=True):
        rows.append(
            "<tr>"
            f"<td>{m['district']}</td><td>{int(float(m['population_2023'])):,}</td>"
            f"<td>{float(m['area_km2']):.0f}</td><td>{float(m['pop_density']):.1f}</td>"
            f"<td>{m['facility_count']}</td><td>{float(m['beds_per_1000']):.2f}</td>"
            f"<td>{m['terrain']}</td><td>{float(m['gap_score']):.1f}</td><td>{m['need_tier']}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def findings_html(metrics):
    ranked = sorted(metrics, key=lambda r: float(r["gap_score"]), reverse=True)[:10]
    items = "\n".join(
        f"<li><strong>{m['district']}</strong> — gap score {float(m['gap_score']):.1f} "
        f"({m['need_tier']}), {m['facility_count']} known facilities, "
        f"{float(m['beds_per_1000']):.2f} beds/1,000 population, "
        f"{m.get('accessibility_km') or 'n/a'} km straight-line to nearest facility.</li>"
        for m in ranked
    )
    return f"<ol>{items}</ol>"


def build(source_boundary, source_population_note):
    boundaries, metrics, facilities, sites = load_data()
    metrics_by_district = {m["district"]: m for m in metrics}

    pop_map_b64 = render_population_map(boundaries, metrics_by_district)
    gap_map_b64 = render_gap_score_map(boundaries, metrics_by_district)
    fac_map_b64 = render_facility_map(boundaries, facilities)

    total_pop = sum(int(m["population_2023"]) for m in metrics)
    total_facilities = len(facilities)
    critical_districts = [m["district"] for m in metrics if m["need_tier"] == "Critical"]

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Khyber Pakhtunkhwa Healthcare System Planning Report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 960px; margin: 0 auto; padding: 2rem 1.5rem; color: #1a1a1a; line-height: 1.55; }}
  h1, h2, h3 {{ color: #0b4f6c; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.85rem; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.5rem; text-align: left; }}
  th {{ background: #0b4f6c; color: white; position: sticky; top: 0; }}
  tr:nth-child(even) {{ background: #f5f7f8; }}
  img {{ max-width: 100%; height: auto; border: 1px solid #ddd; }}
  .figure-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
  .disclaimer {{ background: #fff8e1; border-left: 4px solid #f9a825; padding: 1rem; margin: 1.5rem 0; }}
  .stat {{ display: inline-block; margin: 0.5rem 1.5rem 0.5rem 0; }}
  .stat b {{ display: block; font-size: 1.4rem; color: #0b4f6c; }}
  footer {{ margin-top: 3rem; font-size: 0.8rem; color: #666; }}
</style>
</head>
<body>
<h1>Khyber Pakhtunkhwa Healthcare System Planning Report</h1>
<p>Generated {date.today().isoformat()} — an AI-assisted planning aid built from open and official data sources.</p>

<div class="disclaimer">
<strong>Limitations &amp; disclaimer:</strong> This report is a planning aid assembled from open/official
data (PBS 2023 Digital Census, KP Health Care Commission licensed-facility registry, OpenStreetMap) and
simplified analytical models. It is <strong>not</strong> an official Government of Khyber Pakhtunkhwa
policy document. Facility coordinates vary in precision (exact geocode / street-level / district-centroid
fallback — see the Facilities table's Source column). Accessibility is measured as straight-line distance
to the nearest known facility, not real road travel time, since no routing engine was available when this
report was built. The KPHCC registry currently has no licensed-facility entries for several newly-merged
tribal districts; that is a genuine gap in the underlying licensing rollout, not a data-processing error.
</div>

<h2>Executive Summary</h2>
<div>
  <div class="stat"><b>{total_pop:,}</b> total KP population (2023 census)</div>
  <div class="stat"><b>{len(metrics)}</b> districts analyzed</div>
  <div class="stat"><b>{total_facilities:,}</b> known healthcare facilities</div>
  <div class="stat"><b>{len(critical_districts)}</b> districts at Critical need tier</div>
</div>
<p>Districts at Critical need tier: {", ".join(critical_districts) if critical_districts else "none"}.</p>

<h2>Data Sources &amp; Methodology</h2>
<ul>
  <li><strong>Administrative boundaries:</strong> {source_boundary}</li>
  <li><strong>Population:</strong> Pakistan Bureau of Statistics 2023 Digital Census, district-wise KP figures. {source_population_note}</li>
  <li><strong>Healthcare facilities:</strong> KP Health Care Commission Licensed Health Care Establishment registry (hcc.kp.gov.pk/licensed-hces), supplemented with OpenStreetMap (Overpass API) points where the KPHCC registry has no coverage.</li>
  <li><strong>Roads:</strong> OpenStreetMap major road network (motorway/trunk/primary/secondary).</li>
  <li><strong>Geocoding:</strong> OSM Nominatim, with district-centroid fallback where an address could not be resolved.</li>
</ul>

<h3>AI/ML Methodology (plain-language)</h3>
<ul>
  <li><strong>Gap score (0-100):</strong> a weighted composite of population density, inverse facility density,
  distance to nearest facility, and a mountainous-terrain penalty, each normalized to a common 0-1 scale before
  weighting, then rescaled to 0-100 across KP's districts. Higher = more underserved.</li>
  <li><strong>Need-tier clustering:</strong> unsupervised K-Means grouping of districts by gap score into
  Critical / High / Moderate / Low tiers.</li>
  <li><strong>Demand forecast:</strong> exponential population projection to 2030/2035 using each district's
  own census-derived growth rate (falling back to the KP provincial average where unavailable), compared
  against a simplified facility-per-population norm to estimate additional facilities needed.</li>
  <li><strong>New-site suggestion:</strong> population-weighted K-Means clustering of OSM settlement points
  within the ten highest-gap-score districts, ranked by distance from the nearest existing facility — an
  approximate maximum-coverage heuristic, not a full location-optimization solve.</li>
</ul>

<h2>Current State</h2>
<div class="figure-grid">
  <div><img src="data:image/png;base64,{pop_map_b64}" alt="Population density map"><p><em>Population density by district.</em></p></div>
  <div><img src="data:image/png;base64,{gap_map_b64}" alt="Gap score map"><p><em>Healthcare access gap score by district.</em></p></div>
</div>
<img src="data:image/png;base64,{fac_map_b64}" alt="Facility distribution map">
<p><em>Known healthcare facility distribution (KPHCC + OSM, deduplicated).</em></p>

<h2>District Data</h2>
<table>
<thead><tr><th>District</th><th>Population (2023)</th><th>Area (km²)</th><th>Density (/km²)</th>
<th>Facilities</th><th>Beds/1,000</th><th>Terrain</th><th>Gap Score</th><th>Need Tier</th></tr></thead>
<tbody>
{district_rows_html(metrics)}
</tbody>
</table>

<h2>Findings: Most Underserved Districts</h2>
{findings_html(metrics)}

<h2>Future Planning &amp; Emerging-Technology Recommendations</h2>
<h3>Phased Roadmap</h3>
<ul>
  <li><strong>Short term (0-1 yr):</strong> Extend KPHCC licensing/registration outreach into currently
  unregistered tribal districts to establish an accurate facility baseline; deploy mobile health units to
  the highest-gap-score Critical-tier districts identified above.</li>
  <li><strong>Medium term (1-3 yr):</strong> Establish telemedicine hub connectivity at existing DHQ/THQ
  hospitals serving Critical/High-tier mountainous districts (Chitral, Kohistan, Shangla, Upper/Lower Dir,
  Swat, Battagram, Buner, Torghar) to extend specialist reach without new physical construction; pilot
  AI-assisted triage/queue-management software at high-density urban facilities (Peshawar, Mardan,
  Abbottabad) to reduce overcrowding.</li>
  <li><strong>Long term (3-5+ yr):</strong> Build new facilities at the ML-suggested sites below in the
  highest-priority districts; evaluate drone-based medical resupply (blood, vaccines, essential medicines)
  for the most terrain-isolated Critical-tier districts where road access is seasonally unreliable.</li>
</ul>

<h3>ML-Suggested New Facility Sites (Top Priority Districts)</h3>
<table>
<thead><tr><th>Priority</th><th>District</th><th>Latitude</th><th>Longitude</th><th>Rationale</th></tr></thead>
<tbody>
{"".join(f"<tr><td>{s['priority']}</td><td>{s['district']}</td><td>{float(s['lat']):.4f}</td><td>{float(s['lon']):.4f}</td><td>{s['rationale']}</td></tr>" for s in sites)}
</tbody>
</table>

<h3>Projected 2030/2035 Facility Needs (Top 10 Gap-Score Districts)</h3>
<table>
<thead><tr><th>District</th><th>Pop. 2030</th><th>Pop. 2035</th><th>Add'l Facilities by 2030</th><th>Add'l Facilities by 2035</th></tr></thead>
<tbody>
{"".join(f"<tr><td>{m['district']}</td><td>{int(m['pop_2030']):,}</td><td>{int(m['pop_2035']):,}</td><td>{m['fac_nd30']}</td><td>{m['fac_nd35']}</td></tr>" for m in sorted(metrics, key=lambda r: float(r['gap_score']), reverse=True)[:10])}
</tbody>
</table>

<footer>
Built with an open-data GIS + AI pipeline (shapely, pyshp, scikit-learn, matplotlib) — see the accompanying
QGIS project (<code>gis/KP_Healthcare_Plan.qgz</code>) and shapefiles in <code>gis/</code> for the full
spatial dataset. This report and its underlying shapefiles are a planning aid, not an official government
publication.
</footer>
</body>
</html>
"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "KP_Healthcare_Plan.html").write_text(html, encoding="utf-8")
    print("Wrote report/KP_Healthcare_Plan.html")


if __name__ == "__main__":
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    build(source_boundary=boundaries["source"], source_population_note="See data/processed/kp_district_population_2023.csv for per-row citations.")
```

- [ ] **Step 2: Run it**

Run: `python scripts/14_build_html_report.py`
Expected: `Wrote report/KP_Healthcare_Plan.html`

- [ ] **Step 3: Verify the report is well-formed and self-contained**

Run:
```bash
python -c "
from pathlib import Path
html = Path('report/KP_Healthcare_Plan.html').read_text(encoding='utf-8')
assert '<html' in html and '</html>' in html
assert html.count('data:image/png;base64,') == 3, 'Expected 3 embedded maps'
assert '<script src=' not in html and '<link rel=\"stylesheet\" href=' not in html, 'Report must be self-contained, no external assets'
print('OK: report is well-formed and self-contained, size', len(html), 'bytes')
"
```
Expected: `OK: report is well-formed and self-contained, size N bytes`

- [ ] **Step 4: Commit**

```bash
git add scripts/14_build_html_report.py report/KP_Healthcare_Plan.html
git commit -m "feat: build self-contained HTML healthcare planning report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 17: Final pipeline verification and Artifact publish

**Files:**
- Create: `scripts/run_all.py` (convenience runner chaining all stages in order)

**Interfaces:**
- Consumes: all prior scripts
- Produces: a verified end-to-end pipeline run; the HTML report published as a Claude Artifact

- [ ] **Step 1: Write the convenience runner**

```python
# scripts/run_all.py
"""Run the full KP healthcare planning pipeline end-to-end, in order. Each
stage is idempotent (re-fetches/recomputes into the same output paths), so
re-running after a partial failure is safe — just re-run this script."""
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
STAGES = [
    "01_fetch_boundaries.py",
    "02_compile_population.py",
    "03_fetch_facilities_kphcc.py",
    "04_geocode_kphcc_facilities.py",
    "05_fetch_facilities_osm.py",
    "06_fetch_roads_osm.py",
    "07_merge_facilities.py",
    "08_compute_district_metrics.py",
    "09_gap_score_and_clusters.py",
    "10_forecast_demand.py",
    "11_suggest_new_sites.py",
    "12_write_shapefiles.py",
    "13_build_qgis_project.py",
    "14_build_html_report.py",
]


def main():
    for stage in STAGES:
        print(f"=== Running {stage} ===")
        result = subprocess.run([sys.executable, str(SCRIPTS_DIR / stage)])
        if result.returncode != 0:
            print(f"Stage {stage} failed with exit code {result.returncode}; stopping.")
            sys.exit(result.returncode)
    print("=== Pipeline complete ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full verification suite in order**

Run:
```bash
pytest tests/lib tests/test_merge_facilities.py tests/test_district_metrics.py tests/test_gap_scoring.py tests/test_forecasting.py tests/test_suggest_sites.py -v
python tests/verify_boundaries.py
python tests/verify_population.py
python tests/verify_kphcc_facilities.py
python tests/verify_merged_facilities.py
python tests/verify_district_metrics.py
python tests/verify_shapefiles.py
```
Expected: every pytest suite passes, every verify script prints `OK: ...` with no `AssertionError`.

- [ ] **Step 3: Publish the HTML report as a Claude Artifact**

Publish `report/KP_Healthcare_Plan.html` as a Claude Artifact (Markdown/HTML artifact publish flow), giving it a title ("KP Healthcare System Planning Report"), a one-sentence description, and an appropriate favicon emoji (e.g. 🏥). Confirm the artifact renders the embedded maps and tables correctly before handing the link to the user.

- [ ] **Step 4: Commit**

```bash
git add scripts/run_all.py
git commit -m "feat: add end-to-end pipeline runner

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Final report to user**

Summarize for the user: where the shapefiles/QGIS project/HTML report live on disk, the Artifact link, the total population/facility/district counts actually produced, which districts came out Critical-tier, and a reminder of the documented limitations (straight-line accessibility proxy, KPHCC registry coverage gap in tribal districts, geocoding precision mix).

---

## Self-Review Notes

- **Spec coverage:** boundaries (Task 3), population (Task 4), KPHCC+OSM+Google-later facilities (Tasks 5-7, Google deferred per spec §4.3), roads (Task 8), merge/dedup (Task 9), district metrics/terrain/accessibility (Task 10), gap score + clustering (Task 11), demand forecast (Task 12), ML site suggestion (Task 13), all 6 shapefiles (Task 14), QGIS project + PyQGIS fallback (Task 15), HTML report with all required sections + disclaimer (Task 16), end-to-end verification + Artifact publish (Task 17). Google Places enrichment is intentionally not a task — spec marks it deferred until the user supplies a key; if/when supplied, it slots in as a new `scripts/05b_fetch_facilities_google.py` feeding Task 9's merge with a third `source="Google"` branch, without rerunning earlier stages.
- **Placeholder scan:** no TBD/TODO markers; every step has real code or a concrete research procedure (Task 4's population compilation is data entry, not algorithmic, and is specified as a procedure since exact 2023 figures aren't known until the live research happens — consistent with the Global Constraints note on schema/sanity-based verification for externally-sourced data).
- **Type consistency:** `district_metrics.csv` fieldnames introduced in Task 10 (`district,division,area_km2,population_2023,pop_density,terrain,facility_count,beds_per_1000,accessibility_km`) are extended in place by Task 11 (`gap_score,need_tier`) and Task 12 (`pop_2030,pop_2035,fac_nd30,fac_nd35`) and consumed with those exact names by Tasks 13, 14, and 16 — verified consistent throughout. Shapefile field names (all ≤10 chars) are defined once in Task 14 and reused verbatim in Task 15's QGIS styling (`pop_dens`, `gap_score`, `category`).
- **Scope:** single cohesive pipeline (not decomposed into independent sub-projects) since every stage feeds the next and the spec described one deliverable, not multiple independent subsystems.
