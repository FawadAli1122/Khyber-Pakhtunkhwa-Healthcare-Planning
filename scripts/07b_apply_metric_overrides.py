"""Applies the metric_overrides table (AI-proposed pipeline-data updates,
written by server/metric_overrides.py) on top of
kp_district_population_2023.csv/dev_stats_health.csv, before
08_compute_district_metrics.py reads them. No overrides present is a
byte-for-byte no-op. Re-validates every override's column against the real
file header at apply-time - a genuinely unknown column is a hard failure,
since silently skipping it would let district_metrics.csv compute from
stale data with nothing to signal that.

load_overrides() reads via scripts.lib.local_db rather than a CSV file
directly, for the same reason scripts/07_merge_facilities.py reads
bot_facilities via local_db rather than server/bot_facilities.py: this
project's one-way import constraint (server/ imports from scripts/lib/,
never the reverse) means a plain pipeline script can't import
server/metric_overrides.py, so it talks to the same underlying table
directly instead. See docs/superpowers/plans/
2026-08-16-bundled-local-database.md Task 5.

Every cell an override ever touches is snapshotted into
metric_overrides_baseline.csv the first time it's touched, and restored
from that baseline before each run's overrides are re-applied on top -
this project's admin panel can delete an override (see
server/metric_overrides.py's delete_record), and without a baseline to
revert to, run_downstream.py (which deliberately skips the expensive
02_compile_population.py/17_extract_devstats_health.py re-fetch stages)
would have nothing to restore the cell to but the last-applied override
value, silently keeping a "deleted" override's effect forever. This
assumes 02_compile_population.py/17_extract_devstats_health.py's real
sources are effectively static snapshots (true for this project - the
2023 census and the Dev Stats PDF don't change between runs), so the
first-ever-touched value is a stable "true original" to revert to even
across a full run_all.py re-fetch; a genuinely live-updating source would
need a different design. See docs/superpowers/specs/
2026-08-15-pipeline-data-overrides-phase4d-design.md section 5 and
docs/superpowers/specs/2026-08-16-manage-records-design.md.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib import local_db

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
BASELINE_PATH = PROCESSED / "metric_overrides_baseline.csv"
BASELINE_FIELDNAMES = ("file", "district", "column", "original_value")

TARGET_FILES = {
    "population": PROCESSED / "kp_district_population_2023.csv",
    "health": PROCESSED / "dev_stats_health.csv",
}


def load_overrides():
    return local_db.fetch_all("metric_overrides", order_by="added_at", column_map={"column": "column_name"})


def latest_by_target(overrides):
    """Returns {(district, file, column): value}, keeping only the latest
    row per key - the overrides file is append-only, so a later row for
    the same target wins over an earlier one."""
    latest = {}
    for row in overrides:
        key = (row["district"], row["file"], row["column"])
        latest[key] = row["value"]
    return latest


def load_baseline(path=BASELINE_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {
            (row["file"], row["district"], row["column"]): row["original_value"]
            for row in csv.DictReader(f)
        }


def save_baseline(baseline, path=BASELINE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BASELINE_FIELDNAMES)
        writer.writeheader()
        for (file_key, district, column), value in sorted(baseline.items()):
            writer.writerow({"file": file_key, "district": district, "column": column, "original_value": value})


def _format_value(value):
    """Formats an override value for writing into a pipeline CSV cell.
    metric_overrides.py stores every value as a Python float (e.g.
    "340000.0"), but this project's existing integer-count columns
    (population_2023, govt_beds, etc.) are read downstream with a bare
    int(...) call that rejects a decimal string - 08_compute_district_metrics.py
    itself is never modified (Global Constraint), so this is the one
    place that must produce a format every existing reader already
    accepts: a whole number is written without a trailing ".0"; a
    genuinely fractional value (e.g. growth_rate_pct) keeps its decimal."""
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return str(value)
    if as_float.is_integer():
        return str(int(as_float))
    return str(as_float)


def apply_overrides_to_file(file_key, path, overrides_for_file, baseline=None):
    """baseline: the full {(file, district, column): original_value} dict
    (shared across every TARGET_FILES call in one run), mutated in place -
    the caller persists it via save_baseline() once all files are done.
    Every (district, column) this override system has ever touched for
    this file - whether a current override exists for it or not - is
    restored to its baseline value first, then the current override (if
    any) is re-applied on top. This makes a deleted override correctly
    revert instead of leaving the last-applied value stuck in place."""
    if baseline is None:
        baseline = {}
    baseline_for_file = {
        (district, column): value
        for (fkey, district, column), value in baseline.items()
        if fkey == file_key
    }
    keys_to_process = set(baseline_for_file) | set(overrides_for_file)
    if not keys_to_process:
        return

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    for district, column in keys_to_process:
        if column not in fieldnames:
            raise ValueError(f"Unknown column {column!r} for file {file_key!r} - refusing to apply")
        matched_row = None
        for row in rows:
            if row["district"] == district:
                matched_row = row
                break
        if matched_row is None:
            raise ValueError(f"District {district!r} not found in {path.name} - refusing to apply")

        baseline_key = (file_key, district, column)
        if baseline_key not in baseline:
            baseline[baseline_key] = matched_row[column]
        else:
            matched_row[column] = baseline[baseline_key]

        if (district, column) in overrides_for_file:
            matched_row[column] = _format_value(overrides_for_file[(district, column)])

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    overrides = load_overrides()
    latest = latest_by_target(overrides)
    baseline = load_baseline()

    by_file = {}
    for (district, file_key, column), value in latest.items():
        by_file.setdefault(file_key, {})[(district, column)] = value

    for file_key, path in TARGET_FILES.items():
        apply_overrides_to_file(file_key, path, by_file.get(file_key, {}), baseline)

    save_baseline(baseline)
    print(f"Applied {len(latest)} override(s) across {len(by_file)} file(s)")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(f"Error applying metric overrides: {exc}", file=sys.stderr)
        sys.exit(1)
