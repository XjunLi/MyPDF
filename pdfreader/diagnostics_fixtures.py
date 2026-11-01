"""Portable PDF fixtures; no installed proprietary font is required."""
import pymupdf as fitz
from .engine import PdfDocument


def editable_chinese(path):
    with fitz.open() as source:
        page = source.new_page(width=595, height=842)
        page.insert_text((42, 479), "02", fontsize=12, color=(.3, .5, .7))
        page.insert_text((84, 479), "让批注成为线索", fontname="china-s", fontsize=16, color=(.2, .3, .4))
        source.save(path)
    document = PdfDocument()
    document.open(path)
    return document
