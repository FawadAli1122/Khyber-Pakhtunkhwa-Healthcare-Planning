"""End-to-end route tests via FastAPI's TestClient. All keyring/provider
calls are mocked - no real OS keyring entries or network calls.
"""
import pytest
from fastapi.testclient import TestClient

from server import keystore, providers
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


def test_dashboard_route_serves_report_or_placeholder(client):
    response = client.get("/")
    assert response.status_code in (200, 503)
    assert "html" in response.text.lower()


def test_admin_shows_setup_when_no_password(client):
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Set Up Admin Password" in response.text


def test_full_setup_login_flow(client):
    setup = client.post("/admin/setup", data=SETUP_FORM)
    assert setup.status_code in (200, 303)

    unauth = client.get("/admin")
    assert "Admin Login" in unauth.text

    login = client.post("/admin/login", data={"password": "hunter2hunter2"})
    assert login.status_code in (200, 303)
    # client.post() follows the 303 redirect by default, so `login` is the
    # final GET /admin response, not the POST /admin/login response that
    # carried Set-Cookie - check the client's persistent cookie jar instead.
    assert "kp_admin_session" in client.cookies

    panel = client.get("/admin")
    assert "AI Provider Keys" in panel.text


def test_setup_rejects_mismatched_passwords(client):
    response = client.post("/admin/setup", data={"password": "aaaaaaaa", "confirm": "bbbbbbbb"})
    assert response.status_code == 400


def test_setup_rejects_short_password(client):
    response = client.post("/admin/setup", data={"password": "short", "confirm": "short"})
    assert response.status_code == 400


def test_second_setup_attempt_rejected(client):
    client.post("/admin/setup", data=SETUP_FORM)
    second = client.post("/admin/setup", data={"password": "different1", "confirm": "different1"})
    assert second.status_code == 403


def test_login_wrong_password_rejected(client):
    client.post("/admin/setup", data=SETUP_FORM)
    response = client.post("/admin/login", data={"password": "wrongwrong"})
    assert response.status_code == 401


def test_api_keys_require_authentication(client):
    response = client.get("/admin/api/keys")
    assert response.status_code == 401


def test_authenticated_key_lifecycle(client, monkeypatch):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})

    listing = client.get("/admin/api/keys")
    assert listing.status_code == 200
    assert {row["provider"] for row in listing.json()} == set(keystore.PROVIDERS)

    saved = client.put("/admin/api/keys/anthropic", json={"api_key": "sk-ant-testtest"})
    assert saved.status_code == 200

    listing2 = client.get("/admin/api/keys")
    anthropic_row = next(r for r in listing2.json() if r["provider"] == "anthropic")
    assert anthropic_row["configured"] is True

    monkeypatch.setattr(providers, "test_key", lambda provider, key: (True, "Authenticated, 1 model(s) available"))
    tested = client.post("/admin/api/keys/anthropic/test")
    assert tested.status_code == 200
    assert tested.json()["ok"] is True

    deleted = client.delete("/admin/api/keys/anthropic")
    assert deleted.status_code == 200
    listing3 = client.get("/admin/api/keys")
    anthropic_row3 = next(r for r in listing3.json() if r["provider"] == "anthropic")
    assert anthropic_row3["configured"] is False


def test_unknown_provider_404s(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    response = client.put("/admin/api/keys/bogus", json={"api_key": "x"})
    assert response.status_code == 404


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


def test_admin_panel_extract_hint_mentions_sara_framework(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    assert "SARA" in panel.text


def test_admin_panel_includes_supplemental_data_section(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    for hook in (
        'id="supplemental-instruction"', 'id="supplemental-provider"',
        'id="add-to-report-btn"', "/admin/api/supplemental-data",
    ):
        assert hook in panel.text, f"missing hook: {hook}"


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


def test_admin_panel_includes_database_ingestion_section(client):
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    for hook in (
        'id="db-host"', 'id="db-port"', 'id="db-database"', 'id="db-user"',
        'id="db-password"', 'id="db-sslmode"', 'id="db-connect-btn"',
        'id="db-table-select"', 'id="db-preview-btn"', 'id="db-instruction"',
        'id="db-provider"', 'id="db-ingest-btn"',
        "/admin/api/db/connection", "/admin/api/db/tables", "/admin/api/db/ingest",
    ):
        assert hook in panel.text, f"missing hook: {hook}"


def test_admin_panel_js_escapes_ai_derived_db_ingest_content(client):
    # Same regression class as test_admin_panel_js_escapes_ai_derived_supplemental_content,
    # for the database-ingestion "Add to Report" handler, which renders
    # the identical shape of AI-derived record fields via innerHTML.
    client.post("/admin/setup", data=SETUP_FORM)
    client.post("/admin/login", data={"password": "hunter2hunter2"})
    panel = client.get("/admin")
    db_ingest_js = panel.text.split('id="db-ingest-btn"')[1]
    for hook in (
        "escapeHtml(r.district)", "escapeHtml(r.facility)",
        "escapeHtml(r.category)", "escapeHtml(r.label)",
        "escapeHtml(result.data.rebuild_warning)",
    ):
        assert hook in db_ingest_js, f"missing escaping call in db-ingest handler: {hook}"
