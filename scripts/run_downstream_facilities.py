"""Re-runs the pipeline stages that depend on the merged facility set, for
use after the Telegram bot's /addpoint command (or an admin-panel delete
of a bot-added facility) changes data/processed/bot_facilities.csv - NOT
a full pipeline re-run (skips the expensive fetch/geocode/DEM stages,
same rationale as run_downstream.py, but starts one stage earlier at
07_merge_facilities.py since a new facility changes the merged set
itself, not just an overridden number). See docs/superpowers/specs/
2026-08-16-telegram-connector-design.md section 9.
"""
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
STAGES = [
    "07_merge_facilities.py",
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
    print("=== Downstream facilities pipeline complete ===")


if __name__ == "__main__":
    main()
