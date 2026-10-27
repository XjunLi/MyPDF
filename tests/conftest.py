from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import pymupdf as fitz
from pdfreader.engine import PdfDocument


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "source.pdf"
    d = fitz.open()
    p = d.new_page(width=420, height=580)
    p.insert_text((40, 60), "Offline PDF reading and editing", fontsize=16)
    p.insert_text((40, 100), "Replace this original sentence.", fontsize=12)
    p.insert_text((40, 150), "Searchable words and comments.", fontsize=12)
    p.draw_rect(fitz.Rect(30, 250, 300, 310), color=(0, .6, .4), width=2)
    p = d.new_page(width=420, height=580)
    p.insert_text((40, 60), "Second page", fontsize=16)
    d.set_toc([[1, "Overview", 1], [1, "Second page", 2]])
    d.save(path)
    d.close()
    return path


@pytest.fixture
def document(pdf_path):
    d = PdfDocument()
    d.open(pdf_path)
    yield d
    d.close()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    from pdfreader.style import prepare_fonts
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    prepare_fonts(app)
    yield app
