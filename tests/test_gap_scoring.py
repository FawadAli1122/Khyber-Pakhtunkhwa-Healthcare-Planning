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
