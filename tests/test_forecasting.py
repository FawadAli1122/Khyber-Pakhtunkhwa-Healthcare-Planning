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


def test_beds_needed_uses_beds_per_1000_norm():
    # 250,000 population at 1.0 beds/1000 -> 250 beds
    assert forecast_mod.beds_needed(250000, beds_per_1000=1.0) == 250


def test_beds_needed_zero_population():
    assert forecast_mod.beds_needed(0) == 0
