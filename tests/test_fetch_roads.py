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
