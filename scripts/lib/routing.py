"""In-Python travel-time routing over the OSM road network, weighted by
road class and DEM-derived terrain difficulty. Builds a graph from
scripts/06_fetch_roads_osm.py's road geometry, connects off-network points
(district centroids, facilities) via a straight-line "last mile" leg at a
fixed off-road pace, and finds nearest-facility travel time for every
district in one multi-source Dijkstra pass. See
docs/superpowers/specs/2026-08-15-travel-time-routing-design.md for the
full design rationale.

Two performance fixes below (build_node_index/nearest_node's KD-tree, and
build_graph's once-per-way terrain lookup) exist because KP's real,
expanded road network (scripts/06_fetch_roads_osm.py after tertiary/
unclassified/residential were added) has ~7 million edges - the tiny
synthetic graphs in this module's own tests never exposed how badly a
brute-force O(n) nearest-node scan or a per-edge point-in-polygon lookup
would scale. Found running the real pipeline end-to-end (Task 9 of
docs/superpowers/plans/2026-08-15-travel-time-routing.md), not by a unit
test - a reminder that this module's tests use graphs orders of magnitude
smaller than the real data, so they can't catch a pure scaling problem."""
import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

from scripts.lib.geo_utils import find_containing_district, haversine_km

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
    navigation.

    terrain_lookup is called once per way (using that way's own midpoint
    coordinate), not once per edge within it. A single OSM way essentially
    never crosses a district boundary in practice, and doing a
    point-in-polygon lookup per edge - there can be dozens of edges in one
    way - is a real performance cliff at KP's real road-network scale
    (millions of edges) for a negligible accuracy gain over once-per-way."""
    graph = nx.Graph()
    for rec in road_records:
        coords = rec["coordinates"]
        road_class = rec.get("road_class", "unclassified")
        if len(coords) < 2:
            continue
        mid_lon, mid_lat = coords[len(coords) // 2]
        terrain_difficulty = terrain_lookup(mid_lon, mid_lat)
        speed = effective_speed_kmh(road_class, terrain_difficulty)
        for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
            length_km = haversine_km(lon1, lat1, lon2, lat2)
            minutes = (length_km / speed) * 60 if speed > 0 else float("inf")
            a, b = _node_key(lon1, lat1), _node_key(lon2, lat2)
            if a == b:
                continue  # degenerate zero-length segment
            if graph.has_edge(a, b) and graph[a][b]["minutes"] <= minutes:
                continue  # keep the faster parallel edge if this pair is already connected
            graph.add_edge(a, b, minutes=minutes, length_km=length_km)
    return graph


SUPER_SOURCE = "__super_source__"


class NodeIndex:
    """A snapshot of a graph's real coordinate nodes (everything except
    the SUPER_SOURCE sentinel), with a KD-tree for fast nearest-neighbor
    queries. Build once per graph via build_node_index() and reuse it for
    every snap_point() call against that graph - rebuilding per call, or
    scanning every node per call, is what made the original brute-force
    nearest_node() intractable at KP's real road-network scale (see
    module docstring)."""

    def __init__(self, graph):
        self.nodes = [n for n in graph.nodes if n != SUPER_SOURCE]
        self.tree = cKDTree(self.nodes) if self.nodes else None


def build_node_index(graph):
    return NodeIndex(graph)


def nearest_node(index, lon, lat):
    """index: a NodeIndex (see build_node_index()). Returns (node_key,
    distance_km) for the real coordinate node nearest (lon, lat). Raises
    ValueError if the index has no nodes at all.

    Uses a KD-tree on raw (lon, lat) degrees - a fast, close-enough
    approximation for "which node is nearest" at KP's small geographic
    extent (~6 degrees of latitude, so degree-space Euclidean distance
    and true haversine distance rank points almost identically) - then
    re-ranks the k nearest Euclidean candidates by true haversine
    distance, so the returned distance is always exact even though
    candidate selection uses the coarser, much faster metric."""
    if index.tree is None:
        raise ValueError("Cannot snap to an empty road graph")
    k = min(8, len(index.nodes))
    _, idx = index.tree.query([lon, lat], k=k)
    best_node, best_km = None, float("inf")
    for i in np.atleast_1d(idx):
        node = index.nodes[i]
        d = haversine_km(lon, lat, node[0], node[1])
        if d < best_km:
            best_node, best_km = node, d
    return best_node, best_km


def snap_point(index, lon, lat, off_road_speed_kmh=OFF_ROAD_SPEED_KMH):
    """Snap (lon, lat) to its nearest graph node, via a prebuilt
    NodeIndex. Returns (node_key, off_road_minutes) - the node and the
    straight-line "last mile" time to reach it."""
    node, distance_km = nearest_node(index, lon, lat)
    return node, (distance_km / off_road_speed_kmh) * 60


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

    # Built once and reused for every snap_point() call below - see
    # NodeIndex's docstring for why rebuilding per call (or brute-force
    # scanning per call) doesn't scale to KP's real road-network size.
    index = build_node_index(graph)

    facility_snaps = [snap_point(index, f["lon"], f["lat"]) for f in facilities]
    super_source = add_super_source(graph, facility_snaps)
    travel_times = compute_travel_times(graph, super_source)

    result = {}
    for d in districts:
        centroid_node, off_road_minutes = snap_point(index, d["centroid_lon"], d["centroid_lat"])
        routed_minutes = travel_times.get(centroid_node)
        if routed_minutes is not None:
            result[d["district"]] = round(routed_minutes + off_road_minutes, 2)
        else:
            result[d["district"]] = round(
                _straight_line_fallback_minutes(d["centroid_lon"], d["centroid_lat"], facilities), 2
            )
    return result
