"""Three thin wrappers around the "run a pipeline rebuild script, catch a
timeout, check the exit code" pattern already repeated 8 times across
server/routes/admin.py's routes - shared here so the new Telegram admin-
parity commands (server/telegram_admin_records.py,
telegram_admin_tables.py, telegram_admin_db.py) don't repeat it a further
~10 times. server/routes/admin.py's own routes are left as-is (out of
scope for this feature - see docs/superpowers/specs/
2026-08-16-telegram-admin-parity-design.md section 3)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_BUILD_SCRIPT = ROOT / "scripts" / "14_build_html_report.py"
RUN_DOWNSTREAM_SCRIPT = ROOT / "scripts" / "run_downstream.py"
RUN_DOWNSTREAM_FACILITIES_SCRIPT = ROOT / "scripts" / "run_downstream_facilities.py"


def _run_rebuild_script(script_path, timeout, label):
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)], capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"{label} timed out after {timeout} seconds"
    if result.returncode != 0:
        return False, f"{label} failed: {result.stderr[-500:]}"
    return True, None


def rebuild_report():
    return _run_rebuild_script(REPORT_BUILD_SCRIPT, 300, "Report rebuild")


def rebuild_downstream():
    return _run_rebuild_script(RUN_DOWNSTREAM_SCRIPT, 600, "Downstream pipeline rebuild")


def rebuild_downstream_facilities():
    return _run_rebuild_script(RUN_DOWNSTREAM_FACILITIES_SCRIPT, 600, "Downstream pipeline rebuild")
