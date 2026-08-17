"""End-to-end check on the generated dashboard: run after
scripts/14_build_html_report.py to confirm the interactive markup and
embedded data are present, consistent with the source CSVs, and that no
TBD/blank-content regressions crept in (see the report audit in
docs/superpowers/specs/2026-08-15-interactive-dashboard-phase1-design.md,
section 2).
"""
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "report" / "KP_Healthcare_Plan.html"
METRICS_PATH = ROOT / "data" / "processed" / "district_metrics.csv"
BOUNDARIES_PATH = ROOT / "data" / "processed" / "boundaries.json"


def load_metrics():
    with open(METRICS_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def extract_dashboard_payload(html):
    marker = 'id="dashboard-data">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    return json.loads(html[start:end])


def strip_base64_blobs(html):
    """Base64-encoded PNG data (the matplotlib figures) is long enough to
    coincidentally contain English-looking substrings like "TBD" or "nan"
    purely by chance - this bit the manual report audit that kicked off this
    project. Blank the data URIs out before scanning for placeholder text so
    that scan only sees real page content."""
    return re.sub(r'data:image/png;base64,[A-Za-z0-9+/=]+', "", html)


def main():
    html = REPORT_PATH.read_text(encoding="utf-8")
    metrics = load_metrics()
    boundaries = json.loads(BOUNDARIES_PATH.read_text(encoding="utf-8"))

    # Interactive markup hooks are present.
    for hook in (
        'id="district-table"',
        'id="district-search"',
        'class="tier-filter-chip"',
        'id="choropleth-svg"',
        'id="choropleth-legend"',
        'id="dashboard-data"',
        'id="methodology-global"',
    ):
        assert hook in html, f"missing dashboard hook: {hook}"

    # Embedded JSON payload parses and matches the metrics CSV.
    payload = extract_dashboard_payload(html)
    payload_districts = {d["district"] for d in payload["districts"]}
    metrics_districts = {m["district"] for m in metrics}
    boundary_districts = {d["district"] for d in boundaries["districts"]}

    assert payload_districts, "dashboard payload has no districts"
    assert payload_districts <= metrics_districts, (
        f"payload has districts not in metrics CSV: {payload_districts - metrics_districts}"
    )
    missing_boundaries = metrics_districts - boundary_districts
    assert not missing_boundaries, f"metrics districts with no boundary geometry: {missing_boundaries}"
    # Every metrics district that also has a boundary must make it into the payload
    # (build_dashboard_payload only drops districts absent from the metrics CSV).
    expected_in_payload = metrics_districts & boundary_districts
    assert payload_districts == expected_in_payload, (
        f"payload/metrics mismatch: {expected_in_payload.symmetric_difference(payload_districts)}"
    )

    for d in payload["districts"]:
        assert d["path"].startswith("M") and d["path"].rstrip().endswith("Z"), f"malformed SVG path for {d['district']}"
        assert d["need_tier"] in ("Critical", "High", "Moderate", "Low"), f"bad tier for {d['district']}"

    # Table rows carry the data-* attributes the JS sorts/filters on.
    row_count = html.count("data-gap-score=")
    assert row_count == len(metrics), f"expected {len(metrics)} table rows with data-gap-score, found {row_count}"

    # No blank/placeholder regressions (mirrors the manual audit that kicked off this project).
    # Base64 image blobs are stripped first - they're long enough to coincidentally
    # contain substrings like "TBD" with no relation to actual page content.
    text_html = strip_base64_blobs(html)
    for bad in ("TBD", "TODO", "undefined", "{{", "}}"):
        assert bad not in text_html, f"found placeholder marker in report: {bad!r}"
    empty_cells = re.findall(r"<td[^>]*>\s*</td>", html)
    assert not empty_cells, f"found {len(empty_cells)} empty table cell(s)"

    print(f"OK: dashboard verified - {len(payload['districts'])} districts in payload, {row_count} table rows")


if __name__ == "__main__":
    main()
