# Real Travel-Time Routing (Road Network + DEM) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the gap score's straight-line `accessibility_km` proxy with a real, network- and terrain-adjusted travel-time metric (`accessibility_min`), routed over the OSM road network with speeds derated by DEM-derived terrain difficulty.

**Architecture:** A new pipeline script (`scripts/16b_compute_travel_time_accessibility.py`) builds an in-Python `networkx` graph from the expanded OSM road fetch, snaps every facility and district centroid to it with a straight-line "last mile" leg, and finds every district's nearest-facility travel time in one weighted multi-source Dijkstra pass (via a virtual super-source node). The graph-building and routing logic lives in a new `scripts/lib/routing.py` module; a `scripts/lib/terrain.py` module is extracted from existing code so both the new script and the existing `08_compute_district_metrics.py` share one terrain-difficulty computation without duplication or a circular dependency.

**Tech Stack:** Python, `networkx` (graph + Dijkstra), `shapely` (district polygons/centroids), existing `scripts/lib/geo_utils.py` (haversine, point-in-polygon), `pytest`.

**Spec:** [docs/superpowers/specs/2026-08-15-travel-time-routing-design.md](../specs/2026-08-15-travel-time-routing-design.md)

## Global Constraints

- No external routing infrastructure (no OSRM/Docker/pgRouting) — in-Python `networkx` graph router only, matching this project's existing "single pipeline run, open data, no external infra" pattern.
- Road network expanded to include tertiary/unclassified/residential, not just motorway/trunk/primary/secondary.
- `BASE_SPEED_KMH` (km/h, by OSM `highway` tag): motorway 80, trunk 70, primary 60, secondary 45, tertiary 35, unclassified 20, residential 20. Unknown/untagged classes default to 20.
- `OFF_ROAD_SPEED_KMH = 15.0` — the assumed pace for both the last-mile snap leg (facility/centroid to nearest graph node) and the disconnected-component straight-line fallback. One constant, not two.
- Effective road speed: `BASE_SPEED_KMH[class] * (1 - 0.5 * terrain_difficulty)`, where `terrain_difficulty` is the existing per-district DEM-derived score (0-1) for whichever district the edge's midpoint falls in.
- Facility search is **global** — all ~500 KP facilities, not restricted to the same district (a confirmed correctness fix over the current per-district-restricted `nearest_facility_km`).
- Field rename: `accessibility_km` → `accessibility_min` everywhere it appears (`district_metrics.csv`, the gap-score `WEIGHTS` dict, the HTML report). Same 0.20 gap-score weight, same direction (higher = more underserved).
- `accessibility_km`/`accessibility_min` is not written to any shapefile — no GIS/QGIS project changes needed or in scope.
- A blank/missing `accessibility_min` reaching the gap-score computation must raise a clear `ValueError`, never silently default to `0.0` (which would wrongly bias a district toward looking well-served).
- Road-graph node keys are `(round(lon, 6), round(lat, 6))` tuples — two ways sharing a real OSM intersection emit the exact same float coordinate from Overpass's `out geom`, so this merges topology correctly without needing OSM node IDs.
- Test convention (follow exactly, matching `tests/test_merge_facilities.py`, `tests/test_suggest_sites.py`, `tests/test_dem_zonal.py`): unit-test the pure logic functions directly with in-memory fixtures; never test a numbered script's `main()` via monkeypatched file paths.
- Numbered script modules are imported in tests via `importlib.import_module("scripts.NN_name")`, never `import scripts.NN_name` directly (leading digits aren't a valid Python identifier start).
- Every new/changed docstring and code comment must actually be true after the change — this feature's whole point is correcting several currently-false "no routing engine available" / "straight-line only" claims, so don't leave any stale ones behind in code comments either, not just the report.

---

### Task 1: Extract terrain-difficulty scoring into `scripts/lib/terrain.py`

**Files:**
- Create: `scripts/lib/terrain.py`
- Create: `tests/lib/test_terrain.py`
- Modify: `scripts/08_compute_district_metrics.py`
- Modify: `tests/test_district_metrics.py`

**Interfaces:**
- Produces: `scripts.lib.terrain.compute_terrain_difficulty(rows: list[dict]) -> list[dict]` (adds a `terrain_difficulty` float key to each row, 0-1, based on min-max scaling `mean_elev_m`/`mean_slope_deg` across all rows passed in together), `scripts.lib.terrain.terrain_label(terrain_difficulty: float) -> str` (`"mountainous"` if `> 0.5` else `"plains"`). Task 4/5 (the new routing orchestrator) consumes `compute_terrain_difficulty` directly.

This is a pure refactor — moving two existing, already-correct functions out of `08_compute_district_metrics.py` into a shared module, with no behavior change. It's needed because the new routing script (Task 5) needs the same `terrain_difficulty` score for edge-speed derating, but can't get it from `district_terrain.csv` directly (that file only has raw `mean_elev_m`/`mean_slope_deg` — see spec §3b) or from `08`'s output (that would create a circular dependency, since `08` will depend on the new script's output).

- [ ] **Step 1: Write the failing tests in `tests/lib/test_terrain.py`**

```python
from scripts.lib import terrain


def test_terrain_difficulty_scales_0_to_1():
    rows = [
        {"district": "A", "mean_elev_m": 200, "mean_slope_deg": 1},
        {"district": "B", "mean_elev_m": 4000, "mean_slope_deg": 25},
        {"district": "C", "mean_elev_m": 2000, "mean_slope_deg": 12},
    ]
    scored = terrain.compute_terrain_difficulty(rows)
    by_name = {r["district"]: r["terrain_difficulty"] for r in scored}
    assert by_name["A"] < by_name["C"] < by_name["B"]
    assert all(0 <= v <= 1 for v in by_name.values())


def test_terrain_label_derived_from_difficulty():
    assert terrain.terrain_label(0.8) == "mountainous"
    assert terrain.terrain_label(0.2) == "plains"
    assert terrain.terrain_label(0.5) == "plains"  # boundary is exclusive on the mountainous side
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_terrain.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` (`scripts.lib.terrain` doesn't exist yet)

- [ ] **Step 3: Create `scripts/lib/terrain.py`**

```python
"""District terrain-difficulty scoring, shared by
scripts/08_compute_district_metrics.py (the report's terrain_difficulty
and terrain columns) and scripts/16b_compute_travel_time_accessibility.py
(edge-speed derating for routed accessibility) - both derive the same
continuous, DEM-based score from data/processed/district_terrain.csv's
raw elevation/slope, so it's defined once here rather than duplicated.
See docs/superpowers/specs/2026-08-15-travel-time-routing-design.md
section 3b for why this couldn't just live in one of the two consumers."""


def compute_terrain_difficulty(rows):
    """rows: list of dicts with mean_elev_m and mean_slope_deg (numeric).
    Returns the same rows with a terrain_difficulty field added: the mean
    of independently min-max-scaled elevation and slope, in [0,1]. Scaling
    is relative to the full set of rows passed in (i.e. call this once
    across all districts together, not per-row), so a district's score
    reflects its terrain difficulty relative to the rest of KP."""
    elevs = [float(r["mean_elev_m"]) for r in rows]
    slopes = [float(r["mean_slope_deg"]) for r in rows]
    elev_lo, elev_hi = min(elevs), max(elevs)
    slope_lo, slope_hi = min(slopes), max(slopes)
    out = []
    for r in rows:
        elev_n = (float(r["mean_elev_m"]) - elev_lo) / (elev_hi - elev_lo) if elev_hi > elev_lo else 0.0
        slope_n = (float(r["mean_slope_deg"]) - slope_lo) / (slope_hi - slope_lo) if slope_hi > slope_lo else 0.0
        row = dict(r)
        row["terrain_difficulty"] = round((elev_n + slope_n) / 2, 4)
        out.append(row)
    return out


def terrain_label(terrain_difficulty):
    return "mountainous" if terrain_difficulty > 0.5 else "plains"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_terrain.py -v`
Expected: 2 passed

- [ ] **Step 5: Update `scripts/08_compute_district_metrics.py` to import from the shared module**

Find:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district
from scripts.lib.geo_utils import haversine_km, polygon_area_km2

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"


def compute_terrain_difficulty(rows):
    """rows: list of dicts with mean_elev_m and mean_slope_deg (numeric).
    Returns the same rows with a terrain_difficulty field added: the mean
    of independently min-max-scaled elevation and slope, in [0,1]. Scaling
    is relative to the full set of rows passed in (i.e. call this once
    across all districts together, not per-row), so a district's score
    reflects its terrain difficulty relative to the rest of KP."""
    elevs = [float(r["mean_elev_m"]) for r in rows]
    slopes = [float(r["mean_slope_deg"]) for r in rows]
    elev_lo, elev_hi = min(elevs), max(elevs)
    slope_lo, slope_hi = min(slopes), max(slopes)
    out = []
    for r in rows:
        elev_n = (float(r["mean_elev_m"]) - elev_lo) / (elev_hi - elev_lo) if elev_hi > elev_lo else 0.0
        slope_n = (float(r["mean_slope_deg"]) - slope_lo) / (slope_hi - slope_lo) if slope_hi > slope_lo else 0.0
        row = dict(r)
        row["terrain_difficulty"] = round((elev_n + slope_n) / 2, 4)
        out.append(row)
    return out


def terrain_label(terrain_difficulty):
    return "mountainous" if terrain_difficulty > 0.5 else "plains"


def load_terrain():
```

Replace with:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district
from scripts.lib.geo_utils import haversine_km, polygon_area_km2
from scripts.lib.terrain import compute_terrain_difficulty, terrain_label

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"


def load_terrain():
```

- [ ] **Step 6: Remove the now-duplicated tests from `tests/test_district_metrics.py`**

Find:

```python
import importlib

metrics_mod = importlib.import_module("scripts.08_compute_district_metrics")


def test_terrain_difficulty_scales_0_to_1():
    rows = [
        {"district": "A", "mean_elev_m": 200, "mean_slope_deg": 1},
        {"district": "B", "mean_elev_m": 4000, "mean_slope_deg": 25},
        {"district": "C", "mean_elev_m": 2000, "mean_slope_deg": 12},
    ]
    scored = metrics_mod.compute_terrain_difficulty(rows)
    by_name = {r["district"]: r["terrain_difficulty"] for r in scored}
    assert by_name["A"] < by_name["C"] < by_name["B"]
    assert all(0 <= v <= 1 for v in by_name.values())


def test_terrain_label_derived_from_difficulty():
    assert metrics_mod.terrain_label(0.8) == "mountainous"
    assert metrics_mod.terrain_label(0.2) == "plains"
    assert metrics_mod.terrain_label(0.5) == "plains"  # boundary is exclusive on the mountainous side


def test_nearest_facility_km_finds_closest():
```

Replace with:

```python
import importlib

metrics_mod = importlib.import_module("scripts.08_compute_district_metrics")


def test_nearest_facility_km_finds_closest():
```

- [ ] **Step 7: Run the full test suite to confirm nothing broke**

Run: `pytest tests/ -q`
Expected: all tests pass (the terrain tests moved, not lost; `nearest_facility_km` tests still present and passing — they're removed in Task 6, not here)

- [ ] **Step 8: Commit**

```bash
git add scripts/lib/terrain.py tests/lib/test_terrain.py scripts/08_compute_district_metrics.py tests/test_district_metrics.py
git commit -m "refactor: extract terrain-difficulty scoring into scripts/lib/terrain.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Expand OSM road fetch to tertiary/unclassified/residential

**Files:**
- Modify: `scripts/06_fetch_roads_osm.py`
- Create: `tests/test_fetch_roads.py`

**Interfaces:**
- Produces: `scripts.06_fetch_roads_osm.QUERY_TEMPLATE` (str, now including 7 road classes), `scripts.06_fetch_roads_osm.parse_elements(data: dict) -> list[dict]` (unchanged behavior — each dict has `"coordinates": [[lon, lat], ...]`, `"road_class": str`, `"name": str`, `"osm_id": int`). Task 5's orchestrator reads `data/raw/osm_roads.json`, the file this script writes, whose records are exactly `parse_elements`'s output serialized to JSON.

- [ ] **Step 1: Write the failing tests in `tests/test_fetch_roads.py`**

```python
import importlib

roads_mod = importlib.import_module("scripts.06_fetch_roads_osm")


def test_query_template_includes_all_seven_road_classes():
    for road_class in ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential"]:
        assert f'"highway"="{road_class}"' in roads_mod.QUERY_TEMPLATE


def test_parse_elements_keeps_tertiary_roads():
    data = {
        "elements": [
            {
                "type": "way",
                "id": 1,
                "tags": {"highway": "tertiary", "name": "Village Link Road"},
                "geometry": [{"lon": 71.0, "lat": 34.0}, {"lon": 71.01, "lat": 34.01}],
            }
        ]
    }
    records = roads_mod.parse_elements(data)
    assert len(records) == 1
    assert records[0]["road_class"] == "tertiary"
    assert records[0]["coordinates"] == [[71.0, 34.0], [71.01, 34.01]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_roads.py -v`
Expected: `test_query_template_includes_all_seven_road_classes` FAILs (tertiary/unclassified/residential not yet in the query); `test_parse_elements_keeps_tertiary_roads` already PASSes (that function's logic isn't changing) — confirms the second test is a regression guard, not new behavior

- [ ] **Step 3: Expand the query and docstring**

Find:

```python
"""Fetch major OSM roads (motorway/trunk/primary/secondary) within KP's
bounding box via Overpass, used as an accessibility-proxy layer and for
the HTML report's context maps. Full routing/travel-time is out of scope
(no routing engine available) — straight-line distance is the documented
accessibility proxy used in scripts/08_compute_district_metrics.py."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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
```

Replace with:

```python
"""Fetch OSM roads (motorway/trunk/primary/secondary/tertiary/
unclassified/residential) within KP's bounding box via Overpass, used for
the HTML report's road-context map and as the routable network for
scripts/16b_compute_travel_time_accessibility.py's travel-time
accessibility metric. The expanded road classes (beyond the original
major-roads-only set) are needed to reach rural facilities that don't sit
on a primary/secondary road - a sparser network would leave most
district-to-facility routes with no path to the mapped network at all
near their endpoints. Query size/timeout is correspondingly larger than a
major-roads-only fetch; if Overpass still times out in practice, splitting
the query by district or road-class batch is the next lever (not yet
needed as of this writing)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.http_utils import make_session

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
KP_BBOX = (31.0, 69.2, 36.9, 74.1)

QUERY_TEMPLATE = """
[out:json][timeout:300];
(
  way["highway"="motorway"]({bbox});
  way["highway"="trunk"]({bbox});
  way["highway"="primary"]({bbox});
  way["highway"="secondary"]({bbox});
  way["highway"="tertiary"]({bbox});
  way["highway"="unclassified"]({bbox});
  way["highway"="residential"]({bbox});
);
out geom;
"""


def fetch():
    session = make_session()
    bbox_str = ",".join(str(v) for v in KP_BBOX)
    query = QUERY_TEMPLATE.format(bbox=bbox_str)
    resp = session.post(OVERPASS_URL, data={"data": query}, timeout=400)
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch_roads.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/06_fetch_roads_osm.py tests/test_fetch_roads.py
git commit -m "feat: expand OSM road fetch to tertiary/unclassified/residential

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `scripts/lib/routing.py` — graph construction & edge weights

**Files:**
- Create: `scripts/lib/routing.py`
- Create: `tests/lib/test_routing.py`

**Interfaces:**
- Consumes: `scripts.lib.geo_utils.haversine_km(lon1, lat1, lon2, lat2) -> float` (existing).
- Produces: `scripts.lib.routing.BASE_SPEED_KMH: dict`, `scripts.lib.routing.OFF_ROAD_SPEED_KMH: float`, `scripts.lib.routing.effective_speed_kmh(road_class: str, terrain_difficulty: float) -> float`, `scripts.lib.routing.build_graph(road_records: list[dict], terrain_lookup: Callable[[float, float], float]) -> networkx.Graph` (edges weighted by `"minutes"` and `"length_km"`), `scripts.lib.routing.nearest_node(graph, lon: float, lat: float) -> tuple[node_key, float]`, `scripts.lib.routing.snap_point(graph, lon: float, lat: float, off_road_speed_kmh: float = OFF_ROAD_SPEED_KMH) -> tuple[node_key, float]`. Task 4 adds `add_super_source`/`compute_travel_times`/`compute_district_accessibility` to this same file, consuming these four.

- [ ] **Step 1: Write the failing tests in `tests/lib/test_routing.py`**

```python
from scripts.lib import routing


def test_effective_speed_kmh_derates_by_terrain():
    flat = routing.effective_speed_kmh("primary", terrain_difficulty=0.0)
    mountainous = routing.effective_speed_kmh("primary", terrain_difficulty=1.0)
    assert flat == routing.BASE_SPEED_KMH["primary"]
    assert mountainous == routing.BASE_SPEED_KMH["primary"] * 0.5


def test_effective_speed_kmh_unknown_road_class_uses_default():
    assert routing.effective_speed_kmh("weird_unmapped_tag", terrain_difficulty=0.0) == 20.0


def test_build_graph_merges_shared_intersection_coordinate():
    # Two ways sharing the exact vertex (71.0, 34.0) - a real OSM
    # intersection - must become one graph node, not two.
    road_records = [
        {"road_class": "primary", "coordinates": [[71.0, 34.0], [71.1, 34.1]]},
        {"road_class": "secondary", "coordinates": [[71.0, 34.0], [70.9, 33.9]]},
    ]
    graph = routing.build_graph(road_records, terrain_lookup=lambda lon, lat: 0.0)
    assert graph.number_of_nodes() == 3  # the shared node + the two distinct endpoints
    assert graph.number_of_edges() == 2


def test_build_graph_edge_minutes_match_formula():
    road_records = [{"road_class": "primary", "coordinates": [[71.0, 34.0], [71.0, 34.1]]}]
    graph = routing.build_graph(road_records, terrain_lookup=lambda lon, lat: 0.5)
    from scripts.lib.geo_utils import haversine_km
    length_km = haversine_km(71.0, 34.0, 71.0, 34.1)
    expected_minutes = (length_km / (60.0 * 0.75)) * 60  # primary=60 km/h * (1 - 0.5*0.5)
    node_a, node_b = (71.0, 34.0), (71.0, 34.1)
    assert graph[node_a][node_b]["minutes"] == expected_minutes


def test_nearest_node_finds_closest():
    road_records = [{"road_class": "primary", "coordinates": [[71.0, 34.0], [71.5, 34.5]]}]
    graph = routing.build_graph(road_records, terrain_lookup=lambda lon, lat: 0.0)
    node, distance_km = routing.nearest_node(graph, 71.45, 34.45)
    assert node == (71.5, 34.5)
    from scripts.lib.geo_utils import haversine_km
    assert distance_km == haversine_km(71.45, 34.45, 71.5, 34.5)


def test_nearest_node_empty_graph_raises():
    import networkx as nx
    import pytest
    with pytest.raises(ValueError, match="empty road graph"):
        routing.nearest_node(nx.Graph(), 71.0, 34.0)


def test_snap_point_returns_node_and_off_road_minutes():
    road_records = [{"road_class": "primary", "coordinates": [[71.0, 34.0], [71.5, 34.5]]}]
    graph = routing.build_graph(road_records, terrain_lookup=lambda lon, lat: 0.0)
    node, off_road_minutes = routing.snap_point(graph, 71.45, 34.45, off_road_speed_kmh=15.0)
    from scripts.lib.geo_utils import haversine_km
    distance_km = haversine_km(71.45, 34.45, 71.5, 34.5)
    assert node == (71.5, 34.5)
    assert off_road_minutes == (distance_km / 15.0) * 60
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_routing.py -v`
Expected: FAIL with `ModuleNotFoundError` (`scripts.lib.routing` doesn't exist yet)

- [ ] **Step 3: Create `scripts/lib/routing.py`**

```python
"""In-Python travel-time routing over the OSM road network, weighted by
road class and DEM-derived terrain difficulty. Builds a graph from
scripts/06_fetch_roads_osm.py's road geometry, connects off-network points
(district centroids, facilities) via a straight-line "last mile" leg at a
fixed off-road pace, and finds nearest-facility travel time for every
district in one multi-source Dijkstra pass. See
docs/superpowers/specs/2026-08-15-travel-time-routing-design.md for the
full design rationale."""
import networkx as nx

from scripts.lib.geo_utils import haversine_km

# Assumed free-flow speed by OSM highway tag, km/h - a documented planning
# assumption like every other norm in this report, not a measured value.
BASE_SPEED_KMH = {
    "motorway": 80.0,
    "trunk": 70.0,
    "primary": 60.0,
    "secondary": 45.0,
    "tertiary": 35.0,
    "unclassified": 20.0,
    "residential": 20.0,
}
DEFAULT_SPEED_KMH = 20.0  # any road_class not in BASE_SPEED_KMH (unexpected OSM tag)

# Assumed pace for the straight-line "last mile" between a point (facility
# or district centroid) and the nearest graph node, and the fallback speed
# for districts whose road-network component never reaches any facility's
# component at all. Rough-track/4x4 pace, not walking speed, since most of
# this gap is unmapped track rather than trailless terrain.
OFF_ROAD_SPEED_KMH = 15.0


def _node_key(lon, lat):
    return (round(lon, 6), round(lat, 6))


def effective_speed_kmh(road_class, terrain_difficulty):
    base = BASE_SPEED_KMH.get(road_class, DEFAULT_SPEED_KMH)
    return base * (1 - 0.5 * terrain_difficulty)


def build_graph(road_records, terrain_lookup):
    """road_records: list of dicts like scripts/06_fetch_roads_osm.py's
    output ({"coordinates": [[lon, lat], ...], "road_class": str, ...}).
    terrain_lookup: callable(lon, lat) -> terrain_difficulty in [0,1] for
    whichever district that point falls in. Returns an undirected
    networkx.Graph with edges weighted by "minutes" (and "length_km" kept
    for reference/debugging). No oneway handling - a deliberate
    simplification for an accessibility model, not turn-by-turn
    navigation."""
    graph = nx.Graph()
    for rec in road_records:
        coords = rec["coordinates"]
        road_class = rec.get("road_class", "unclassified")
        for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
            length_km = haversine_km(lon1, lat1, lon2, lat2)
            mid_lon, mid_lat = (lon1 + lon2) / 2, (lat1 + lat2) / 2
            terrain_difficulty = terrain_lookup(mid_lon, mid_lat)
            speed = effective_speed_kmh(road_class, terrain_difficulty)
            minutes = (length_km / speed) * 60 if speed > 0 else float("inf")
            a, b = _node_key(lon1, lat1), _node_key(lon2, lat2)
            if a == b:
                continue  # degenerate zero-length segment
            if graph.has_edge(a, b) and graph[a][b]["minutes"] <= minutes:
                continue  # keep the faster parallel edge if this pair is already connected
            graph.add_edge(a, b, minutes=minutes, length_km=length_km)
    return graph


def nearest_node(graph, lon, lat):
    """Brute-force nearest graph node to (lon, lat) by haversine distance.
    Returns (node_key, distance_km). Raises ValueError if the graph has no
    nodes at all."""
    if graph.number_of_nodes() == 0:
        raise ValueError("Cannot snap to an empty road graph")
    best_node, best_km = None, float("inf")
    for node in graph.nodes:
        node_lon, node_lat = node
        d = haversine_km(lon, lat, node_lon, node_lat)
        if d < best_km:
            best_node, best_km = node, d
    return best_node, best_km


def snap_point(graph, lon, lat, off_road_speed_kmh=OFF_ROAD_SPEED_KMH):
    """Snap (lon, lat) to its nearest graph node. Returns (node_key,
    off_road_minutes) - the node and the straight-line "last mile" time to
    reach it."""
    node, distance_km = nearest_node(graph, lon, lat)
    return node, (distance_km / off_road_speed_kmh) * 60
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_routing.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/routing.py tests/lib/test_routing.py
git commit -m "feat: add road-graph construction and edge-weight model to scripts/lib/routing.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `scripts/lib/routing.py` — multi-source Dijkstra, fallback, orchestrator

**Files:**
- Modify: `scripts/lib/routing.py`
- Modify: `tests/lib/test_routing.py`

**Interfaces:**
- Consumes: everything from Task 3 (`build_graph`, `snap_point`, `OFF_ROAD_SPEED_KMH`), plus `scripts.lib.geo_utils.find_containing_district(lon, lat, districts) -> str | None` (existing).
- Produces: `scripts.lib.routing.add_super_source(graph, facility_snap_points: list[tuple]) -> str` (returns the super-source node key), `scripts.lib.routing.compute_travel_times(graph, super_source) -> dict`, `scripts.lib.routing.compute_district_accessibility(road_records: list[dict], facilities: list[dict], districts: list[dict], terrain_by_district: dict) -> dict[str, float | None]` — the function Task 5's orchestrator script calls directly. `districts` entries need `"district"`, `"geometry"` (shapely geometry), `"centroid_lon"`, `"centroid_lat"` keys; `facilities` entries need `"lon"`, `"lat"`.

- [ ] **Step 1: Write the failing tests (append to `tests/lib/test_routing.py`)**

```python
from shapely.geometry import Polygon


def test_add_super_source_connects_every_facility():
    road_records = [{"road_class": "primary", "coordinates": [[71.0, 34.0], [71.5, 34.5]]}]
    graph = routing.build_graph(road_records, terrain_lookup=lambda lon, lat: 0.0)
    facility_snaps = [routing.snap_point(graph, 71.0, 34.0), routing.snap_point(graph, 71.5, 34.5)]
    super_source = routing.add_super_source(graph, facility_snaps)
    assert graph.has_edge(super_source, (71.0, 34.0))
    assert graph.has_edge(super_source, (71.5, 34.5))


def test_compute_travel_times_matches_min_of_individual_dijkstra():
    import networkx as nx
    road_records = [
        {"road_class": "primary", "coordinates": [[71.0, 34.0], [71.1, 34.0], [71.2, 34.0]]},
    ]
    graph = routing.build_graph(road_records, terrain_lookup=lambda lon, lat: 0.0)
    facility_snaps = [routing.snap_point(graph, 71.0, 34.0), routing.snap_point(graph, 71.2, 34.0)]
    super_source = routing.add_super_source(graph, facility_snaps)
    travel_times = routing.compute_travel_times(graph, super_source)
    middle_node = (71.1, 34.0)
    # Two individual Dijkstra runs (one per facility, ignoring their own
    # off-road legs since both are 0 here - both points sit exactly on the
    # graph), taking the min at the middle node, must match the multi-source result.
    d_from_a = nx.dijkstra_path_length(graph, (71.0, 34.0), middle_node, weight="minutes")
    d_from_b = nx.dijkstra_path_length(graph, (71.2, 34.0), middle_node, weight="minutes")
    assert travel_times[middle_node] == pytest.approx(min(d_from_a, d_from_b))


def test_compute_district_accessibility_routes_through_network():
    road_records = [{"road_class": "primary", "coordinates": [[71.0, 34.0], [71.0, 34.1]]}]
    facilities = [{"lon": 71.0, "lat": 34.0}]
    districts = [{
        "district": "TestDistrict",
        "geometry": Polygon([(70.9, 33.9), (71.1, 33.9), (71.1, 34.2), (70.9, 34.2)]),
        "centroid_lon": 71.0, "centroid_lat": 34.1,
    }]
    result = routing.compute_district_accessibility(road_records, facilities, districts, terrain_by_district={"TestDistrict": 0.0})
    assert result["TestDistrict"] is not None
    assert result["TestDistrict"] > 0


def test_compute_district_accessibility_falls_back_when_disconnected():
    # Two separate road components: the facility sits on one, the district
    # centroid snaps to the other - no path exists between them.
    road_records = [
        {"road_class": "primary", "coordinates": [[71.0, 34.0], [71.01, 34.0]]},   # facility's component
        {"road_class": "primary", "coordinates": [[75.0, 38.0], [75.01, 38.0]]},   # district's component, far away
    ]
    facilities = [{"lon": 71.0, "lat": 34.0}]
    districts = [{
        "district": "Isolated",
        "geometry": Polygon([(74.9, 37.9), (75.1, 37.9), (75.1, 38.1), (74.9, 38.1)]),
        "centroid_lon": 75.005, "centroid_lat": 38.0,
    }]
    result = routing.compute_district_accessibility(road_records, facilities, districts, terrain_by_district={"Isolated": 0.0})
    from scripts.lib.geo_utils import haversine_km
    expected_km = haversine_km(75.005, 38.0, 71.0, 34.0)
    expected_minutes = round((expected_km / routing.OFF_ROAD_SPEED_KMH) * 60, 2)
    assert result["Isolated"] == expected_minutes


def test_compute_district_accessibility_empty_graph_falls_back_for_every_district():
    facilities = [{"lon": 71.0, "lat": 34.0}]
    districts = [{
        "district": "NoRoads",
        "geometry": Polygon([(70.9, 33.9), (71.1, 33.9), (71.1, 34.1), (70.9, 34.1)]),
        "centroid_lon": 71.0, "centroid_lat": 34.05,
    }]
    result = routing.compute_district_accessibility([], facilities, districts, terrain_by_district={"NoRoads": 0.0})
    assert result["NoRoads"] is not None


def test_compute_district_accessibility_no_facilities_returns_none():
    districts = [{
        "district": "Empty",
        "geometry": Polygon([(70.9, 33.9), (71.1, 33.9), (71.1, 34.1), (70.9, 34.1)]),
        "centroid_lon": 71.0, "centroid_lat": 34.0,
    }]
    result = routing.compute_district_accessibility([], [], districts, terrain_by_district={"Empty": 0.0})
    assert result["Empty"] is None
```

Add `import pytest` to the top of `tests/lib/test_routing.py` alongside the existing `from scripts.lib import routing` (needed for `pytest.approx`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_routing.py -v`
Expected: the 6 new tests FAIL with `AttributeError` (`add_super_source`/`compute_travel_times`/`compute_district_accessibility` don't exist yet); the 7 existing tests from Task 3 still PASS

- [ ] **Step 3: Append the routing/fallback/orchestrator functions to `scripts/lib/routing.py`**

Find:

```python
def snap_point(graph, lon, lat, off_road_speed_kmh=OFF_ROAD_SPEED_KMH):
    """Snap (lon, lat) to its nearest graph node. Returns (node_key,
    off_road_minutes) - the node and the straight-line "last mile" time to
    reach it."""
    node, distance_km = nearest_node(graph, lon, lat)
    return node, (distance_km / off_road_speed_kmh) * 60
```

Replace with:

```python
def snap_point(graph, lon, lat, off_road_speed_kmh=OFF_ROAD_SPEED_KMH):
    """Snap (lon, lat) to its nearest graph node. Returns (node_key,
    off_road_minutes) - the node and the straight-line "last mile" time to
    reach it."""
    node, distance_km = nearest_node(graph, lon, lat)
    return node, (distance_km / off_road_speed_kmh) * 60


SUPER_SOURCE = "__super_source__"


def add_super_source(graph, facility_snap_points):
    """facility_snap_points: list of (node_key, off_road_minutes) tuples,
    one per facility (from snap_point()). Adds a virtual super-source node
    connected to every facility's snapped node at that facility's off-road
    time, so a single Dijkstra run from the super-source gives every
    node's travel time to its nearest facility. Returns the super-source's
    node key."""
    for node, off_road_minutes in facility_snap_points:
        if graph.has_edge(SUPER_SOURCE, node) and graph[SUPER_SOURCE][node]["minutes"] <= off_road_minutes:
            continue
        graph.add_edge(SUPER_SOURCE, node, minutes=off_road_minutes, length_km=0.0)
    return SUPER_SOURCE


def compute_travel_times(graph, super_source):
    """Returns {node_key: minutes} - every node's shortest travel time
    from the super-source (i.e. to its nearest facility). Nodes in a
    disconnected component with no facility are simply absent from the
    result."""
    return nx.single_source_dijkstra_path_length(graph, super_source, weight="minutes")


def _straight_line_fallback_minutes(lon, lat, facilities):
    """Used when the road graph can't connect a point to any facility at
    all - either the whole graph is empty, or the point's component never
    reaches a facility's component. Same OFF_ROAD_SPEED_KMH pace as the
    last-mile snap leg, not a second invented constant."""
    nearest_km = min(haversine_km(lon, lat, f["lon"], f["lat"]) for f in facilities)
    return (nearest_km / OFF_ROAD_SPEED_KMH) * 60


def compute_district_accessibility(road_records, facilities, districts, terrain_by_district):
    """Top-level orchestrator. road_records: scripts/06_fetch_roads_osm.py's
    output. facilities: list of {"lon": float, "lat": float} - every KP
    facility, searched globally regardless of district (see design spec
    section 3a). districts: list of {"district": str, "geometry": shapely
    geometry, "centroid_lon": float, "centroid_lat": float}.
    terrain_by_district: {district_name: terrain_difficulty}. Returns
    {district_name: accessibility_min}, routed where the road network
    connects a district to a facility, falling back to straight-line
    distance at OFF_ROAD_SPEED_KMH where it doesn't (disconnected
    component, or an empty road graph), or None if there are no facilities
    anywhere in KP at all (matching scripts/08's previous "n/a"
    convention for that theoretical case)."""
    if not facilities:
        return {d["district"]: None for d in districts}

    def terrain_lookup(lon, lat):
        name = find_containing_district(lon, lat, districts)
        return terrain_by_district.get(name, 0.0)

    graph = build_graph(road_records, terrain_lookup)

    if graph.number_of_nodes() == 0:
        return {
            d["district"]: round(_straight_line_fallback_minutes(d["centroid_lon"], d["centroid_lat"], facilities), 2)
            for d in districts
        }

    facility_snaps = [snap_point(graph, f["lon"], f["lat"]) for f in facilities]
    super_source = add_super_source(graph, facility_snaps)
    travel_times = compute_travel_times(graph, super_source)

    result = {}
    for d in districts:
        centroid_node, off_road_minutes = snap_point(graph, d["centroid_lon"], d["centroid_lat"])
        routed_minutes = travel_times.get(centroid_node)
        if routed_minutes is not None:
            result[d["district"]] = round(routed_minutes + off_road_minutes, 2)
        else:
            result[d["district"]] = round(
                _straight_line_fallback_minutes(d["centroid_lon"], d["centroid_lat"], facilities), 2
            )
    return result
```

Also find (top of file, imports):

```python
import networkx as nx

from scripts.lib.geo_utils import haversine_km
```

Replace with:

```python
import networkx as nx

from scripts.lib.geo_utils import find_containing_district, haversine_km
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_routing.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/routing.py tests/lib/test_routing.py
git commit -m "feat: add multi-source Dijkstra routing and disconnected-component fallback

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `scripts/16b_compute_travel_time_accessibility.py` orchestrator + pipeline wiring

**Files:**
- Create: `scripts/16b_compute_travel_time_accessibility.py`
- Create: `tests/test_travel_time_accessibility.py`
- Modify: `scripts/run_all.py`

**Interfaces:**
- Consumes: `scripts.lib.terrain.compute_terrain_difficulty` (Task 1), `scripts.lib.routing.compute_district_accessibility` (Task 4).
- Produces: `data/processed/district_travel_time.csv` (columns `district,accessibility_min`), read by Task 6's `08_compute_district_metrics.py`. Also produces the pure functions `scripts.16b_compute_travel_time_accessibility.build_districts_with_centroids(boundaries: dict) -> list[dict]`, `load_facilities(rows: list[dict]) -> list[dict]`, `build_terrain_by_district(terrain_rows: list[dict]) -> dict`.

- [ ] **Step 1: Write the failing tests in `tests/test_travel_time_accessibility.py`**

```python
import importlib

travel_time_mod = importlib.import_module("scripts.16b_compute_travel_time_accessibility")


def test_build_districts_with_centroids_computes_geometric_centroid():
    boundaries = {
        "districts": [
            {
                "district": "TestDistrict",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[71.0, 34.0], [71.2, 34.0], [71.2, 34.2], [71.0, 34.2], [71.0, 34.0]]],
                },
            }
        ]
    }
    result = travel_time_mod.build_districts_with_centroids(boundaries)
    assert len(result) == 1
    assert result[0]["district"] == "TestDistrict"
    assert result[0]["centroid_lon"] == pytest.approx(71.1)
    assert result[0]["centroid_lat"] == pytest.approx(34.1)


def test_load_facilities_drops_duplicates_and_keeps_lon_lat():
    rows = [
        {"lon": "71.5", "lat": "34.5", "is_duplicate_of": ""},
        {"lon": "71.6", "lat": "34.6", "is_duplicate_of": "Some Other Hospital"},
    ]
    facilities = travel_time_mod.load_facilities(rows)
    assert facilities == [{"lon": 71.5, "lat": 34.5}]


def test_build_terrain_by_district_returns_name_to_difficulty_map():
    terrain_rows = [
        {"district": "A", "mean_elev_m": 200, "mean_slope_deg": 1},
        {"district": "B", "mean_elev_m": 4000, "mean_slope_deg": 25},
    ]
    result = travel_time_mod.build_terrain_by_district(terrain_rows)
    assert set(result.keys()) == {"A", "B"}
    assert result["A"] < result["B"]
```

Add `import pytest` to the top of the file (needed for `pytest.approx`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_travel_time_accessibility.py -v`
Expected: FAIL with `ModuleNotFoundError` (the script doesn't exist yet)

- [ ] **Step 3: Create `scripts/16b_compute_travel_time_accessibility.py`**

```python
"""Compute each district's accessibility_min: network- and terrain-
adjusted travel time (minutes) to the nearest KP facility, searched
globally across all of KP rather than restricted to the district's own
mapped facilities (see docs/superpowers/specs/2026-08-15-travel-time-
routing-design.md section 3a). Routes over the OSM road network
(data/raw/osm_roads.json, scripts/06_fetch_roads_osm.py), with road speed
derated by the DEM-derived terrain_difficulty score
(scripts/lib/terrain.py) for whichever district each road segment sits
in, and a straight-line "last mile" leg connecting facilities/district
centroids to the mapped network. Falls back to a straight-line estimate
where the road network doesn't connect a district to any facility at all
(scripts/lib/routing.py handles both the routing and the fallback).
Writes data/processed/district_travel_time.csv, consumed by
scripts/08_compute_district_metrics.py."""
import csv
import json
import sys
from pathlib import Path

from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib import routing
from scripts.lib.terrain import compute_terrain_difficulty

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"


def build_districts_with_centroids(boundaries):
    """boundaries: parsed boundaries.json dict. Returns a list of
    {"district": str, "geometry": shapely geometry, "centroid_lon": float,
    "centroid_lat": float} - the shape scripts.lib.routing.compute_district_accessibility
    expects. Uses the district polygon's plain geometric centroid, the
    same point scripts/08_compute_district_metrics.py has always used
    (via geom.centroid) despite that script's docstring historically
    saying "population centroid" - not actually population-weighted."""
    out = []
    for d in boundaries["districts"]:
        geom = shape(d["geometry"])
        centroid = geom.centroid
        out.append({
            "district": d["district"],
            "geometry": geom,
            "centroid_lon": centroid.x,
            "centroid_lat": centroid.y,
        })
    return out


def load_facilities(rows):
    """rows: csv.DictReader rows from data/processed/facilities_merged.csv.
    Returns [{"lon": float, "lat": float}, ...] for every non-duplicate
    facility in all of KP (global search, not restricted to the same
    district - see module docstring)."""
    return [
        {"lon": float(r["lon"]), "lat": float(r["lat"])}
        for r in rows
        if not r["is_duplicate_of"]
    ]


def build_terrain_by_district(terrain_rows):
    """terrain_rows: csv.DictReader rows from
    data/processed/district_terrain.csv. Returns {district_name:
    terrain_difficulty} via the shared scripts.lib.terrain function."""
    scored = compute_terrain_difficulty(list(terrain_rows))
    return {r["district"]: r["terrain_difficulty"] for r in scored}


def main():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    districts = build_districts_with_centroids(boundaries)

    road_records = json.loads((RAW / "osm_roads.json").read_text())

    with open(PROCESSED / "facilities_merged.csv", newline="", encoding="utf-8") as f:
        facilities = load_facilities(csv.DictReader(f))

    with open(PROCESSED / "district_terrain.csv", newline="", encoding="utf-8") as f:
        terrain_by_district = build_terrain_by_district(list(csv.DictReader(f)))

    accessibility = routing.compute_district_accessibility(road_records, facilities, districts, terrain_by_district)

    out_path = PROCESSED / "district_travel_time.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "accessibility_min"])
        writer.writeheader()
        for d in districts:
            value = accessibility[d["district"]]
            writer.writerow({"district": d["district"], "accessibility_min": value if value is not None else ""})
    print(f"Wrote district_travel_time.csv for {len(districts)} districts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_travel_time_accessibility.py -v`
Expected: 3 passed

- [ ] **Step 5: Wire the new stage into `scripts/run_all.py`**

Find:

```python
    "16_compute_dem_zonal_stats.py",          # needs KP_DEM.tif (15) + boundaries.json
    "17_extract_devstats_health.py",          # independent - reads the Dev Stats PDF directly
```

Replace with:

```python
    "16_compute_dem_zonal_stats.py",          # needs KP_DEM.tif (15) + boundaries.json
    "16b_compute_travel_time_accessibility.py",  # needs 06 (roads) + 07 (facilities) + 16 (terrain) + boundaries.json
    "17_extract_devstats_health.py",          # independent - reads the Dev Stats PDF directly
```

- [ ] **Step 6: Commit**

```bash
git add scripts/16b_compute_travel_time_accessibility.py tests/test_travel_time_accessibility.py scripts/run_all.py
git commit -m "feat: add scripts/16b_compute_travel_time_accessibility.py pipeline stage

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Wire `accessibility_min` into `scripts/08_compute_district_metrics.py`

**Files:**
- Modify: `scripts/08_compute_district_metrics.py`
- Delete: `tests/test_district_metrics.py`

**Interfaces:**
- Consumes: `data/processed/district_travel_time.csv` (Task 5's output, columns `district,accessibility_min`).
- Produces: `district_metrics.csv` now has an `accessibility_min` column instead of `accessibility_km`, consumed by Task 7's `09_gap_score_and_clusters.py` and Task 8's `14_build_html_report.py`.

- [ ] **Step 1: Remove the two `nearest_facility_km` tests from `tests/test_district_metrics.py`**

`nearest_facility_km` is deleted in this task with no drop-in pure-function replacement — the new accessibility value is just a dict lookup against `district_travel_time.csv` (via `load_travel_time()`), the same shape as this file's other already-untested loaders (`load_population()`, `load_dev_stats_health()`, `load_terrain()` all have no dedicated tests either — this project's convention only unit-tests functions with real branching logic, via in-memory fixtures, never file I/O; see Global Constraints). So this task removes tests rather than adding any — there's nothing new here worth a file-I/O test that the rest of this module doesn't already skip testing for the same reason.

Find:

```python
import importlib

metrics_mod = importlib.import_module("scripts.08_compute_district_metrics")


def test_nearest_facility_km_finds_closest():
    facilities = [
        {"lon": 71.0, "lat": 34.0},
        {"lon": 71.5, "lat": 34.5},
    ]
    d = metrics_mod.nearest_facility_km(71.45, 34.45, facilities)
    from scripts.lib.geo_utils import haversine_km
    assert d == haversine_km(71.45, 34.45, 71.5, 34.5)


def test_nearest_facility_km_empty_list_returns_none():
    assert metrics_mod.nearest_facility_km(71.0, 34.0, []) is None
```

Delete this entire file's contents (it becomes empty of tests once these two are removed — `test_terrain_difficulty_scales_0_to_1`/`test_terrain_label_derived_from_difficulty` already moved out in Task 1). An empty test file isn't useful to keep around: this module's only two pieces of independently-testable logic have now both been extracted elsewhere (`scripts/lib/terrain.py` in Task 1, `scripts/lib/routing.py` in Tasks 3-4) or deleted outright, matching `scripts/16_compute_dem_zonal_stats.py`'s own convention of having zero tests on its `main()`-level orchestration, only on its extracted pure helper.

- [ ] **Step 2: Delete `tests/test_district_metrics.py`**

```bash
git rm tests/test_district_metrics.py
```

- [ ] **Step 3: Wire `accessibility_min` into `scripts/08_compute_district_metrics.py`**

Find:

```python
"""Compute per-district metrics feeding the gap-score analysis: area,
population density, a straight-line accessibility proxy (nearest facility
to the district's population centroid — no routing engine available in
this environment, so this is documented as a proxy, not a real travel
time), a DEM-derived continuous terrain difficulty score
(data/processed/district_terrain.csv, built by
scripts/16_compute_dem_zonal_stats.py from the Copernicus GLO-30 DEM) in
place of the earlier hand-classified mountainous/plains flag, and
institution/bed/doctor counts sourced from Development Statistics of KP
2025 (data/processed/dev_stats_health.csv), the official KP Bureau of
Statistics publication, used as the primary source for facility-density,
bed-capacity, and staffing figures throughout the gap-score model and
report in preference to the merged KPHCC/OSM facility registry - which
is kept only for the two things Dev Stats cannot provide (it publishes
district totals, not site locations): the accessibility_km straight-line
proxy and the facility-distribution map."""
import csv
import json
import sys
from pathlib import Path

from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district
from scripts.lib.geo_utils import haversine_km, polygon_area_km2
from scripts.lib.terrain import compute_terrain_difficulty, terrain_label

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"


def load_terrain():
    with open(PROCESSED / "district_terrain.csv", newline="", encoding="utf-8") as f:
        return {normalize_district(r["district"]): r for r in csv.DictReader(f)}


def nearest_facility_km(centroid_lon, centroid_lat, facilities):
    if not facilities:
        return None
    return min(
        haversine_km(centroid_lon, centroid_lat, f["lon"], f["lat"]) for f in facilities
    )


def load_population():
```

Replace with:

```python
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
```

Find:

```python
def main():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    population = load_population()
    facilities_by_district = load_facilities_by_district()
    terrain = load_terrain()
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

        centroid = geom.centroid
        accessibility_km = nearest_facility_km(centroid.x, centroid.y, facilities)

        terrain_row = terrain.get(name)
```

Replace with:

```python
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

        terrain_row = terrain.get(name)
```

Find:

```python
                "govt_pvt_institutions": govt_institutions + pvt_hospitals,
                "facility_count": len(facilities),
                "beds_per_1000": beds_per_1000,
                "doctors_per_1000": doctors_per_1000,
                "accessibility_km": round(accessibility_km, 2) if accessibility_km is not None else "",
            }
        )
```

Replace with:

```python
                "govt_pvt_institutions": govt_institutions + pvt_hospitals,
                "facility_count": len(facilities),
                "beds_per_1000": beds_per_1000,
                "doctors_per_1000": doctors_per_1000,
                "accessibility_min": accessibility_min if accessibility_min is not None else "",
            }
        )
```

- [ ] **Step 4: Run the full test suite to confirm nothing broke**

Run: `pytest tests/ -q`
Expected: all tests pass except `tests/test_gap_scoring.py` (Task 7 fixes that — it still references `accessibility_km`, which no longer exists in `district_metrics.csv`'s shape, but that file's tests build their own in-memory fixtures so this only breaks if Task 7 hasn't renamed them yet; if it fails here, that confirms the dependency is real and Task 7 is next, not a regression to chase down now). `tests/test_district_metrics.py` is gone, so it simply no longer appears in the run.

- [ ] **Step 5: Commit**

```bash
git add scripts/08_compute_district_metrics.py
git commit -m "feat: wire accessibility_min (routed travel time) into district_metrics.csv

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

(`tests/test_district_metrics.py`'s removal was already staged by Step 2's `git rm` — this commit picks it up along with the script changes.)

---

### Task 7: Rename to `accessibility_min` and fix silent-zero fallback in `scripts/09_gap_score_and_clusters.py`

**Files:**
- Modify: `scripts/09_gap_score_and_clusters.py`
- Modify: `tests/test_gap_scoring.py`

**Interfaces:**
- Consumes: `district_metrics.csv`'s `accessibility_min` column (Task 6's output).
- Produces: `compute_gap_scores(rows)` now reads `accessibility_min` instead of `accessibility_km`, raising `ValueError` on a blank/missing value instead of silently substituting `0.0`.

- [ ] **Step 1: Write the failing tests in `tests/test_gap_scoring.py`**

Find:

```python
import importlib

gap_mod = importlib.import_module("scripts.09_gap_score_and_clusters")


def make_row(district, pop_density, govt_pvt_institutions, area_km2, accessibility_km, terrain_difficulty,
             beds_per_1000=1.0, doctors_per_1000=1.0):
    return {
        "district": district, "pop_density": pop_density, "govt_pvt_institutions": govt_pvt_institutions,
        "area_km2": area_km2, "accessibility_km": accessibility_km, "terrain_difficulty": terrain_difficulty,
        "beds_per_1000": beds_per_1000, "doctors_per_1000": doctors_per_1000,
        "population_2023": pop_density * area_km2,
    }


def test_higher_density_lower_facilities_scores_higher_gap():
    rows = [
        make_row("Underserved", pop_density=2000, govt_pvt_institutions=1, area_km2=100, accessibility_km=40, terrain_difficulty=1.0),
        make_row("WellServed", pop_density=200, govt_pvt_institutions=50, area_km2=100, accessibility_km=2, terrain_difficulty=0.0),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    by_name = {r["district"]: r["gap_score"] for r in scored}
    assert by_name["Underserved"] > by_name["WellServed"]


def test_fewer_beds_and_doctors_scores_higher_gap():
    rows = [
        make_row("FewBeds", pop_density=500, govt_pvt_institutions=10, area_km2=100, accessibility_km=10,
                 terrain_difficulty=0.0, beds_per_1000=0.1, doctors_per_1000=0.1),
        make_row("ManyBeds", pop_density=500, govt_pvt_institutions=10, area_km2=100, accessibility_km=10,
                 terrain_difficulty=0.0, beds_per_1000=3.0, doctors_per_1000=3.0),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    by_name = {r["district"]: r["gap_score"] for r in scored}
    assert by_name["FewBeds"] > by_name["ManyBeds"]


def test_gap_scores_are_bounded_0_100():
    rows = [
        make_row("A", 2000, 1, 100, 40, 1.0),
        make_row("B", 200, 50, 100, 2, 0.0),
        make_row("C", 800, 10, 100, 15, 0.0),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    for r in scored:
        assert 0 <= r["gap_score"] <= 100


def test_assign_need_tiers_labels_highest_score_critical():
    rows = [
        make_row("A", 2000, 1, 100, 40, 1.0),
        make_row("B", 200, 50, 100, 2, 0.0),
        make_row("C", 800, 10, 100, 15, 0.0),
        make_row("D", 900, 8, 100, 18, 1.0),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    tiered = gap_mod.assign_need_tiers(scored)
    by_name = {r["district"]: r for r in tiered}
    assert set(r["need_tier"] for r in tiered).issubset({"Critical", "High", "Moderate", "Low"})
    highest_gap_district = max(tiered, key=lambda r: r["gap_score"])["district"]
    assert by_name[highest_gap_district]["need_tier"] in ("Critical", "High")
```

Replace with:

```python
import importlib

import pytest

gap_mod = importlib.import_module("scripts.09_gap_score_and_clusters")


def make_row(district, pop_density, govt_pvt_institutions, area_km2, accessibility_min, terrain_difficulty,
             beds_per_1000=1.0, doctors_per_1000=1.0):
    return {
        "district": district, "pop_density": pop_density, "govt_pvt_institutions": govt_pvt_institutions,
        "area_km2": area_km2, "accessibility_min": accessibility_min, "terrain_difficulty": terrain_difficulty,
        "beds_per_1000": beds_per_1000, "doctors_per_1000": doctors_per_1000,
        "population_2023": pop_density * area_km2,
    }


def test_higher_density_lower_facilities_scores_higher_gap():
    rows = [
        make_row("Underserved", pop_density=2000, govt_pvt_institutions=1, area_km2=100, accessibility_min=40, terrain_difficulty=1.0),
        make_row("WellServed", pop_density=200, govt_pvt_institutions=50, area_km2=100, accessibility_min=2, terrain_difficulty=0.0),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    by_name = {r["district"]: r["gap_score"] for r in scored}
    assert by_name["Underserved"] > by_name["WellServed"]


def test_fewer_beds_and_doctors_scores_higher_gap():
    rows = [
        make_row("FewBeds", pop_density=500, govt_pvt_institutions=10, area_km2=100, accessibility_min=10,
                 terrain_difficulty=0.0, beds_per_1000=0.1, doctors_per_1000=0.1),
        make_row("ManyBeds", pop_density=500, govt_pvt_institutions=10, area_km2=100, accessibility_min=10,
                 terrain_difficulty=0.0, beds_per_1000=3.0, doctors_per_1000=3.0),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    by_name = {r["district"]: r["gap_score"] for r in scored}
    assert by_name["FewBeds"] > by_name["ManyBeds"]


def test_gap_scores_are_bounded_0_100():
    rows = [
        make_row("A", 2000, 1, 100, 40, 1.0),
        make_row("B", 200, 50, 100, 2, 0.0),
        make_row("C", 800, 10, 100, 15, 0.0),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    for r in scored:
        assert 0 <= r["gap_score"] <= 100


def test_assign_need_tiers_labels_highest_score_critical():
    rows = [
        make_row("A", 2000, 1, 100, 40, 1.0),
        make_row("B", 200, 50, 100, 2, 0.0),
        make_row("C", 800, 10, 100, 15, 0.0),
        make_row("D", 900, 8, 100, 18, 1.0),
    ]
    scored = gap_mod.compute_gap_scores(rows)
    tiered = gap_mod.assign_need_tiers(scored)
    by_name = {r["district"]: r for r in tiered}
    assert set(r["need_tier"] for r in tiered).issubset({"Critical", "High", "Moderate", "Low"})
    highest_gap_district = max(tiered, key=lambda r: r["gap_score"])["district"]
    assert by_name[highest_gap_district]["need_tier"] in ("Critical", "High")


def test_missing_accessibility_min_raises_clear_error():
    rows = [make_row("A", 500, 10, 100, accessibility_min="", terrain_difficulty=0.0)]
    with pytest.raises(ValueError, match="Missing accessibility_min"):
        gap_mod.compute_gap_scores(rows)
```

- [ ] **Step 2: Run tests to verify the new one fails and the others still pass**

Run: `pytest tests/test_gap_scoring.py -v`
Expected: `test_missing_accessibility_min_raises_clear_error` FAILs (the current code silently defaults blank to `0.0`, no error raised); the other 4 tests currently FAIL too, since `scripts/09_gap_score_and_clusters.py` still reads `accessibility_km`, a key that no longer exists in these renamed fixtures — confirming the rename is real and required, not cosmetic

- [ ] **Step 3: Rename the field and fix the silent-zero fallback in `scripts/09_gap_score_and_clusters.py`**

Find:

```python
"""Composite facility-access gap score per district (0-100, higher =
more underserved) and a KMeans need-tier clustering on the same feature
set. Weighting/method is documented in plain language in the HTML report
(scripts/14_build_html_report.py) — this is a transparent weighted score
plus an unsupervised grouping, not a black-box model. Development
Statistics of KP 2025 (the official KP Bureau of Statistics publication)
is the primary data source for institution/bed/staffing figures; the
merged KPHCC/OSM facility registry is used only for accessibility_km,
which needs real point coordinates that Dev Stats doesn't publish."""
import csv
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import MinMaxScaler

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

# Feature weights for the composite gap score. Each feature is min-max
# normalized to [0,1] first so weights are comparable; terrain_penalty is
# a continuous DEM-derived difficulty score (see
# scripts/08_compute_district_metrics.py's compute_terrain_difficulty),
# already in [0,1], since mountainous terrain independently worsens real
# access beyond what straight-line distance captures. institution density,
# beds_per_1000, and doctors_per_1000 all come from Development Statistics
# of KP 2025 (data/processed/dev_stats_health.csv) - the official KP
# Bureau of Statistics publication, used as the primary source in
# preference to the merged KPHCC/OSM facility registry, which undercounts
# small government BHUs/dispensaries that are rarely individually mapped.
# Beds and doctors are kept as their own weighted terms rather than folded
# into institution density, since a district can have many small
# facilities but few beds, or vice versa - distinct dimensions of access,
# not restatements of the same thing. accessibility_km still comes from
# the merged registry's point coordinates since Dev Stats publishes
# district totals only, never facility locations.
WEIGHTS = {
    "pop_density": 0.25,               # more people per km^2 with few facilities -> higher need
    "inverse_facility_density": 0.15,  # official (Dev Stats) institutions per capita, inverted
    "accessibility_km": 0.20,          # distance to nearest mapped facility
    "terrain_penalty": 0.10,           # flat bump for mountainous districts
    "inverse_beds_per_1000": 0.15,     # official government+private beds per capita, inverted
    "inverse_doctors_per_1000": 0.15,  # official medical staff per capita, inverted
}


def _feature_matrix(rows):
    pop_density = np.array([float(r["pop_density"]) for r in rows]).reshape(-1, 1)
    institution_count = np.array([max(float(r["govt_pvt_institutions"]), 0.0) for r in rows])
    population = np.array([float(r["population_2023"]) for r in rows])
    facility_density = np.divide(
        institution_count, population, out=np.zeros_like(institution_count), where=population > 0
    )
    inverse_facility_density = (-facility_density).reshape(-1, 1)  # more institutions -> lower gap
    accessibility = np.array(
        [float(r["accessibility_km"]) if r["accessibility_km"] not in ("", None) else 0.0 for r in rows]
    ).reshape(-1, 1)
    terrain_penalty = np.array([float(r["terrain_difficulty"]) for r in rows]).reshape(-1, 1)
```

Replace with:

```python
"""Composite facility-access gap score per district (0-100, higher =
more underserved) and a KMeans need-tier clustering on the same feature
set. Weighting/method is documented in plain language in the HTML report
(scripts/14_build_html_report.py) — this is a transparent weighted score
plus an unsupervised grouping, not a black-box model. Development
Statistics of KP 2025 (the official KP Bureau of Statistics publication)
is the primary data source for institution/bed/staffing figures; the
merged KPHCC/OSM facility registry is used only for accessibility_min
(network- and terrain-adjusted travel time to the nearest facility,
searched across all of KP regardless of district - see
scripts/16b_compute_travel_time_accessibility.py), which needs real point
coordinates that Dev Stats doesn't publish."""
import csv
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import MinMaxScaler

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

# Feature weights for the composite gap score. Each feature is min-max
# normalized to [0,1] first so weights are comparable; terrain_penalty is
# a continuous DEM-derived difficulty score (see scripts/lib/terrain.py's
# compute_terrain_difficulty), already in [0,1], since mountainous terrain
# independently worsens real access beyond what travel time alone
# captures. institution density, beds_per_1000, and doctors_per_1000 all
# come from Development Statistics of KP 2025
# (data/processed/dev_stats_health.csv) - the official KP Bureau of
# Statistics publication, used as the primary source in preference to the
# merged KPHCC/OSM facility registry, which undercounts small government
# BHUs/dispensaries that are rarely individually mapped. Beds and doctors
# are kept as their own weighted terms rather than folded into institution
# density, since a district can have many small facilities but few beds,
# or vice versa - distinct dimensions of access, not restatements of the
# same thing. accessibility_min still comes from the merged registry's
# point coordinates since Dev Stats publishes district totals only, never
# facility locations.
WEIGHTS = {
    "pop_density": 0.25,               # more people per km^2 with few facilities -> higher need
    "inverse_facility_density": 0.15,  # official (Dev Stats) institutions per capita, inverted
    "accessibility_min": 0.20,         # travel time to nearest mapped facility
    "terrain_penalty": 0.10,           # flat bump for mountainous districts
    "inverse_beds_per_1000": 0.15,     # official government+private beds per capita, inverted
    "inverse_doctors_per_1000": 0.15,  # official medical staff per capita, inverted
}


def _require_accessibility_min(row):
    """With the routed accessibility_min in place, every district should
    always get a real value - a routed time, or the disconnected-
    component/no-facilities-in-KP straight-line fallback (see
    scripts/16b_compute_travel_time_accessibility.py and
    scripts/lib/routing.py). A blank value reaching this point means real
    upstream data is missing, not "this district is well-served" -
    silently treating it as 0.0 would wrongly bias that district toward
    looking well-served instead of surfacing the real problem."""
    value = row.get("accessibility_min")
    if value in (None, ""):
        raise ValueError(
            f"Missing accessibility_min for district {row.get('district')!r} - "
            "district_travel_time.csv should have a value for every district "
            "(run scripts/16b_compute_travel_time_accessibility.py before this script)"
        )
    return float(value)


def _feature_matrix(rows):
    pop_density = np.array([float(r["pop_density"]) for r in rows]).reshape(-1, 1)
    institution_count = np.array([max(float(r["govt_pvt_institutions"]), 0.0) for r in rows])
    population = np.array([float(r["population_2023"]) for r in rows])
    facility_density = np.divide(
        institution_count, population, out=np.zeros_like(institution_count), where=population > 0
    )
    inverse_facility_density = (-facility_density).reshape(-1, 1)  # more institutions -> lower gap
    accessibility = np.array([_require_accessibility_min(r) for r in rows]).reshape(-1, 1)
    terrain_penalty = np.array([float(r["terrain_difficulty"]) for r in rows]).reshape(-1, 1)
```

Find:

```python
def compute_gap_scores(rows):
    features = _feature_matrix(rows)
    weights = np.array([
        WEIGHTS["pop_density"], WEIGHTS["inverse_facility_density"], WEIGHTS["accessibility_km"],
        WEIGHTS["terrain_penalty"], WEIGHTS["inverse_beds_per_1000"], WEIGHTS["inverse_doctors_per_1000"],
    ])
```

Replace with:

```python
def compute_gap_scores(rows):
    features = _feature_matrix(rows)
    weights = np.array([
        WEIGHTS["pop_density"], WEIGHTS["inverse_facility_density"], WEIGHTS["accessibility_min"],
        WEIGHTS["terrain_penalty"], WEIGHTS["inverse_beds_per_1000"], WEIGHTS["inverse_doctors_per_1000"],
    ])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gap_scoring.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add scripts/09_gap_score_and_clusters.py tests/test_gap_scoring.py
git commit -m "feat: rename accessibility_km to accessibility_min in gap score, fail loudly on missing values

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Correct report text in `scripts/14_build_html_report.py`

**Files:**
- Modify: `scripts/14_build_html_report.py`
- Create: `tests/test_report_wording.py`

**Interfaces:**
- Consumes: `findings_html(metrics: list[dict]) -> str`, `methodology_html() -> str` (existing functions, now reading `accessibility_min`).

This is the last task — it depends on Task 7's rename being in place, since `findings_html` reads the `accessibility_min` key directly from each metrics row.

- [ ] **Step 1: Write the failing tests in `tests/test_report_wording.py`**

```python
import importlib
from pathlib import Path

report_mod = importlib.import_module("scripts.14_build_html_report")
REPORT_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "14_build_html_report.py"


def test_findings_html_reports_travel_time_not_distance():
    metrics = [{
        "district": "TestDistrict", "need_tier": "Critical", "gap_score": "90.0",
        "govt_pvt_institutions": "5", "beds_per_1000": "0.5", "doctors_per_1000": "0.1",
        "accessibility_min": 42,
    }]
    html = report_mod.findings_html(metrics)
    assert "42 min travel-time to nearest mapped facility" in html
    assert "km straight-line" not in html


def test_findings_html_handles_missing_accessibility():
    metrics = [{
        "district": "TestDistrict", "need_tier": "Critical", "gap_score": "90.0",
        "govt_pvt_institutions": "5", "beds_per_1000": "0.5", "doctors_per_1000": "0.1",
        "accessibility_min": "",
    }]
    html = report_mod.findings_html(metrics)
    assert "n/a min travel-time to nearest mapped facility" in html


def test_methodology_html_describes_routed_accessibility():
    html = report_mod.methodology_html()
    assert "accessibility_min" in html
    assert "accessibility_km" not in html
    assert "network- and terrain-adjusted travel-time" in html


def test_report_source_has_no_stale_straight_line_or_no_routing_wording():
    source = REPORT_SCRIPT.read_text(encoding="utf-8")
    assert "straight-line accessibility" not in source
    assert "straight-line distance" not in source
    assert "no routing engine was available" not in source
    assert "travel-time accessibility" in source
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report_wording.py -v`
Expected: all 4 FAIL — `findings_html`/`methodology_html` still reference `accessibility_km` and the old straight-line wording is still present in the source

- [ ] **Step 3: Correct `findings_html()`**

Find:

```python
        f"per 1,000 population, {m.get('accessibility_km') or 'n/a'} km straight-line to nearest mapped facility.</li>"
```

Replace with:

```python
        f"per 1,000 population, {m.get('accessibility_min') or 'n/a'} min travel-time to nearest mapped facility.</li>"
```

- [ ] **Step 4: Correct `methodology_html()`'s 2SFCA and AccessMod bullets**

Find:

```python
  <li><strong>Two-Step Floating Catchment Area (2SFCA) / Enhanced 2SFCA:</strong> the standard method for
  measuring spatial accessibility, pairing population points with facilities within a threshold distance or
  travel time and weighting by facility capacity and demand competition. This report's <code>accessibility_km</code>
  metric is a straight-line distance to the nearest mapped facility only &mdash; it does not weight by facility
  capacity or account for competing demand from neighboring districts the way 2SFCA does. Adopting full 2SFCA is
  the natural next step once network-distance routing is available.</li>
  <li><strong>WHO AccessMod:</strong> WHO's own GIS extension for modeling travel-time accessibility to health
  facilities from a DEM, road network, and land cover. This pipeline already fetches the two spatial inputs
  AccessMod needs &mdash; the Copernicus GLO-30 DEM (<code>gis/KP_DEM.tif</code>) and OpenStreetMap roads
  (<code>data/raw/osm_roads.json</code>) &mdash; but currently uses the DEM only for the terrain-difficulty score
  and the roads only for the road-length figures in Official Infrastructure Context, not for routing.
  Accessibility here is straight-line distance, not the travel-time surface AccessMod would produce.</li>
```

Replace with:

```python
  <li><strong>Two-Step Floating Catchment Area (2SFCA) / Enhanced 2SFCA:</strong> the standard method for
  measuring spatial accessibility, pairing population points with facilities within a threshold distance or
  travel time and weighting by facility capacity and demand competition. This report's <code>accessibility_min</code>
  metric is a network- and terrain-adjusted travel-time estimate to the nearest mapped facility (shortest path over
  the OSM road network, road-class speeds derated by DEM-derived terrain difficulty, plus an assumed off-road pace
  for the last mile between a point and the mapped network) &mdash; it does not weight by facility capacity or
  account for competing demand from neighboring districts the way full 2SFCA does. Adopting that capacity-weighting
  is the natural next refinement.</li>
  <li><strong>WHO AccessMod:</strong> WHO's own GIS extension for modeling travel-time accessibility to health
  facilities from a DEM, road network, and land cover. This pipeline uses the same two spatial inputs AccessMod
  needs &mdash; the Copernicus GLO-30 DEM (<code>gis/KP_DEM.tif</code>) and OpenStreetMap roads
  (<code>data/raw/osm_roads.json</code>) &mdash; for its own routing (DEM-derived terrain difficulty derates road
  speed; roads form the routable network), not just for the terrain-difficulty score and road-length figures in
  Official Infrastructure Context. Accessibility here is a single shortest-path travel time per district, not the
  full raster travel-time surface AccessMod would produce, and it doesn't model traffic, road condition, or
  seasonal variation.</li>
```

- [ ] **Step 5: Correct the Limitations & disclaimer paragraph**

Find:

```python
Accessibility is measured as straight-line distance to the nearest known facility, not real road travel time,
since no routing engine was available when this report was built. The KPHCC registry currently has no
```

Replace with:

```python
Accessibility is measured as network- and terrain-adjusted travel time to the nearest known facility (shortest
path over the mapped OSM road network, road-class speeds derated by DEM-derived terrain difficulty, plus an
assumed off-road pace for the last mile between a point and the mapped network) &mdash; it does not model
traffic, road condition, or seasonal variation. The KPHCC registry currently has no
```

- [ ] **Step 6: Correct the executive summary sentence**

Find:

```python
facility density, straight-line accessibility, and mountainous terrain.</p>
```

Replace with:

```python
facility density, travel-time accessibility, and mountainous terrain.</p>
```

- [ ] **Step 7: Correct the District Data section intro**

Find:

```python
site coordinates, so the mapped KPHCC/OSM registry remains the source for straight-line accessibility and the
```

Replace with:

```python
site coordinates, so the mapped KPHCC/OSM registry remains the source for travel-time accessibility and the
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_report_wording.py -v`
Expected: 4 passed

- [ ] **Step 9: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 10: Commit**

```bash
git add scripts/14_build_html_report.py tests/test_report_wording.py
git commit -m "fix: correct report text now that travel-time routing replaces the straight-line proxy

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Full pipeline run and manual verification

**Files:** none (verification only)

This task has no code changes — it's the manual check that the whole feature actually works end-to-end against the real project data, matching this project's established "TDD per task, then manual verification against the real running app" cadence (see project memory on the standard workflow).

- [ ] **Step 1: Run the full automated test suite one more time**

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 2: Run the affected pipeline stages in order against real data**

Run: `python scripts/06_fetch_roads_osm.py && python scripts/16b_compute_travel_time_accessibility.py && python scripts/08_compute_district_metrics.py && python scripts/09_gap_score_and_clusters.py && python scripts/14_build_html_report.py`

Expected: each stage prints its normal success line (`Wrote N OSM road segments`, `Wrote district_travel_time.csv for 35 districts`, `Wrote district_metrics.csv for 35 districts`, `Updated district_metrics.csv with gap_score/need_tier for 35 districts`, `Wrote report/KP_Healthcare_Plan.html`) with no traceback. The Overpass fetch (`06`) may take noticeably longer than before given the expanded road classes — that's expected, not a hang, per the design spec's flagged risk; if it does time out, that's a real finding to report, not something to silently retry past.

- [ ] **Step 3: Spot-check the output data makes sense**

Open `data/processed/district_metrics.csv` and confirm:
- Every district has a numeric `accessibility_min` value (no blanks) — the fallback logic means every district should get *something*.
- Values are in a plausible range for province-level travel times (minutes, not fractions of a minute or absurdly large numbers in the tens of thousands) — a sanity check on units, not a precise expected value.
- Districts previously showing "n/a" for `accessibility_km` (Kolai Palas Kohistan, Upper Kohistan, Torghar, per the report read earlier in this session) now have a real number, confirming the global-facility-search fix (spec section 3a) actually took effect.

- [ ] **Step 4: Open the rebuilt report and confirm the corrected wording renders**

Run: `python -m server` (or however the project's admin/report server is normally started — see project memory), then view the report at its usual local URL.

Confirm:
- The Limitations & disclaimer paragraph no longer says "no routing engine was available."
- The "Findings: Most Underserved Districts" list shows "N min travel-time to nearest mapped facility" instead of "N km straight-line."
- The Methodology: International GIS Standards section's 2SFCA and AccessMod bullets describe the routed metric, not the old straight-line one.
- The gap scores and need tiers have shifted at least somewhat from before (expected, given the global-facility-search fix and the switch from straight-line to routed time) — this isn't a bug to chase, it's the intended effect of the correctness fixes in this plan; if the ranking is wildly different in a way that looks wrong (e.g. an obviously well-connected district like Peshawar suddenly showing as unreachable), that's worth investigating before considering this task done.

- [ ] **Step 5: Report findings**

If everything above checks out clean, this task (and the whole plan) is done — no further commit needed beyond what Tasks 1-8 already made. If anything looks wrong, that's a real bug to fix (with its own test) before considering the feature complete, following this project's standard practice of not claiming success without having actually run the real pipeline end-to-end.
