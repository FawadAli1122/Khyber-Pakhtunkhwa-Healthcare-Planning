# AI-Editable Pipeline Data via Overrides (Phase 4d) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin update the core pipeline's population and aggregate health-facility numbers, per district, through the same document-upload-plus-instruction AI-extraction UX phases 4b/4c already established — with the update flowing through `district_metrics.csv`'s computed columns, the GIS shapefiles, the QGIS project, and the HTML report — without ever risking silent data loss on a future full pipeline re-run, and without an implausible AI-proposed number quietly corrupting the gap-score model.

**Architecture:** A new append-only `data/processed/metric_overrides.csv` overlay, applied by a new pipeline stage (`scripts/07b_apply_metric_overrides.py`) inserted right before `08_compute_district_metrics.py` — which stays completely unmodified, along with every extraction script (`02`, `17`, `18`, `19`). A new `server/metric_overrides.py` module (structurally a sibling of `server/supplemental_data.py`) extracts and validates AI-proposed `{district, file, column, value}` tuples, including a sanity-bounded swing check against the *current* live value. A new `scripts/run_downstream.py` re-runs only the stages that actually depend on these numbers (skipping the expensive fetch/geocode/DEM stages), and a new admin route triggers it exactly like phase 4b's report-rebuild subprocess pattern, just pointed at a bigger downstream sequence.

**Tech Stack:** Python 3.12, existing project dependencies only (no new installs) — reuses `ai_client`, `document_extraction`, `supplemental_data.load_known_districts`, `scripts.lib.districts.normalize_district`, `keystore`, FastAPI, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-15-pipeline-data-overrides-phase4d-design.md`

## Global Constraints

- No new dependencies.
- Reuse `ai_client.ask()` and `document_extraction.extract()` exactly as they exist today — this phase adds no new provider-dispatch or format-parsing logic of its own.
- `08_compute_district_metrics.py` and every extraction script (`02_compile_population.py`, `17_extract_devstats_health.py`, `18_extract_devstats_roads.py`, `19_extract_devstats_budget.py`) are never modified by this phase — they continue to read/write exactly as they do today.
- Only a whitelisted set of independently-settable columns per file are overridable: `population` → `{population_2023, population_prior, growth_rate_pct}`; `health` → `{govt_institutions, govt_beds, pvt_hospitals, pvt_beds, medical_staff, paramedical_staff, pvt_practitioners}`. Never every column in a file, never a derived column (e.g. `pop_per_bed`).
- Every AI-proposed district is validated against the real 35-district list (reused from `supplemental_data.load_known_districts`), case-insensitive-matched to the canonical name.
- Every AI-proposed value must be non-negative and within the sanity-swing threshold of the column's *current* value, read live from the real target CSV at validation time (never cached, never assumed): ±50% for `population` fields, ±100% for `health` fields. A current value of exactly 0 skips the swing check (a percentage swing from zero isn't meaningful).
- `scripts/07b_apply_metric_overrides.py` re-validates every override's column against the real file header at apply-time — an unknown column there is a hard pipeline-stage failure (non-zero exit), never silently skipped.
- `data/processed/metric_overrides.csv` is append-only; the apply stage reads the *latest* row per `(district, file, column)` as the effective value.
- `scripts/run_downstream.py` re-runs only `07b → 08 → 09 → 10 → 11 → 20 → 12 → 13 → 14` — never the expensive fetch/geocode/DEM stages, never a full `run_all.py` re-run.
- Every typed exception (`MetricOverrideError`, reused `UnsupportedFormatError`/`ExtractionError`/`AIProviderError`) carries a message safe to show the admin directly, never a raw traceback.
- Every AI provider call in every test is mocked — no test may require a real API key or network access, same posture as every earlier phase.
- If the downstream-rebuild subprocess fails or times out after overrides were already appended, the route still returns 200 with the added records plus a `rebuild_warning` — data that was genuinely saved is never reported as a failure.
- **Ordering note:** Task 3's Find/Replace blocks against `server/routes/admin.py` and `server/admin_ui.py` are written against those files' current state (phase 4b's post-fix-wave content) — phase 4c's plan (database ingestion) has NOT been executed as of this plan's writing and touches the exact same insertion points. If phase 4c is executed before this plan's Task 3, its implementer must re-derive the Find blocks against the then-current file content rather than force a stale match — the standard "STOP and escalate if a Find block doesn't match" instruction already covers this; it is expected, not a plan defect.

---

### Task 1: `server/metric_overrides.py` — core module

**Files:**
- Create: `server/metric_overrides.py`
- Test: `tests/server/test_metric_overrides.py`

**Interfaces:**
- Produces: `metric_overrides.MetricOverrideError(Exception)`; `metric_overrides.OVERRIDES_PATH`, `metric_overrides.OVERRIDABLE_FIELDS` (module-level constants); `metric_overrides.FIELDNAMES` (tuple); `metric_overrides.load_records(path=OVERRIDES_PATH) -> list[dict]`; `metric_overrides.append_records(records, path=OVERRIDES_PATH) -> None`; `metric_overrides.build_override_question(instruction, known_districts) -> str`; `metric_overrides.parse_override_response(raw_text, known_districts) -> list[dict]` (raises `MetricOverrideError`); `metric_overrides.add_from_document(provider, key, document_text, instruction, source_document) -> list[dict]`.
- Consumes: `ai_client.ask(provider, key, question, context)` (phase 3, called as `ai_client.ask(...)` — a module-attribute lookup, so tests can monkeypatch it the same way `test_supplemental_data.py` does); `scripts.lib.districts.normalize_district` (existing); `server.supplemental_data.load_known_districts` (phase 4b, imported not duplicated — this phase reads the same district list).

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_metric_overrides.py`:

```python
"""Unit tests for server/metric_overrides.py. ai_client.ask is mocked in
every test that calls add_from_document - no real provider calls. CSV I/O
uses tmp_path, never the real data files. See docs/superpowers/specs/
2026-08-15-pipeline-data-overrides-phase4d-design.md sections 3-4.
"""
import json

import pytest

from server import ai_client, metric_overrides

KNOWN_DISTRICTS = ["Peshawar", "Chitral", "Abbottabad"]


@pytest.fixture
def fake_fields(tmp_path, monkeypatch):
    population_csv = tmp_path / "population.csv"
    population_csv.write_text(
        "district,population_2023,population_prior,growth_rate_pct\n"
        "Peshawar,4750388,4269079,1.10\n"
        "Chitral,318234,300000,0.60\n",
        encoding="utf-8",
    )
    health_csv = tmp_path / "health.csv"
    health_csv.write_text(
        "district,govt_institutions,govt_beds,pvt_hospitals,pvt_beds,medical_staff,paramedical_staff,pvt_practitioners\n"
        "Peshawar,129,5310,147,14532,2941,5285,1035\n"
        "Chitral,0,0,0,0,0,0,0\n",
        encoding="utf-8",
    )
    fields = {
        "population": (population_csv, {"population_2023", "population_prior", "growth_rate_pct"}, 0.5),
        "health": (health_csv, {"govt_institutions", "govt_beds", "pvt_hospitals", "pvt_beds",
                                 "medical_staff", "paramedical_staff", "pvt_practitioners"}, 1.0),
    }
    monkeypatch.setattr(metric_overrides, "OVERRIDABLE_FIELDS", fields)
    return fields


def test_parse_override_response_valid_json(fake_fields):
    raw = json.dumps([
        {"district": "Peshawar", "file": "population", "column": "population_2023",
         "value": 5000000, "reason": "New provincial estimate"},
    ])
    records = metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)
    assert records == [
        {"district": "Peshawar", "file": "population", "column": "population_2023",
         "value": 5000000.0, "reason": "New provincial estimate"},
    ]


def test_parse_override_response_strips_code_fence(fake_fields):
    raw = "```json\n" + json.dumps([
        {"district": "Peshawar", "file": "health", "column": "govt_beds", "value": 6000, "reason": ""},
    ]) + "\n```"
    records = metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)
    assert records[0]["column"] == "govt_beds"


def test_parse_override_response_invalid_json_raises(fake_fields):
    with pytest.raises(metric_overrides.MetricOverrideError):
        metric_overrides.parse_override_response("not json at all", KNOWN_DISTRICTS)


def test_parse_override_response_unknown_district_raises(fake_fields):
    raw = json.dumps([{"district": "Atlantis", "file": "population", "column": "population_2023",
                        "value": 100, "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError, match="Atlantis"):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_unknown_file_raises(fake_fields):
    raw = json.dumps([{"district": "Peshawar", "file": "roads", "column": "road_length_km",
                        "value": 100, "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError, match="roads"):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_unknown_column_raises(fake_fields):
    raw = json.dumps([{"district": "Peshawar", "file": "population", "column": "pop_per_bed",
                        "value": 100, "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError, match="pop_per_bed"):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_non_numeric_value_raises(fake_fields):
    raw = json.dumps([{"district": "Peshawar", "file": "population", "column": "population_2023",
                        "value": "a lot", "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_negative_value_raises(fake_fields):
    raw = json.dumps([{"district": "Peshawar", "file": "population", "column": "population_2023",
                        "value": -5, "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_excessive_swing_raises(fake_fields):
    # Peshawar's current population_2023 is 4750388; +/-50% threshold allows up to 7125582.
    raw = json.dumps([{"district": "Peshawar", "file": "population", "column": "population_2023",
                        "value": 20000000, "reason": ""}])
    with pytest.raises(metric_overrides.MetricOverrideError, match="swing"):
        metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)


def test_parse_override_response_within_swing_threshold_accepted(fake_fields):
    # +30% is within the +/-50% population threshold.
    raw = json.dumps([{"district": "Peshawar", "file": "population", "column": "population_2023",
                        "value": 6175504, "reason": "growth"}])
    records = metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)
    assert records[0]["value"] == 6175504.0


def test_parse_override_response_zero_current_value_skips_swing_check(fake_fields):
    # Chitral's govt_institutions is 0 - any positive value is accepted since a
    # percentage swing from zero isn't meaningful.
    raw = json.dumps([{"district": "Chitral", "file": "health", "column": "govt_institutions",
                        "value": 5, "reason": "new registrations"}])
    records = metric_overrides.parse_override_response(raw, KNOWN_DISTRICTS)
    assert records[0]["value"] == 5.0


def test_parse_override_response_empty_array_raises(fake_fields):
    with pytest.raises(metric_overrides.MetricOverrideError, match="no updates"):
        metric_overrides.parse_override_response("[]", KNOWN_DISTRICTS)


def test_parse_override_response_not_a_list_raises(fake_fields):
    with pytest.raises(metric_overrides.MetricOverrideError):
        metric_overrides.parse_override_response(json.dumps({"district": "Peshawar"}), KNOWN_DISTRICTS)


def test_append_and_load_records_round_trip(tmp_path):
    path = tmp_path / "metric_overrides.csv"
    metric_overrides.append_records(
        [{"district": "Peshawar", "file": "population", "column": "population_2023",
          "value": 5000000, "reason": "estimate", "source": "report.pdf",
          "added_at": "2026-08-15T00:00:00+00:00"}],
        path=path,
    )
    records = metric_overrides.load_records(path=path)
    assert len(records) == 1
    assert records[0]["district"] == "Peshawar"
    assert records[0]["column"] == "population_2023"


def test_append_records_writes_header_into_existing_empty_file(tmp_path):
    path = tmp_path / "metric_overrides.csv"
    path.touch()
    metric_overrides.append_records(
        [{"district": "Peshawar", "file": "population", "column": "population_2023",
          "value": 5000000, "reason": "", "source": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}],
        path=path,
    )
    records = metric_overrides.load_records(path=path)
    assert len(records) == 1
    assert records[0]["district"] == "Peshawar"


def test_load_records_returns_empty_list_when_file_missing(tmp_path):
    path = tmp_path / "does_not_exist.csv"
    assert metric_overrides.load_records(path=path) == []


def test_add_from_document_success(fake_fields, tmp_path, monkeypatch):
    overrides_path = tmp_path / "metric_overrides.csv"
    monkeypatch.setattr(metric_overrides, "OVERRIDES_PATH", overrides_path)
    monkeypatch.setattr(metric_overrides, "load_known_districts", lambda: KNOWN_DISTRICTS)

    raw_response = json.dumps([
        {"district": "Peshawar", "file": "population", "column": "population_2023",
         "value": 5000000, "reason": "New estimate"},
    ])
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw_response)

    added = metric_overrides.add_from_document(
        "anthropic", "sk-ant-real", "some document text", "update Peshawar's population", "census.pdf",
    )
    assert len(added) == 1
    assert added[0]["district"] == "Peshawar"
    assert added[0]["source"] == "census.pdf"
    assert "added_at" in added[0]

    saved = metric_overrides.load_records(path=overrides_path)
    assert len(saved) == 1


def test_add_from_document_validation_failure_raises(fake_fields, monkeypatch):
    monkeypatch.setattr(metric_overrides, "load_known_districts", lambda: KNOWN_DISTRICTS)
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: "not valid json")
    with pytest.raises(metric_overrides.MetricOverrideError):
        metric_overrides.add_from_document("anthropic", "sk-ant-real", "text", "", "doc.pdf")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_metric_overrides.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.metric_overrides'`

- [ ] **Step 3: Implement `server/metric_overrides.py`**

```python
"""AI-editable overrides for the core pipeline's population and aggregate
health-facility input numbers - a separate, append-only overlay applied by
scripts/07b_apply_metric_overrides.py, never a direct edit to the
regenerated source files themselves. See docs/superpowers/specs/
2026-08-15-pipeline-data-overrides-phase4d-design.md.
"""
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from scripts.lib.districts import normalize_district
from server import ai_client
from server.supplemental_data import load_known_districts

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
OVERRIDES_PATH = PROCESSED / "metric_overrides.csv"

FIELDNAMES = ("district", "file", "column", "value", "reason", "source", "added_at")

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


def load_records(path=OVERRIDES_PATH):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: (v if v is not None else "") for k, v in row.items()} for row in csv.DictReader(f)]


def append_records(records, path=OVERRIDES_PATH):
    path = Path(path)
    is_new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in FIELDNAMES})


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
        record["source"] = source_document
        record["added_at"] = now

    append_records(records, path=OVERRIDES_PATH)
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_metric_overrides.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add server/metric_overrides.py tests/server/test_metric_overrides.py
git commit -m "feat: add metric_overrides module for AI-validated pipeline-data updates

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: New pipeline stage + downstream-only rebuild script

**Files:**
- Create: `scripts/07b_apply_metric_overrides.py`
- Create: `scripts/run_downstream.py`
- Test: `tests/test_apply_metric_overrides.py`
- Modify: `scripts/run_all.py`

**Interfaces:**
- Produces: `07b_apply_metric_overrides.load_overrides(path=OVERRIDES_PATH) -> list[dict]`; `07b_apply_metric_overrides.latest_by_target(overrides) -> dict[(district, file, column), value]`; `07b_apply_metric_overrides.apply_overrides_to_file(file_key, path, overrides_for_file: dict[(district, column), value]) -> None` (raises `ValueError` on an unknown column or district); `07b_apply_metric_overrides.main()`.
- Consumes: `data/processed/metric_overrides.csv` (written by Task 1's `server/metric_overrides.py`, via the admin route in Task 3).

This task does not depend on Task 1's Python module directly (it reads the CSV file Task 1 writes, not the module itself), so it can be implemented independently.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_apply_metric_overrides.py`:

```python
"""Unit tests for scripts/07b_apply_metric_overrides.py. Module names
starting with a digit aren't valid Python identifiers, so this project's
tests for numbered pipeline-stage scripts import them via
importlib.import_module rather than a normal import statement - this test
follows that same established convention (see tests/test_district_metrics.py).
"""
import csv
import importlib

import pytest

apply_mod = importlib.import_module("scripts.07b_apply_metric_overrides")


def test_load_overrides_returns_empty_list_when_file_missing(tmp_path):
    path = tmp_path / "missing.csv"
    assert apply_mod.load_overrides(path=path) == []


def test_latest_by_target_keeps_last_row_per_key():
    overrides = [
        {"district": "Peshawar", "file": "population", "column": "population_2023", "value": "5000000"},
        {"district": "Peshawar", "file": "population", "column": "population_2023", "value": "5200000"},
        {"district": "Chitral", "file": "health", "column": "govt_beds", "value": "10"},
    ]
    latest = apply_mod.latest_by_target(overrides)
    assert latest[("Peshawar", "population", "population_2023")] == "5200000"
    assert latest[("Chitral", "health", "govt_beds")] == "10"


def test_apply_overrides_to_file_patches_matching_row(tmp_path):
    path = tmp_path / "population.csv"
    path.write_text(
        "district,population_2023,growth_rate_pct\n"
        "Peshawar,4750388,1.10\n"
        "Chitral,318234,0.60\n",
        encoding="utf-8",
    )
    apply_mod.apply_overrides_to_file("population", path, {("Peshawar", "population_2023"): "5000000"})
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    by_district = {r["district"]: r for r in rows}
    assert by_district["Peshawar"]["population_2023"] == "5000000"
    assert by_district["Chitral"]["population_2023"] == "318234"  # untouched


def test_apply_overrides_to_file_no_overrides_is_a_noop(tmp_path):
    path = tmp_path / "population.csv"
    original = "district,population_2023\nPeshawar,4750388\n"
    path.write_text(original, encoding="utf-8")
    apply_mod.apply_overrides_to_file("population", path, {})
    assert path.read_text(encoding="utf-8") == original


def test_apply_overrides_to_file_unknown_column_raises(tmp_path):
    path = tmp_path / "population.csv"
    path.write_text("district,population_2023\nPeshawar,4750388\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bogus_column"):
        apply_mod.apply_overrides_to_file("population", path, {("Peshawar", "bogus_column"): "1"})


def test_apply_overrides_to_file_unknown_district_raises(tmp_path):
    path = tmp_path / "population.csv"
    path.write_text("district,population_2023\nPeshawar,4750388\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Atlantis"):
        apply_mod.apply_overrides_to_file("population", path, {("Atlantis", "population_2023"): "1"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_apply_metric_overrides.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.07b_apply_metric_overrides'`

- [ ] **Step 3: Implement `scripts/07b_apply_metric_overrides.py`**

```python
"""Applies data/processed/metric_overrides.csv (AI-proposed pipeline-data
updates, written by server/metric_overrides.py) on top of the freshly
regenerated kp_district_population_2023.csv/dev_stats_health.csv, before
08_compute_district_metrics.py reads them. No overrides present is a
byte-for-byte no-op. Re-validates every override's column against the real
file header at apply-time - a genuinely unknown column is a hard failure,
since silently skipping it would let district_metrics.csv compute from
stale data with nothing to signal that. See docs/superpowers/specs/
2026-08-15-pipeline-data-overrides-phase4d-design.md section 5.
"""
import csv
import sys
from pathlib import Path

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
OVERRIDES_PATH = PROCESSED / "metric_overrides.csv"

TARGET_FILES = {
    "population": PROCESSED / "kp_district_population_2023.csv",
    "health": PROCESSED / "dev_stats_health.csv",
}


def load_overrides(path=OVERRIDES_PATH):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def latest_by_target(overrides):
    """Returns {(district, file, column): value}, keeping only the latest
    row per key - the overrides file is append-only, so a later row for
    the same target wins over an earlier one."""
    latest = {}
    for row in overrides:
        key = (row["district"], row["file"], row["column"])
        latest[key] = row["value"]
    return latest


def apply_overrides_to_file(file_key, path, overrides_for_file):
    if not overrides_for_file:
        return

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    for (district, column), value in overrides_for_file.items():
        if column not in fieldnames:
            raise ValueError(f"Unknown column {column!r} for file {file_key!r} - refusing to apply")
        matched = False
        for row in rows:
            if row["district"] == district:
                row[column] = value
                matched = True
                break
        if not matched:
            raise ValueError(f"District {district!r} not found in {path.name} - refusing to apply")

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    overrides = load_overrides()
    latest = latest_by_target(overrides)

    by_file = {}
    for (district, file_key, column), value in latest.items():
        by_file.setdefault(file_key, {})[(district, column)] = value

    for file_key, path in TARGET_FILES.items():
        apply_overrides_to_file(file_key, path, by_file.get(file_key, {}))

    print(f"Applied {len(latest)} override(s) across {len(by_file)} file(s)")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(f"Error applying metric overrides: {exc}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_apply_metric_overrides.py -v`
Expected: 6 passed

- [ ] **Step 5: Create `scripts/run_downstream.py`**

```python
"""Re-runs only the pipeline stages that depend on population/health
numbers, for use after an admin applies pipeline-data overrides via the
admin panel - NOT a full pipeline re-run (skips the expensive
fetch/geocode/DEM stages, which aren't affected by these overrides). Each
stage is idempotent, same as run_all.py's own stages. See
docs/superpowers/specs/2026-08-15-pipeline-data-overrides-phase4d-design.md
section 5.
"""
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
STAGES = [
    "07b_apply_metric_overrides.py",
    "08_compute_district_metrics.py",
    "09_gap_score_and_clusters.py",
    "10_forecast_demand.py",
    "11_suggest_new_sites.py",
    "20_cross_validate_facility_counts.py",
    "12_write_shapefiles.py",
    "13_build_qgis_project.py",
    "14_build_html_report.py",
]


def main():
    for stage in STAGES:
        print(f"=== Running {stage} ===")
        result = subprocess.run([sys.executable, str(SCRIPTS_DIR / stage)])
        if result.returncode != 0:
            print(f"Stage {stage} failed with exit code {result.returncode}; stopping.")
            sys.exit(result.returncode)
    print("=== Downstream pipeline complete ===")


if __name__ == "__main__":
    main()
```

No dedicated test file for `run_downstream.py` — it is a pure subprocess-orchestrator script, matching the existing, unmodified precedent that `scripts/run_all.py` (the same kind of script) also has no test file in this project. It is exercised directly in Task 3's manual verification.

- [ ] **Step 6: Insert the new stage into `scripts/run_all.py`**

Find:

```python
    "17_extract_devstats_health.py",          # independent - reads the Dev Stats PDF directly
    "18_extract_devstats_roads.py",           # independent - reads the Dev Stats PDF directly
    "19_extract_devstats_budget.py",          # independent - reads the Dev Stats PDF directly
    "08_compute_district_metrics.py",         # needs 07 (facilities) + 16 (terrain)
```

Replace with:

```python
    "17_extract_devstats_health.py",          # independent - reads the Dev Stats PDF directly
    "18_extract_devstats_roads.py",           # independent - reads the Dev Stats PDF directly
    "19_extract_devstats_budget.py",          # independent - reads the Dev Stats PDF directly
    "07b_apply_metric_overrides.py",          # applies data/processed/metric_overrides.csv on top of 02/17 (before 08 reads them)
    "08_compute_district_metrics.py",         # needs 07 (facilities) + 16 (terrain) + 07b (overrides applied)
```

- [ ] **Step 7: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass — no regressions from the `run_all.py` edit (it's a pure data/list change, no existing test exercises `run_all.py` directly per the "no test file" precedent noted in Step 5).

- [ ] **Step 8: Manually verify the new stage is a true no-op with no overrides present**

Run: `python scripts/07b_apply_metric_overrides.py`
Expected: prints `Applied 0 override(s) across 0 file(s)`, exit code 0, and `git diff data/processed/kp_district_population_2023.csv data/processed/dev_stats_health.csv` shows no changes (there is no `metric_overrides.csv` yet at this point, so this exercises the empty/no-op path against the real files).

- [ ] **Step 9: Commit**

```bash
git add scripts/07b_apply_metric_overrides.py scripts/run_downstream.py scripts/run_all.py tests/test_apply_metric_overrides.py
git commit -m "feat: add metric-overrides pipeline stage and downstream-only rebuild script

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Wire it in — `/admin/api/metric-overrides` route, admin panel UI, end-to-end tests, manual verification

**Files:**
- Modify: `server/routes/admin.py`
- Modify: `server/admin_ui.py`
- Modify: `tests/server/test_routes.py`
- Create: `tests/server/test_metric_overrides_route.py`

**Interfaces:**
- Consumes: `metric_overrides.add_from_document`/`MetricOverrideError` (Task 1), `run_downstream.py` (Task 2, invoked as a subprocess — not imported), `document_extraction.extract` (phase 4a), `ai_client.AIProviderError` (phase 3), `keystore.PROVIDERS`/`get_key` (phase 2).
- Produces: the final, verified phase-4d feature.

**Reminder:** per this plan's Global Constraints "Ordering note," the Find blocks below target `server/routes/admin.py` and `server/admin_ui.py` as they exist with only phase 4b's changes applied (no phase 4c changes). If phase 4c has been implemented first, STOP and re-derive the Find blocks against the actual current file content rather than forcing a stale match.

- [ ] **Step 1: Update imports in `server/routes/admin.py`**

Find:

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

Replace with:

```python
"""GET/POST /admin, /admin/setup, /admin/login, /admin/logout, the
/admin/api/keys* JSON API, /admin/api/extract for document upload,
/admin/api/supplemental-data for AI-extracted facility/district records,
and /admin/api/metric-overrides for AI-validated pipeline-data updates.
See docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md
sections 6 and 8, 2026-08-15-document-upload-phase4a-design.md section 4,
2026-08-15-supplemental-facility-data-phase4b-design.md section 5, and
2026-08-15-pipeline-data-overrides-phase4d-design.md section 6.
"""
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Body, Cookie, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from server import admin_ui, ai_client, auth, document_extraction, keystore, metric_overrides, providers, supplemental_data

REPORT_BUILD_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "14_build_html_report.py"
RUN_DOWNSTREAM_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "run_downstream.py"
```

- [ ] **Step 2: Append the new route to `server/routes/admin.py`**

Find (the end of the file):

```python
@router.post("/admin/api/extract")
async def extract_document(
    file: UploadFile = File(...),
    kp_admin_session: str | None = Cookie(default=None),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    content_bytes = await file.read()
    try:
        result = document_extraction.extract(file.filename or "upload", content_bytes)
    except document_extraction.UnsupportedFormatError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=415)
    except document_extraction.ExtractionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(result.to_dict())
```

Replace with:

```python
@router.post("/admin/api/extract")
async def extract_document(
    file: UploadFile = File(...),
    kp_admin_session: str | None = Cookie(default=None),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    content_bytes = await file.read()
    try:
        result = document_extraction.extract(file.filename or "upload", content_bytes)
    except document_extraction.UnsupportedFormatError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=415)
    except document_extraction.ExtractionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(result.to_dict())


@router.post("/admin/api/metric-overrides")
async def apply_metric_overrides(
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
        added = metric_overrides.add_from_document(
            provider, key, extracted.text, instruction, extracted.filename
        )
    except metric_overrides.MetricOverrideError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)

    try:
        result = subprocess.run(
            [sys.executable, str(RUN_DOWNSTREAM_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse(
            {"added": added, "rebuild_warning": "Downstream pipeline rebuild timed out after 600 seconds"}
        )
    if result.returncode != 0:
        return JSONResponse(
            {"added": added, "rebuild_warning": f"Downstream pipeline rebuild failed: {result.stderr[-500:]}"}
        )
    return JSONResponse({"added": added})
```

- [ ] **Step 3: Add the new section's CSS to `server/admin_ui.py`**

Find:

```python
#supplemental-status { display: none; }
#supplemental-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }
"""
```

Replace with:

```python
#supplemental-status { display: none; }
#supplemental-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }
#metric-file-input { display: block; margin-bottom: 0.75rem; }
#metric-instruction, #metric-provider {
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
#metric-status { display: none; }
#metric-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }
"""
```

- [ ] **Step 4: Add the new section's JS to `server/admin_ui.py`**

Find:

```python
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
                var facility = r.facility ? " / " + escapeHtml(r.facility) : "";
                return escapeHtml(r.district) + facility + " - " + escapeHtml(r.category) + ": " + escapeHtml(r.label);
              }).join("<br>");
              resultEl.innerHTML = "<p>Added " + added.length + " record(s):</p><p>" + summary + "</p>";
              if (result.data && result.data.rebuild_warning) {
                resultEl.innerHTML += "<p class='error'>" + escapeHtml(result.data.rebuild_warning) + "</p>";
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

Replace with:

```python
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
                var facility = r.facility ? " / " + escapeHtml(r.facility) : "";
                return escapeHtml(r.district) + facility + " - " + escapeHtml(r.category) + ": " + escapeHtml(r.label);
              }).join("<br>");
              resultEl.innerHTML = "<p>Added " + added.length + " record(s):</p><p>" + summary + "</p>";
              if (result.data && result.data.rebuild_warning) {
                resultEl.innerHTML += "<p class='error'>" + escapeHtml(result.data.rebuild_warning) + "</p>";
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

    var applyMetricUpdateBtn = byId("apply-metric-update-btn");
    if (applyMetricUpdateBtn) {
      applyMetricUpdateBtn.addEventListener("click", function () {
        var fileInput = byId("metric-file-input");
        var instructionInput = byId("metric-instruction");
        var providerSelect = byId("metric-provider");
        var statusEl = byId("metric-status");
        var resultEl = byId("metric-result");
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
        applyMetricUpdateBtn.disabled = true;
        applyMetricUpdateBtn.textContent = "Applying...";

        fetch("/admin/api/metric-overrides", { method: "POST", body: formData })
          .then(function (res) {
            return res.json().then(function (data) { return { ok: res.ok, data: data }; });
          })
          .then(function (result) {
            applyMetricUpdateBtn.disabled = false;
            applyMetricUpdateBtn.textContent = "Apply Update";
            if (result.ok) {
              var added = (result.data && result.data.added) || [];
              var summary = added.map(function (r) {
                return escapeHtml(r.district) + " / " + escapeHtml(r.column) + ": now " + escapeHtml(r.value);
              }).join("<br>");
              resultEl.innerHTML = "<p>Applied " + added.length + " update(s):</p><p>" + summary + "</p>";
              if (result.data && result.data.rebuild_warning) {
                resultEl.innerHTML += "<p class='error'>" + escapeHtml(result.data.rebuild_warning) + "</p>";
              }
            } else {
              statusEl.textContent = (result.data && result.data.detail) || "Request failed";
              statusEl.style.display = "block";
            }
          })
          .catch(function (err) {
            applyMetricUpdateBtn.disabled = false;
            applyMetricUpdateBtn.textContent = "Apply Update";
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

Replace with:

```python
  <button type="button" class="primary" id="add-to-report-btn">Add to Report</button>
  <p id="supplemental-status" class="error"></p>
  <div id="supplemental-result"></div>
</div>
<div class="upload-section">
  <h2>Update Pipeline Data</h2>
  <p class="hint">Upload a document (or a short instruction typed into a small text file) describing an update to a district's population or aggregate health-facility numbers - e.g. "Peshawar's population is now 5.1 million per the new provincial estimate." The AI proposes a validated change; implausible swings from the current value are rejected automatically. Applying a change recomputes the gap score, GIS layers, and report.</p>
  <input type="file" id="metric-file-input" accept=".xlsx,.xls,.pdf,.docx,.html,.htm,.txt,.csv">
  <label for="metric-instruction">Instruction (optional)</label>
  <textarea id="metric-instruction" rows="2" placeholder="e.g. Peshawar's population is now 5.1 million per the new provincial estimate"></textarea>
  <label for="metric-provider">AI provider</label>
  <select id="metric-provider">
{provider_options}
  </select>
  <button type="button" class="primary" id="apply-metric-update-btn">Apply Update</button>
  <p id="metric-status" class="error"></p>
  <div id="metric-result"></div>
</div>
<p style="margin-top:1.5rem"><button type="button" class="secondary" id="logout-btn">Log Out</button></p>
</div>
<script>{ADMIN_JS}</script>
</body>
</html>
"""
```

- [ ] **Step 6: Write `tests/server/test_metric_overrides_route.py`**

```python
"""End-to-end /admin/api/metric-overrides tests via FastAPI's TestClient.
document_extraction.extract, metric_overrides.add_from_document, and the
downstream-rebuild subprocess call are all mocked - no real file parsing,
AI provider call, or pipeline run. keyring is mocked too, same pattern as
tests/server/test_supplemental_data_route.py.
"""
import io
import subprocess

import pytest
from fastapi.testclient import TestClient

from server import document_extraction, keystore, metric_overrides
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


def _upload(client, filename="update.txt"):
    return client.post(
        "/admin/api/metric-overrides",
        files={"file": (filename, io.BytesIO(b"x"), "application/octet-stream")},
        data={"provider": "anthropic", "instruction": "test instruction"},
    )


def test_metric_overrides_requires_authentication(client):
    response = _upload(client)
    assert response.status_code == 401


def test_metric_overrides_success(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="update.txt", format="txt", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)
    fake_added = [{"district": "Peshawar", "file": "population", "column": "population_2023",
                   "value": 5000000.0, "reason": "estimate", "source": "update.txt",
                   "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(metric_overrides, "add_from_document", lambda *args, **kwargs: fake_added)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())

    response = _upload(client)
    assert response.status_code == 200
    assert response.json() == {"added": fake_added}


def test_metric_overrides_without_configured_key_returns_400(client):
    _login(client)
    response = _upload(client)
    assert response.status_code == 400


def test_metric_overrides_unsupported_format_returns_415(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")

    def failing_extract(filename, content_bytes):
        raise document_extraction.UnsupportedFormatError("Unsupported file type: .zip")

    monkeypatch.setattr(document_extraction, "extract", failing_extract)
    response = _upload(client, filename="update.zip")
    assert response.status_code == 415


def test_metric_overrides_validation_failure_returns_400(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="update.txt", format="txt", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)

    def failing_add(*args, **kwargs):
        raise metric_overrides.MetricOverrideError("AI response was not valid JSON")

    monkeypatch.setattr(metric_overrides, "add_from_document", failing_add)
    response = _upload(client)
    assert response.status_code == 400


def test_metric_overrides_provider_failure_returns_502(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="update.txt", format="txt", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)

    def failing_add(*args, **kwargs):
        raise metric_overrides.ai_client.AIProviderError("Anthropic API returned 500")

    monkeypatch.setattr(metric_overrides, "add_from_document", failing_add)
    response = _upload(client)
    assert response.status_code == 502


def test_metric_overrides_rebuild_failure_still_returns_added_records(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="update.txt", format="txt", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)
    fake_added = [{"district": "Peshawar", "file": "population", "column": "population_2023",
                   "value": 5000000.0, "reason": "", "source": "update.txt",
                   "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(metric_overrides, "add_from_document", lambda *args, **kwargs: fake_added)

    class FailedProcess:
        returncode = 1
        stderr = "Traceback: something broke"

    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FailedProcess())

    response = _upload(client)
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == fake_added
    assert "rebuild_warning" in body


def test_metric_overrides_rebuild_timeout_still_returns_added_records(client, monkeypatch):
    _login(client)
    keystore.set_key("anthropic", "sk-ant-real")
    fake_result = document_extraction.ExtractionResult(filename="update.txt", format="txt", text="some text")
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)
    fake_added = [{"district": "Peshawar", "file": "population", "column": "population_2023",
                   "value": 5000000.0, "reason": "", "source": "update.txt",
                   "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(metric_overrides, "add_from_document", lambda *args, **kwargs: fake_added)

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["python", "run_downstream.py"], timeout=600)

    monkeypatch.setattr(admin_route.subprocess, "run", raise_timeout)

    response = _upload(client)
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == fake_added
    assert "rebuild_warning" in body
    assert "timed out" in body["rebuild_warning"].lower()
```

Note: `test_metric_overrides_provider_failure_returns_502` reaches `ai_client.AIProviderError` via `metric_overrides.ai_client.AIProviderError` (the `ai_client` module as imported inside `metric_overrides.py`), same pattern phase 4c's route tests already established for `supplemental_data.ai_client.AIProviderError`.

- [ ] **Step 7: Add UI-presence and escaping-regression assertions to `tests/server/test_routes.py`**

Find (the end of the file):

```python
def test_admin_panel_js_escapes_ai_derived_supplemental_content(client):
    # Regression guard: the "Add to Report" success handler renders
    # AI-extracted record fields (facility/category/label) and the
    # rebuild_warning subprocess output via innerHTML. Those values are
    # untrusted (only district is whitelist-validated), so they must be
    # run through escapeHtml() before interpolation - see
    # docs/superpowers/sdd/2026-08-15-supplemental-facility-data-phase4b
    # task 6 review finding.
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    assert "function escapeHtml(str)" in panel.text
    for hook in (
        "escapeHtml(r.district)", "escapeHtml(r.facility)",
        "escapeHtml(r.category)", "escapeHtml(r.label)",
        "escapeHtml(result.data.rebuild_warning)",
    ):
        assert hook in panel.text, f"missing escaping call: {hook}"
```

Replace with:

```python
def test_admin_panel_js_escapes_ai_derived_supplemental_content(client):
    # Regression guard: the "Add to Report" success handler renders
    # AI-extracted record fields (facility/category/label) and the
    # rebuild_warning subprocess output via innerHTML. Those values are
    # untrusted (only district is whitelist-validated), so they must be
    # run through escapeHtml() before interpolation - see
    # docs/superpowers/sdd/2026-08-15-supplemental-facility-data-phase4b
    # task 6 review finding.
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    assert "function escapeHtml(str)" in panel.text
    for hook in (
        "escapeHtml(r.district)", "escapeHtml(r.facility)",
        "escapeHtml(r.category)", "escapeHtml(r.label)",
        "escapeHtml(result.data.rebuild_warning)",
    ):
        assert hook in panel.text, f"missing escaping call: {hook}"


def test_admin_panel_includes_metric_overrides_section(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    for hook in (
        'id="metric-file-input"', 'id="metric-instruction"', 'id="metric-provider"',
        'id="apply-metric-update-btn"', "/admin/api/metric-overrides",
    ):
        assert hook in panel.text, f"missing hook: {hook}"


def test_admin_panel_js_escapes_ai_derived_metric_override_content(client):
    # Same regression class as test_admin_panel_js_escapes_ai_derived_supplemental_content,
    # for the pipeline-data-update "Apply Update" handler, which renders
    # AI-derived district/column/value fields via innerHTML.
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    metric_js = panel.text.split('id="apply-metric-update-btn"')[1]
    for hook in ("escapeHtml(r.district)", "escapeHtml(r.column)", "escapeHtml(result.data.rebuild_warning)"):
        assert hook in metric_js, f"missing escaping call in metric-update handler: {hook}"
```

- [ ] **Step 8: Run the new and modified tests**

Run: `pytest tests/server/test_metric_overrides_route.py tests/server/test_routes.py -v`
Expected: 24 passed (8 in `test_metric_overrides_route.py`, 16 in `test_routes.py` — the 14 already there plus the 2 new ones)

- [ ] **Step 9: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass — the existing 176 plus this phase's 18 (`test_metric_overrides.py`) + 6 (`test_apply_metric_overrides.py`) + 8 (`test_metric_overrides_route.py`) + 2 (`test_routes.py`) = 210; exact count isn't load-bearing, "all pass" is.

- [ ] **Step 10: Manual browser verification**

Start the server: `python -m server`

In a browser at `http://127.0.0.1:8420/admin` (log in):
- The admin panel now shows an "Update Pipeline Data" section below "Extract Document" (and, if phase 4c has since been implemented, below "Database Ingestion" too), with a file input, an instruction textarea, a provider dropdown, and an "Apply Update" button.
- Prepare a small real test file (e.g. a `.txt` file: "Peshawar's population is now 5,100,000 per the new provincial estimate."). Choose it, pick a provider you have a real key configured for, click "Apply Update".
- Confirm the button shows "Applying..." briefly (this rebuild takes noticeably longer than phase 4b's report-only rebuild, since it now includes shapefile writing and QGIS project regeneration — this is expected), then a summary of the applied update(s) appears (district / column: now value).
- Go to `http://127.0.0.1:8420/` and confirm the district's population and its `gap_score`/`need_tier` reflect the update (check the District Data table).
- Confirm `data/processed/kp_district_population_2023.csv` on disk actually shows the new value for that district (not just the report — the underlying data file).
- If QGIS is installed and you have `report/gis/KP_Healthcare_Plan.qgz` (or wherever `13_build_qgis_project.py` writes it) open, confirm reopening the project reflects the updated shapefile data.
- Try the sanity-swing rejection: submit an obviously implausible update (e.g. "Peshawar's population is now 50 million") and confirm a clear "swing... exceeds the sanity threshold" error surfaces rather than a crash or a silently-accepted bad number.
- Confirm `POST /admin/api/metric-overrides` without a session returns 401 (e.g. via `curl -F "file=@somefile.txt" -F "provider=anthropic" http://127.0.0.1:8420/admin/api/metric-overrides`).

If any of these fail, this is a real bug to fix before committing — do not report success without having actually driven the browser through each step, including confirming the underlying CSV changed and the report/GIS layers reflect it. Clean up afterward: delete any test keys/records this created that shouldn't remain in the real data files — if a test override was applied, this is harder to cleanly "undo" than phase 4b's append-only supplemental records, since it genuinely patched `kp_district_population_2023.csv`/`dev_stats_health.csv` in place. Restore those two files to their pre-test state (e.g. `git checkout -- data/processed/kp_district_population_2023.csv data/processed/dev_stats_health.csv` if they were committed before this test, or by re-running the regenerating stages `python scripts/02_compile_population.py && python scripts/17_extract_devstats_health.py`), delete the test row(s) from `data/processed/metric_overrides.csv` (or delete the file entirely if it didn't exist before), and re-run `python scripts/run_downstream.py` so the committed report/shapefiles/QGIS project don't carry throwaway test content — matching the same cleanup discipline used in every earlier phase's manual verification, extended to cover the extra files this phase touches.

- [ ] **Step 11: Final commit**

```bash
git add server/routes/admin.py server/admin_ui.py tests/server/test_metric_overrides_route.py tests/server/test_routes.py
git commit -m "feat: wire AI-validated pipeline-data updates into the admin panel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
