"""Re-runs only the pipeline stages that depend on population/health
numbers, for use after an admin applies pipeline-data overrides via the
admin panel - NOT a full pipeline re-run (skips the expensive
fetch/geocode/DEM stages, which aren't affected by these overrides). Each
stage is idempotent, same as run_all.py's own stages. See
docs/superpowers/specs/2026-08-15-pipeline-data-overrides-phase4d-design.md
section 5.
"""
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
STAGES = [
    "07b_apply_metric_overrides.py",
    "08_compute_district_metrics.py",
    "09_gap_score_and_clusters.py",
    "10_forecast_demand.py",
    "11_suggest_new_sites.py",
    "20_cross_validate_facility_counts.py",
    "12_write_shapefiles.py",
    "13_build_qgis_project.py",
    "14_build_html_report.py",
    "25_sync_processed_to_db.py",
]


def main():
    for stage in STAGES:
        print(f"=== Running {stage} ===")
        result = subprocess.run([sys.executable, str(SCRIPTS_DIR / stage)])
        if result.returncode != 0:
            print(f"Stage {stage} failed with exit code {result.returncode}; stopping.")
            sys.exit(result.returncode)
    print("=== Downstream pipeline complete ===")


if __name__ == "__main__":
    main()
