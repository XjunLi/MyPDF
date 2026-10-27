import pymupdf as fitz
from pdfreader.engine import PdfDocument


def test_scanned_text_replacement_erases_only_selected_image_region(tmp_path):
    source = fitz.open()
    p = source.new_page(width=300, height=300)
    p.insert_text((30, 60), "OLD SCANNED TEXT", fontsize=16)
    p.insert_text((30, 160), "KEEP THIS REGION", fontsize=16)
    pix = p.get_pixmap(dpi=144, alpha=False)
    scanned = fitz.open()
    page = scanned.new_page(width=300, height=300)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    path = tmp_path / "scan.pdf"
    scanned.save(path)
    scanned.close()
    source.close()
    doc = PdfDocument()
    doc.open(path)
    untouched = doc.doc[0].get_pixmap(clip=fitz.Rect(0, 120, 300, 200)).samples
    before = doc.doc[0].get_pixmap(clip=fitz.Rect(0, 25, 300, 80)).samples
    doc.add_text(0, (25, 35, 280, 82), "NEW TEXT", 14, replace=True, raster_replace=True)
    assert doc.doc[0].search_for("NEW TEXT")
    assert doc.doc[0].get_pixmap(clip=fitz.Rect(0, 120, 300, 200)).samples == untouched
    assert doc.doc[0].get_pixmap(clip=fitz.Rect(0, 25, 300, 80)).samples != before
    doc.undo()
    assert doc.doc[0].get_pixmap(clip=fitz.Rect(0, 25, 300, 80)).samples == before
    doc.close()
