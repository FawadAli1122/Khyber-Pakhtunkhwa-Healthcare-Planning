"""AI-editable overrides for the core pipeline's population and aggregate
health-facility input numbers - a separate, append-only overlay applied by
scripts/07b_apply_metric_overrides.py, never a direct edit to the
regenerated source files themselves. See docs/superpowers/specs/
2026-08-15-pipeline-data-overrides-phase4d-design.md.
"""
import csv
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts.lib import local_db
from scripts.lib.districts import normalize_district
from server import ai_client
from server.supplemental_data import load_known_districts

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

FIELDNAMES = ("id", "district", "file", "column", "value", "reason", "source", "added_at")
_COLUMN_MAP = {"column": "column_name"}

# {file_key: (csv_path, overridable_columns, swing_threshold_fraction)}
OVERRIDABLE_FIELDS = {
    "population": (
        PROCESSED / "kp_district_population_2023.csv",
        {"population_2023", "population_prior", "growth_rate_pct"},
        0.5,
    ),
    "health": (
        PROCESSED / "dev_stats_health.csv",
        {"govt_institutions", "govt_beds", "pvt_hospitals", "pvt_beds",
         "medical_staff", "paramedical_staff", "pvt_practitioners"},
        1.0,
    ),
}

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class MetricOverrideError(Exception):
    """Raised when an AI-proposed pipeline-data override fails validation -
    message safe to show the admin directly, never a raw traceback."""


def load_records():
    return local_db.fetch_all("metric_overrides", order_by="added_at", column_map=_COLUMN_MAP)


def append_records(records):
    local_db.insert_many("metric_overrides", FIELDNAMES, records, column_map=_COLUMN_MAP)


def delete_record(record_id):
    return local_db.delete_by_id("metric_overrides", record_id)


def _read_current_value(file_key, district, column):
    csv_path, _columns, _threshold = OVERRIDABLE_FIELDS[file_key]
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if normalize_district(row["district"]) == district:
                raw = row.get(column, "")
                return float(raw) if raw not in (None, "") else 0.0
    raise MetricOverrideError(f"District {district!r} not found in {csv_path.name}")


def build_override_question(instruction, known_districts):
    instruction_line = (
        instruction.strip() if instruction and instruction.strip()
        else "(none given - infer everything from the document itself)"
    )
    districts_list = ", ".join(known_districts)
    fields_list = "; ".join(
        f'"{file_key}": columns {sorted(columns)}'
        for file_key, (_path, columns, _threshold) in OVERRIDABLE_FIELDS.items()
    )
    return (
        'Extract structured updates to Khyber Pakhtunkhwa healthcare '
        'planning pipeline input data from the document content above. '
        'Respond with ONLY a JSON array (no prose, no markdown code fence) '
        'of objects shaped exactly like: '
        '{"district": "...", "file": "...", "column": "...", "value": ..., "reason": "..."}. '
        f'"district" MUST be one of these exact names: {districts_list}. '
        f'"file" and "column" MUST be one of these exact pairs: {fields_list}. '
        '"value" is the new numeric value (a number, not a string). '
        '"reason" is a short explanation of why this value is changing. '
        'If there is nothing to update, respond with an empty JSON array: []. '
        f"Admin's instruction: {instruction_line}"
    )


def parse_override_response(raw_text, known_districts):
    text = raw_text.strip()
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MetricOverrideError(
            f"AI response was not valid JSON: {exc} — the document may contain more updates than one "
            "request can return; try splitting it into smaller uploads."
        ) from exc

    if not isinstance(parsed, list):
        raise MetricOverrideError("AI response must be a JSON array of updates")
    if not parsed:
        raise MetricOverrideError("AI did not find any updates to make - no updates to add")

    districts_by_lower = {d.lower(): d for d in known_districts}

    records = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise MetricOverrideError(f"Update {index} is not a JSON object")

        district_raw = str(item.get("district", "")).strip()
        district = normalize_district(district_raw)
        district = districts_by_lower.get(district.lower()) if district else None
        if not district:
            raise MetricOverrideError(f"Update {index} has an unknown district: {district_raw!r}")

        file_key = str(item.get("file", "")).strip()
        if file_key not in OVERRIDABLE_FIELDS:
            raise MetricOverrideError(f"Update {index} has an unknown file: {file_key!r}")

        _path, columns, threshold = OVERRIDABLE_FIELDS[file_key]
        column = str(item.get("column", "")).strip()
        if column not in columns:
            raise MetricOverrideError(f"Update {index} has an unknown column {column!r} for file {file_key!r}")

        raw_value = item.get("value")
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            raise MetricOverrideError(f"Update {index} has a non-numeric value: {raw_value!r}") from None
        if value < 0:
            raise MetricOverrideError(f"Update {index} has a negative value: {value!r}")

        current = _read_current_value(file_key, district, column)
        if current > 0:
            swing = abs(value - current) / current
            if swing > threshold:
                raise MetricOverrideError(
                    f"Update {index} for {district}/{column} changes {current:g} to {value:g}, a "
                    f"{swing:.0%} swing - exceeds the {threshold:.0%} sanity threshold for {file_key!r} "
                    "data. If this is a genuine large change, resubmit with an instruction explaining why."
                )

        reason = str(item.get("reason") or "").strip()
        records.append({
            "district": district,
            "file": file_key,
            "column": column,
            "value": value,
            "reason": reason,
        })
    return records


def add_from_document(provider, key, document_text, instruction, source_document):
    known_districts = load_known_districts()
    question = build_override_question(instruction, known_districts)
    raw_response = ai_client.ask(provider, key, question, document_text)
    records = parse_override_response(raw_response, known_districts)

    now = datetime.now(timezone.utc).isoformat()
    for record in records:
        record["id"] = uuid.uuid4().hex[:12]
        record["source"] = source_document
        record["added_at"] = now

    append_records(records)
    return records
