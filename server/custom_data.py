"""Admin-defined custom data tables - real, dynamically created Postgres
tables (scripts/lib/local_db.py Part C) with their own admin-chosen
columns, alongside the three fixed admin-overlay tables
(supplemental_data.py/metric_overrides.py/bot_facilities.py). This module
owns non-AI CRUD (Part A, below); AI schema inference/row extraction/
report placement live in the same module (Parts B and C, added by later
tasks in this feature's plan). See docs/superpowers/specs/
2026-08-16-admin-custom-tables-design.md.
"""
import json
import re
import uuid
from datetime import date, datetime, timezone

from scripts.lib import local_db
from server import ai_client

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

TABLE_FIELDNAMES = ("id", "label", "table_name", "created_at", "report_title", "report_narrative", "report_placement")
COLUMN_FIELDNAMES = ("id", "custom_table_id", "label", "column_name", "column_type")
VALID_COLUMN_TYPES = ("text", "number", "date")


class CustomDataError(Exception):
    """Raised when a custom-table operation fails validation - message
    safe to show the admin directly, never a raw traceback."""


def _validate_columns(columns):
    if not columns:
        raise CustomDataError("At least one column is required")
    seen = set()
    for col in columns:
        label = (col.get("label") or "").strip()
        column_type = col.get("type")
        if not label:
            raise CustomDataError("Every column needs a label")
        if column_type not in VALID_COLUMN_TYPES:
            raise CustomDataError(f"Unknown column type: {column_type!r} (must be one of {VALID_COLUMN_TYPES})")
        key = local_db.slugify(label)
        if key in seen:
            raise CustomDataError(f"Duplicate column name: {label!r}")
        seen.add(key)


def list_tables():
    tables = local_db.fetch_all("custom_tables", order_by="created_at")
    columns = local_db.fetch_all("custom_table_columns")
    by_table = {}
    for col in columns:
        by_table.setdefault(col["custom_table_id"], []).append(col)
    for table in tables:
        table["columns"] = by_table.get(table["id"], [])
    return tables


def get_table(table_id):
    for table in list_tables():
        if table["id"] == table_id:
            return table
    return None


def create_table(label, columns):
    """columns: list of {"label": str, "type": "text"|"number"|"date"}."""
    label = (label or "").strip()
    if not label:
        raise CustomDataError("Table label is required")
    _validate_columns(columns)

    table_name = f"custom_{local_db.slugify(label)}"
    existing = local_db.fetch_all("custom_tables")
    if any(t["table_name"] == table_name for t in existing):
        raise CustomDataError(f"A table named {label!r} (or one that maps to the same internal name) already exists")

    ddl_columns = [(local_db.slugify(c["label"]), c["type"]) for c in columns]
    local_db.create_table(table_name, ddl_columns)

    table_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    local_db.insert_many("custom_tables", TABLE_FIELDNAMES, [{
        "id": table_id, "label": label, "table_name": table_name, "created_at": now,
        "report_title": "", "report_narrative": "", "report_placement": "",
    }])
    for col in columns:
        local_db.insert_many("custom_table_columns", COLUMN_FIELDNAMES, [{
            "id": uuid.uuid4().hex[:12], "custom_table_id": table_id,
            "label": col["label"].strip(), "column_name": local_db.slugify(col["label"]),
            "column_type": col["type"],
        }])
    return get_table(table_id)


def add_column(table_id, label, column_type):
    table = get_table(table_id)
    if table is None:
        return None
    _validate_columns([{"label": label, "type": column_type}])
    column_name = local_db.slugify(label)
    if any(c["column_name"] == column_name for c in table["columns"]):
        raise CustomDataError(f"A column named {label!r} already exists on this table")
    local_db.add_column(table["table_name"], column_name, column_type)
    local_db.insert_many("custom_table_columns", COLUMN_FIELDNAMES, [{
        "id": uuid.uuid4().hex[:12], "custom_table_id": table_id,
        "label": label.strip(), "column_name": column_name, "column_type": column_type,
    }])
    return get_table(table_id)


def delete_column(table_id, column_id):
    table = get_table(table_id)
    if table is None:
        return False
    column = next((c for c in table["columns"] if c["id"] == column_id), None)
    if column is None:
        return False
    local_db.drop_column(table["table_name"], column["column_name"])
    local_db.delete_by_id("custom_table_columns", column_id)
    return True


def delete_table(table_id):
    table = get_table(table_id)
    if table is None:
        return False
    local_db.drop_table(table["table_name"])
    for column in table["columns"]:
        local_db.delete_by_id("custom_table_columns", column["id"])
    local_db.delete_by_id("custom_tables", table_id)
    return True


def delete_row(table_id, record_id):
    table = get_table(table_id)
    if table is None:
        return False
    return local_db.delete_by_id(table["table_name"], record_id)


def list_records(table_id):
    table = get_table(table_id)
    if table is None:
        return None
    return local_db.fetch_all(table["table_name"], order_by="added_at")


def build_schema_prompt(prompt):
    return (
        "Propose a database table structure for a Khyber Pakhtunkhwa healthcare "
        f"planning admin who wants to track: {prompt}. Respond with ONLY a JSON "
        'object (no prose, no markdown code fence) shaped exactly like: '
        '{"label": "...", "columns": [{"label": "...", "type": "text"}]}. '
        '"label" is a short, human-readable table name (2-5 words). "columns" is '
        'a list of 2-8 sensible fields for this data - each with a short "label" '
        'and a "type" that MUST be exactly one of "text", "number", or "date". '
        'Always include a column for whichever facility/district/entity the data '
        "is about, if applicable."
    )


def parse_schema_response(raw_text):
    text = raw_text.strip()
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CustomDataError(f"AI response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CustomDataError("AI response must be a JSON object")
    label = str(parsed.get("label", "")).strip()
    columns = parsed.get("columns")
    if not label:
        raise CustomDataError("AI did not propose a table label")
    if not isinstance(columns, list) or not columns:
        raise CustomDataError("AI did not propose any columns")
    cleaned_columns = []
    for index, col in enumerate(columns):
        if not isinstance(col, dict):
            raise CustomDataError(f"Column {index} is not a JSON object")
        col_label = str(col.get("label", "")).strip()
        col_type = col.get("type")
        if not col_label:
            raise CustomDataError(f"Column {index} is missing a label")
        if col_type not in VALID_COLUMN_TYPES:
            raise CustomDataError(f"Column {index} has an unknown type: {col_type!r}")
        cleaned_columns.append({"label": col_label, "type": col_type})
    return {"label": label, "columns": cleaned_columns}


def propose_schema(provider, key, prompt):
    question = build_schema_prompt(prompt)
    raw_response = ai_client.ask(provider, key, question, "")
    return parse_schema_response(raw_response)


REPORT_ANCHORS = (
    "current-state", "infrastructure-context", "terrain-elevation", "land-cover", "district-data",
    "findings", "future-planning", "supplemental-data", "facility-readiness",
)


def build_extraction_question(table, instruction):
    columns_desc = "; ".join(f'"{c["column_name"]}" ({c["column_type"]})' for c in table["columns"])
    instruction_line = f"Admin's instruction: {instruction}. " if instruction else ""
    return (
        "Extract structured records from the document content above, for a table "
        f'called "{table["label"]}" with these exact fields: {columns_desc}. Respond '
        "with ONLY a JSON array (no prose, no markdown code fence) of objects using "
        'exactly those field names as keys - every value for a "number" field must be '
        'a JSON number (not a string), every value for a "date" field must be an ISO '
        '"YYYY-MM-DD" string, and every value for a "text" field must be a string. If '
        'a field is not mentioned for a given record, use "" for text, null for '
        "number/date. If there is nothing extractable, respond with an empty JSON "
        f"array: []. {instruction_line}"
    )


def _validate_row_value(value, column_type, column_label):
    if column_type == "text":
        return "" if value is None else str(value)
    if column_type == "number":
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            raise CustomDataError(f"{column_label!r} must be a number, got {value!r}")
    if column_type == "date":
        if value is None or value == "":
            return None
        try:
            date.fromisoformat(str(value))
        except ValueError:
            raise CustomDataError(f"{column_label!r} must be an ISO date (YYYY-MM-DD), got {value!r}")
        return str(value)
    raise CustomDataError(f"Unknown column type: {column_type!r}")


def parse_extraction_response(raw_text, table):
    text = raw_text.strip()
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CustomDataError(f"AI response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise CustomDataError("AI response must be a JSON array of records")
    if not parsed:
        raise CustomDataError("AI did not find any records to add")

    rows = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise CustomDataError(f"Record {index} is not a JSON object")
        row = {}
        for col in table["columns"]:
            row[col["column_name"]] = _validate_row_value(
                item.get(col["column_name"]), col["column_type"], col["label"]
            )
        rows.append(row)
    return rows


def build_placement_question(table, rows):
    columns_desc = ", ".join(c["label"] for c in table["columns"])
    sample = json.dumps(rows[:5])
    anchors_list = ", ".join(REPORT_ANCHORS)
    return (
        f'A new admin-defined data table called "{table["label"]}" (columns: '
        f"{columns_desc}) has {len(rows)} record(s) that will appear in a Khyber "
        f"Pakhtunkhwa healthcare planning report. Sample records: {sample}. "
        'Respond with ONLY a JSON object (no prose, no markdown code fence) shaped '
        'exactly like: {"title": "...", "narrative": "...", "placement": "..."}. '
        '"title" is a short section heading for this data. "narrative" is 1-3 '
        'sentences interpreting what this data shows. "placement" MUST be exactly '
        'the string "new_section", or exactly "after:<anchor>" where <anchor> is '
        f"one of these existing report section ids: {anchors_list}."
    )


def parse_placement_response(raw_text):
    text = raw_text.strip()
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"title": "", "narrative": "", "placement": "new_section"}
    if not isinstance(parsed, dict):
        return {"title": "", "narrative": "", "placement": "new_section"}
    title = str(parsed.get("title", "")).strip()
    narrative = str(parsed.get("narrative", "")).strip()
    placement = parsed.get("placement")
    valid_after = isinstance(placement, str) and placement.startswith("after:") \
        and placement[len("after:"):] in REPORT_ANCHORS
    if placement != "new_section" and not valid_after:
        placement = "new_section"
    return {"title": title, "narrative": narrative, "placement": placement}


def propose_placement(provider, key, table, rows):
    question = build_placement_question(table, rows)
    raw_response = ai_client.ask(provider, key, question, "")
    return parse_placement_response(raw_response)


def preview_extraction(provider, key, table_id, document_text, instruction):
    """Runs AI extraction only - never writes to the database. Lets the
    admin review (and, in the admin UI, edit) proposed rows before they
    commit via add_rows(). See docs/superpowers/specs/
    2026-08-16-admin-custom-tables-design.md."""
    table = get_table(table_id)
    if table is None:
        return None
    question = build_extraction_question(table, instruction)
    raw_response = ai_client.ask(provider, key, question, document_text)
    return parse_extraction_response(raw_response, table)


def add_rows(table_id, raw_rows, provider, key):
    """raw_rows: list of {column_name: raw_value} dicts as submitted from
    the browser - a mix of AI-previewed-then-possibly-edited and/or
    manually-typed values. Always treated as untrusted raw input and
    re-validated fresh here regardless of origin (never assumes a value
    already validated by preview_extraction is still valid - the admin
    may have edited it in the browser). provider/key are used only for
    the report-placement decision, not for extraction."""
    table = get_table(table_id)
    if table is None:
        return None
    if not raw_rows:
        raise CustomDataError("No rows to add")

    rows = []
    for raw_row in raw_rows:
        row = {}
        for col in table["columns"]:
            row[col["column_name"]] = _validate_row_value(
                raw_row.get(col["column_name"]), col["column_type"], col["label"]
            )
        rows.append(row)

    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        row["id"] = uuid.uuid4().hex[:12]
        row["added_at"] = now
    fieldnames = ("id", "added_at") + tuple(c["column_name"] for c in table["columns"])
    local_db.insert_many(table["table_name"], fieldnames, rows)

    all_rows = local_db.fetch_all(table["table_name"], order_by="added_at")
    placement = propose_placement(provider, key, table, all_rows)
    local_db.update_by_id("custom_tables", table_id, {
        "report_title": placement["title"] or table["label"],
        "report_narrative": placement["narrative"],
        "report_placement": placement["placement"],
    })
    return rows
