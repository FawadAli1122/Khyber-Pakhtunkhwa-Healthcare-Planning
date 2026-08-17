# Document Upload + Extraction (Phase 4a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the admin panel accept an uploaded Excel/PDF/Word/HTML file and show its extracted text content, with no AI involvement and no change to any plan data yet — the plumbing phase 4b (AI-driven autonomous data update) will consume.

**Architecture:** One new pure module, `server/document_extraction.py`, exposing `extract(filename, content_bytes) -> ExtractionResult`, dispatching by file extension to a per-format parser (`openpyxl` for Excel, `fitz`+`pdfplumber` for PDF, `python-docx` for Word, `beautifulsoup4` for HTML). It raises typed exceptions (`UnsupportedFormatError`, `ExtractionError`) carrying messages safe to show the user directly, the same pattern `ai_client.AIProviderError` established in phase 3. This module is then wired into the existing admin panel: a new auth-gated `POST /admin/api/extract` route in `server/routes/admin.py`, and a new upload-and-preview section added to `server/admin_ui.py`'s existing panel page.

**Tech Stack:** Python 3.12, `openpyxl`, `python-docx` (imported as `docx`), `beautifulsoup4` (imported as `bs4`), `pdfplumber`, `fitz` (PyMuPDF) — all already project dependencies, no new installs. FastAPI's `UploadFile`/`File` for the multipart route (uses `python-multipart`, already present — `Form(...)` in this same file already depends on it).

**Spec:** `docs/superpowers/specs/2026-08-15-document-upload-phase4a-design.md`

## Global Constraints

- No new dependencies — `openpyxl`, `docx`, `bs4`, `pdfplumber`, `fitz` are already installed and already used elsewhere in this project (`scripts/lib/pdf_tables.py` uses `fitz`+`pdfplumber`).
- Every parser raises `document_extraction.UnsupportedFormatError` (unrecognized extension) or `document_extraction.ExtractionError` (parse failure, oversized file, or empty extracted content) — never an uncaught exception — carrying a message safe to show the user directly, same posture as `ai_client.AIProviderError`.
- `MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024` (20MB) is checked and rejected *before* any parser runs — a resource-use guard, not a security boundary (single-user local tool).
- No test depends on a checked-in fixture binary — every fixture file is built in-memory inline in the test itself.
- Phase 4a makes no data or report changes: no CSV writes, no pipeline stage triggers, no dashboard changes. It only extracts and displays text.
- Upload lives only behind the existing admin-panel auth gate (`_require_auth` in `server/routes/admin.py`), never on the public dashboard route.

---

### Task 1: `server/document_extraction.py` — per-format extraction module

**Files:**
- Create: `server/document_extraction.py`
- Test: `tests/server/test_document_extraction.py`

**Interfaces:**
- Produces: `document_extraction.UnsupportedFormatError(Exception)`; `document_extraction.ExtractionError(Exception)`; `document_extraction.ExtractionResult` (attributes `filename`, `format`, `text`; method `to_dict() -> dict` returning `{"filename": ..., "format": ..., "text": ...}`); `document_extraction.MAX_FILE_SIZE_BYTES` (int, `20 * 1024 * 1024`); `document_extraction.extract(filename: str, content_bytes: bytes) -> ExtractionResult` — raises `UnsupportedFormatError` for an unrecognized extension, `ExtractionError` for an oversized file, a parse failure, or content that extracts to nothing.
- Consumes: nothing from other tasks — this is a pure module with no FastAPI or `server.*` dependency.

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_document_extraction.py`:

```python
"""Unit tests for server/document_extraction.py. Every fixture is built
in-memory - no checked-in binary files. See docs/superpowers/specs/
2026-08-15-document-upload-phase4a-design.md section 5.
"""
import io

import fitz
import openpyxl
import pytest
from docx import Document

from server import document_extraction


def _build_xlsx_bytes():
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Districts"
    sheet.append(["district", "population"])
    sheet.append(["Peshawar", 4750388])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _build_docx_bytes():
    document = Document()
    document.add_paragraph("Peshawar has a population of 4,750,388.")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "district"
    table.rows[0].cells[1].text = "population"
    table.rows[1].cells[0].text = "Peshawar"
    table.rows[1].cells[1].text = "4750388"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _build_pdf_bytes():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Peshawar has a population of 4,750,388.")
    data = doc.tobytes()
    doc.close()
    return data


def _build_html_bytes():
    html = (
        "<html><body><p>Peshawar has a population of 4,750,388.</p>"
        "<table><tr><th>district</th><th>population</th></tr>"
        "<tr><td>Peshawar</td><td>4750388</td></tr></table>"
        "</body></html>"
    )
    return html.encode("utf-8")


def test_extract_xlsx_covers_every_sheet():
    result = document_extraction.extract("stats.xlsx", _build_xlsx_bytes())
    assert result.format == "xlsx"
    assert "Sheet: Districts" in result.text
    assert "Peshawar" in result.text
    assert "4750388" in result.text


def test_extract_docx_includes_paragraphs_and_tables():
    result = document_extraction.extract("notes.docx", _build_docx_bytes())
    assert result.format == "docx"
    assert "Peshawar has a population of 4,750,388." in result.text
    assert "Peshawar | 4750388" in result.text


def test_extract_pdf_includes_page_text():
    result = document_extraction.extract("report.pdf", _build_pdf_bytes())
    assert result.format == "pdf"
    assert "Peshawar has a population of 4,750,388." in result.text


def test_extract_html_includes_prose_and_table():
    result = document_extraction.extract("page.html", _build_html_bytes())
    assert result.format == "html"
    assert "Peshawar has a population of 4,750,388." in result.text
    assert "Peshawar | 4750388" in result.text


def test_extract_unsupported_extension_raises():
    with pytest.raises(document_extraction.UnsupportedFormatError):
        document_extraction.extract("data.csv", b"district,population\nPeshawar,4750388\n")


def test_extract_corrupt_xlsx_raises_extraction_error():
    with pytest.raises(document_extraction.ExtractionError):
        document_extraction.extract("broken.xlsx", b"not a real xlsx file")


def test_extract_oversized_file_rejected_before_parsing():
    oversized = b"0" * (document_extraction.MAX_FILE_SIZE_BYTES + 1)
    with pytest.raises(document_extraction.ExtractionError):
        document_extraction.extract("huge.xlsx", oversized)


def test_extract_empty_document_raises_extraction_error():
    empty_html = b"<html><body></body></html>"
    with pytest.raises(document_extraction.ExtractionError):
        document_extraction.extract("empty.html", empty_html)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_document_extraction.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.document_extraction'`

- [ ] **Step 3: Implement `server/document_extraction.py`**

```python
"""Extracts a normalized text representation from an uploaded document -
Excel, PDF, Word, or HTML. No AI, no data mutation - this is the plumbing
phase 4b will consume. See docs/superpowers/specs/
2026-08-15-document-upload-phase4a-design.md section 3.
"""
import io
from pathlib import Path

import fitz
import openpyxl
import pdfplumber
from bs4 import BeautifulSoup
from docx import Document

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20MB


class UnsupportedFormatError(Exception):
    """Raised for a file extension with no parser - message safe to show
    the user directly."""


class ExtractionError(Exception):
    """Raised when a recognized format fails to parse, or parses to no
    content - message safe to show the user directly, never a raw
    traceback."""


def _extract_xlsx(content_bytes):
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content_bytes), data_only=True)
    except Exception as exc:
        raise ExtractionError(f"Could not read Excel file: {exc}") from exc

    blocks = []
    for sheet in workbook.worksheets:
        rows = []
        for row in sheet.iter_rows(values_only=True):
            if all(cell is None for cell in row):
                continue
            rows.append(" | ".join("" if cell is None else str(cell) for cell in row))
        if rows:
            blocks.append(f"Sheet: {sheet.title}\n" + "\n".join(rows))
    return "\n\n".join(blocks)


def _extract_docx(content_bytes):
    try:
        document = Document(io.BytesIO(content_bytes))
    except Exception as exc:
        raise ExtractionError(f"Could not read Word document: {exc}") from exc

    parts = []
    for paragraph in document.paragraphs:
        if paragraph.text.strip():
            parts.append(paragraph.text.strip())
    for table_index, table in enumerate(document.tables, start=1):
        rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
        if rows:
            parts.append(f"Table {table_index}\n" + "\n".join(rows))
    return "\n\n".join(parts)


def _extract_html(content_bytes):
    try:
        soup = BeautifulSoup(content_bytes, "html.parser")
    except Exception as exc:
        raise ExtractionError(f"Could not read HTML file: {exc}") from exc

    for tag in soup(["script", "style"]):
        tag.decompose()

    # Pull tables out of the tree before extracting prose text, so a
    # table's cell contents aren't duplicated between the prose block and
    # the table's own structured block below.
    tables = soup.find_all("table")
    for table in tables:
        table.extract()

    text = soup.get_text(separator="\n", strip=True)
    parts = [text] if text else []

    for table_index, table in enumerate(tables, start=1):
        rows = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            row_text = " | ".join(cell.get_text(strip=True) for cell in cells)
            if row_text:
                rows.append(row_text)
        if rows:
            parts.append(f"Table {table_index}\n" + "\n".join(rows))
    return "\n\n".join(parts)


def _extract_pdf(content_bytes):
    parts = []
    try:
        with fitz.open(stream=content_bytes, filetype="pdf") as doc:
            for page_index in range(len(doc)):
                page_text = doc[page_index].get_text().strip()
                if page_text:
                    parts.append(f"Page {page_index + 1} text\n{page_text}")
    except Exception as exc:
        raise ExtractionError(f"Could not read PDF file: {exc}") from exc

    try:
        with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                for table_index, table in enumerate(page.extract_tables() or [], start=1):
                    rows = [" | ".join((cell or "").strip() for cell in row) for row in table]
                    rows = [row for row in rows if row.strip(" |")]
                    if rows:
                        parts.append(f"Page {page_index} table {table_index}\n" + "\n".join(rows))
    except Exception as exc:
        raise ExtractionError(f"Could not extract tables from PDF file: {exc}") from exc

    return "\n\n".join(parts)


# Legacy .xls binary files can't actually be read by openpyxl (it only
# understands the modern zip-based .xlsx format), but routing them through
# the same parser still produces a clean ExtractionError rather than
# silently misreporting the extension as unsupported - a mislabeled export
# occasionally turns out to be readable anyway.
_EXTENSION_PARSERS = {
    ".xlsx": _extract_xlsx,
    ".xls": _extract_xlsx,
    ".docx": _extract_docx,
    ".html": _extract_html,
    ".htm": _extract_html,
    ".pdf": _extract_pdf,
}


class ExtractionResult:
    def __init__(self, filename, format, text):
        self.filename = filename
        self.format = format
        self.text = text

    def to_dict(self):
        return {"filename": self.filename, "format": self.format, "text": self.text}


def extract(filename, content_bytes):
    if len(content_bytes) > MAX_FILE_SIZE_BYTES:
        raise ExtractionError(
            f"File is too large ({len(content_bytes):,} bytes) - the limit is {MAX_FILE_SIZE_BYTES:,} bytes."
        )
    extension = Path(filename).suffix.lower()
    parser = _EXTENSION_PARSERS.get(extension)
    if parser is None:
        raise UnsupportedFormatError(f"Unsupported file type: {extension or '(no extension)'}")
    text = parser(content_bytes)
    if not text.strip():
        raise ExtractionError("No extractable content found in this file.")
    return ExtractionResult(filename=filename, format=extension.lstrip("."), text=text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_document_extraction.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add server/document_extraction.py tests/server/test_document_extraction.py
git commit -m "feat: add document_extraction module for Excel/PDF/Word/HTML parsing

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Wire it in — `/admin/api/extract` route, admin panel upload UI, end-to-end tests, manual verification

**Files:**
- Modify: `server/routes/admin.py`
- Modify: `server/admin_ui.py`
- Modify: `tests/server/test_routes.py`
- Create: `tests/server/test_extract_route.py`

**Interfaces:**
- Consumes: `document_extraction.extract`, `document_extraction.UnsupportedFormatError`, `document_extraction.ExtractionError`, `document_extraction.ExtractionResult` from Task 1; `_require_auth` (already defined in `server/routes/admin.py`).
- Produces: the final, verified phase-4a upload-and-preview feature.

- [ ] **Step 1: Update imports in `server/routes/admin.py`**

Find:

```python
"""GET/POST /admin, /admin/setup, /admin/login, /admin/logout, and the
/admin/api/keys* JSON API. See docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md sections 6 and 8.
"""
from fastapi import APIRouter, Body, Cookie, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from server import admin_ui, auth, keystore, providers
```

Replace with:

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

- [ ] **Step 2: Append the new route to `server/routes/admin.py`**

Find (the end of the file):

```python
    key_to_test = candidate_key or keystore.get_key(provider)
    ok, detail = providers.test_key(provider, key_to_test or "")
    return JSONResponse({"ok": ok, "detail": detail})
```

Replace with:

```python
    key_to_test = candidate_key or keystore.get_key(provider)
    ok, detail = providers.test_key(provider, key_to_test or "")
    return JSONResponse({"ok": ok, "detail": detail})


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

- [ ] **Step 3: Add the upload section's CSS to `server/admin_ui.py`**

Find:

```python
.provider-actions { display: flex; gap: 0.4rem; align-items: center; }
.provider-actions input { margin: 0; width: 160px; }
"""
```

Replace with:

```python
.provider-actions { display: flex; gap: 0.4rem; align-items: center; }
.provider-actions input { margin: 0; width: 160px; }
.upload-section { margin-top: 1.5rem; padding-top: 1.5rem; border-top: 1px solid var(--line); }
.upload-section h2 { font-size: 1rem; margin: 0 0 0.25rem; }
.upload-section .hint { color: var(--muted); font-size: 0.85rem; margin: 0 0 0.75rem; }
#extract-file-input { display: block; margin-bottom: 0.75rem; }
#extract-status { display: none; }
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

- [ ] **Step 4: Add the upload section's JS to `server/admin_ui.py`**

Find:

```python
    var logoutBtn = byId("logout-btn");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", function () {
        apiCall("POST", "/admin/logout").then(function () {
          window.location.href = "/admin";
        });
      });
    }
  });
})();
"""
```

Replace with:

```python
    var logoutBtn = byId("logout-btn");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", function () {
        apiCall("POST", "/admin/logout").then(function () {
          window.location.href = "/admin";
        });
      });
    }

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

- [ ] **Step 5: Add the upload section's markup to `render_admin_panel()` in `server/admin_ui.py`**

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

- [ ] **Step 6: Write `tests/server/test_extract_route.py`**

```python
"""End-to-end /admin/api/extract tests via FastAPI's TestClient.
document_extraction.extract is mocked - this file exercises the route's
auth/status-code plumbing only; the real extraction logic is covered by
tests/server/test_document_extraction.py. keyring is mocked too, same
pattern as tests/server/test_routes.py.
"""
import io

import pytest
from fastapi.testclient import TestClient

from server import document_extraction, keystore
from server.app import create_app


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


def test_extract_requires_authentication(client):
    response = client.post(
        "/admin/api/extract",
        files={"file": ("data.xlsx", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert response.status_code == 401


def test_extract_success(client, monkeypatch):
    _login(client)
    fake_result = document_extraction.ExtractionResult(
        filename="data.xlsx", format="xlsx", text="Peshawar | 4750388"
    )
    monkeypatch.setattr(document_extraction, "extract", lambda filename, content_bytes: fake_result)
    response = client.post(
        "/admin/api/extract",
        files={"file": ("data.xlsx", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json() == {"filename": "data.xlsx", "format": "xlsx", "text": "Peshawar | 4750388"}


def test_extract_unsupported_format_returns_415(client, monkeypatch):
    _login(client)

    def failing_extract(filename, content_bytes):
        raise document_extraction.UnsupportedFormatError("Unsupported file type: .csv")

    monkeypatch.setattr(document_extraction, "extract", failing_extract)
    response = client.post(
        "/admin/api/extract",
        files={"file": ("data.csv", io.BytesIO(b"x"), "text/csv")},
    )
    assert response.status_code == 415
    assert "csv" in response.json()["detail"]


def test_extract_parse_failure_returns_422(client, monkeypatch):
    _login(client)

    def failing_extract(filename, content_bytes):
        raise document_extraction.ExtractionError("Could not read Excel file: corrupt")

    monkeypatch.setattr(document_extraction, "extract", failing_extract)
    response = client.post(
        "/admin/api/extract",
        files={"file": ("data.xlsx", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert response.status_code == 422
    assert "corrupt" in response.json()["detail"]
```

- [ ] **Step 7: Add a UI-presence assertion to `tests/server/test_routes.py`**

Find (the end of the file):

```python
def test_logout_clears_session(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    client.post("/admin/logout")
    response = client.get("/admin/api/keys")
    assert response.status_code == 401
```

Replace with:

```python
def test_logout_clears_session(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    client.post("/admin/logout")
    response = client.get("/admin/api/keys")
    assert response.status_code == 401


def test_admin_panel_includes_extract_upload_section(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    for hook in ('id="extract-file-input"', 'id="extract-btn"', 'id="extract-result"', "/admin/api/extract"):
        assert hook in panel.text, f"missing hook: {hook}"
```

- [ ] **Step 8: Run the new and modified tests**

Run: `pytest tests/server/test_extract_route.py tests/server/test_routes.py -v`
Expected: 16 passed (4 in `test_extract_route.py`, 12 in `test_routes.py` — the 11 already there plus the new one)

- [ ] **Step 9: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all tests pass — the existing ~120 plus this phase's 8 (`test_document_extraction.py`) + 4 (`test_extract_route.py`) + 1 (new `test_routes.py` case) = roughly 133; exact count isn't load-bearing, "all pass" is.

- [ ] **Step 10: Manual browser verification**

Start the server: `python -m server`

In a browser at `http://127.0.0.1:8420/admin` (log in, or complete setup first if this is a fresh keyring):
- The admin panel now shows an "Extract Document" section below the provider rows, with a file picker, an "Extract" button, and an empty read-only text area.
- Prepare a small real test file of each type on disk (e.g. a one-sheet `.xlsx`, a one-paragraph `.docx`, a one-page `.pdf`, a small `.html` file — reuse anything handy, e.g. export a tiny sheet from Excel/LibreOffice, or save a short Word doc) and for each:
  - Choose the file, click Extract, confirm the button shows "Extracting..." briefly, then the text area fills with recognizable content from the file (not garbled, not empty).
- Try an unsupported file (e.g. rename any `.txt` or `.csv` file to have no double-checked extension, or pick a `.zip`): confirm a clear "Unsupported file type: ..." message appears where the status paragraph is, not a crash or blank response.
- Try a corrupted file (e.g. rename a `.txt` file to `.xlsx` and upload it): confirm a clear "Could not read Excel file: ..." message appears, not a crash.
- Confirm the whole flow works while logged in, and confirm hitting `POST /admin/api/extract` without a session (e.g. via `curl -F "file=@somefile.xlsx" http://127.0.0.1:8420/admin/api/extract`) returns 401.

If any of these fail, this is a real bug to fix before committing — do not report success without having actually driven the browser through each step.

- [ ] **Step 11: Final commit**

```bash
git add server/routes/admin.py server/admin_ui.py tests/server/test_extract_route.py tests/server/test_routes.py
git commit -m "feat: wire document upload and extraction into the admin panel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
