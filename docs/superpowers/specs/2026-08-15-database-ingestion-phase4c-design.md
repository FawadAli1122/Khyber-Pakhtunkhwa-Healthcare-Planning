# KP Healthcare Plan — Database Ingestion (Phase 4c)

Status: Approved design, pre-implementation
Date: 2026-08-15

## 1. Purpose

Let the admin connect to a PostgreSQL database, browse its tables, and pull
a table's rows through the same AI-extraction pipeline phase 4b built for
document upload — producing the same kind of supplemental facility/district
record (equipment, medicine, departments, diseases treated, outbreaks, or
anything else), appended to the same `data/processed/supplemental_records.csv`
store, shown in the same report section, grounding the same "Ask AI" chat.

This is the "Phase 4c — Database ingestion" item both the phase 4a and
phase 4b specs flagged as a distinct input mechanism needing its own
credential-storage design, deferred until now. It is a second *front door*
into phase 4b's engine, not a new extraction/validation/storage pipeline —
phase 4b's `supplemental_data.add_from_document()` is reused unchanged.

Explicitly out of scope: this phase never feeds `district_metrics.csv` or
any computed gap-score column — same boundary phase 4b drew for document
upload. Feeding the deterministic pipeline's own inputs (population,
dev-stats figures) from a database remains a separate, undesigned, deferred
piece, same as it was after phase 4a and 4b.

## 2. Scope Decisions From Brainstorming

- **Feeds phase 4b's supplemental-records store, not the core pipeline.**
  Reuses `supplemental_data.add_from_document()` exactly as it exists today —
  this phase adds no new AI-extraction, validation, or storage logic of its
  own, only a new way to produce the `document_text` that function already
  accepts.
- **PostgreSQL only.** One driver (`psycopg2`), one connection-string shape,
  smallest surface area for a first cut. Not a generic multi-engine
  abstraction (e.g. SQLAlchemy) — YAGNI until a real need for a second
  engine appears.
- **Browse tables and pick one, not a raw SQL query box.** Friendlier for
  a non-SQL-fluent admin, and closes the SQL-injection surface entirely:
  the only table names ever used in a query are ones `list_tables()` itself
  returned, never raw admin or AI input.
- **One connection at a time**, not multiple named/saved connections.
  Matches this project's single-user local-tool scope (same reasoning as
  phase 2's single admin password, not per-user accounts). Saving a new
  connection overwrites the old one.
- **No app-level read-only enforcement.** The connection is not forced into
  a read-only transaction mode. Safety here is the admin's own database
  user's permissions, not this application's — an explicit choice, not an
  oversight, made during brainstorming. (The module itself never issues
  anything but `SELECT`, so there's no code path that could write even with
  a writable DB user — see §5.)
- **Row-capped table pulls (200 rows fixed, not admin-configurable).**
  Pulling a whole table risks the same AI-response-truncation failure mode
  phase 4b's own final review flagged and partially mitigated for large
  documents (`server/supplemental_data.py`'s `MAX_ANSWER_TOKENS` ceiling).
  A fixed cap, clearly labeled in the rendered text so the AI and the admin
  both know it's a sample, is simpler than exposing a tunable that mostly
  just shifts the same truncation problem to a different size.
- **Public schema only, no schema picker.** Simplest default for a first
  cut; revisit only if a real need for non-public-schema tables appears.
- **Preview before commit**, matching phase 4a/4b's file-upload UX exactly:
  a "Preview" step (renders the pulled rows, no AI call yet) separate from
  "Add to Report" (runs the real AI extraction + save + report rebuild) —
  the same two-step shape as the existing "Extract" vs. "Add to Report"
  buttons, so an admin already familiar with document upload gets an
  identical mental model for database ingestion.

## 3. Architecture

New `server/db_ingestion.py` — the database-specific counterpart to
`server/document_extraction.py`. No AI, no validation, no storage logic of
its own; it only connects, lists tables, fetches rows, and renders them to
the same pipe-delimited text format `document_extraction._extract_csv`
already produces, so the resulting text is indistinguishable — from
`supplemental_data.add_from_document()`'s point of view — from an uploaded
CSV file's extracted text. `add_from_document()`, `ai_client.py`,
`report_context.py`, and the report-rendering section (`scripts/14_build_html_report.py`,
`scripts/lib/supplemental_records.py`) are all reused exactly as they exist
today — no changes to any of them in this phase.

Driver: `psycopg2` (via the `psycopg2-binary` distribution), which is
already installed in this environment — not a new dependency to add. (The
original brainstorm considered the modern `psycopg` v3 package; `psycopg2`
was chosen instead specifically because it requires no new install step,
and its synchronous connect/query/fetch API is sufficient for this phase's
needs — connect, list tables, run one capped `SELECT`.)

## 4. Credential Storage

Extends `server/keystore.py` with one reserved keyring entry — following
the exact pattern `ADMIN_PASSWORD_KEY`/`SESSION_SECRET_KEY` already
established for single, non-provider-list secrets:

- `DB_CONNECTION_KEY = "db_connection"` — stores one JSON-serialized blob:
  `{"host": str, "port": int, "database": str, "user": str, "password": str, "sslmode": str}`.
- `keystore.get_db_connection() -> dict | None` — reads and JSON-decodes the
  entry, `None` if never set.
- `keystore.set_db_connection(conn_info: dict) -> None` — JSON-encodes and
  stores it.
- `keystore.delete_db_connection() -> None` — same delete-is-a-no-op-if-absent
  posture as `delete_key()`.

`sslmode` defaults to `"prefer"` (libpq's own default) if the admin leaves
it blank, so most hosted Postgres providers (Azure, RDS, Supabase, etc.)
work without the admin needing to know libpq's SSL vocabulary; an optional
field lets them override it (e.g. `"require"`, `"disable"`) for the cases
that need it.

The password is never re-sent to the browser after saving. Unlike an AI
provider key, a database password has no natural "show last 4 characters"
representation, so the UI shows connection status as "Connected to
`<host>/<database>`" (host and database name are not secret) rather than a
masked password string.

## 5. `db_ingestion.py` — Connect, List, Fetch, Render

- `DbIngestionError(Exception)` — typed exception, message safe to show the
  admin directly, never a raw traceback or raw `psycopg2` error — same
  posture as `SupplementalDataError`/`ExtractionError`/`AIProviderError`.
- `test_connection(conn_info: dict) -> (bool, str)` — attempts
  `psycopg2.connect(**conn_info, connect_timeout=5)`, returns `(True,
  "Connected")` on success or `(False, <safe message>)` on failure. Mirrors
  `providers.test_key()`'s `(ok, detail)` shape from phase 2/3.
- `list_tables(conn_info: dict) -> list[str]` — queries
  `information_schema.tables` for `table_schema = 'public'`, returns table
  and view names, sorted.
- `fetch_table_text(conn_info: dict, table_name: str, row_limit: int = 200) -> str`
  — validates `table_name` is a member of `list_tables(conn_info)`'s own
  result first (raises `DbIngestionError` for anything else — this is the
  only gate that matters for injection safety, since the table name is
  f-string-interpolated into the query but only ever after this check),
  then runs `SELECT * FROM "<table_name>" LIMIT <row_limit>`, and renders
  the result as: a header line of column names, then one `" | "`-joined
  line per row (identical convention to
  `document_extraction._extract_csv`), prefixed with a
  `"(showing first {row_limit} rows)"` note so both the AI and a
  human previewing it know it's a capped sample.

`row_limit` defaults to 200 and is not exposed as an admin-configurable
setting in this phase.

No function in this module ever issues anything but `SELECT` — there is no
code path, admin input, or AI output that reaches an `INSERT`/`UPDATE`/
`DELETE`/DDL statement.

## 6. Routes & Admin UI

Three new routes in `server/routes/admin.py`, auth-gated like every other
`/admin/api/*` route:

- `POST /admin/api/db/connection` — body: `{host, port, database, user, password, sslmode}`.
  Saves via `keystore.set_db_connection()`, then calls `db_ingestion.test_connection()`,
  returns `{"ok": bool, "detail": str}` — same shape the AI-provider "Test"
  buttons already render.
- `GET /admin/api/db/tables` — returns `{"tables": [...]}` from
  `db_ingestion.list_tables()` using the saved connection; 400 with a clear
  message if no connection is saved yet.
- `POST /admin/api/db/ingest` — body: `{table, instruction, provider}`, plus
  a `preview` query/body flag.
  - `preview=true`: calls `fetch_table_text()` only, returns
    `{"text": "..."}` — no AI call, no save. Mirrors `/admin/api/extract`.
  - Real ingest (default): calls `fetch_table_text()`, then
    `supplemental_data.add_from_document(provider, key, text, instruction, f"db:{table}")`
    (the `source_document` field records `"db:<table_name>"` so the report
    and grounding digest can distinguish a database-sourced record from a
    file-sourced one), then the same
    `subprocess.run([sys.executable, "scripts/14_build_html_report.py"], timeout=300)`
    rebuild phase 4b's route already does, with the same
    `rebuild_warning`-on-failure/timeout behavior. Returns `{"added": [...]}`
    or `{"added": [...], "rebuild_warning": "..."}`, both 200; 400/502 for
    `DbIngestionError`/`SupplementalDataError`/`AIProviderError`, matching
    phase 4b's established status-code conventions exactly.

Admin panel gains a "Database Ingestion" section, same visual pattern
(card, labeled fields, buttons) as the existing "AI Provider Keys" and
"Extract Document" sections — not a new page. Fields: host, port (default
5432), database, username, password, and a collapsed/optional `sslmode`
field. A "Save & Test Connection" button calls the connection route. Once a
connection is saved, a table `<select>` populates from the tables route
(fetched on page load if a connection exists, or right after a successful
save/test). Below it: an instruction textarea + AI provider dropdown
(identical markup/behavior to phase 4b's), a "Preview" button (renders the
pulled rows in a read-only textarea, mirroring the "Extract" button), and
an "Add to Report" button (mirrors phase 4b's, including the same
`escapeHtml()` treatment on every AI-derived field in the result summary —
this feeds the identical `add_from_document()` path and carries the
identical untrusted-AI-content-in-HTML risk phase 4b's final review found
and fixed twice).

## 7. Error Handling & Security

Every `psycopg2` exception (connection refused, authentication failure,
table not found, query timeout, etc.) is caught at the `db_ingestion.py`
boundary and re-raised as `DbIngestionError` with a message safe to show
the admin — never a raw `psycopg2` exception or traceback. Route status
codes mirror phase 4b's established conventions: 400 for a bad/untested
connection or `DbIngestionError`, 502 for `AIProviderError`, 200 +
`rebuild_warning` if the report-rebuild subprocess fails or times out after
records were already saved (data already saved is never reported as a
failure — same rule phase 4b's Global Constraints stated).

Security surface:
- The database password lives in the OS credential store via
  `keystore`/`keyring`, identical to every other secret in this project —
  never in a file, never re-sent to the browser after saving.
- Table names used in a query are always validated against
  `list_tables()`'s own output before use — never raw admin or AI input —
  closing the SQL-injection surface even though the query itself is built
  with an f-string.
- The row cap (200) bounds how much data a single ingest can pull.
- No write capability exists anywhere in this module.
- No app-level read-only enforcement (`SET TRANSACTION READ ONLY`) — an
  explicit scope decision (§2); the admin's own database user's permissions
  are the safety boundary, not this application's connection handling.

## 8. Testing Strategy

- `tests/server/test_db_ingestion.py` — `test_connection` success/failure
  (mocking `psycopg2.connect`), `list_tables` parsing a mocked query result,
  `fetch_table_text`'s row-cap/rendering format (including the
  "(showing first N rows)" line) and its rejection of a table name not in
  `list_tables()`'s result. Every `psycopg2` call in every test is mocked —
  no test requires a real database, same posture as every AI provider call
  being mocked throughout this project.
- `tests/server/test_db_ingestion_route.py` — FastAPI `TestClient` against
  all three new routes: auth-gating (401 without a session), connection
  save+test success/failure, table listing with/without a saved connection,
  preview vs. real ingest, validation-failure and provider-failure status
  codes, and the rebuild-timeout/failure-still-returns-200 case — mirroring
  `tests/server/test_supplemental_data_route.py`'s exact structure and
  mocking pattern.
- `tests/server/test_routes.py` gains a UI-presence test for the new
  "Database Ingestion" admin panel section's hooks (input/select/button
  ids), matching the existing pattern for the "Extract Document" and
  supplemental-data sections.
- No test depends on a real PostgreSQL connection or a real AI provider
  call; the underlying pieces this phase reuses (`supplemental_data`,
  `ai_client`, `14_build_html_report.py`) already have their own
  real-behavior coverage from phases 3/4b.

## 9. Roadmap (context for later phases — not this spec's scope)

- **Prompt-guided updates to existing pipeline data** (population,
  dev-stats health/roads/budget figures feeding `district_metrics.csv`),
  whether from a document or a database, remains a separate, undesigned,
  deferred piece — this phase never writes to those files or to any
  computed column.
- **Phase 1b — Methodology upgrade** (2SFCA/p-median), unchanged, still
  independent of the document-ingestion and database-ingestion phases.
- **Multi-engine support / multiple named connections / schema picker /
  admin-configurable row cap** — all explicitly deferred in §2; revisit
  only if a real need appears.
