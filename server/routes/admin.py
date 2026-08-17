"""GET/POST /admin, /admin/setup, /admin/login, /admin/logout, the
/admin/api/keys* JSON API, /admin/api/extract for document upload,
/admin/api/supplemental-data for AI-extracted facility/district records,
/admin/api/metric-overrides for AI-validated pipeline-data updates, and
/admin/api/db/* for PostgreSQL table browsing/ingestion. See
docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md
sections 6 and 8, 2026-08-15-document-upload-phase4a-design.md section 4,
2026-08-15-supplemental-facility-data-phase4b-design.md section 5,
2026-08-15-pipeline-data-overrides-phase4d-design.md section 6, and
2026-08-15-database-ingestion-phase4c-design.md section 6.
"""
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Body, Cookie, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from server import (
    admin_ui,
    ai_client,
    auth,
    bot_facilities,
    custom_data,
    db_browser,
    db_ingestion,
    document_extraction,
    keystore,
    metric_overrides,
    providers,
    supplemental_data,
    telegram_bot,
)

REPORT_BUILD_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "14_build_html_report.py"
RUN_DOWNSTREAM_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "run_downstream.py"
RUN_DOWNSTREAM_FACILITIES_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "run_downstream_facilities.py"

router = APIRouter()

SESSION_COOKIE_NAME = "kp_admin_session"


def _authenticated(session_cookie):
    if not session_cookie:
        return False
    return auth.verify_session_cookie(session_cookie, auth.get_session_secret())


def _require_auth(session_cookie):
    if not _authenticated(session_cookie):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return None


@router.get("/admin", response_class=HTMLResponse)
def admin_home(kp_admin_session: str | None = Cookie(default=None)):
    if not auth.is_admin_password_set():
        return HTMLResponse(admin_ui.render_setup_page())
    if not _authenticated(kp_admin_session):
        return HTMLResponse(admin_ui.render_login_page())
    return HTMLResponse(admin_ui.render_admin_panel(keystore.list_status()))


@router.post("/admin/setup")
def admin_setup(password: str = Form(...), confirm: str = Form(...)):
    if auth.is_admin_password_set():
        return HTMLResponse(admin_ui.render_login_page(), status_code=403)
    if password != confirm:
        return HTMLResponse(admin_ui.render_setup_page(error="Passwords do not match"), status_code=400)
    if len(password) < 8:
        return HTMLResponse(
            admin_ui.render_setup_page(error="Password must be at least 8 characters"), status_code=400
        )
    auth.set_admin_password(password)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/login")
def admin_login(password: str = Form(...)):
    if not auth.verify_admin_password(password):
        return HTMLResponse(admin_ui.render_login_page(error="Incorrect password"), status_code=401)
    cookie_value = auth.create_session_cookie(auth.get_session_secret())
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(SESSION_COOKIE_NAME, cookie_value, httponly=True, samesite="lax")
    return response


@router.post("/admin/logout")
def admin_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/admin/api/keys")
def list_keys(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    return JSONResponse(keystore.list_status())


@router.put("/admin/api/keys/{provider}")
def set_key(
    provider: str,
    kp_admin_session: str | None = Cookie(default=None),
    api_key: str = Body(..., embed=True),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    keystore.set_key(provider, api_key)
    return JSONResponse({"ok": True})


@router.delete("/admin/api/keys/{provider}")
def delete_key(provider: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    keystore.delete_key(provider)
    return JSONResponse({"ok": True})


@router.post("/admin/api/keys/{provider}/test")
async def test_key_route(
    provider: str,
    request: Request,
    kp_admin_session: str | None = Cookie(default=None),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    # Manually inspect the body rather than a FastAPI Body(...) param: the
    # "test the already-saved key" call sends no body at all, and the two
    # cases (candidate key vs. saved key) are simplest handled explicitly.
    body_bytes = await request.body()
    candidate_key = None
    if body_bytes:
        payload = await request.json()
        candidate_key = payload.get("api_key")
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

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"added": added, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"added": added, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"added": added})


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


@router.get("/admin/api/telegram/config")
def get_telegram_config(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    config = keystore.get_telegram_config()
    if not config:
        return JSONResponse({"configured": False})
    return JSONResponse({
        "configured": True,
        "token_hint": keystore.mask(config["token"]),
        "allowed_user_id": config["allowed_user_id"],
    })


@router.post("/admin/api/telegram/config")
async def save_telegram_config(
    kp_admin_session: str | None = Cookie(default=None),
    token: str = Body(...),
    allowed_user_id: str = Body(...),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    keystore.set_telegram_config({"token": token, "allowed_user_id": allowed_user_id})
    await telegram_bot.stop_bot_task()
    started = await telegram_bot.start_bot_task()
    if not started:
        return JSONResponse({"ok": True, "bot_warning": "Saved, but the bot failed to start - check the token."})
    return JSONResponse({"ok": True})


@router.delete("/admin/api/telegram/config")
async def delete_telegram_config(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    keystore.delete_telegram_config()
    await telegram_bot.stop_bot_task()
    return JSONResponse({"ok": True})


@router.get("/admin/api/bot-facilities/records")
def list_bot_facilities(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    return JSONResponse({"records": bot_facilities.load_records()})


@router.delete("/admin/api/bot-facilities/records/{record_id}")
def delete_bot_facility(record_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    found = bot_facilities.delete_record(record_id)
    if not found:
        return JSONResponse({"detail": "No record with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(RUN_DOWNSTREAM_FACILITIES_SCRIPT)],
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


@router.post("/admin/api/db/connection")
def save_db_connection(
    kp_admin_session: str | None = Cookie(default=None),
    host: str = Body(...),
    port: int = Body(5432),
    database: str = Body(...),
    user: str = Body(...),
    password: str = Body(...),
    sslmode: str = Body(""),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    conn_info = {
        "host": host, "port": port, "database": database,
        "user": user, "password": password, "sslmode": sslmode or "prefer",
    }
    keystore.set_db_connection(conn_info)
    ok, detail = db_ingestion.test_connection(conn_info)
    return JSONResponse({"ok": ok, "detail": detail})


@router.get("/admin/api/db/tables")
def list_db_tables(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    conn_info = keystore.get_db_connection()
    if not conn_info:
        return JSONResponse(
            {"detail": "No database connection configured - save one in the admin panel first."},
            status_code=400,
        )
    try:
        tables = db_ingestion.list_tables(conn_info)
    except db_ingestion.DbIngestionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse({"tables": tables})


@router.post("/admin/api/db/ingest")
def ingest_from_db(
    kp_admin_session: str | None = Cookie(default=None),
    table: str = Body(...),
    provider: str = Body(""),
    instruction: str = Body(""),
    preview: bool = Body(False),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    conn_info = keystore.get_db_connection()
    if not conn_info:
        return JSONResponse(
            {"detail": "No database connection configured - save one in the admin panel first."},
            status_code=400,
        )

    try:
        text = db_ingestion.fetch_table_text(conn_info, table)
    except db_ingestion.DbIngestionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    if preview:
        return JSONResponse({"text": text})

    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    key = keystore.get_key(provider)
    if not key:
        return JSONResponse(
            {"detail": f"No API key configured for {provider} - add one in the admin panel first."},
            status_code=400,
        )

    try:
        added = supplemental_data.add_from_document(provider, key, text, instruction, f"db:{table}")
    except supplemental_data.SupplementalDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"added": added, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"added": added, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"added": added})


@router.get("/admin/api/custom-data/tables")
def list_custom_tables(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    return JSONResponse({"tables": custom_data.list_tables()})


@router.post("/admin/api/custom-data/tables")
def create_custom_table(
    kp_admin_session: str | None = Cookie(default=None),
    label: str = Body(...),
    columns: list = Body(...),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    try:
        table = custom_data.create_table(label, columns)
    except custom_data.CustomDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse({"table": table})


@router.post("/admin/api/custom-data/propose-schema")
def propose_custom_schema(
    kp_admin_session: str | None = Cookie(default=None),
    provider: str = Body(...),
    prompt: str = Body(...),
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
    try:
        proposal = custom_data.propose_schema(provider, key, prompt)
    except custom_data.CustomDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)
    return JSONResponse({"proposal": proposal})


@router.post("/admin/api/custom-data/tables/{table_id}/columns")
def add_custom_column(
    table_id: str,
    kp_admin_session: str | None = Cookie(default=None),
    label: str = Body(...),
    type: str = Body(...),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    try:
        table = custom_data.add_column(table_id, label, type)
    except custom_data.CustomDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    if table is None:
        return JSONResponse({"detail": "No custom table with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"table": table, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"table": table, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"table": table})


@router.delete("/admin/api/custom-data/tables/{table_id}/columns/{column_id}")
def delete_custom_column(table_id: str, column_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    found = custom_data.delete_column(table_id, column_id)
    if not found:
        return JSONResponse({"detail": "No custom table/column with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"deleted": True, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"deleted": True, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"deleted": True})


@router.get("/admin/api/custom-data/tables/{table_id}/records")
def list_custom_records(table_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    records = custom_data.list_records(table_id)
    if records is None:
        return JSONResponse({"detail": "No custom table with that id"}, status_code=404)
    return JSONResponse({"records": records})


@router.post("/admin/api/custom-data/tables/{table_id}/preview")
async def preview_custom_data(
    table_id: str,
    file: UploadFile = File(...),
    provider: str = Form(...),
    instruction: str = Form(""),
    kp_admin_session: str | None = Cookie(default=None),
):
    """AI extraction only - never writes to the database. The admin
    panel shows the returned rows in an editable grid; committing them
    is a separate action (POST .../records below)."""
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
        rows = custom_data.preview_extraction(provider, key, table_id, extracted.text, instruction)
    except custom_data.CustomDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)
    if rows is None:
        return JSONResponse({"detail": "No custom table with that id"}, status_code=404)
    return JSONResponse({"rows": rows})


@router.post("/admin/api/custom-data/tables/{table_id}/records")
def add_custom_rows(
    table_id: str,
    kp_admin_session: str | None = Cookie(default=None),
    provider: str = Body(...),
    rows: list = Body(...),
):
    """Commits rows the admin has reviewed/edited in the grid - whether
    they originated from preview_custom_data() above or were typed
    manually. provider is used only for the report-placement AI call."""
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
    try:
        added = custom_data.add_rows(table_id, rows, provider, key)
    except custom_data.CustomDataError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)
    if added is None:
        return JSONResponse({"detail": "No custom table with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"added": added, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"added": added, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"added": added})


@router.delete("/admin/api/custom-data/tables/{table_id}/records/{record_id}")
def delete_custom_record(table_id: str, record_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    found = custom_data.delete_row(table_id, record_id)
    if not found:
        return JSONResponse({"detail": "No custom table/record with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"deleted": True, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"deleted": True, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"deleted": True})


@router.delete("/admin/api/custom-data/tables/{table_id}")
def delete_custom_table(table_id: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    found = custom_data.delete_table(table_id)
    if not found:
        return JSONResponse({"detail": "No custom table with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"deleted": True, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"deleted": True, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"deleted": True})


@router.get("/admin/api/db-browser/tables")
def list_db_browser_tables(kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    return JSONResponse({"tables": db_browser.list_tables()})


@router.get("/admin/api/db-browser/tables/{table}/rows")
def get_db_browser_rows(table: str, kp_admin_session: str | None = Cookie(default=None)):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    columns = db_browser.get_table_columns(table)
    if columns is None:
        return JSONResponse({"detail": "No table with that name"}, status_code=404)
    return JSONResponse({"columns": columns, "rows": db_browser.get_table_rows(table)})


@router.put("/admin/api/db-browser/tables/{table}/rows/{record_id}")
def update_db_browser_row(
    table: str,
    record_id: str,
    kp_admin_session: str | None = Cookie(default=None),
    fields: dict = Body(...),
):
    unauthorized = _require_auth(kp_admin_session)
    if unauthorized:
        return unauthorized
    try:
        updated = db_browser.update_row(table, record_id, fields)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    if updated is None:
        return JSONResponse({"detail": "No table with that name"}, status_code=404)
    if not updated:
        return JSONResponse({"detail": "No row with that id"}, status_code=404)

    try:
        result = subprocess.run(
            [sys.executable, str(REPORT_BUILD_SCRIPT)], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"updated": True, "rebuild_warning": "Report rebuild timed out after 300 seconds"})
    if result.returncode != 0:
        return JSONResponse({"updated": True, "rebuild_warning": f"Report rebuild failed: {result.stderr[-500:]}"})
    return JSONResponse({"updated": True})
