"""Reads the supplemental_records table (scripts/lib/local_db.py) for
scripts/14_build_html_report.py's "Additional Facility & District
Information" section - a thin wrapper living here since the report-build
script can't import from server/ (it runs standalone, never inside the
FastAPI app). See docs/superpowers/specs/
2026-08-15-supplemental-facility-data-phase4b-design.md section 6 and
2026-08-16-bundled-local-database-design.md.

Previously read data/processed/supplemental_records.csv directly - left
stale by the bundled-local-database migration (which moved storage onto
the database and stopped writing that CSV at all, same as it did for
07_merge_facilities.py's bot_facilities.csv and
07b_apply_metric_overrides.py's metric_overrides.csv), silently leaving
this report section permanently empty. Found and fixed while starting
the admin-custom-tables feature, which touches this exact code path.
"""
from scripts.lib import local_db


def load_records():
    return local_db.fetch_all("supplemental_records", order_by="added_at")
