"""Reads admin-defined custom data tables (scripts/lib/local_db.py) for
scripts/14_build_html_report.py's report sections - a report-build-side
counterpart to server/custom_data.py's admin-panel orchestration, living
here since the report-build script can't import from server/ (it runs
standalone, never inside the FastAPI app - same reason
scripts/lib/supplemental_records.py exists). See docs/superpowers/specs/
2026-08-16-admin-custom-tables-design.md sections 6-7.
"""
import html

from scripts.lib import local_db


def list_tables_with_data():
    """Every custom table that has at least one row - report-ready:
    registry metadata (label/report_title/report_narrative/
    report_placement), its columns, and its current rows. Empty tables
    are omitted entirely (see spec section 6)."""
    tables = local_db.fetch_all("custom_tables", order_by="created_at")
    columns = local_db.fetch_all("custom_table_columns")
    by_table = {}
    for col in columns:
        by_table.setdefault(col["custom_table_id"], []).append(col)

    result = []
    for table in tables:
        table["columns"] = by_table.get(table["id"], [])
        rows = local_db.fetch_all(table["table_name"], order_by="added_at")
        if not rows:
            continue
        table["rows"] = rows
        result.append(table)
    return result


def render_section_html(table):
    title = html.escape(table["report_title"] or table["label"])
    narrative = html.escape(table["report_narrative"]) if table["report_narrative"] else ""
    narrative_html = f"<p>{narrative}</p>" if narrative else ""
    header_cells = "".join(f"<th>{html.escape(c['label'])}</th>" for c in table["columns"])
    body_rows = []
    for row in table["rows"]:
        cells = "".join(
            f"<td>{html.escape(str(row.get(c['column_name']) or ''))}</td>" for c in table["columns"]
        )
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        f'<section id="custom-{table["id"]}">\n'
        f"<h2>{title}</h2>\n"
        f"{narrative_html}\n"
        '<div class="table-wrap"><table>\n'
        f"<thead><tr>{header_cells}</tr></thead>\n"
        f'<tbody>{"".join(body_rows)}</tbody>\n'
        "</table></div>\n"
        "</section>"
    )
