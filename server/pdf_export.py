"""Renders an HTML string to PDF bytes via Playwright's already-installed
Chromium. Takes HTML as input rather than reading report/
KP_Healthcare_Plan.html itself, so the caller controls exactly what gets
rendered - server/routes/dashboard.py passes the raw pipeline output, not
the served page, so the "Ask AI" chat widget (injected only at serve-time)
never ends up baked into the PDF.
"""
from playwright.sync_api import sync_playwright


def render_report_pdf(html_text):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html_text, wait_until="networkidle")
            return page.pdf(format="A4", print_background=True)
        finally:
            browser.close()
