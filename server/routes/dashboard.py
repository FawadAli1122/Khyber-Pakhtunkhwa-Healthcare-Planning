"""GET / - serves report/KP_Healthcare_Plan.html with the "Ask AI" chat
panel and a top-right "Admin" + "Download PDF" link bar injected before
</body>, re-read from disk on every request so a pipeline rebuild is
picked up without a server restart. The pipeline's own output
(scripts/14_build_html_report.py) is never modified - everything is
injected here, at serve-time only. GET /report.pdf renders that same raw
pipeline output (not the served page) to PDF, so neither the chat widget
nor the link bar ends up baked into the download. See
docs/superpowers/specs/2026-08-15-backend-admin-panel-phase2-design.md
section 4 and 2026-08-15-ai-chat-panel-phase3-design.md section 3.
"""
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse, JSONResponse, Response

from server import ai_client, chat_ui, keystore, pdf_export, report_context

REPORT_PATH = Path(__file__).resolve().parent.parent.parent / "report" / "KP_Healthcare_Plan.html"

router = APIRouter()

TOP_BAR_CSS = r"""
#top-bar-links {
  position: fixed;
  top: 1rem;
  right: 1.5rem;
  z-index: 1000;
  display: flex;
  gap: 0.5rem;
}
#top-bar-links a {
  background: var(--panel, #fff);
  color: var(--ink, #16211f);
  border: 1px solid var(--line, rgba(22,33,31,0.13));
  border-radius: 999px;
  padding: 0.5rem 1rem;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  font-size: 0.85rem;
  text-decoration: none;
  box-shadow: 0 4px 16px rgba(0,0,0,0.2);
}
"""


def _inject_chat_widget(html_text):
    widget = chat_ui.render_widget()
    if "</body>" in html_text:
        return html_text.replace("</body>", widget + "</body>", 1)
    return html_text + widget


def _inject_top_bar_links(html_text):
    markup = (
        f"<style>{TOP_BAR_CSS}</style>\n"
        '<div id="top-bar-links">'
        '<a href="/admin" id="admin-link">Admin</a>'
        '<a href="/report.pdf" id="pdf-download-link">Download PDF</a>'
        "</div>"
    )
    if "</body>" in html_text:
        return html_text.replace("</body>", markup + "</body>", 1)
    return html_text + markup


@router.get("/", response_class=HTMLResponse)
def get_dashboard():
    if not REPORT_PATH.exists():
        return HTMLResponse(
            "<h1>Report not built yet</h1>"
            "<p>Run <code>python scripts/14_build_html_report.py</code> first.</p>",
            status_code=503,
        )
    html_text = REPORT_PATH.read_text(encoding="utf-8")
    html_text = _inject_chat_widget(html_text)
    html_text = _inject_top_bar_links(html_text)
    return HTMLResponse(html_text)


@router.get("/report.pdf")
def download_report_pdf():
    if not REPORT_PATH.exists():
        return JSONResponse({"detail": "Report not built yet"}, status_code=503)
    html_text = REPORT_PATH.read_text(encoding="utf-8")
    pdf_bytes = pdf_export.render_report_pdf(html_text)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="KP_Healthcare_Plan.pdf"'},
    )


@router.post("/api/ask")
def ask_ai(provider: str = Body(...), question: str = Body(...)):
    if provider not in keystore.PROVIDERS:
        return JSONResponse({"detail": f"Unknown provider: {provider}"}, status_code=404)
    key = keystore.get_key(provider)
    if not key:
        display_name = dict(chat_ui.PROVIDER_OPTIONS).get(provider, provider)
        return JSONResponse(
            {"detail": f"No API key configured for {display_name} - add one in the admin panel first."},
            status_code=400,
        )
    context = report_context.build_context()
    try:
        answer = ai_client.ask(provider, key, question, context)
    except ai_client.AIProviderError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)
    return JSONResponse({"answer": answer})
