# KP Healthcare Plan — Document Upload + Extraction (Phase 4a)

Status: Approved design, pre-implementation
Date: 2026-08-15

## 1. Purpose

Accept an uploaded document (Excel/PDF/Word/HTML) in the admin panel and
extract its content into a normalized text form — no AI involved yet, no
data or report changes. This is the plumbing phase 4b (AI-driven,
autonomous data updates) will consume; phase 4a is useful on its own as a
way to confirm extraction actually works before anything downstream acts
on it, matching the "build the pipe before the water" pattern used for
phases 2→3.

## 2. Scope Decisions From Brainstorming

- **Document ingestion decomposed into 4a/4b/4c**, not built as one lump.
  4a (this spec): upload + extraction, no data mutation. 4b (separate,
  later spec): AI decides what changed from 4a's extracted text and
  applies it autonomously (per the standing "full autonomy" decision from
  the original project brainstorm) — but to the pipeline's *raw* source
  CSVs, never directly to *computed* columns like `gap_score`/`need_tier`,
  so the deterministic pipeline stages still recompute those the same
  trustworthy way they always have; applied changes get logged as a
  record, not gated behind a review step. 4c (separate, deferred):
  database ingestion — a fundamentally different input mechanism (a
  connection/credential to store, its own security surface like phase 2's
  API keys) that doesn't belong bolted onto a file-upload flow.
- **Any number of districts per upload, not capped at one.** A real
  document (e.g. a revised province-wide statistics table) may legitimately
  touch many districts at once — extraction must produce output structured
  enough for phase 4b to act on all of them, not force artificial
  one-district-per-upload splitting.
- **Upload lives in the admin panel, not the public dashboard.** This is
  the on-ramp to phase 4b's autonomous data changes — a materially bigger
  action than the read-only "Ask AI" chat, which stays on the public
  dashboard where it already is.

## 3. Approach

No new dependencies. This project already has `openpyxl`, `python-docx`
(imported as `docx`), `beautifulsoup4`, `pdfplumber`, and `fitz` (PyMuPDF)
installed — the last two already used by `scripts/lib/pdf_tables.py` for
parsing the Dev Stats PDF in the existing pipeline.

`server/document_extraction.py` exposes one function,
`extract(filename, content_bytes) -> ExtractionResult` (`{filename,
format, text}`), dispatching by file extension to a per-format parser:

| Format | Library | Approach |
|---|---|---|
| Excel (`.xlsx`/`.xls`) | `openpyxl` | Every sheet rendered as a labeled pipe-delimited table — not just the active sheet, so phase 4b's AI sees everything the workbook contains |
| PDF | `fitz` (page text) + `pdfplumber` (tables) | Generalizes `scripts/lib/pdf_tables.py`'s approach for an arbitrary uploaded PDF that has no "Table No. N" marker to key off of: page text plus any tables `pdfplumber` detects per page |
| Word (`.docx`) | `python-docx` | Paragraph text plus any embedded tables, in document order |
| HTML (`.html`/`.htm`) | `beautifulsoup4` | Visible text via `get_text()`, plus each `<table>` element additionally extracted as its own pipe-delimited block (raw text extraction alone jumbles tabular structure) |

Every parser raises a typed exception — `UnsupportedFormatError` for an
unrecognized extension, `ExtractionError` for a parse failure — carrying a
message safe to show the user directly, the same pattern established by
`ai_client.AIProviderError` in phase 3. A 20MB file-size cap guards
against the server choking on something huge; this is a resource-use
guard, not a security boundary, consistent with this being a single-user
local tool.

## 4. Route and Admin UI

`POST /admin/api/extract` — auth-gated like every other `/admin/api/*`
route, accepts a multipart file upload, returns `{"filename": ...,
"format": ..., "text": ...}` on success or a 4xx with `{"detail": ...}` on
an unsupported format, oversized file, or parse failure.

The admin panel gets a new section: a file picker plus an "Extract" button,
and a read-only text area that displays the returned `text` after a
successful extraction — so the admin can visually confirm the extraction
actually captured what the document contains before phase 4b ever
consumes it.

## 5. Testing Strategy

No test depends on a checked-in fixture binary or a real document:

- `tests/server/test_document_extraction.py` — one test per format,
  building a tiny fixture file inline: a 2-row `openpyxl` workbook, a
  one-paragraph `python-docx` document, a minimal PDF built directly via
  `fitz.open()` + `insert_page` + `insert_text`, and a small HTML string.
  Plus: unsupported extension raises `UnsupportedFormatError`, a corrupt/
  unparseable file raises `ExtractionError` rather than an uncaught
  exception, and an oversized payload is rejected before parsing is
  attempted.
- `tests/server/test_extract_route.py` — FastAPI `TestClient` against
  `/admin/api/extract`, with `document_extraction.extract` mocked (same
  pattern as phase 3's `test_ask_route.py` mocking `ai_client.ask`),
  covering success, unauthenticated access, and a parse failure surfaced
  as a clean error.

## 6. Roadmap (context for later phases — not this spec's scope)

- **Phase 4b — AI-driven autonomous data update.** Consumes 4a's
  `extract()` output; AI decides what changed and writes it to the
  pipeline's raw source CSVs (never to computed columns), logs what
  changed, then triggers the relevant pipeline stages plus
  `14_build_html_report.py` so the dashboard reflects the update. No
  human-in-the-loop review gate, per the standing project decision.
- **Phase 4c — Database ingestion.** A distinct input mechanism from file
  upload; needs its own credential-storage design (extending phase 2's
  `keystore`/`keyring` pattern) before any design work starts.
- **Phase 1b — Methodology upgrade.** 2SFCA-style accessibility and
  p-median/MCLP site suggestion, replacing the current heuristics in the
  deterministic pipeline. Independent of phases 2–4.
