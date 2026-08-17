"""Unit tests for scripts/07b_apply_metric_overrides.py. Module names
starting with a digit aren't valid Python identifiers, so this project's
tests for numbered pipeline-stage scripts import them via
importlib.import_module rather than a normal import statement - this test
follows that same established convention (see tests/test_district_metrics.py).
load_overrides() reads the metric_overrides table via scripts.lib.local_db
(mocked below, matching tests/lib/test_local_db.py's own established
pattern) since server/metric_overrides.py moved off metric_overrides.csv -
see docs/superpowers/plans/2026-08-16-bundled-local-database.md Task 5.
"""
import csv
import importlib

import pytest

apply_mod = importlib.import_module("scripts.07b_apply_metric_overrides")


def test_load_overrides_calls_fetch_all(monkeypatch):
    fake_records = [{"id": "aaa111", "district": "Peshawar", "file": "population",
                      "column": "population_2023", "value": "5000000", "reason": "estimate",
                      "source": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(
        apply_mod.local_db, "fetch_all",
        lambda table, order_by=None, column_map=None: fake_records if table == "metric_overrides" else [],
    )
    assert apply_mod.load_overrides() == fake_records


def test_latest_by_target_keeps_last_row_per_key():
    overrides = [
        {"district": "Peshawar", "file": "population", "column": "population_2023", "value": "5000000"},
        {"district": "Peshawar", "file": "population", "column": "population_2023", "value": "5200000"},
        {"district": "Chitral", "file": "health", "column": "govt_beds", "value": "10"},
    ]
    latest = apply_mod.latest_by_target(overrides)
    assert latest[("Peshawar", "population", "population_2023")] == "5200000"
    assert latest[("Chitral", "health", "govt_beds")] == "10"


def test_apply_overrides_to_file_patches_matching_row(tmp_path):
    path = tmp_path / "population.csv"
    path.write_text(
        "district,population_2023,growth_rate_pct\n"
        "Peshawar,4750388,1.10\n"
        "Chitral,318234,0.60\n",
        encoding="utf-8",
    )
    apply_mod.apply_overrides_to_file("population", path, {("Peshawar", "population_2023"): "5000000"})
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    by_district = {r["district"]: r for r in rows}
    assert by_district["Peshawar"]["population_2023"] == "5000000"
    assert by_district["Chitral"]["population_2023"] == "318234"  # untouched


def test_apply_overrides_to_file_no_overrides_is_a_noop(tmp_path):
    path = tmp_path / "population.csv"
    original = "district,population_2023\nPeshawar,4750388\n"
    path.write_text(original, encoding="utf-8")
    apply_mod.apply_overrides_to_file("population", path, {})
    assert path.read_text(encoding="utf-8") == original


def test_apply_overrides_to_file_snapshots_baseline_on_first_touch(tmp_path):
    path = tmp_path / "population.csv"
    path.write_text("district,population_2023\nPeshawar,4750388\n", encoding="utf-8")
    baseline = {}
    apply_mod.apply_overrides_to_file(
        "population", path, {("Peshawar", "population_2023"): "5000000"}, baseline
    )
    assert baseline[("population", "Peshawar", "population_2023")] == "4750388"


def test_apply_overrides_to_file_restores_baseline_when_override_removed(tmp_path):
    path = tmp_path / "population.csv"
    path.write_text("district,population_2023\nPeshawar,4750388\n", encoding="utf-8")
    baseline = {}
    # First run: an override is applied - baseline snapshots the true original.
    apply_mod.apply_overrides_to_file(
        "population", path, {("Peshawar", "population_2023"): "5000000"}, baseline
    )
    with open(path, newline="", encoding="utf-8") as f:
        assert list(csv.DictReader(f))[0]["population_2023"] == "5000000"

    # Second run: the override has been deleted (empty overrides_for_file),
    # but the baseline entry persists - the cell must revert, not stay at
    # the last-applied override value.
    apply_mod.apply_overrides_to_file("population", path, {}, baseline)
    with open(path, newline="", encoding="utf-8") as f:
        assert list(csv.DictReader(f))[0]["population_2023"] == "4750388"


def test_apply_overrides_to_file_baseline_stays_fixed_across_repeated_overrides(tmp_path):
    # The baseline must capture the TRUE original only once - a later,
    # different override value must never overwrite an already-recorded
    # baseline.
    path = tmp_path / "population.csv"
    path.write_text("district,population_2023\nPeshawar,4750388\n", encoding="utf-8")
    baseline = {}
    apply_mod.apply_overrides_to_file(
        "population", path, {("Peshawar", "population_2023"): "5000000"}, baseline
    )
    apply_mod.apply_overrides_to_file(
        "population", path, {("Peshawar", "population_2023"): "5200000"}, baseline
    )
    assert baseline[("population", "Peshawar", "population_2023")] == "4750388"
    with open(path, newline="", encoding="utf-8") as f:
        assert list(csv.DictReader(f))[0]["population_2023"] == "5200000"


def test_load_baseline_returns_empty_dict_when_file_missing(tmp_path):
    path = tmp_path / "missing.csv"
    assert apply_mod.load_baseline(path=path) == {}


def test_save_and_load_baseline_round_trip(tmp_path):
    path = tmp_path / "baseline.csv"
    baseline = {("population", "Peshawar", "population_2023"): "4750388"}
    apply_mod.save_baseline(baseline, path=path)
    assert apply_mod.load_baseline(path=path) == baseline


def test_apply_overrides_to_file_unknown_column_raises(tmp_path):
    path = tmp_path / "population.csv"
    path.write_text("district,population_2023\nPeshawar,4750388\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bogus_column"):
        apply_mod.apply_overrides_to_file("population", path, {("Peshawar", "bogus_column"): "1"})


def test_apply_overrides_to_file_unknown_district_raises(tmp_path):
    path = tmp_path / "population.csv"
    path.write_text("district,population_2023\nPeshawar,4750388\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Atlantis"):
        apply_mod.apply_overrides_to_file("population", path, {("Atlantis", "population_2023"): "1"})


def test_apply_overrides_to_file_whole_number_value_written_without_trailing_zero(tmp_path):
    # Regression test: metric_overrides.py stores every value as a Python
    # float (e.g. "340000.0"), but population_2023 is read downstream with
    # a bare int(...) call that rejects a decimal string - caught live via
    # manual verification (08_compute_district_metrics.py raised
    # ValueError: invalid literal for int() with base 10: '340000.0').
    path = tmp_path / "population.csv"
    path.write_text("district,population_2023\nPeshawar,4750388\n", encoding="utf-8")
    apply_mod.apply_overrides_to_file("population", path, {("Peshawar", "population_2023"): "340000.0"})
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["population_2023"] == "340000"
    int(rows[0]["population_2023"])  # must not raise, matching 08_compute_district_metrics.py's own parsing


def test_apply_overrides_to_file_fractional_value_keeps_decimal(tmp_path):
    # growth_rate_pct is a genuinely fractional column - must not be
    # truncated to an integer.
    path = tmp_path / "population.csv"
    path.write_text("district,growth_rate_pct\nPeshawar,1.10\n", encoding="utf-8")
    apply_mod.apply_overrides_to_file("population", path, {("Peshawar", "growth_rate_pct"): "2.35"})
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["growth_rate_pct"] == "2.35"
