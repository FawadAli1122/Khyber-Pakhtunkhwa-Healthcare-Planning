"""End-to-end /report.pdf tests via FastAPI's TestClient.
pdf_export.render_report_pdf is mocked - the real (slow) Playwright/
Chromium check lives in tests/server/test_pdf_export.py, not here.
"""
from fastapi.testclient import TestClient

from server import pdf_export
from server.app import create_app


def test_download_report_pdf_success(monkeypatch):
    monkeypatch.setattr(pdf_export, "render_report_pdf", lambda html_text: b"%PDF-1.4 fake pdf bytes")
    client = TestClient(create_app())
    response = client.get("/report.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert "KP_Healthcare_Plan.pdf" in response.headers["content-disposition"]
    assert response.content == b"%PDF-1.4 fake pdf bytes"
