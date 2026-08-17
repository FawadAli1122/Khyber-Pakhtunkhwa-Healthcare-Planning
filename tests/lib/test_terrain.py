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
