"""Run the full KP healthcare planning pipeline end-to-end, in order. Each
stage is idempotent (re-fetches/recomputes into the same output paths), so
re-running after a partial failure is safe — just re-run this script."""
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
STAGES = [
    "01_fetch_boundaries.py",
    "02_compile_population.py",
    "03_fetch_facilities_kphcc.py",
    "04_geocode_kphcc_facilities.py",
    "05_fetch_facilities_osm.py",
    "06_fetch_roads_osm.py",
    "21_fetch_facilities_marham.py",          # independent - only needs the district-slug mapping, not boundaries.json
    "22_geocode_marham_facilities.py",        # needs 21 (raw fetch) + boundaries.json (01, for centroid fallback)
    "07_merge_facilities.py",                 # needs 03/04 (KPHCC) + 05 (OSM) + 21/22 (Marham) + boundaries.json
    "15_fetch_dem.py",                        # needs boundaries.json (01)
    "16_compute_dem_zonal_stats.py",          # needs KP_DEM.tif (15) + boundaries.json
    "23_fetch_landcover.py",                  # needs boundaries.json (01) - independent of 15/16, grouped here since both are one-time raster fetches
    "24_compute_landcover_zonal_stats.py",    # needs KP_LandCover.tif (23) + boundaries.json
    "16b_compute_travel_time_accessibility.py",  # needs 06 (roads) + 07 (facilities) + 16 (terrain) + boundaries.json
    "17_extract_devstats_health.py",          # independent - reads the Dev Stats PDF directly
    "18_extract_devstats_roads.py",           # independent - reads the Dev Stats PDF directly
    "19_extract_devstats_budget.py",          # independent - reads the Dev Stats PDF directly
    "07b_apply_metric_overrides.py",          # applies data/processed/metric_overrides.csv on top of 02/17 (before 08 reads them)
    "08_compute_district_metrics.py",         # needs 07 (facilities) + 16 (terrain) + 16b (travel time) + 07b (overrides applied)
    "09_gap_score_and_clusters.py",           # needs 08
    "10_forecast_demand.py",                  # needs 08/09 + 17 (govt_beds baseline)
    "11_suggest_new_sites.py",
    "20_cross_validate_facility_counts.py",   # needs 08/09/10 (district_metrics.csv) + 17
    "12_write_shapefiles.py",                 # needs district_metrics.csv + 17/18
    "13_build_qgis_project.py",               # needs 12 (shapefiles) + 15 (DEM raster)
    "14_build_html_report.py",                # needs everything above
    "25_sync_processed_to_db.py",             # reloads data/processed/* into the bundled local database
]


def main():
    for stage in STAGES:
        print(f"=== Running {stage} ===")
        result = subprocess.run([sys.executable, str(SCRIPTS_DIR / stage)])
        if result.returncode != 0:
            print(f"Stage {stage} failed with exit code {result.returncode}; stopping.")
            sys.exit(result.returncode)
    print("=== Pipeline complete ===")


if __name__ == "__main__":
    main()
