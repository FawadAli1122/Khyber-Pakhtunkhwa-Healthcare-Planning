"""AI-extracted supplemental facility/district records - equipment,
medicine, departments, diseases treated, outbreaks, or any other kind of
fact a document contains that the pipeline's structured data has no
column for. Appends to its own store; never touches district_metrics.csv
or any computed column. See docs/superpowers/specs/
2026-08-15-supplemental-facility-data-phase4b-design.md.
"""
import csv
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts.lib.districts import normalize_district
from scripts.lib.facility_readiness import TRACER_ITEMS
from server import ai_client

from scripts.lib import local_db

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
METRICS_PATH = PROCESSED / "district_metrics.csv"

FIELDNAMES = ("id", "district", "facility", "category", "label", "detail", "source_document", "added_at")

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class SupplementalDataError(Exception):
    """Raised when the AI's extracted records fail validation - message
    safe to show the admin directly, never a raw traceback."""


def load_known_districts(path=METRICS_PATH):
    with open(path, newline="", encoding="utf-8") as f:
        return [row["district"] for row in csv.DictReader(f)]


def load_records():
    return local_db.fetch_all("supplemental_records", order_by="added_at")


def append_records(records):
    local_db.insert_many("supplemental_records", FIELDNAMES, records)


def delete_record(record_id):
    return local_db.delete_by_id("supplemental_records", record_id)


def build_extraction_question(instruction, known_districts):
    instruction_line = (
        instruction.strip() if instruction and instruction.strip()
        else "(none given - infer everything from the document itself)"
    )
    districts_list = ", ".join(known_districts)
    tracer_items_text = "; ".join(
        f'{domain}: {", ".join(items)}' for domain, items in TRACER_ITEMS.items()
    )
    return (
        'Extract structured supplemental facility/district records from the '
        'document content above for a Khyber Pakhtunkhwa healthcare planning '
        'report. Respond with ONLY a JSON array (no prose, no markdown code '
        'fence) of objects shaped exactly like: '
        '{"district": "...", "facility": "...", "category": "...", "label": "...", "detail": "..."}. '
        f'"district" MUST be one of these exact names: {districts_list}. '
        '"facility" is the specific hospital/clinic name, or an empty string '
        'if the fact is district-wide (e.g. an outbreak). '
        '"category" is a short label you choose for what kind of fact this is '
        '(equipment, medicine, department, disease_treated, outbreak, or '
        'anything else that fits - it is not a fixed list) - EXCEPT: if the '
        "document supports it, use the WHO SARA facility-readiness framework "
        'instead - set "category" to one of these exact domain names and '
        '"label" to one of that domain\'s exact tracer item names below, and '
        'set "detail" to exactly "present" or "absent" for whether that '
        f'facility has that item: {tracer_items_text}. '
        '"label" is the short name of the fact (for non-SARA records - use '
        'the exact tracer item name for SARA records, as listed above). '
        '"detail" is a short elaboration (quantity, status, date, case '
        'count, etc, or "present"/"absent" for SARA tracer items). If there '
        'is nothing extractable, respond with an empty JSON array: []. '
        f"Admin's instruction: {instruction_line}"
    )


def parse_ai_response(raw_text, known_districts):
    text = raw_text.strip()
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SupplementalDataError(
            f"AI response was not valid JSON: {exc} — the document may contain more records than one "
            "request can return; try splitting it into smaller uploads."
        ) from exc

    if not isinstance(parsed, list):
        raise SupplementalDataError("AI response must be a JSON array of records")
    if not parsed:
        raise SupplementalDataError("AI did not find any records to add - no records to add")

    districts_by_lower = {d.lower(): d for d in known_districts}

    records = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise SupplementalDataError(f"Record {index} is not a JSON object")
        district_raw = str(item.get("district", "")).strip()
        district = normalize_district(district_raw)
        district = districts_by_lower.get(district.lower()) if district else None
        if not district:
            raise SupplementalDataError(f"Record {index} has an unknown district: {district_raw!r}")
        category = str(item.get("category", "")).strip()
        label = str(item.get("label", "")).strip()
        if not category or not label:
            raise SupplementalDataError(f"Record {index} is missing a required field (category/label)")
        facility = str(item.get("facility") or "").strip()
        detail = str(item.get("detail") or "").strip()
        records.append({
            "district": district,
            "facility": facility,
            "category": category,
            "label": label,
            "detail": detail,
        })
    return records


def add_from_document(provider, key, document_text, instruction, source_document):
    known_districts = load_known_districts()
    question = build_extraction_question(instruction, known_districts)
    raw_response = ai_client.ask(provider, key, question, document_text)
    records = parse_ai_response(raw_response, known_districts)

    now = datetime.now(timezone.utc).isoformat()
    for record in records:
        record["id"] = uuid.uuid4().hex[:12]
        record["source_document"] = source_document
        record["added_at"] = now

    append_records(records)
    return records
