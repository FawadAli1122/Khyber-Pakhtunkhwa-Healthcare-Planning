import importlib
pdf_tables = importlib.import_module("scripts.lib.pdf_tables")

import fitz


def _make_test_pdf(tmp_path, pages_text):
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    path = str(tmp_path / "test.pdf")
    doc.save(path)
    doc.close()
    return path


def test_find_table_pages_locates_marker(tmp_path):
    path = _make_test_pdf(tmp_path, ["Intro page", "Table No. 105 data here", "Other content", "Table No. 105 repeated"])
    doc = fitz.open(path)
    pages = pdf_tables.find_table_pages(doc, 105)
    assert pages == [1, 3]
    doc.close()


def test_find_table_pages_no_match_returns_empty(tmp_path):
    path = _make_test_pdf(tmp_path, ["No tables here"])
    doc = fitz.open(path)
    pages = pdf_tables.find_table_pages(doc, 999)
    assert pages == []
    doc.close()
