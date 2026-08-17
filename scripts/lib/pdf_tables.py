"""Shared helpers for locating and extracting tables from the Development
Statistics of Khyber Pakhtunkhwa 2024 PDF. Table titles/numbers repeat
across pages (a table spanning many districts continues on the next page
under the same "Table No. N" marker), and the same table number can also
appear 2-3x total for different year-snapshots in some sections — callers
are responsible for picking the right occurrence (see
scripts/17_extract_devstats_health.py for the "use the last occurrence"
convention used throughout this project)."""
import pdfplumber


def find_table_pages(doc, table_no):
    """doc: an open fitz.Document. Returns 0-based page indices whose text
    contains the literal marker "Table No. {table_no}" (case-sensitive,
    matching the PDF's own formatting)."""
    marker = f"Table No. {table_no}"
    pages = []
    for i in range(len(doc)):
        if marker in doc[i].get_text():
            pages.append(i)
    return pages


def extract_table_rows(pdf_path, page_index):
    """Grid-based table extraction via pdfplumber for a single 0-based page
    index. Returns a list of rows, each a list of cell strings (None cells
    become empty strings). Prefer this over raw text extraction for actual
    tabular data since it respects the PDF's line/grid structure."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        table = page.extract_table()
        if table is None:
            return []
        return [[(cell or "").strip() for cell in row] for row in table]
