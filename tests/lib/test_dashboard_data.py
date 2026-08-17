import json

import pytest
from shapely.geometry import Polygon

from scripts.lib import dashboard_data


def test_project_maps_corners_to_svg_bounds():
    x0, y0 = dashboard_data.project(dashboard_data.LON_MIN, dashboard_data.LAT_MAX)
    x1, y1 = dashboard_data.project(dashboard_data.LON_MAX, dashboard_data.LAT_MIN)
    assert x0 == pytest.approx(0.0)
    assert y0 == pytest.approx(0.0)
    assert x1 == pytest.approx(dashboard_data.SVG_WIDTH)
    assert y1 == pytest.approx(dashboard_data.SVG_HEIGHT)


def test_polygon_to_svg_path_triangle_keeps_all_vertices_no_duplicate_close():
    triangle = Polygon([(71.0, 34.0), (72.0, 34.0), (71.5, 35.0)])
    path = dashboard_data.polygon_to_svg_path(triangle)
    assert path.startswith("M")
    assert path.endswith("Z")
    # 3 unique vertices, closing duplicate dropped (SVG "Z" re-closes it) -> 1 "M" + 2 "L"
    assert path.count("L") == 2


def test_build_dashboard_payload_matches_metrics_and_skips_unmatched_boundary():
    square = [[71.0, 34.0], [72.0, 34.0], [71.5, 35.0], [71.0, 34.0]]
    boundaries = {
        "districts": [
            {"district": "Testabad", "geometry": {"type": "Polygon", "coordinates": [square]}},
            {"district": "NoMetricsDistrict", "geometry": {"type": "Polygon", "coordinates": [square]}},
        ]
    }
    metrics = [
        {"district": "Testabad", "gap_score": "42.34", "pop_density": "100.06", "need_tier": "High"},
    ]

    payload = dashboard_data.build_dashboard_payload(boundaries, metrics)

    assert len(payload["districts"]) == 1
    rec = payload["districts"][0]
    assert rec["district"] == "Testabad"
    assert rec["gap_score"] == pytest.approx(42.3)
    assert rec["pop_density"] == pytest.approx(100.1)
    assert rec["need_tier"] == "High"
    assert rec["path"].startswith("M")
    json.dumps(payload)  # must be JSON-serializable
