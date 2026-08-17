# Manage Supplemental Records & Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin view and delete individual records in `supplemental_records.csv` and `metric_overrides.csv` from the admin panel, instead of the current pure-append-only, no-visibility state.

**Architecture:** Both stores gain a synthetic per-record `id` (stamped at write time, backfilled-and-persisted for legacy rows on first load) and a `delete_record(id)` function. Two new route pairs (`GET .../records`, `DELETE .../records/{id}`) expose this, each delete triggering the same rebuild subprocess call its store's existing add-path already makes. The admin panel gets two new table sections built on one shared JS helper, using a two-step inline confirm instead of a native `confirm()` dialog.

**Tech Stack:** Python 3.12, FastAPI, plain HTML/CSS/vanilla JS (no framework - matches every existing admin panel piece), pytest + FastAPI `TestClient`.

**Spec:** `docs/superpowers/specs/2026-08-16-manage-records-design.md`

## Global Constraints

- Both `supplemental_records.csv` and `metric_overrides.csv` get an `id` field, first in their `FIELDNAMES` tuple, generated as `uuid.uuid4().hex[:12]`.
- `id` is generated per record, not per batch - unlike `added_at`, two records from the same upload get different `id`s.
- Legacy rows with no `id` are backfilled on first `load_records()` call and the file is rewritten immediately so the id is stable on every subsequent load.
- View + delete only - no in-place field editing (out of scope per spec).
- Every delete triggers the same automatic rebuild its store's add path already triggers: `14_build_html_report.py` for supplemental records, `run_downstream.py` for overrides. Same `rebuild_warning`-in-a-200-response pattern on rebuild failure/timeout as the existing add routes.
- Every new route is auth-gated with the existing `_require_auth(kp_admin_session)` pattern, unchanged.
- Delete UI uses a two-step inline confirm (button text changes, second click confirms) - never a native `confirm()` dialog.
- No pagination (YAGNI - record counts are small).

---

### Task 1: `server/supplemental_data.py` - per-record id, backfill, delete

**Files:**
- Modify: `server/supplemental_data.py`
- Test: `tests/server/test_supplemental_data.py`

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `supplemental_data.delete_record(record_id, path=RECORDS_PATH) -> bool` (Task 3 consumes this). `FIELDNAMES` now starts with `"id"`. `load_records()` and `add_from_document()` return records that include an `"id"` key.

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_supplemental_data.py`:

```python
def test_delete_record_removes_only_matching_row(tmp_path):
    path = tmp_path / "supplemental_records.csv"
    supplemental_data.append_records(
        [
            {"id": "aaa111", "district": "Peshawar", "facility": "", "category": "equipment",
             "label": "X-ray", "detail": "", "source_document": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"},
            {"id": "bbb222", "district": "Chitral", "facility": "", "category": "outbreak",
             "label": "Cholera", "detail": "", "source_document": "b.pdf", "added_at": "2026-08-15T00:01:00+00:00"},
        ],
        path=path,
    )
    deleted = supplemental_data.delete_record("aaa111", path=path)
    assert deleted is True
    remaining = supplemental_data.load_records(path=path)
    assert len(remaining) == 1
    assert remaining[0]["id"] == "bbb222"


def test_delete_record_returns_false_for_unknown_id(tmp_path):
    path = tmp_path / "supplemental_records.csv"
    supplemental_data.append_records(
        [{"id": "aaa111", "district": "Peshawar", "facility": "", "category": "equipment",
          "label": "X-ray", "detail": "", "source_document": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}],
        path=path,
    )
    assert supplemental_data.delete_record("does-not-exist", path=path) is False
    assert len(supplemental_data.load_records(path=path)) == 1


def test_delete_record_on_missing_file_returns_false(tmp_path):
    path = tmp_path / "does_not_exist.csv"
    assert supplemental_data.delete_record("anything", path=path) is False


def test_load_records_backfills_missing_ids_and_persists(tmp_path):
    path = tmp_path / "supplemental_records.csv"
    path.write_text(
        "district,facility,category,label,detail,source_document,added_at\n"
        "Peshawar,DHQ Hospital,equipment,MRI Machine,1 unit,a.pdf,2026-08-15T00:00:00+00:00\n",
        encoding="utf-8",
    )
    first_load = supplemental_data.load_records(path=path)
    assert len(first_load) == 1
    assert first_load[0]["id"]
    second_load = supplemental_data.load_records(path=path)
    assert second_load[0]["id"] == first_load[0]["id"]


def test_add_from_document_stamps_distinct_id_per_record(tmp_path, monkeypatch):
    records_path = tmp_path / "supplemental_records.csv"
    monkeypatch.setattr(supplemental_data, "RECORDS_PATH", records_path)
    monkeypatch.setattr(supplemental_data, "load_known_districts", lambda path=None: KNOWN_DISTRICTS)

    raw_response = json.dumps([
        {"district": "Peshawar", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit"},
        {"district": "Chitral", "facility": "", "category": "outbreak",
         "label": "Cholera", "detail": "12 cases"},
    ])
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw_response)

    added = supplemental_data.add_from_document(
        "anthropic", "sk-ant-real", "some document text", "", "equipment.pdf",
    )
    assert len(added) == 2
    assert added[0]["id"] != added[1]["id"]
    assert added[0]["added_at"] == added[1]["added_at"]
```

Also update the existing `test_append_records_appends_without_duplicating_header` (its header-line check assumed `district` was the first column, which is no longer true now that `id` is first):

```python
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
        header_count = sum(1 for line in f if line.startswith("id,"))
    assert header_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_supplemental_data.py -v`
Expected: the new tests FAIL (`AttributeError: module 'server.supplemental_data' has no attribute 'delete_record'`), and `test_append_records_appends_without_duplicating_header` FAILS too (header still starts with `district,`, not `id,` - `FIELDNAMES` hasn't changed yet).

- [ ] **Step 3: Implement**

In `server/supplemental_data.py`, add `import uuid` (alphabetically after `re`, before the `from ...` imports):

```python
import csv
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
```

Change `FIELDNAMES`:

```python
FIELDNAMES = ("id", "district", "facility", "category", "label", "detail", "source_document", "added_at")
```

Replace `load_records` and add `_write_records` right after it (a full rewrite, used by both the backfill below and `delete_record`):

```python
def load_records(path=RECORDS_PATH):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        records = [{k: (v if v is not None else "") for k, v in row.items()} for row in csv.DictReader(f)]
    if any(not record.get("id") for record in records):
        for record in records:
            if not record.get("id"):
                record["id"] = uuid.uuid4().hex[:12]
        _write_records(records, path)
    return records


def _write_records(records, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in FIELDNAMES})
```

Add `delete_record` right after `append_records`:

```python
def delete_record(record_id, path=RECORDS_PATH):
    path = Path(path)
    records = load_records(path=path)
    remaining = [r for r in records if r.get("id") != record_id]
    if len(remaining) == len(records):
        return False
    _write_records(remaining, path)
    return True
```

Update `add_from_document` to stamp a distinct `id` per record:

```python
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

    append_records(records, path=RECORDS_PATH)
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_supplemental_data.py -v`
Expected: PASS (all tests, including every pre-existing one - confirm none regressed).

- [ ] **Step 5: Commit**

```bash
git add server/supplemental_data.py tests/server/test_supplemental_data.py
git commit -m "feat: add per-record id and delete_record to supplemental_data

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `server/metric_overrides.py` - same shape

**Files:**
- Modify: `server/metric_overrides.py`
- Test: `tests/server/test_metric_overrides.py`

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `metric_overrides.delete_record(record_id, path=OVERRIDES_PATH) -> bool` (Task 4 consumes this). `FIELDNAMES` now starts with `"id"`. `load_records()` and `add_from_document()` return records that include an `"id"` key.

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_metric_overrides.py`:

```python
def test_delete_record_removes_only_matching_row(tmp_path):
    path = tmp_path / "metric_overrides.csv"
    metric_overrides.append_records(
        [
            {"id": "aaa111", "district": "Peshawar", "file": "population", "column": "population_2023",
             "value": 5000000, "reason": "estimate", "source": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"},
            {"id": "bbb222", "district": "Chitral", "file": "health", "column": "govt_beds",
             "value": 10, "reason": "", "source": "b.pdf", "added_at": "2026-08-15T00:01:00+00:00"},
        ],
        path=path,
    )
    deleted = metric_overrides.delete_record("aaa111", path=path)
    assert deleted is True
    remaining = metric_overrides.load_records(path=path)
    assert len(remaining) == 1
    assert remaining[0]["id"] == "bbb222"


def test_delete_record_returns_false_for_unknown_id(tmp_path):
    path = tmp_path / "metric_overrides.csv"
    metric_overrides.append_records(
        [{"id": "aaa111", "district": "Peshawar", "file": "population", "column": "population_2023",
          "value": 5000000, "reason": "", "source": "a.pdf", "added_at": "2026-08-15T00:00:00+00:00"}],
        path=path,
    )
    assert metric_overrides.delete_record("does-not-exist", path=path) is False
    assert len(metric_overrides.load_records(path=path)) == 1


def test_delete_record_on_missing_file_returns_false(tmp_path):
    path = tmp_path / "does_not_exist.csv"
    assert metric_overrides.delete_record("anything", path=path) is False


def test_load_records_backfills_missing_ids_and_persists(tmp_path):
    path = tmp_path / "metric_overrides.csv"
    path.write_text(
        "district,file,column,value,reason,source,added_at\n"
        "Peshawar,population,population_2023,5000000,estimate,a.pdf,2026-08-15T00:00:00+00:00\n",
        encoding="utf-8",
    )
    first_load = metric_overrides.load_records(path=path)
    assert len(first_load) == 1
    assert first_load[0]["id"]
    second_load = metric_overrides.load_records(path=path)
    assert second_load[0]["id"] == first_load[0]["id"]


def test_add_from_document_stamps_distinct_id_per_record(fake_fields, tmp_path, monkeypatch):
    overrides_path = tmp_path / "metric_overrides.csv"
    monkeypatch.setattr(metric_overrides, "OVERRIDES_PATH", overrides_path)
    monkeypatch.setattr(metric_overrides, "load_known_districts", lambda: KNOWN_DISTRICTS)

    raw_response = json.dumps([
        {"district": "Peshawar", "file": "population", "column": "population_2023",
         "value": 5000000, "reason": "estimate"},
        {"district": "Chitral", "file": "health", "column": "govt_beds",
         "value": 10, "reason": "new count"},
    ])
    monkeypatch.setattr(ai_client, "ask", lambda provider, key, question, context: raw_response)

    added = metric_overrides.add_from_document(
        "anthropic", "sk-ant-real", "some document text", "", "census.pdf",
    )
    assert len(added) == 2
    assert added[0]["id"] != added[1]["id"]
    assert added[0]["added_at"] == added[1]["added_at"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_metric_overrides.py -v`
Expected: the new tests FAIL (`AttributeError: module 'server.metric_overrides' has no attribute 'delete_record'`).

- [ ] **Step 3: Implement**

In `server/metric_overrides.py`, add `import uuid` (alphabetically after `re`):

```python
import csv
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
```

Change `FIELDNAMES`:

```python
FIELDNAMES = ("id", "district", "file", "column", "value", "reason", "source", "added_at")
```

Replace `load_records` and add `_write_records` right after it:

```python
def load_records(path=OVERRIDES_PATH):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        records = [{k: (v if v is not None else "") for k, v in row.items()} for row in csv.DictReader(f)]
    if any(not record.get("id") for record in records):
        for record in records:
            if not record.get("id"):
                record["id"] = uuid.uuid4().hex[:12]
        _write_records(records, path)
    return records


def _write_records(records, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in FIELDNAMES})
```

Add `delete_record` right after `append_records`:

```python
def delete_record(record_id, path=OVERRIDES_PATH):
    path = Path(path)
    records = load_records(path=path)
    remaining = [r for r in records if r.get("id") != record_id]
    if len(remaining) == len(records):
        return False
    _write_records(remaining, path)
    return True
```

Update `add_from_document` to stamp a distinct `id` per record:

```python
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

    append_records(records, path=OVERRIDES_PATH)
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_metric_overrides.py -v`
Expected: PASS (all tests, including every pre-existing one).

- [ ] **Step 5: Commit**

```bash
git add server/metric_overrides.py tests/server/test_metric_overrides.py
git commit -m "feat: add per-record id and delete_record to metric_overrides

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Supplemental records list/delete routes

**Files:**
- Modify: `server/routes/admin.py`
- Test: `tests/server/test_supplemental_data_route.py`

**Interfaces:**
- Consumes: `supplemental_data.delete_record(record_id, path=RECORDS_PATH) -> bool` and `supplemental_data.load_records()` (Task 1).
- Produces: `GET /admin/api/supplemental-data/records` and `DELETE /admin/api/supplemental-data/records/{record_id}` (Task 5's UI consumes these).

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_supplemental_data_route.py`:

```python
def test_list_supplemental_records_requires_authentication(client):
    response = client.get("/admin/api/supplemental-data/records")
    assert response.status_code == 401


def test_list_supplemental_records_returns_records(client, monkeypatch):
    _login(client)
    fake_records = [{"id": "aaa111", "district": "Peshawar", "facility": "", "category": "equipment",
                      "label": "X-ray", "detail": "", "source_document": "a.pdf",
                      "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(supplemental_data, "load_records", lambda: fake_records)
    response = client.get("/admin/api/supplemental-data/records")
    assert response.status_code == 200
    assert response.json() == {"records": fake_records}


def test_delete_supplemental_record_requires_authentication(client):
    response = client.delete("/admin/api/supplemental-data/records/aaa111")
    assert response.status_code == 401


def test_delete_supplemental_record_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(supplemental_data, "delete_record", lambda record_id, path=None: True)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.delete("/admin/api/supplemental-data/records/aaa111")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_delete_supplemental_record_unknown_id_returns_404(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(supplemental_data, "delete_record", lambda record_id, path=None: False)
    response = client.delete("/admin/api/supplemental-data/records/does-not-exist")
    assert response.status_code == 404


def test_delete_supplemental_record_rebuild_failure_still_returns_deleted(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(supplemental_data, "delete_record", lambda record_id, path=None: True)

    class FailedProcess:
        returncode = 1
        stderr = "Traceback: something broke"

    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FailedProcess())
    response = client.delete("/admin/api/supplemental-data/records/aaa111")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert "rebuild_warning" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_supplemental_data_route.py -v`
Expected: the new tests FAIL with 404 (route doesn't exist yet - FastAPI returns 404 for an unmatched route, not the 401/200/200/404/200 the tests expect).

- [ ] **Step 3: Implement**

In `server/routes/admin.py`, add these two routes right after `add_supplemental_data` (before `extract_document`):

```python
@router.get("/admin/api/supplemental-data/records")
def list_supplemental_records(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    return JSONResponse({"records": supplemental_data.load_records()})


@router.delete("/admin/api/supplemental-data/records/{record_id}")
def delete_supplemental_record(record_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    found = supplemental_data.delete_record(record_id)
    if not found:
        return JSONResponse({"detail": "No record with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"deleted": True, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"deleted": True, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"deleted": True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_supplemental_data_route.py -v`
Expected: PASS (all tests, including every pre-existing one).

- [ ] **Step 5: Commit**

```bash
git add server/routes/admin.py tests/server/test_supplemental_data_route.py
git commit -m "feat: add list/delete routes for supplemental records

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Metric override records list/delete routes

**Files:**
- Modify: `server/routes/admin.py`
- Test: `tests/server/test_metric_overrides_route.py`

**Interfaces:**
- Consumes: `metric_overrides.delete_record(record_id, path=OVERRIDES_PATH) -> bool` and `metric_overrides.load_records()` (Task 2).
- Produces: `GET /admin/api/metric-overrides/records` and `DELETE /admin/api/metric-overrides/records/{record_id}` (Task 6's UI consumes these).

- [ ] **Step 1: Write the failing tests**

Add to `tests/server/test_metric_overrides_route.py`:

```python
def test_list_metric_override_records_requires_authentication(client):
    response = client.get("/admin/api/metric-overrides/records")
    assert response.status_code == 401


def test_list_metric_override_records_returns_records(client, monkeypatch):
    _login(client)
    fake_records = [{"id": "aaa111", "district": "Peshawar", "file": "population", "column": "population_2023",
                      "value": 5000000, "reason": "estimate", "source": "a.pdf",
                      "added_at": "2026-08-15T00:00:00+00:00"}]
    monkeypatch.setattr(metric_overrides, "load_records", lambda: fake_records)
    response = client.get("/admin/api/metric-overrides/records")
    assert response.status_code == 200
    assert response.json() == {"records": fake_records}


def test_delete_metric_override_record_requires_authentication(client):
    response = client.delete("/admin/api/metric-overrides/records/aaa111")
    assert response.status_code == 401


def test_delete_metric_override_record_success(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(metric_overrides, "delete_record", lambda record_id, path=None: True)
    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    response = client.delete("/admin/api/metric-overrides/records/aaa111")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_delete_metric_override_record_unknown_id_returns_404(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(metric_overrides, "delete_record", lambda record_id, path=None: False)
    response = client.delete("/admin/api/metric-overrides/records/does-not-exist")
    assert response.status_code == 404


def test_delete_metric_override_record_rebuild_failure_still_returns_deleted(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(metric_overrides, "delete_record", lambda record_id, path=None: True)

    class FailedProcess:
        returncode = 1
        stderr = "Traceback: something broke"

    monkeypatch.setattr(admin_route.subprocess, "run", lambda *args, **kwargs: FailedProcess())
    response = client.delete("/admin/api/metric-overrides/records/aaa111")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert "rebuild_warning" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_metric_overrides_route.py -v`
Expected: the new tests FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Implement**

In `server/routes/admin.py`, add these two routes right after `apply_metric_overrides` (before `save_db_connection`):

```python
@router.get("/admin/api/metric-overrides/records")
def list_metric_override_records(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    return JSONResponse({"records": metric_overrides.load_records()})


@router.delete("/admin/api/metric-overrides/records/{record_id}")
def delete_metric_override_record(record_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    found = metric_overrides.delete_record(record_id)
    if not found:
        return JSONResponse({"detail": "No record with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(RUN_DOWNSTREAM_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse(
            {"deleted": True, "rebuild_warning": "Downstream pipeline rebuild timed out after 600 seconds"}
        )
    if result.returncode != 0:
        return JSONResponse(
            {"deleted": True, "rebuild_warning": f"Downstream pipeline rebuild failed: {result.stderr[-500:]}"}
        )
    return JSONResponse({"deleted": True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_metric_overrides_route.py -v`
Expected: PASS (all tests, including every pre-existing one).

- [ ] **Step 5: Commit**

```bash
git add server/routes/admin.py tests/server/test_metric_overrides_route.py
git commit -m "feat: add list/delete routes for metric override records

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Admin UI - Supplemental Records table

**Files:**
- Modify: `server/admin_ui.py`

**Interfaces:**
- Consumes: `GET /admin/api/supplemental-data/records`, `DELETE /admin/api/supplemental-data/records/{id}` (Task 3).
- Produces: the shared JS helper `initRecordsTable(options)` and its supporting pieces (`renderRow`, the global confirm-reset click listener), and the `.records-table`/`.records-table-wrap`/`.records-empty` CSS classes - Task 6 reuses all of these for the overrides table.

No dedicated automated test: `admin_ui.py` is a pure HTML/CSS/JS rendering module with no existing unit tests in this codebase (verified: nothing under `tests/` imports or tests it) - it is verified manually, like every other admin-panel rendering change in this project. Task 7 covers this task's manual verification live.

- [ ] **Step 1: Add the table CSS**

In `server/admin_ui.py`, append to the end of `ADMIN_CSS` (right after the existing `#db-ingest-result { margin-top: 0.75rem; font-size: 0.85rem; color: var(--ink-soft); }` line):

```css
.records-table-wrap { overflow-x: auto; margin-top: 0.75rem; }
.records-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.records-table th, .records-table td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--line); white-space: nowrap; }
.records-table th { color: var(--ink-soft); font-weight: 600; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.02em; }
.records-empty { color: var(--muted); text-align: center; padding: 0.75rem; white-space: normal; }
#supplemental-records-status, #override-records-status { display: none; margin-top: 0.5rem; }
```

- [ ] **Step 2: Add the shared JS helpers**

In `server/admin_ui.py`'s `ADMIN_JS`, insert right after the `apiCall` function definition (before `document.addEventListener("DOMContentLoaded", function () {`):

```javascript
function renderRecordRow(record, options, statusEl) {
  var tr = document.createElement("tr");
  options.columns.forEach(function (col) {
    var td = document.createElement("td");
    td.textContent = record[col] == null ? "" : String(record[col]);
    tr.appendChild(td);
  });
  var actionTd = document.createElement("td");
  var deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "danger delete-record-btn";
  deleteBtn.textContent = "Delete";
  deleteBtn.addEventListener("click", function () {
    if (deleteBtn.getAttribute("data-confirming") !== "true") {
      deleteBtn.setAttribute("data-confirming", "true");
      deleteBtn.textContent = "Confirm delete?";
      return;
    }
    deleteBtn.disabled = true;
    deleteBtn.textContent = "Deleting...";
    fetch(options.deleteUrlPrefix + encodeURIComponent(record.id), { method: "DELETE" })
      .then(function (res) {
        return res.json().then(function (data) { return { status: res.status, data: data }; });
      })
      .then(function (result) {
        if (result.status === 200 || result.status === 404) {
          if (tr.parentNode) tr.parentNode.removeChild(tr);
          if (result.data && result.data.rebuild_warning) {
            statusEl.textContent = result.data.rebuild_warning;
            statusEl.style.display = "block";
          }
        } else {
          deleteBtn.removeAttribute("data-confirming");
          deleteBtn.disabled = false;
          deleteBtn.textContent = "Delete";
          statusEl.textContent = (result.data && result.data.detail) || "Delete failed";
          statusEl.style.display = "block";
        }
      })
      .catch(function (err) {
        deleteBtn.removeAttribute("data-confirming");
        deleteBtn.disabled = false;
        deleteBtn.textContent = "Delete";
        statusEl.textContent = "Request failed: " + err;
        statusEl.style.display = "block";
      });
  });
  actionTd.appendChild(deleteBtn);
  tr.appendChild(actionTd);
  return tr;
}

function initRecordsTable(options) {
  var tbody = byId(options.tbodyId);
  var statusEl = byId(options.statusId);
  if (!tbody) return;
  apiCall("GET", options.listUrl).then(function (result) {
    tbody.innerHTML = "";
    var records = (result.data && result.data.records) || [];
    if (!records.length) {
      var emptyTr = document.createElement("tr");
      var emptyTd = document.createElement("td");
      emptyTd.colSpan = options.columns.length + 1;
      emptyTd.className = "records-empty";
      emptyTd.textContent = "No records yet.";
      emptyTr.appendChild(emptyTd);
      tbody.appendChild(emptyTr);
      return;
    }
    records.forEach(function (record) {
      tbody.appendChild(renderRecordRow(record, options, statusEl));
    });
  });
}

document.addEventListener("click", function (evt) {
  document.querySelectorAll('.delete-record-btn[data-confirming="true"]').forEach(function (btn) {
    if (btn !== evt.target) {
      btn.removeAttribute("data-confirming");
      btn.textContent = "Delete";
    }
  });
});
```

- [ ] **Step 3: Wire up the supplemental records table init call**

Still inside `ADMIN_JS`, inside the existing `document.addEventListener("DOMContentLoaded", function () { ... });` block, add at the end (right before the block's closing `});`):

```javascript
    initRecordsTable({
      listUrl: "/admin/api/supplemental-data/records",
      deleteUrlPrefix: "/admin/api/supplemental-data/records/",
      tbodyId: "supplemental-records-tbody",
      statusId: "supplemental-records-status",
      columns: ["district", "facility", "category", "label", "detail", "source_document", "added_at"],
    });
```

- [ ] **Step 4: Add the table HTML section**

In `render_admin_panel`, insert a new `upload-section` directly after the "Extract Document" section's closing `</div>` and before the "Update Pipeline Data" section's opening `<div class="upload-section">`:

```html
<div class="upload-section">
  <h2>Supplemental Records</h2>
  <p class="hint">Every fact currently in the report from document upload or database ingestion. Delete a record to remove it and rebuild the report automatically.</p>
  <div class="records-table-wrap">
    <table class="records-table">
      <thead>
        <tr><th>District</th><th>Facility</th><th>Category</th><th>Label</th><th>Detail</th><th>Source</th><th>Added</th><th></th></tr>
      </thead>
      <tbody id="supplemental-records-tbody">
        <tr><td colspan="8" class="records-empty">Loading...</td></tr>
      </tbody>
    </table>
  </div>
  <p id="supplemental-records-status" class="error"></p>
</div>
```

- [ ] **Step 5: Manually smoke-test the page renders without a JS error**

Run: `python -c "from server.admin_ui import render_admin_panel; html = render_admin_panel([]); assert 'supplemental-records-tbody' in html; assert 'initRecordsTable' in html; print('renders OK')"`
Expected: `renders OK`

- [ ] **Step 6: Commit**

```bash
git add server/admin_ui.py
git commit -m "feat: add Supplemental Records table to the admin panel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Admin UI - Pipeline Overrides table

**Files:**
- Modify: `server/admin_ui.py`

**Interfaces:**
- Consumes: `GET /admin/api/metric-overrides/records`, `DELETE /admin/api/metric-overrides/records/{id}` (Task 4); `initRecordsTable(options)` and the `.records-table`/`.records-table-wrap`/`.records-empty` CSS classes (Task 5).
- Produces: nothing new for later tasks.

No dedicated automated test, same reasoning as Task 5 - verified manually in Task 7.

- [ ] **Step 1: Wire up the overrides table init call**

In `server/admin_ui.py`'s `ADMIN_JS`, inside the `document.addEventListener("DOMContentLoaded", function () { ... });` block, add right after Task 5's `initRecordsTable({...})` call for supplemental records:

```javascript
    initRecordsTable({
      listUrl: "/admin/api/metric-overrides/records",
      deleteUrlPrefix: "/admin/api/metric-overrides/records/",
      tbodyId: "override-records-tbody",
      statusId: "override-records-status",
      columns: ["district", "file", "column", "value", "reason", "added_at"],
    });
```

- [ ] **Step 2: Add the table HTML section**

In `render_admin_panel`, insert a new `upload-section` directly after the "Update Pipeline Data" section's closing `</div>` and before the "Database Ingestion" section's opening `<div class="upload-section">`:

```html
<div class="upload-section">
  <h2>Pipeline Overrides</h2>
  <p class="hint">Every population/health-number override currently applied to the pipeline. Delete one to remove its effect and rerun the pipeline automatically.</p>
  <div class="records-table-wrap">
    <table class="records-table">
      <thead>
        <tr><th>District</th><th>File</th><th>Column</th><th>Value</th><th>Reason</th><th>Added</th><th></th></tr>
      </thead>
      <tbody id="override-records-tbody">
        <tr><td colspan="7" class="records-empty">Loading...</td></tr>
      </tbody>
    </table>
  </div>
  <p id="override-records-status" class="error"></p>
</div>
```

- [ ] **Step 3: Manually smoke-test the page renders without a JS error**

Run: `python -c "from server.admin_ui import render_admin_panel; html = render_admin_panel([]); assert 'override-records-tbody' in html; assert html.count('initRecordsTable(') == 3; print('renders OK')"`
Expected: `renders OK` (one `initRecordsTable(` occurrence is the function definition itself, plus one call each for supplemental records and overrides = 3 total).

- [ ] **Step 4: Commit**

```bash
git add server/admin_ui.py
git commit -m "feat: add Pipeline Overrides table to the admin panel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Full test suite and live manual verification

**Files:** none (verification only).

This is an admin-panel-touching feature, so per this project's established cadence it needs manual browser verification against the real running server (not just mocks).

- [ ] **Step 1: Run the full automated test suite**

Run: `pytest tests/ -q`
Expected: all tests pass (318 pre-existing + the new tests from Tasks 1-4).

- [ ] **Step 2: Start the server and log in**

Run: `python -m server`, then log in at `http://127.0.0.1:8420/admin` with whatever admin password is currently set.

- [ ] **Step 3: Confirm the empty state renders correctly**

With `data/processed/supplemental_records.csv` and `data/processed/metric_overrides.csv` in whatever state they're currently in (likely empty/absent after prior sessions' cleanup), confirm both new tables show "No records yet." and no browser console error appears.

- [ ] **Step 4: Add a real supplemental record and confirm it appears**

Using whichever AI provider key is currently configured, upload a small test document via "Extract Document" / "Add to Report" (e.g. a short text file: `Test District General Hospital, Peshawar: MRI Machine, 1 unit, operational`). Confirm the new record appears in the "Supplemental Records" table with the correct fields, without a page reload.

- [ ] **Step 5: Delete it via the two-step confirm and confirm the report updates**

Click that row's Delete button - confirm the button text changes to "Confirm delete?" without anything being deleted yet. Click elsewhere on the page and confirm the button resets back to "Delete". Click Delete again and then click "Confirm delete?" - confirm the row disappears from the table immediately, and (via `python scripts/14_build_html_report.py` or checking `report/KP_Healthcare_Plan.html` if the delete route's rebuild already ran it) confirm the deleted fact no longer appears in the report.

- [ ] **Step 6: Repeat Steps 4-5 for a pipeline override**

Upload a small test document to "Update Pipeline Data" (e.g. a short text file describing a modest, plausible population change for one district). Confirm it appears in the "Pipeline Overrides" table. Delete it via the same two-step confirm and confirm `data/processed/district_metrics.csv` reflects the removal (rerun `python scripts/run_downstream.py` if needed to check, matching the delete route's own rebuild step).

- [ ] **Step 7: Confirm 404-on-unknown-id is handled gracefully**

With the browser's dev tools open (or via `curl`/a second terminal), send a second DELETE for a record id that's already been deleted (or any nonexistent id) directly to `/admin/api/supplemental-data/records/{id}` while logged in. Confirm the server returns 404 without crashing.

- [ ] **Step 8: Clean up the test data**

Following this project's established manual-verification cleanup discipline: remove any leftover test record(s) from `data/processed/supplemental_records.csv` / `data/processed/metric_overrides.csv` (should already be empty if Steps 5-6 deleted them correctly - confirm with a quick read of both files), rebuild the report and rerun the downstream pipeline so committed output isn't stale, and confirm via `git status`/`git diff` that nothing changed relative to what's already committed. If the AI provider key used was added solely for this test, remove it via the admin panel's "Delete" button for that provider.

- [ ] **Step 9: Stop the server**

Stop the `python -m server` process (PowerShell `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` + `Stop-Process -Force`, matching this project's established Windows-specific cleanup).

- [ ] **Step 10: Report findings**

If everything above checks out clean, this task (and the whole plan) is done - no further commit needed beyond what Tasks 1-6 already made (Step 8's cleanup should leave the tree clean). If anything looks wrong (a row doesn't render, delete doesn't actually remove the fact from the report/pipeline, the two-step confirm doesn't reset correctly), that's a real bug to fix with its own test before considering this complete.
