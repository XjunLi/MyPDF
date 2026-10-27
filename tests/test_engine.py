from pathlib import Path
import pytest
import pymupdf as fitz

from pdfreader.engine import PdfDocument, PdfError, PasswordRequired, parse_page_range
from pdfreader.ocr import recognize


def test_annotations_save_roundtrip(document, tmp_path):
    box = document.doc[0].search_for("reading")[0]
    document.add_markup(0, box)
    document.add_comment(0, (70, 180), "中文评论：请核对。")
    document.add_ink(0, [[(100, 210), (115, 195), (130, 215), (150, 196)]], signature=True)
    path = tmp_path / "annotated.pdf"
    document.save(path)
    assert not document.dirty
    with fitz.open(path) as d:
        p = d[0]
        annotations = list(p.annots())
        assert [a.type[1] for a in annotations] == ["Highlight", "Text", "Ink"]
        assert annotations[1].info["content"] == "中文评论：请核对。"
        assert annotations[2].vertices


def test_replace_really_removes_old_text_and_undo(document, tmp_path):
    document.add_text(0, (35, 82, 360, 120), "Updated sentence.", 12, replace=True)
    assert "original" not in document.page_text(0)
    assert "Updated sentence" in document.page_text(0)
    assert len(document.doc[0].get_drawings()) == 1
    assert document.dirty
    document.undo()
    assert "original" in document.page_text(0)
    assert not document.dirty
    document.redo()
    assert "Updated sentence" in document.page_text(0)
    document.save(tmp_path / "replacement.pdf")
    assert not document.dirty
    document.undo()
    assert document.dirty


def test_text_overflow_rolls_back(document):
    state = document.state
    text = document.page_text(0)
    with pytest.raises(PdfError):
        document.add_text(0, (35, 82, 90, 100), "a very long sentence " * 20, 18, replace=True)
    assert document.state == state
    assert document.page_text(0) == text
    assert not document.undo_stack


def test_chinese_added_text_searches(document):
    document.add_text(0, (35, 340, 360, 420), "你好，离线阅读器。", 16)
    assert "离线阅读器" in document.page_text(0)
    assert document.doc[0].search_for("离线")
    box = document.doc[0].search_for("离线")[0]
    assert document.selection_text(0, box) == "离线"
    document.add_markup(0, box)
    assert document.annotations()[0]["type"] == "Highlight"


def test_edit_does_not_apply_existing_redactions(document):
    p = document.doc[0]
    p.add_redact_annot(fitz.Rect(35, 30, 350, 70))
    with pytest.raises(PdfError):
        document.add_text(0, (35, 82, 360, 120), "Updated.", 12, replace=True)
    assert "Offline" in document.page_text(0)
    assert "original" in document.page_text(0)
    assert document.annotations()[0]["type"] == "Redact"


def test_page_operations_and_export(document, pdf_path, tmp_path):
    document.rotate(0)
    assert document.doc[0].rect.width == 580
    document.insert_blank(0)
    assert document.count == 3
    document.move_page(2, 0)
    assert "Second" in document.page_text(0)
    document.delete_page(2)
    assert document.count == 2
    document.merge(pdf_path, 0)
    assert document.count == 4
    export = tmp_path / "extract.pdf"
    document.export_pages([3, 0], export)
    with fitz.open(export) as d:
        assert len(d) == 2
        assert "Second" in d[1].get_text()
    with pytest.raises(PdfError):
        document.export_pages([0], pdf_path)


def test_comment_update_and_delete(document):
    document.add_comment(0, (50, 200), "old")
    xref = document.annotations()[0]["xref"]
    document.edit_comment(0, xref, "new")
    assert document.annotations()[0]["text"] == "new"
    document.delete_annotation(0, xref)
    assert not document.annotations()
    document.undo()
    assert document.annotations()[0]["text"] == "new"


def test_encrypted_save_undo_and_reopen(pdf_path, tmp_path):
    encrypted = tmp_path / "private.pdf"
    with fitz.open(pdf_path) as d:
        d.save(encrypted, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="reader", owner_pw="owner",
               permissions=fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_COPY)
    document = PdfDocument()
    with pytest.raises(PasswordRequired):
        document.open(encrypted)
    document.open(encrypted, "reader")
    document.add_comment(0, (70, 200), "encrypted note")
    document.undo()
    document.redo()
    document.save(encrypted)
    with fitz.open(encrypted) as d:
        assert d.needs_pass
        assert d.authenticate("reader")
        p = d[0]
        assert next(p.annots()).info["content"] == "encrypted note"
    document.close()


def test_read_only_pdf_enforces_edit_permission(pdf_path, tmp_path):
    path = tmp_path / "readonly.pdf"
    with fitz.open(pdf_path) as d:
        d.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="reader", owner_pw="owner", permissions=fitz.PDF_PERM_PRINT)
    document = PdfDocument()
    document.open(path, "reader")
    with pytest.raises(PdfError):
        document.add_comment(0, (40, 200), "not permitted")
    with pytest.raises(PdfError):
        document.page_text(0)
    document.close()


def test_form_value_persists(document, tmp_path):
    p = document.doc[0]
    field = fitz.Widget()
    field.field_name = "name"
    field.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    field.rect = fitz.Rect(40, 450, 300, 480)
    p.add_widget(field)
    xref = next(document.doc[0].widgets()).xref
    document.update_widget(0, xref, "Ada Lovelace")
    target = tmp_path / "form.pdf"
    document.save(target)
    with fitz.open(target) as d:
        assert next(d[0].widgets()).field_value == "Ada Lovelace"


def test_range_validation():
    assert parse_page_range("1-3, 5,2", 6) == [0, 1, 2, 4]
    for bad in ["0", "8", "4-1", "x"]:
        with pytest.raises((PdfError, ValueError)):
            parse_page_range(bad, 6)


def test_failed_open_preserves_current_document(document, tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"invalid")
    with pytest.raises(Exception):
        document.open(path)
    assert document.count == 2
    assert "Offline" in document.page_text(0)


def test_ocr_is_offline_searchable_and_preserves_original(tmp_path):
    original = fitz.open()
    p = original.new_page(width=420, height=580)
    p.insert_text((40, 90), "AUTOMATIC OFFLINE OCR", fontsize=22)
    p.insert_text((40, 140), "This scanned page becomes searchable.", fontsize=16)
    pix = p.get_pixmap(dpi=170, alpha=False)
    source = tmp_path / "scanned.pdf"
    scan = fitz.open()
    p = scan.new_page(width=420, height=580)
    p.insert_image(p.rect, stream=pix.tobytes("png"))
    p.set_rotation(90)
    p.add_text_annot((50, 220), "Keep this annotation")
    scan.save(source)
    scan.close()
    original.close()
    document = PdfDocument()
    document.open(source)
    assert document.scan_candidates() == [0]
    before = document.doc[0].get_pixmap(dpi=72).samples
    overlays = recognize(source, tmp_path / "layers", [0], "eng+chi_sim")
    assert len(overlays) == 1
    document.apply_ocr(overlays)
    assert "OFFLINE" in document.page_text(0)
    assert document.doc[0].search_for("searchable")
    assert document.doc[0].rotation == 90
    assert document.annotations()[0]["text"] == "Keep this annotation"
    after = document.doc[0].get_pixmap(dpi=72).samples
    assert after == before, "OCR must add only invisible text, not change appearance"
    target = tmp_path / "searchable.pdf"
    document.save(target)
    document.close()
    with fitz.open(target) as d:
        assert "AUTOMATIC" in d[0].get_text()


def test_save_failure_does_not_replace_source(document, pdf_path, monkeypatch):
    import pdfreader.engine as engine
    before = pdf_path.read_bytes()
    document.add_comment(0, (50, 200), "unsaved")
    def fail(*args):
        raise PermissionError("simulated lock")
    monkeypatch.setattr(engine.os, "replace", fail)
    with pytest.raises(PermissionError):
        document.save(pdf_path)
    assert pdf_path.read_bytes() == before
    assert document.dirty
