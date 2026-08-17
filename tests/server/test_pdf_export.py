"""Real (unmocked) Playwright smoke test - launches actual Chromium once.
Deliberately slower than the rest of this suite; kept isolated here so no
other test needs a real browser. See server/pdf_export.py.
"""
from server import pdf_export


def test_render_report_pdf_produces_real_pdf_bytes():
    html = "<html><body><h1>Test Report</h1><p>Sample content.</p></body></html>"
    pdf_bytes = pdf_export.render_report_pdf(html)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000
