import pytest
from shapely.geometry import Polygon

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
    index = routing.build_node_index(graph)
    node, distance_km = routing.nearest_node(index, 71.45, 34.45)
    assert node == (71.5, 34.5)
    from scripts.lib.geo_utils import haversine_km
    assert distance_km == haversine_km(71.45, 34.45, 71.5, 34.5)


def test_nearest_node_empty_graph_raises():
    import networkx as nx
    index = routing.build_node_index(nx.Graph())
    with pytest.raises(ValueError, match="empty road graph"):
        routing.nearest_node(index, 71.0, 34.0)


def test_snap_point_returns_node_and_off_road_minutes():
    road_records = [{"road_class": "primary", "coordinates": [[71.0, 34.0], [71.5, 34.5]]}]
    graph = routing.build_graph(road_records, terrain_lookup=lambda lon, lat: 0.0)
    index = routing.build_node_index(graph)
    node, off_road_minutes = routing.snap_point(index, 71.45, 34.45, off_road_speed_kmh=15.0)
    from scripts.lib.geo_utils import haversine_km
    distance_km = haversine_km(71.45, 34.45, 71.5, 34.5)
    assert node == (71.5, 34.5)
    assert off_road_minutes == (distance_km / 15.0) * 60


def test_add_super_source_connects_every_facility():
    road_records = [{"road_class": "primary", "coordinates": [[71.0, 34.0], [71.5, 34.5]]}]
    graph = routing.build_graph(road_records, terrain_lookup=lambda lon, lat: 0.0)
    index = routing.build_node_index(graph)
    facility_snaps = [routing.snap_point(index, 71.0, 34.0), routing.snap_point(index, 71.5, 34.5)]
    super_source = routing.add_super_source(graph, facility_snaps)
    assert graph.has_edge(super_source, (71.0, 34.0))
    assert graph.has_edge(super_source, (71.5, 34.5))


def test_compute_travel_times_matches_min_of_individual_dijkstra():
    import networkx as nx
    road_records = [
        {"road_class": "primary", "coordinates": [[71.0, 34.0], [71.1, 34.0], [71.2, 34.0]]},
    ]
    graph = routing.build_graph(road_records, terrain_lookup=lambda lon, lat: 0.0)
    index = routing.build_node_index(graph)
    facility_snaps = [routing.snap_point(index, 71.0, 34.0), routing.snap_point(index, 71.2, 34.0)]
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


def test_node_index_nearest_node_matches_brute_force_on_larger_graph():
    # A 20x20 grid of nodes (400 total) - large enough that the KD-tree's
    # k-nearest-then-haversine-rerank result must still match a plain
    # brute-force haversine scan over every node, not just the tiny
    # 2-3-node graphs the other tests use.
    import networkx as nx
    from scripts.lib.geo_utils import haversine_km

    graph = nx.Graph()
    for i in range(20):
        for j in range(20):
            lon, lat = 71.0 + i * 0.01, 34.0 + j * 0.01
            if i > 0:
                graph.add_edge((lon, lat), (71.0 + (i - 1) * 0.01, lat), minutes=1.0, length_km=1.0)
            if j > 0:
                graph.add_edge((lon, lat), (lon, 34.0 + (j - 1) * 0.01), minutes=1.0, length_km=1.0)

    index = routing.build_node_index(graph)
    query_lon, query_lat = 71.083, 34.137  # an off-grid point, not exactly on any node

    kdtree_node, kdtree_km = routing.nearest_node(index, query_lon, query_lat)

    brute_force_node, brute_force_km = None, float("inf")
    for node in graph.nodes:
        d = haversine_km(query_lon, query_lat, node[0], node[1])
        if d < brute_force_km:
            brute_force_node, brute_force_km = node, d

    assert kdtree_node == brute_force_node
    assert kdtree_km == pytest.approx(brute_force_km)


def test_build_graph_calls_terrain_lookup_once_per_way_not_per_edge():
    # A single way with 5 points (4 edges) - terrain_lookup must be called
    # once for this way, not once per edge, or it won't scale to KP's real
    # road network (~7 million edges across ~450,000 ways).
    calls = []

    def counting_terrain_lookup(lon, lat):
        calls.append((lon, lat))
        return 0.0

    road_records = [{
        "road_class": "primary",
        "coordinates": [[71.0, 34.0], [71.01, 34.0], [71.02, 34.0], [71.03, 34.0], [71.04, 34.0]],
    }]
    routing.build_graph(road_records, counting_terrain_lookup)
    assert len(calls) == 1
