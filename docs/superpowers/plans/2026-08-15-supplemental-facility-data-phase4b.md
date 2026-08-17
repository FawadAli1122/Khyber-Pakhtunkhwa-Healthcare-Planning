# AI-Extracted Supplemental Facility & District Data (Phase 4b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the admin upload a document (text/CSV/Excel/Word/PDF/HTML) plus an optional free-text instruction, have the AI extract structured supplemental facts (equipment, medicine, departments, diseases treated, outbreaks, or anything else) it contains, append them to a new store, show them in a new report section, and ground "Ask AI" answers in them too — full autonomy, no review step.

**Architecture:** One new core module, `server/supplemental_data.py` (district validation, AI prompt-building, response parsing/validation, CSV storage), reusing `ai_client.ask()` and `document_extraction.extract()` as-is rather than duplicating provider dispatch or format-parsing logic. `document_extraction.py` gains `.txt`/`.csv` parsers. A new admin route composes extraction → AI extraction → storage → a `scripts/14_build_html_report.py` subprocess re-run (mirroring `scripts/run_all.py`'s own subprocess pattern) so the change is visible on the next dashboard load. The report-build script gains a small `scripts/lib/supplemental_records.py` reader (it can't import from `server/`) and a new report section. `report_context.py` folds the same data into the AI chat's grounding digest.

**Tech Stack:** Python 3.12, existing project dependencies only (no new installs) — reuses `ai_client`, `document_extraction`, `keystore`, FastAPI, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-15-supplemental-facility-data-phase4b-design.md`

## Global Constraints

- No new dependencies.
- This phase never writes to `district_metrics.csv` or any computed column (`gap_score`, `need_tier`, forecast columns) — only ever appends to its own new `data/processed/supplemental_records.csv`.
- Every AI-proposed `district` is validated against the real 35-district list read from `district_metrics.csv`; an unknown district raises `SupplementalDataError` rather than silently creating a bogus district.
- `category` is free text the AI assigns — never a fixed enum — so a genuinely new kind of fact doesn't need a code change.
- Records are append-only; no dedup/update-in-place in this phase.
- Every typed exception (`SupplementalDataError`, reused `UnsupportedFormatError`/`ExtractionError`/`AIProviderError`) carries a message safe to show the admin directly, never a raw traceback.
- Every AI provider call in every test is mocked — no test may require a real API key or network access, same posture as every earlier phase.
- If the report-rebuild subprocess fails after records were already appended, the route still returns 200 with the added records plus a `rebuild_warning` — data that was genuinely saved is never reported as a failure.
- Reuse `ai_client.ask()` and `document_extraction.extract()` exactly as they exist today — this phase adds no new provider-dispatch or format-parsing logic of its own.

---

### Task 1: `server/supplemental_data.py` — core module

**Files:**
- Create: `server/supplemental_data.py`
- Test: `tests/server/test_supplemental_data.py`

**Interfaces:**
- Produces: `supplemental_data.SupplementalDataError(Exception)`; `supplemental_data.RECORDS_PATH`, `supplemental_data.METRICS_PATH` (module-level path constants); `supplemental_data.FIELDNAMES` (tuple); `supplemental_data.load_known_districts(path=METRICS_PATH) -> list[str]`; `supplemental_data.load_records(path=RECORDS_PATH) -> list[dict]`; `supplemental_data.append_records(records, path=RECORDS_PATH) -> None`; `supplemental_data.build_extraction_question(instruction, known_districts) -> str`; `supplemental_data.parse_ai_response(raw_text, known_districts) -> list[dict]` (raises `SupplementalDataError`); `supplemental_data.add_from_document(provider, key, document_text, instruction, source_document) -> list[dict]`.
- Consumes: `ai_client.ask(provider, key, question, context)` (phase 3, called as `ai_client.ask(...)` — a module-attribute lookup, not an imported name, so tests can monkeypatch it the same way `test_ask_route.py` does); `scripts.lib.districts.normalize_district` (existing).

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_supplemental_data.py`:

```python
"""Unit tests for server/supplemental_data.py. ai_client.ask is mocked in
every test that calls add_from_document - no real provider calls. CSV I/O
uses tmp_path, never the real data files. See docs/superpowers/specs/
2026-08-15-supplemental-facility-data-phase4b-design.md sections 3-4.
"""
import json

import pytest

from server import ai_client, supplemental_data

KNOWN_DISTRICTS = ["Peshawar", "Chitral", "Abbottabad"]


def test_parse_ai_response_valid_json():
    raw = json.dumps([
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit, operational"},
    ])
    records = supplemental_data.parse_ai_response(raw, KNOWN_DISTRICTS)
    assert records == [
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit, operational"},
    ]


def test_parse_ai_response_strips_code_fence():
    raw = "```json\n" + json.dumps([
        {"district": "Chitral", "facility": "", "category": "outbreak",
         "label": "Cholera", "detail": "12 confirmed cases"},
    ]) + "\n```"
    records = supplemental_data.parse_ai_response(raw, KNOWN_DISTRICTS)
    assert records[0]["category"] == "outbreak"


def test_parse_ai_response_invalid_json_raises():
    with pytest.raises(supplemental_data.SupplementalDataError):
        supplemental_data.parse_ai_response("not json at all", KNOWN_DISTRICTS)


def test_parse_ai_response_unknown_district_raises():
    raw = json.dumps([{"district": "Atlantis", "facility": "", "category": "equipment",
                        "label": "X-ray", "detail": ""}])
    with pytest.raises(supplemental_data.SupplementalDataError, match="Atlantis"):
        supplemental_data.parse_ai_response(raw, KNOWN_DISTRICTS)


def test_parse_ai_response_missing_required_field_raises():
    raw = json.dumps([{"district": "Peshawar", "facility": "", "category": "",
                        "label": "X-ray", "detail": ""}])
    with pytest.raises(supplemental_data.SupplementalDataError):
        supplemental_data.parse_ai_response(raw, KNOWN_DISTRICTS)


def test_parse_ai_response_empty_array_raises():
    with pytest.raises(supplemental_data.SupplementalDataError, match="no records"):
        supplemental_data.parse_ai_response("[]", KNOWN_DISTRICTS)


def test_parse_ai_response_not_a_list_raises():
    with pytest.raises(supplemental_data.SupplementalDataError):
        supplemental_data.parse_ai_response(json.dumps({"district": "Peshawar"}), KNOWN_DISTRICTS)


def test_append_and_load_records_round_trip(tmp_path):
    path = tmp_path / "supplemental_records.csv"
    supplemental_data.append_records(
        [{"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
          "label": "MRI Machine", "detail": "1 unit", "source_document": "report.pdf",
          "added_at": "2026-08-15T00:00:00+00:00"}],
        path=path,
    )
    records = supplemental_data.load_records(path=path)
    assert len(records) == 1
    assert records[0]["district"] == "Peshawar"
    assert records[0]["label"] == "MRI Machine"


def test_append_records_appends_without_duplicating_header(tmp_path):
    path = tmp_path / "supplemental_records.csv"
    supplemental_data.append_records(
        [{"district": "Peshawar", "facility": "", "category": "equipment", "label": "X-ray",
          "detail": "", "source_document": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}],
        path=path,
    )
    supplemental_data.append_records(
        [{"district": "Chitral", "facility": "", "category": "outbreak", "label": "Cholera",
          "detail": "", "source_document": "b.pdf", "added_at": "2026-08-15T00:01:00+00:00"}],
        path=path,
    )
    records = supplemental_data.load_records(path=path)
    assert len(records) == 2
    with open(path, newline="", encoding="utf-8") as f:
        header_count = sum(1 for line in f if line.startswith("district,"))
    assert header_count == 1


def test_load_records_returns_empty_list_when_file_missing(tmp_path):
    path = tmp_path / "does_not_exist.csv"
    assert supplemental_data.load_records(path=path) == []


def test_add_from_document_success(tmp_path, monkeypatch):
    records_path = tmp_path / "supplemental_records.csv"
    monkeypatch.setattr(supplemental_data, "RECORDS_PATH", records_path)
    monkeypatch.setattr(supplemental_data, "load_known_districts", lambda path=None: KNOWN_DISTRICTS)

    raw_response = json.dumps([
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit"},
    ])
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw_response)

    added = supplemental_data.add_from_document(
        "anthropic", "sk-ant-real", "some document text",
        "this is for Peshawar's DHQ Hospital", "equipment.pdf",
    )
    assert len(added) == 1
    assert added[0]["district"] == "Peshawar"
    assert added[0]["source_document"] == "equipment.pdf"
    assert "added_at" in added[0]

    saved = supplemental_data.load_records(path=records_path)
    assert len(saved) == 1


def test_add_from_document_validation_failure_raises(monkeypatch):
    monkeypatch.setattr(supplemental_data, "load_known_districts", lambda path=None: KNOWN_DISTRICTS)
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: "not valid json")
    with pytest.raises(supplemental_data.SupplementalDataError):
        supplemental_data.add_from_document("anthropic", "sk-ant-real", "text", "", "doc.pdf")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_supplemental_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.supplemental_data'`

- [ ] **Step 3: Implement `server/supplemental_data.py`**

```python
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
from datetime import datetime, timezone
from pathlib import Path

from scripts.lib.districts import normalize_district
from server import ai_client

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
METRICS_PATH = PROCESSED / "district_metrics.csv"
RECORDS_PATH = PROCESSED / "supplemental_records.csv"

FIELDNAMES = ("district", "facility", "category", "label", "detail", "source_document", "added_at")

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class SupplementalDataError(Exception):
    """Raised when the AI's extracted records fail validation - message
    safe to show the admin directly, never a raw traceback."""


def load_known_districts(path=METRICS_PATH):
    with open(path, newline="", encoding="utf-8") as f:
        return [row["district"] for row in csv.DictReader(f)]


def load_records(path=RECORDS_PATH):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_records(records, path=RECORDS_PATH):
    path = Path(path)
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in FIELDNAMES})


def build_extraction_question(instruction, known_districts):
    instruction_line = (
        instruction.strip() if instruction and instruction.strip()
        else "(none given - infer everything from the document itself)"
    )
    districts_list = ", ".join(known_districts)
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
        'anything else that fits - it is not a fixed list). '
        '"label" is the short name of the fact. "detail" is a short '
        'elaboration (quantity, status, date, case count, etc). If there is '
        'nothing extractable, respond with an empty JSON array: []. '
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
        raise SupplementalDataError(f"AI response was not valid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise SupplementalDataError("AI response must be a JSON array of records")
    if not parsed:
        raise SupplementalDataError("AI did not find any records to add - no records to add")

    records = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise SupplementalDataError(f"Record {index} is not a JSON object")
        district_raw = str(item.get("district", "")).strip()
        district = normalize_district(district_raw)
        if not district or district not in known_districts:
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
        record["source_document"] = source_document
        record["added_at"] = now

    append_records(records, path=RECORDS_PATH)
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_supplemental_data.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add server/supplemental_data.py tests/server/test_supplemental_data.py
git commit -m "feat: add supplemental_data module for AI-extracted facility/district records

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `document_extraction.py` — `.txt`/`.csv` support

**Files:**
- Modify: `server/document_extraction.py`
- Modify: `tests/server/test_document_extraction.py`

**Interfaces:**
- Produces: `.txt` and `.csv` become valid `extract()` inputs, `result.format` is `"txt"`/`"csv"` respectively; every existing typed-exception behavior (`UnsupportedFormatError`, `ExtractionError` on undecodable bytes) applies unchanged.
- Consumes: nothing new — this task only extends the existing `_EXTENSION_PARSERS` dispatch table from phase 4a.

- [ ] **Step 1: Add the failing tests**

Find (the end of `tests/server/test_document_extraction.py`):

```python
def test_extract_empty_document_raises_extraction_error():
    empty_html = b"<html><body></body></html>"
    with pytest.raises(document_extraction.ExtractionError):
        document_extraction.extract("empty.html", empty_html)
```

Replace with:

```python
def test_extract_empty_document_raises_extraction_error():
    empty_html = b"<html><body></body></html>"
    with pytest.raises(document_extraction.ExtractionError):
        document_extraction.extract("empty.html", empty_html)


def test_extract_txt_returns_plain_text():
    result = document_extraction.extract("notes.txt", b"Peshawar has a population of 4,750,388.")
    assert result.format == "txt"
    assert "Peshawar has a population of 4,750,388." in result.text


def test_extract_csv_renders_pipe_delimited_rows():
    csv_bytes = b"district,population\nPeshawar,4750388\n"
    result = document_extraction.extract("stats.csv", csv_bytes)
    assert result.format == "csv"
    assert "district | population" in result.text
    assert "Peshawar | 4750388" in result.text


def test_extract_txt_undecodable_bytes_raises_extraction_error():
    with pytest.raises(document_extraction.ExtractionError):
        document_extraction.extract("bad.txt", b"\xff\xfe\x00\xff not valid utf-8 \xff")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_document_extraction.py -v`
Expected: 3 new FAILs (`KeyError`/`UnsupportedFormatError` since `.txt`/`.csv` aren't registered yet)

- [ ] **Step 3: Add the new parsers**

Find:

```python
def _extract_pdf(content_bytes):
```

Replace with:

```python
def _extract_txt(content_bytes):
    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractionError(f"Could not read text file: {exc}") from exc
    return text.strip()


def _extract_csv(content_bytes):
    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractionError(f"Could not read CSV file: {exc}") from exc
    reader = csv.reader(io.StringIO(text))
    rows = [" | ".join(cell.strip() for cell in row) for row in reader]
    rows = [row for row in rows if row.strip(" |")]
    return "\n".join(rows)


def _extract_pdf(content_bytes):
```

Find:

```python
import io
from pathlib import Path

import fitz
```

Replace with:

```python
import csv
import io
from pathlib import Path

import fitz
```

Find:

```python
_EXTENSION_PARSERS = {
    ".xlsx": _extract_xlsx,
    ".xls": _extract_xlsx,
    ".docx": _extract_docx,
    ".html": _extract_html,
    ".htm": _extract_html,
    ".pdf": _extract_pdf,
}
```

Replace with:

```python
_EXTENSION_PARSERS = {
    ".xlsx": _extract_xlsx,
    ".xls": _extract_xlsx,
    ".docx": _extract_docx,
    ".html": _extract_html,
    ".htm": _extract_html,
    ".pdf": _extract_pdf,
    ".txt": _extract_txt,
    ".csv": _extract_csv,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_document_extraction.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add server/document_extraction.py tests/server/test_document_extraction.py
git commit -m "feat: add .txt and .csv support to document_extraction

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `scripts/lib/supplemental_records.py` — reader for the report-build script

**Files:**
- Create: `scripts/lib/supplemental_records.py`
- Test: `tests/lib/test_supplemental_records.py`

**Interfaces:**
- Produces: `supplemental_records.RECORDS_PATH`; `supplemental_records.load_records(path=RECORDS_PATH) -> list[dict]`.
- Consumes: nothing — a thin, standalone CSV reader, deliberately not importing `server/` (the report-build script runs standalone via `python scripts/14_build_html_report.py`, never inside the FastAPI app).

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/test_supplemental_records.py`:

```python
from scripts.lib import supplemental_records


def test_load_records_returns_empty_list_when_file_missing(tmp_path):
    path = tmp_path / "missing.csv"
    assert supplemental_records.load_records(path=path) == []


def test_load_records_reads_existing_rows(tmp_path):
    path = tmp_path / "supplemental_records.csv"
    path.write_text(
        "district,facility,category,label,detail,source_document,added_at\n"
        "Peshawar,DHQ Hospital,equipment,MRI Machine,1 unit,equip.pdf,2026-08-15T00:00:00+00:00\n",
        encoding="utf-8",
    )
    records = supplemental_records.load_records(path=path)
    assert len(records) == 1
    assert records[0]["district"] == "Peshawar"
    assert records[0]["label"] == "MRI Machine"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_supplemental_records.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.lib.supplemental_records'`

- [ ] **Step 3: Implement `scripts/lib/supplemental_records.py`**

```python
"""Reads data/processed/supplemental_records.csv for
scripts/14_build_html_report.py's "Additional Facility & District
Information" section - a thin duplicate of server/supplemental_data.py's
load_records() logic, living here since the report-build script can't
import from server/ (it runs standalone, never inside the FastAPI app).
See docs/superpowers/specs/
2026-08-15-supplemental-facility-data-phase4b-design.md section 6.
"""
import csv
from pathlib import Path

RECORDS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "supplemental_records.csv"


def load_records(path=RECORDS_PATH):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_supplemental_records.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/supplemental_records.py tests/lib/test_supplemental_records.py
git commit -m "feat: add scripts/lib/supplemental_records reader

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `scripts/14_build_html_report.py` — new report section

**Files:**
- Modify: `scripts/14_build_html_report.py`
- Test: `tests/test_supplemental_data_section.py`

**Interfaces:**
- Consumes: `scripts.lib.supplemental_records.load_records()` (Task 3).
- Produces: `supplemental_data_rows_html(records) -> str` (a new function in `scripts/14_build_html_report.py`); a new `<section id="supplemental-data">` in the rendered report, present (with an empty-state message) even when no records have been added yet.

Module names starting with a digit aren't valid Python identifiers, so this
project's existing tests for other numbered pipeline-stage scripts (e.g.
`tests/test_district_metrics.py`) import them via `importlib.import_module`
rather than a normal `import` statement — this task follows that same
established convention.

- [ ] **Step 1: Write the failing test**

Create `tests/test_supplemental_data_section.py`:

```python
import importlib

report_mod = importlib.import_module("scripts.14_build_html_report")


def test_supplemental_data_rows_html_empty_state():
    html = report_mod.supplemental_data_rows_html([])
    assert "No additional information has been added yet." in html


def test_supplemental_data_rows_html_renders_populated_records():
    records = [
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit, operational", "source_document": "equip.pdf"},
        {"district": "Chitral", "facility": "", "category": "outbreak",
         "label": "Cholera", "detail": "12 confirmed cases", "source_document": "outbreak.txt"},
    ]
    html = report_mod.supplemental_data_rows_html(records)
    assert "Peshawar" in html
    assert "MRI Machine" in html
    assert "Chitral" in html
    assert "Cholera" in html
    assert "No additional information has been added yet." not in html


def test_supplemental_data_rows_html_sorted_by_district():
    records = [
        {"district": "Chitral", "facility": "", "category": "outbreak", "label": "Cholera", "detail": ""},
        {"district": "Abbottabad", "facility": "", "category": "equipment", "label": "X-ray", "detail": ""},
    ]
    html = report_mod.supplemental_data_rows_html(records)
    assert html.index("Abbottabad") < html.index("Chitral")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_supplemental_data_section.py -v`
Expected: FAIL with `AttributeError: module 'scripts.14_build_html_report' has no attribute 'supplemental_data_rows_html'`

- [ ] **Step 3: Add the import**

Find:

```python
from scripts.lib.dashboard_assets import DASHBOARD_CSS, DASHBOARD_JS
from scripts.lib.dashboard_data import build_dashboard_payload
```

Replace with:

```python
from scripts.lib.dashboard_assets import DASHBOARD_CSS, DASHBOARD_JS
from scripts.lib.dashboard_data import build_dashboard_payload
from scripts.lib.supplemental_records import load_records as load_supplemental_records
```

- [ ] **Step 4: Load supplemental records in `load_data()`**

Find:

```python
    dev_budget = json.loads((PROCESSED / "dev_stats_budget.json").read_text())
    return boundaries, metrics, facilities, sites, dev_health, dev_roads, cross_val, dev_budget
```

Replace with:

```python
    dev_budget = json.loads((PROCESSED / "dev_stats_budget.json").read_text())
    supplemental_records = load_supplemental_records()
    return boundaries, metrics, facilities, sites, dev_health, dev_roads, cross_val, dev_budget, supplemental_records
```

- [ ] **Step 5: Update the unpacking call site**

Find:

```python
    boundaries, metrics, facilities, sites, dev_health, dev_roads, cross_val, dev_budget = load_data()
```

Replace with:

```python
    boundaries, metrics, facilities, sites, dev_health, dev_roads, cross_val, dev_budget, supplemental_records = load_data()
```

- [ ] **Step 6: Add the rows-rendering function**

Find:

```python
def methodology_html():
```

Replace with:

```python
def supplemental_data_rows_html(records):
    if not records:
        return '<tr><td colspan="6">No additional information has been added yet.</td></tr>'
    rows = []
    for r in sorted(records, key=lambda r: (r["district"], r.get("facility", ""), r["category"])):
        facility = r.get("facility") or "&mdash;"
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td>{facility}</td>"
            f"<td>{r['category']}</td>"
            f"<td>{r['label']}</td>"
            f"<td>{r.get('detail', '')}</td>"
            f"<td>{r.get('source_document', '')}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def methodology_html():
```

- [ ] **Step 7: Splice the new section into the report template**

Find:

```python
  <li><strong>Long term ({y20}):</strong> every new facility in a high-elevation, high-terrain-difficulty
  district built to the climate/terrain-resilient standard described above as a matter of policy, not
  case-by-case decision &mdash; seismic design, winter access redundancy, and off-grid power as default
  specification, not an upgrade option.</li>
</ul>
</section>

<footer>
```

Replace with:

```python
  <li><strong>Long term ({y20}):</strong> every new facility in a high-elevation, high-terrain-difficulty
  district built to the climate/terrain-resilient standard described above as a matter of policy, not
  case-by-case decision &mdash; seismic design, winter access redundancy, and off-grid power as default
  specification, not an upgrade option.</li>
</ul>
</section>

<section id="supplemental-data">
<h2>Additional Facility &amp; District Information</h2>
<p>Equipment, medicine, departments, diseases treated, outbreak records, and other facts added via the
admin panel's document upload &mdash; not part of the deterministic gap-score model above.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Facility</th><th>Category</th><th>Label</th><th>Detail</th><th>Source</th></tr></thead>
<tbody>
{supplemental_data_rows_html(supplemental_records)}
</tbody>
</table>
</div>
</section>

<footer>
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `pytest tests/test_supplemental_data_section.py -v`
Expected: 3 passed

- [ ] **Step 9: Rebuild the report and spot-check**

Run: `python scripts/14_build_html_report.py`
Expected: `Wrote report/KP_Healthcare_Plan.html` with no errors (there is no `supplemental_records.csv` yet at this point, so this exercises the empty-state path).

Run: `python -c "print('supplemental-data' in open('report/KP_Healthcare_Plan.html', encoding='utf-8').read())"`
Expected: `True`

Run: `python -c "print('No additional information has been added yet.' in open('report/KP_Healthcare_Plan.html', encoding='utf-8').read())"`
Expected: `True`

- [ ] **Step 10: Commit**

```bash
git add scripts/14_build_html_report.py tests/test_supplemental_data_section.py report/KP_Healthcare_Plan.html
git commit -m "feat: render Additional Facility & District Information report section

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `server/report_context.py` — fold supplemental records into AI grounding

**Files:**
- Modify: `server/report_context.py`
- Modify: `tests/server/test_report_context.py`

**Interfaces:**
- Consumes: `supplemental_data.load_records()` (Task 1).
- Produces: `report_context.build_context(metrics=None, supplemental_records=None) -> str` — the new optional second parameter defaults to `supplemental_data.load_records()`, matching the existing `metrics=None` → `load_metrics()` pattern.

- [ ] **Step 1: Add the failing tests**

Find (the end of `tests/server/test_report_context.py`):

```python
def test_build_context_loads_real_metrics_by_default():
    context = report_context.build_context()
    assert "Total districts: 35" in context
    assert "Peshawar" in context
```

Replace with:

```python
def test_build_context_loads_real_metrics_by_default():
    context = report_context.build_context()
    assert "Total districts: 35" in context
    assert "Peshawar" in context


def test_build_context_includes_supplemental_records():
    supplemental = [
        {"district": "Alpha", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit, operational"},
    ]
    context = report_context.build_context(_fixture_metrics(), supplemental)
    assert "MRI Machine" in context
    assert "DHQ Hospital" in context


def test_build_context_omits_supplemental_section_when_empty():
    context = report_context.build_context(_fixture_metrics(), [])
    assert "Additional facility/district information" not in context
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_report_context.py -v`
Expected: `test_build_context_includes_supplemental_records` FAILs with `TypeError: build_context() takes from 0 to 1 positional arguments but 2 were given`

- [ ] **Step 3: Implement the change**

Find:

```python
"""Builds a compact text digest of the healthcare plan's data - the AI's
grounding context for the "Ask AI" chat panel. Deliberately not the raw
report HTML: markup is wasteful token-wise and invites the model to
comment on styling instead of data. See docs/superpowers/specs/
2026-08-15-ai-chat-panel-phase3-design.md section 3.
"""
import csv
from pathlib import Path

METRICS_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "district_metrics.csv"

TIER_ORDER = ("Critical", "High", "Moderate", "Low")


def load_metrics(path=METRICS_PATH):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_context(metrics=None):
    if metrics is None:
        metrics = load_metrics()
```

Replace with:

```python
"""Builds a compact text digest of the healthcare plan's data - the AI's
grounding context for the "Ask AI" chat panel. Deliberately not the raw
report HTML: markup is wasteful token-wise and invites the model to
comment on styling instead of data. See docs/superpowers/specs/
2026-08-15-ai-chat-panel-phase3-design.md section 3 and
2026-08-15-supplemental-facility-data-phase4b-design.md section 6.
"""
import csv
from pathlib import Path

from server import supplemental_data

METRICS_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "district_metrics.csv"

TIER_ORDER = ("Critical", "High", "Moderate", "Low")


def load_metrics(path=METRICS_PATH):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_context(metrics=None, supplemental_records=None):
    if metrics is None:
        metrics = load_metrics()
    if supplemental_records is None:
        supplemental_records = supplemental_data.load_records()
```

Find:

```python
        lines.append(
            f"{m['district']} | {m['need_tier']} | {float(m['gap_score']):.1f} | "
            f"{int(float(m['population_2023'])):,} | {float(m['beds_per_1000']):.2f} | "
            f"{float(m['doctors_per_1000']):.2f} | {m['terrain']}"
        )
    return "\n".join(lines)
```

Replace with:

```python
        lines.append(
            f"{m['district']} | {m['need_tier']} | {float(m['gap_score']):.1f} | "
            f"{int(float(m['population_2023'])):,} | {float(m['beds_per_1000']):.2f} | "
            f"{float(m['doctors_per_1000']):.2f} | {m['terrain']}"
        )

    if supplemental_records:
        lines.append("")
        lines.append("Additional facility/district information (from uploaded documents):")
        lines.append("district | facility | category | label | detail")
        ranked_supplemental = sorted(
            supplemental_records, key=lambda r: (r["district"], r.get("facility", ""), r["category"])
        )
        for r in ranked_supplemental:
            facility = r.get("facility") or "(district-wide)"
            lines.append(f"{r['district']} | {facility} | {r['category']} | {r['label']} | {r.get('detail', '')}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_report_context.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add server/report_context.py tests/server/test_report_context.py
git commit -m "feat: fold supplemental records into Ask AI's grounding context

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Wire it in — `/admin/api/supplemental-data` route, admin panel UI, end-to-end tests, manual verification

**Files:**
- Modify: `server/routes/admin.py`
- Modify: `server/admin_ui.py`
- Modify: `tests/server/test_routes.py`
- Create: `tests/server/test_supplemental_data_route.py`

**Interfaces:**
- Consumes: `supplemental_data.add_from_document`/`SupplementalDataError` (Task 1), `document_extraction.extract` (already wired in phase 4a, now also handling `.txt`/`.csv` from Task 2), `ai_client.AIProviderError` (phase 3), `keystore.PROVIDERS`/`get_key` (phase 2).
- Produces: the final, verified phase-4b feature.

- [ ] **Step 1: Update imports in `server/routes/admin.py`**

Find:

```python
"""GET/POST /admin, /admin/setup, /admin/login, /admin/logout, the
/admin/api/keys* JSON API, and /admin/api/extract for document upload. See
docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md
sections 6 and 8, and 2026-08-15-document-upload-phase4a-design.md
section 4.
"""
from fastapi import APIRouter, Body, Cookie, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from server import admin_ui, auth, document_extraction, keystore, providers
```

Replace with:

```python
"""GET/POST /admin, /admin/setup, /admin/login, /admin/logout, the
/admin/api/keys* JSON API, /admin/api/extract for document upload, and
/admin/api/supplemental-data for AI-extracted facility/district records.
See docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md
sections 6 and 8, 2026-08-15-document-upload-phase4a-design.md section 4,
and 2026-08-15-supplemental-facility-data-phase4b-design.md section 5.
"""
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Body, Cookie, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from server import admin_ui, ai_client, auth, document_extraction, keystore, providers, supplemental_data

REPORT_BUILD_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "14_build_html_report.py"
```

- [ ] **Step 2: Append the new route to `server/routes/admin.py`**

Find (the end of the file):

```python
    key_to_test = candidate_key or keystore.get_key(provider)
    ok, detail = providers.test_key(provider, key_to_test or "")
    return JSONResponse({"ok": ok, "detail": detail})


@router.post("/admin/api/extract")
```

Replace with:

```python
    key_to_test = candidate_key or keystore.get_key(provider)
    ok, detail = providers.test_key(provider, key_to_test or "")
    return JSONResponse({"ok": ok, "detail": detail})


@router.post("/admin/api/supplemental-data")
async def add_supplemental_data(
    file: UploadFile = File(...),
    provider: str = Form(...),
    instruction: str = Form(""),
    kp_admin_session: str | None = Cookie(default=None),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    key = keystore.get_key(provider)
    if not key:
        return JSONResponse(
            {"detail": f"No API key configured for {provider} - add one in the admin panel first."},
            status_code=400,
        )

    content_bytes = await file.read()
    try:
        extracted = document_extraction.extract(file.filename or "upload", content_bytes)
    except document_extraction.UnsupportedFormatError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=415)
    except document_extraction.ExtractionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    try:
        added = supplemental_data.add_from_document(
            provider, key, extracted.text, instruction, extracted.filename
        )
    except supplemental_data.SupplementalDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)

    result = subprocess.run(
        [sys.executable, str(REPORT_BUILD_SCRIPT)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return JSONResponse({"added": added, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"added": added})


@router.post("/admin/api/extract")
```

- [ ] **Step 3: Add the new section's CSS to `server/admin_ui.py`**

Find:

```python
#extract-result {
  display: block;
  width: 100%;
  margin-top: 0.75rem;
  padding: 0.6rem 0.7rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.8rem;
  resize: vertical;
}
"""
```

Replace with:

```python
#extract-result {
  display: block;
  width: 100%;
  margin-top: 0.75rem;
  padding: 0.6rem 0.7rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.8rem;
  resize: vertical;
}
#supplemental-instruction, #supplemental-provider {
  display: block;
  width: 100%;
  margin: 0.5rem 0;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  font-size: 0.85rem;
  font-family: inherit;
}
#supplemental-status { display: none; }
#supplemental-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }
"""
```

- [ ] **Step 4: Add the new section's JS to `server/admin_ui.py`**

Find:

```python
    var extractBtn = byId("extract-btn");
    if (extractBtn) {
      extractBtn.addEventListener("click", function () {
        var fileInput = byId("extract-file-input");
        var statusEl = byId("extract-status");
        var resultEl = byId("extract-result");
        var file = fileInput.files[0];
        statusEl.style.display = "none";
        resultEl.value = "";

        if (!file) {
          statusEl.textContent = "Choose a file first";
          statusEl.style.display = "block";
          return;
        }

        var formData = new FormData();
        formData.append("file", file);
        extractBtn.disabled = true;
        extractBtn.textContent = "Extracting...";

        fetch("/admin/api/extract", { method: "POST", body: formData })
          .then(function (res) {
            return res.json().then(function (data) { return { ok: res.ok, data: data }; });
          })
          .then(function (result) {
            extractBtn.disabled = false;
            extractBtn.textContent = "Extract";
            if (result.ok) {
              resultEl.value = result.data.text;
            } else {
              statusEl.textContent = (result.data && result.data.detail) || "Extraction failed";
              statusEl.style.display = "block";
            }
          })
          .catch(function (err) {
            extractBtn.disabled = false;
            extractBtn.textContent = "Extract";
            statusEl.textContent = "Request failed: " + err;
            statusEl.style.display = "block";
          });
      });
    }
  });
})();
"""
```

Replace with:

```python
    var extractBtn = byId("extract-btn");
    if (extractBtn) {
      extractBtn.addEventListener("click", function () {
        var fileInput = byId("extract-file-input");
        var statusEl = byId("extract-status");
        var resultEl = byId("extract-result");
        var file = fileInput.files[0];
        statusEl.style.display = "none";
        resultEl.value = "";

        if (!file) {
          statusEl.textContent = "Choose a file first";
          statusEl.style.display = "block";
          return;
        }

        var formData = new FormData();
        formData.append("file", file);
        extractBtn.disabled = true;
        extractBtn.textContent = "Extracting...";

        fetch("/admin/api/extract", { method: "POST", body: formData })
          .then(function (res) {
            return res.json().then(function (data) { return { ok: res.ok, data: data }; });
          })
          .then(function (result) {
            extractBtn.disabled = false;
            extractBtn.textContent = "Extract";
            if (result.ok) {
              resultEl.value = result.data.text;
            } else {
              statusEl.textContent = (result.data && result.data.detail) || "Extraction failed";
              statusEl.style.display = "block";
            }
          })
          .catch(function (err) {
            extractBtn.disabled = false;
            extractBtn.textContent = "Extract";
            statusEl.textContent = "Request failed: " + err;
            statusEl.style.display = "block";
          });
      });
    }

    var addToReportBtn = byId("add-to-report-btn");
    if (addToReportBtn) {
      addToReportBtn.addEventListener("click", function () {
        var fileInput = byId("extract-file-input");
        var instructionInput = byId("supplemental-instruction");
        var providerSelect = byId("supplemental-provider");
        var statusEl = byId("supplemental-status");
        var resultEl = byId("supplemental-result");
        var file = fileInput.files[0];
        statusEl.style.display = "none";
        resultEl.innerHTML = "";

        if (!file) {
          statusEl.textContent = "Choose a file first";
          statusEl.style.display = "block";
          return;
        }

        var formData = new FormData();
        formData.append("file", file);
        formData.append("provider", providerSelect.value);
        formData.append("instruction", instructionInput.value);
        addToReportBtn.disabled = true;
        addToReportBtn.textContent = "Adding...";

        fetch("/admin/api/supplemental-data", { method: "POST", body: formData })
          .then(function (res) {
            return res.json().then(function (data) { return { ok: res.ok, data: data }; });
          })
          .then(function (result) {
            addToReportBtn.disabled = false;
            addToReportBtn.textContent = "Add to Report";
            if (result.ok) {
              var added = (result.data && result.data.added) || [];
              var summary = added.map(function (r) {
                return r.district + (r.facility ? " / " + r.facility : "") + " - " + r.category + ": " + r.label;
              }).join("<br>");
              resultEl.innerHTML = "<p>Added " + added.length + " record(s):</p><p>" + summary + "</p>";
              if (result.data && result.data.rebuild_warning) {
                resultEl.innerHTML += "<p class='error'>" + result.data.rebuild_warning + "</p>";
              }
            } else {
              statusEl.textContent = (result.data && result.data.detail) || "Request failed";
              statusEl.style.display = "block";
            }
          })
          .catch(function (err) {
            addToReportBtn.disabled = false;
            addToReportBtn.textContent = "Add to Report";
            statusEl.textContent = "Request failed: " + err;
            statusEl.style.display = "block";
          });
      });
    }
  });
})();
"""
```

- [ ] **Step 5: Add the new section's markup to `admin_ui.py`**

Find:

```python
def _provider_row_html(status):
```

Replace with:

```python
def _provider_option_html(status):
    provider = status["provider"]
    display_name = DISPLAY_NAMES.get(provider, provider)
    return f'<option value="{html.escape(provider)}">{html.escape(display_name)}</option>'


def _provider_row_html(status):
```

Find:

```python
def render_admin_panel(statuses):
    rows = "\n".join(_provider_row_html(s) for s in statuses)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Provider Keys</title>
<style>{ADMIN_CSS}</style>
</head>
<body>
<div class="card panel-card">
<h1>AI Provider Keys</h1>
<p>Keys are stored in this machine's OS credential store, never in a file or sent to the browser after saving.</p>
{rows}
<div class="upload-section">
  <h2>Extract Document</h2>
  <p class="hint">Upload an Excel, PDF, Word, or HTML file to preview its extracted text. This does not change any plan data yet.</p>
  <input type="file" id="extract-file-input" accept=".xlsx,.xls,.pdf,.docx,.html,.htm">
  <button type="button" class="primary" id="extract-btn">Extract</button>
  <p id="extract-status" class="error"></p>
  <textarea id="extract-result" readonly rows="12" placeholder="Extracted text will appear here"></textarea>
</div>
<p style="margin-top:1.5rem"><button type="button" class="secondary" id="logout-btn">Log Out</button></p>
</div>
<script>{ADMIN_JS}</script>
</body>
</html>
"""
```

Replace with:

```python
def render_admin_panel(statuses):
    rows = "\n".join(_provider_row_html(s) for s in statuses)
    provider_options = "\n".join(_provider_option_html(s) for s in statuses)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Provider Keys</title>
<style>{ADMIN_CSS}</style>
</head>
<body>
<div class="card panel-card">
<h1>AI Provider Keys</h1>
<p>Keys are stored in this machine's OS credential store, never in a file or sent to the browser after saving.</p>
{rows}
<div class="upload-section">
  <h2>Extract Document</h2>
  <p class="hint">Upload an Excel, PDF, Word, Text, CSV, or HTML file to preview its extracted text, or add it to the report as supplemental facility/district information (equipment, medicine, departments, diseases treated, outbreaks, or anything else).</p>
  <input type="file" id="extract-file-input" accept=".xlsx,.xls,.pdf,.docx,.html,.htm,.txt,.csv">
  <button type="button" class="primary" id="extract-btn">Extract</button>
  <p id="extract-status" class="error"></p>
  <textarea id="extract-result" readonly rows="12" placeholder="Extracted text will appear here"></textarea>
  <label for="supplemental-instruction">Instruction (optional)</label>
  <textarea id="supplemental-instruction" rows="2" placeholder="e.g. this equipment list is for Peshawar's DHQ Hospital"></textarea>
  <label for="supplemental-provider">AI provider</label>
  <select id="supplemental-provider">
{provider_options}
  </select>
  <button type="button" class="primary" id="add-to-report-btn">Add to Report</button>
  <p id="supplemental-status" class="error"></p>
  <div id="supplemental-result"></div>
</div>
<p style="margin-top:1.5rem"><button type="button" class="secondary" id="logout-btn">Log Out</button></p>
</div>
<script>{ADMIN_JS}</script>
</body>
</html>
"""
```

- [ ] **Step 6: Write `tests/server/test_supplemental_data_route.py`**

```python
"""End-to-end /admin/api/supplemental-data tests via FastAPI's TestClient.
document_extraction.extract, supplemental_data.add_from_document, and the
report-rebuild subprocess call are all mocked - no real file parsing, AI
provider call, or report-build script run. keyring is mocked too, same
pattern as tests/server/test_routes.py.
"""
import io

import pytest
from fastapi.testclient import TestClient

from server import document_extraction, keystore, supplemental_data
from server.app import create_app
from server.routes import admin as admin_route


class FakeStore:
    def __init__(self):
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        del self.data[(service, username)]


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(keystore.keyring, "get_password", store.get_password)
    monkeypatch.setattr(keystore.keyring, "set_password", store.set_password)
    monkeypatch.setattr(keystore.keyring, "delete_password", store.delete_password)
    return store


@pytest.fixture
def client(fake_store):
    return TestClient(create_app())


SETUP_FORM = {"password": "hunter2hunter2", "confirm": "hunter2hunter2"}


def _login(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})


class FakeCompletedProcess:
    returncode = 0
    stderr = ""


def _upload(client, filename="data.xlsx"):
    return client.post(
        "/admin/api/supplemental-data",
        files={"file": (filename, io.BytesIO(b"x"), "application/octet-stream")},
        data={"provider": "anthropic", "instruction": "test instruction"},
    )


def test_supplemental_data_requires_authentication(client):
    response = _upload(client)
    assert response.status_code == 401


def test_supplemental_data_success(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="data.xlsx", format="xlsx", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)
    fake_added = [{"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
                   "label": "MRI Machine", "detail": "1 unit", "source_document": "data.xlsx",
                   "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data, "add_from_document", lambda *args, **kwargs: fake_added)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())

    response = _upload(client)
    assert response.status_code == 200
    assert response.json() == {"added": fake_added}


def test_supplemental_data_without_configured_key_returns_400(client):
    _login(client)
    response = _upload(client)
    assert response.status_code == 400


def test_supplemental_data_unsupported_format_returns_415(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")

    def failing_extract(filename, content_bytes):
        raise document_extraction.UnsupportedFormatError("Unsupported file type: .zip")

    monkeypatch.setattr(document_extraction, "extract", failing_extract)
    response = _upload(client, filename="data.zip")
    assert response.status_code == 415


def test_supplemental_data_validation_failure_returns_400(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="data.xlsx", format="xlsx", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)

    def failing_add(*args, **kwargs):
        raise supplemental_data.SupplementalDataError("AI response was not valid JSON")

    monkeypatch.setattr(supplemental_data, "add_from_document", failing_add)
    response = _upload(client)
    assert response.status_code == 400


def test_supplemental_data_rebuild_failure_still_returns_added_records(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="data.xlsx", format="xlsx", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)
    fake_added = [{"district": "Peshawar", "facility": "", "category": "equipment", "label": "X-ray",
                   "detail": "", "source_document": "data.xlsx", "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data, "add_from_document", lambda *args, **kwargs: fake_added)

    class FailedProcess:
        returncode = 1
        stderr = "Traceback: something broke"

    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FailedProcess())

    response = _upload(client)
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == fake_added
    assert "rebuild_warning" in body
```

- [ ] **Step 7: Add a UI-presence assertion to `tests/server/test_routes.py`**

Find (the end of the file):

```python
def test_admin_panel_includes_extract_upload_section(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    for hook in ('id="extract-file-input"', 'id="extract-btn"', 'id="extract-result"', "/admin/api/extract"):
        assert hook in panel.text, f"missing hook: {hook}"
```

Replace with:

```python
def test_admin_panel_includes_extract_upload_section(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    for hook in ('id="extract-file-input"', 'id="extract-btn"', 'id="extract-result"', "/admin/api/extract"):
        assert hook in panel.text, f"missing hook: {hook}"


def test_admin_panel_includes_supplemental_data_section(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    for hook in (
        'id="supplemental-instruction"', 'id="supplemental-provider"',
        'id="add-to-report-btn"', "/admin/api/supplemental-data",
    ):
        assert hook in panel.text, f"missing hook: {hook}"
```

- [ ] **Step 8: Run the new and modified tests**

Run: `pytest tests/server/test_supplemental_data_route.py tests/server/test_routes.py -v`
Expected: 19 passed (6 in `test_supplemental_data_route.py`, 13 in `test_routes.py` — the 12 already there plus the new one)

- [ ] **Step 9: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass — the existing 136 plus this phase's 12 (`test_supplemental_data.py`) + 3 (`test_document_extraction.py` new cases) + 2 (`test_supplemental_records.py`) + 3 (`test_supplemental_data_section.py`) + 2 (`test_report_context.py` new cases) + 6 (`test_supplemental_data_route.py`) + 1 (`test_routes.py` new case) = roughly 165; exact count isn't load-bearing, "all pass" is.

- [ ] **Step 10: Manual browser verification**

Start the server: `python -m server`

In a browser at `http://127.0.0.1:8420/admin` (log in):
- The "Extract Document" section now shows an instruction textarea, a provider dropdown listing all 5 providers, and an "Add to Report" button alongside the existing "Extract" button.
- Prepare a small real test file (e.g. a `.txt` file: "Peshawar's DHQ Hospital has 1 MRI machine, fully operational, and 2 dialysis machines, one currently out of service."). Choose it, optionally type an instruction, pick a provider you have a real key configured for, click "Add to Report".
- Confirm the button shows "Adding..." briefly, then a summary of the added record(s) appears (district / facility - category: label).
- Go to `http://127.0.0.1:8420/` and confirm the new "Additional Facility & District Information" section near the bottom of the report shows the record(s) just added.
- Ask the "Ask AI" chat panel a question referencing the uploaded fact (e.g. "What equipment does Peshawar's DHQ Hospital have?") and confirm the answer references it.
- Try an instruction-only edge case: upload the same file again but with an obviously bogus instruction like "this is for a district called Atlantis" and confirm a clear "unknown district" error surfaces rather than a crash (only if the AI actually follows the bogus instruction over the real document content — if it doesn't, that's fine too, note it and move on).
- Confirm `POST /admin/api/supplemental-data` without a session returns 401 (e.g. via `curl -F "file=@somefile.txt" -F "provider=anthropic" http://127.0.0.1:8420/admin/api/supplemental-data`).

If any of these fail, this is a real bug to fix before committing — do not report success without having actually driven the browser through each step. Clean up afterward: delete any test keys/records this created that shouldn't remain in the real data files (if a bogus test record was added to `data/processed/supplemental_records.csv`, remove it and rebuild the report so the committed report file doesn't carry throwaway test content), matching the same cleanup discipline used in every earlier phase's manual verification.

- [ ] **Step 11: Final commit**

```bash
git add server/routes/admin.py server/admin_ui.py tests/server/test_supplemental_data_route.py tests/server/test_routes.py
git commit -m "feat: wire AI-extracted supplemental data into the admin panel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
