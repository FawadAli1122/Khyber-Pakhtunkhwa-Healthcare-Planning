# KP Healthcare Planning — Real Travel-Time Routing (Road Network + DEM)

Status: Approved design, pre-implementation
Date: 2026-08-15
Extends: [2026-08-14-kp-healthcare-gis-planning-design.md](./2026-08-14-kp-healthcare-gis-planning-design.md), [2026-08-14-devstats-dem-horizons-extension-design.md](./2026-08-14-devstats-dem-horizons-extension-design.md)

## 1. Purpose

Replace the gap score's straight-line `accessibility_km` proxy (haversine
distance from a district's population centroid to the nearest mapped
facility) with a real, network- and terrain-adjusted travel-time estimate,
`accessibility_min`. The DEM (`gis/KP_DEM.tif`, Copernicus GLO-30) and the
OSM road network (`data/raw/osm_roads.json`) are both already fetched by
this pipeline but neither is used for routing today — this closes that gap
using an in-Python graph router, with no new external infrastructure.

## 2. Confirmed During Brainstorming

- **Scope: gap score only.** New-site suggestion (`11_suggest_new_sites.py`,
  still a K-Means heuristic) and any broader rewrite of the report's
  methodology narrative are explicitly out of scope for this pass. The
  report's narrative still needs the *specific false claims* it currently
  makes about `accessibility_km` corrected (see §6) — that's a factual-
  accuracy fix, not the "narrative rewrite" option the user declined.
- **Routing engine: in-Python graph router via `networkx`** (already
  installed), not an external service (OSRM/pgRouting). Matches this
  project's existing "single pipeline run, open data, no external infra"
  pattern used throughout `scripts/01`-`20`.
- **Road network: expand `06_fetch_roads_osm.py`'s Overpass query** to
  include tertiary/unclassified/residential, not just
  motorway/trunk/primary/secondary. The current major-roads-only network is
  too sparse to reach most rural facilities. Side effect (acceptable,
  discussed and accepted): the wider road set also flows into the
  `KP_Roads` shapefile and the report's road-context map — a richer map,
  not a regression.
- **Last-mile connection: straight-line snap to nearest road, at a fixed
  off-road speed.** Every district centroid and every facility gets an
  off-road leg (its straight-line distance to the nearest graph node,
  converted to time at an assumed pace) rather than a hard distance cutoff
  that would leave some districts on the old proxy and others on the new
  metric.
- **DEM is load-bearing in the routing itself**, not just the pre-existing
  separate `terrain_difficulty` score: road-segment speed is derated by the
  district-level `terrain_difficulty` the segment sits in (reusing that
  already-computed, already-vetted DEM-derived value rather than a second
  raw-DEM per-edge sampling path).

## 3. Pipeline Placement

New script: **`scripts/16b_compute_travel_time_accessibility.py`** (fits the
existing `07b`/`13b` insertion-suffix convention; runs after
`16_compute_dem_zonal_stats.py` since it consumes that stage's
`district_terrain.csv`).

Inputs:
- `data/raw/osm_roads.json` (06, expanded — see §2)
- `data/processed/` facility list (07's merged-facilities output — same list
  `nearest_facility_km` reads today)
- `data/processed/district_terrain.csv` (16 — per-district
  `mean_elev_m`/`mean_slope_deg`, from which `terrain_difficulty` is
  derived via the shared function described in §3b below)
- `data/processed/boundaries.json` (01 — district polygons, for the same
  geometric centroid `08_compute_district_metrics.py` already uses today
  via `geom.centroid` — despite that script's docstring saying "population
  centroid," the actual code takes the district polygon's plain geometric
  centroid; the new script reproduces that exact same point for continuity,
  not a population-weighted one)

Output: `data/processed/district_travel_time.csv`, columns `district,
accessibility_min` — one row per district, analogous to how
`district_terrain.csv` feeds `08_compute_district_metrics.py` today.

`scripts/run_all.py`'s `STAGES` list gets the new script inserted after
`16_compute_dem_zonal_stats.py`.

**Confirmed not affected:** `accessibility_km` is not written to any
shapefile (`12_write_shapefiles.py`'s `DISTRICT_FIELDS`/`GAP_FIELDS` don't
include it) — the GIS layer and QGIS project need no changes. Project-wide,
only five files reference `accessibility_km` today: `06`, `08`, `09`, `14`,
and `tests/test_gap_scoring.py` — all covered below.

### 3a. Correctness Fix Discovered During Planning: Facility Search Scope

`08_compute_district_metrics.py`'s current `nearest_facility_km()` does
**not** actually search all KP facilities — `load_facilities_by_district()`
groups facilities by district first, so a district only searches its own
mapped facilities. A district with zero facilities mapped inside its own
boundary gets "n/a" even if a real facility sits just across the border.
This contradicts the report's own text ("distance to the nearest **known**
facility," no district caveat) and the routing algorithm in §5 below, which
is a genuinely global multi-source search across all ~500 KP facilities
regardless of district. Confirmed with the user: **go global** — the new
`accessibility_min` searches every KP facility, not just in-district ones.
This will change some districts' values (particularly ones currently
showing "n/a") and could shift a few gap-score rankings; that's an
intentional correctness fix, not a side effect to work around.

### 3b. Correctness Fix Discovered During Planning: `terrain_difficulty` Isn't in `district_terrain.csv`

`data/processed/district_terrain.csv` (written by
`16_compute_dem_zonal_stats.py`) only has raw `mean_elev_m`/`mean_slope_deg`
— `terrain_difficulty` itself is computed later, inside
`08_compute_district_metrics.py`'s `compute_terrain_difficulty()`, across
*all* districts together (it needs the full-province min/max to scale
each district relative to the rest of KP), and only ends up in the final
`district_metrics.csv`. The new routing script needs `terrain_difficulty`
for edge-speed derating (§4) but can't read it from a file that doesn't
have it, and can't depend on `08`'s output without creating a circular
dependency (`08` needs `accessibility_min` from the new script, which
would need `terrain_difficulty` from `08`). Fix: extract
`compute_terrain_difficulty()` and `terrain_label()` out of
`08_compute_district_metrics.py` into a new `scripts/lib/terrain.py`, so
both `08` (for the report's `terrain_difficulty`/`terrain` columns, as
today) and the new script (for edge-speed derating) call the same shared,
already-tested function independently from the same raw
`district_terrain.csv` input — no circular dependency, no duplicated
logic, identical results either way since it's a pure function of the
same 35-row input.

## 4. Graph Model

**Node/edge construction.** `osm_roads.json` stores each way's full vertex
geometry (`[[lon, lat], ...]`) but not shared OSM node IDs (Overpass's `out
geom` omits them). When two ways meet at a real intersection, Overpass
emits the *exact same* float lon/lat for that shared vertex, since it's the
same underlying OSM node — so keying graph nodes by `(round(lon, 6),
round(lat, 6))` correctly merges intersections across ways without needing
node IDs. Consecutive vertices within a way become edges in an undirected
`networkx.Graph` (no `oneway` handling — a deliberate simplification for a
planning-aid, not turn-by-turn navigation).

**Edge weight (minutes):**

```
minutes = length_km / effective_speed_kmh * 60
effective_speed_kmh = BASE_SPEED[road_class] * (1 - 0.5 * terrain_difficulty)
```

- `BASE_SPEED` is a documented per-road-class table: motorway 80, trunk 70,
  primary 60, secondary 45, tertiary 35, unclassified/residential 20 km/h —
  the same "explicit documented assumption" style as the report's existing
  norms (population-per-facility, beds-per-1,000).
- `terrain_difficulty` is the existing per-district DEM-derived score
  (0-1) for whichever district the edge's midpoint falls in — reused
  as-is, not re-sampled from the raw DEM per edge. District lookup by
  point reuses `scripts/lib/geo_utils.py`'s existing
  `find_containing_district()` helper (point-in-polygon, with a
  nearest-district fallback for points outside every polygon — relevant
  near district borders) rather than a new lookup implementation.

**Last-mile / off-road legs.** Each facility and each district centroid
snaps to its nearest graph node (nearest-node, not nearest-edge-
projection — simpler, and the expanded network is dense enough that the
difference is marginal at province scale). The straight-line snap distance
becomes an off-road leg at a fixed **15 km/h** assumed pace (rough-
track/4x4, not pure walking speed, since most of this "last mile" is
unmapped track rather than trailless terrain) — documented explicitly.

## 5. Routing Algorithm

**Weighted multi-source shortest path via a virtual super-source.** Rather
than running Dijkstra separately from each of ~500 facilities (wasteful)
or from each of 35 district centroids to every facility (also wasteful):
add one virtual "super-source" node, connect it to every facility's
snapped node with edge weight equal to that facility's off-road leg time,
then run a single `networkx` Dijkstra from the super-source. The result
gives every node's travel-time to its nearest facility in one pass. Each
district's `accessibility_min` = its centroid's snapped-node distance from
the super-source + its own off-road leg time. O((V+E) log V) once per
pipeline run — fine for a batch job, not a live query path.

**Disconnected-component fallback.** KP's terrain means some districts
will plausibly have road-network components with no path to any
facility-containing component even after the road-class expansion — not a
hypothetical edge case here (e.g. Kolai Palas Kohistan, Torghar). When
Dijkstra finds no path, fall back to straight-line haversine distance
converted to minutes at the same 15 km/h assumption (reusing the off-road
constant rather than inventing a second one). This is distinct from the
existing "zero facilities exist anywhere" case (`nearest_facility_km`
already returns `None` for that, rendered as "n/a" in the report) — that
case is preserved unchanged.

**Bug fix folded in:** `09_gap_score_and_clusters.py` currently turns a
blank/missing accessibility value into a silent `0.0` in the gap-score
feature matrix (`float(r["accessibility_km"]) if ... else 0.0`) — a real
gap in the existing code. With the fallback above, every district should
have a real (routed or straight-line-fallback) value, but the silent-zero
behavior is fixed regardless rather than carried forward under the new
field name.

## 6. Downstream Integration

**`08_compute_district_metrics.py`:** drop `nearest_facility_km()` and its
haversine call; read `district_travel_time.csv` and populate
`accessibility_min` in the per-district metrics dict instead of
`accessibility_km`. Docstring/comments describing "straight-line
accessibility proxy" get corrected to describe the routed metric.

**`09_gap_score_and_clusters.py`:** rename the `WEIGHTS` dict key and the
`_feature_matrix` field read from `accessibility_km` to `accessibility_min`.
Same weight (0.20), same direction (higher = more underserved — MinMaxScaler
preserves direction, so this is a true drop-in at this layer besides the
silent-zero fix in §5).

**`14_build_html_report.py`:** five precise corrections, verified against
the actual current text with a full-file grep for "straight-line" (line
numbers approximate, subject to drift by implementation time — locate by
the quoted strings):

1. `findings_html()` (~line 204): `f"{m.get('accessibility_km') or 'n/a'}
   km straight-line to nearest mapped facility"` → `f"{m.get
   ('accessibility_min') or 'n/a'} min travel-time to nearest mapped
   facility"`.
2. `methodology_html()`'s 2SFCA bullet (~line 314-318): currently claims
   `accessibility_km` "is a straight-line distance to the nearest mapped
   facility only... Adopting full 2SFCA is the natural next step once
   network-distance routing is available." Corrected to describe what's
   now implemented (network- and terrain-adjusted routing, off-road
   last-mile assumption) while still honestly stating what's *still*
   missing versus full 2SFCA (no facility-capacity weighting, no
   demand competition from neighboring districts) — the "next step"
   framing shifts to that remaining gap, it doesn't disappear.
3. Same method's AccessMod bullet (~line 319-320): currently claims the
   DEM and roads are fetched but "not for routing" and that accessibility
   "is straight-line distance, not the travel-time surface AccessMod would
   produce." Corrected to state the DEM and roads are now used for routing,
   while still honestly noting the real remaining gap versus AccessMod
   (single shortest-path per district, not a full raster travel-time
   surface; no traffic/road-condition/seasonal variation).
4. **Limitations & disclaimer paragraph (~line 645-646)** — found via a
   full-file grep during plan-writing, not caught in the original 4-item
   list: "Accessibility is measured as straight-line distance to the
   nearest known facility, not real road travel time, since no routing
   engine was available when this report was built." This is the single
   most directly false claim in the whole report once this ships — it
   gets replaced with a statement of what's now measured (network- and
   terrain-adjusted travel time, off-road last-mile assumption) and what
   still isn't modeled (traffic, road condition, seasonal variation),
   dropping the "no routing engine was available" framing entirely since
   it becomes false.
5. Executive summary (~line 660): "facility density, straight-line
   accessibility, and mountainous terrain" → "facility density, travel-time
   accessibility, and mountainous terrain".
6. District Data section intro (~line 817): "source for straight-line
   accessibility and the facility-distribution map" → "source for
   travel-time accessibility and the facility-distribution map".

**No change needed** at the weights-paragraph sentence about Dev Stats not
supplying location (~line 688-689) — that sentence is about facility
*coordinates*, not distance-vs-time, and stays true either way. Confirmed
by re-reading all five files referencing `accessibility_km` — nothing else
touches this field.

## 7. Testing

New `tests/test_travel_time_routing.py`, following this project's
per-module test-file convention:

- **Graph construction / topology merge:** a small synthetic
  `osm_roads.json` fixture where two ways share an exact coordinate —
  assert they merge into one graph node. (The trickiest part to get
  subtly wrong.)
- **Edge weight formula:** given a road class and a `terrain_difficulty`,
  assert effective speed and resulting minutes match §4's formula.
- **Snap-to-nearest-node + off-road leg:** a point not exactly on the
  graph gets the correct nearest node and correct off-road minutes.
- **Super-source Dijkstra:** a small hand-built graph with a known
  shortest path — assert the multi-source result matches running Dijkstra
  from each source individually and taking the min.
- **Disconnected-component fallback:** a facility and a district centroid
  in separate graph components — assert it falls back to the
  straight-line/15 km/h estimate rather than raising or returning `None`.
- **Existing tests:** `tests/test_gap_scoring.py`'s fixture data gets its
  `accessibility_km` keys renamed to `accessibility_min` (values
  unchanged — the tests only care about relative ordering, not units).

## 8. Known Limitations (documented, not hidden)

- Roads are undirected — no `oneway` handling, a deliberate simplification
  for a planning-aid, not turn-by-turn navigation.
- `BASE_SPEED` per road class and the 15 km/h off-road/last-mile pace are
  assumed constants, not measured — same category of transparent norm as
  the existing beds-per-1,000 planning heuristic.
- No traffic, seasonal road closure, or time-of-day variation — a single
  static "typical" travel time.
- Single shortest path per district, not a full raster travel-time
  surface (that's the AccessMod-style approach, out of scope here).
- No facility-capacity weighting or cross-district demand competition
  (that's full 2SFCA, out of scope here).

## 9. Implementation Risk to Flag

Expanding the Overpass query to tertiary/unclassified/residential across
KP's full bounding box is a real fetch-time risk (timeout, oversized
response) — implementation needs to handle this defensively (a generous
timeout, possibly retry/backoff, or splitting the query if the full-bbox
request proves too large in practice). Not fully solvable at design time;
flagged here so the implementation plan budgets for it rather than
assuming it away.
