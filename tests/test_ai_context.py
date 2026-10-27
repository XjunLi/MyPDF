import base64
import pytest
import pymupdf as fitz
from pdfreader.ai_context import build_document_context, background_message
from pdfreader.engine import PdfDocument


def test_pdf_snapshot_is_complete_includes_unsaved_edits_and_leaves_source_intact(document, pdf_path):
    original = pdf_path.read_bytes()
    document.add_comment(1, (50, 80), "Unsaved note on the second page")
    state = document.state
    context = build_document_context(document, "book.pdf", "session")
    message = background_message(context)
    file = message["content"][1]["file"]
    assert file["filename"] == "book.pdf"
    raw = base64.b64decode(file["file_data"].split(",", 1)[1])
    with fitz.open(stream=raw, filetype="pdf") as attached:
        assert len(attached) == 2
        assert "Second page" in attached[1].get_text()
        assert next(attached[1].annots()).info["content"] == "Unsaved note on the second page"
    assert pdf_path.read_bytes() == original and document.state == state and document.dirty
    assert build_document_context(document, "book.pdf", "session")["digest"] == context["digest"]


def test_text_mode_extracts_all_pages_and_marks_pages_without_text(document):
    with document.change("blank page"):
        document.doc.new_page()
    context = build_document_context(document, "book.pdf", "session", "text")
    assert "[第 1 页]" in context["data"] and "[第 3 页]" in context["data"]
    assert "Second page" in context["data"] and context["missing_pages"] == [3]
    assert isinstance(background_message(context)["content"], str)


def test_limits_fail_explicitly_without_partial_context(document, monkeypatch):
    monkeypatch.setattr("pdfreader.ai_context.PDF_LIMIT_BYTES", 50)
    with pytest.raises(ValueError, match="未发送"):
        build_document_context(document, "book.pdf", "session")
    monkeypatch.setattr("pdfreader.ai_context.TEXT_LIMIT", 50)
    with pytest.raises(ValueError, match="未截断"):
        build_document_context(document, "book.pdf", "session", "text")


def test_empty_text_has_no_fake_full_document_fallback():
    document = PdfDocument()
    document.new()
    try:
        with pytest.raises(ValueError, match="尚无可提取文字"):
            build_document_context(document, "未命名", "session", "text")
        message = background_message(build_document_context(document, "未命名", "session"))
        assert message["content"][1]["file"]["filename"] == "未命名.pdf"
    finally:
        document.close()


def test_unlocked_pdf_snapshot_omits_encryption_and_password(pdf_path, tmp_path):
    path = tmp_path / "locked.pdf"
    with fitz.open(pdf_path) as source:
        source.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="open-test-password",
                    owner_pw="owner-test-password", permissions=fitz.PDF_PERM_COPY)
    document = PdfDocument()
    document.open(path, "open-test-password")
    original = path.read_bytes()
    try:
        context = build_document_context(document, "locked.pdf", "session")
        raw = base64.b64decode(context["data"])
        with fitz.open(stream=raw, filetype="pdf") as attached:
            assert not attached.needs_pass and len(attached) == 2
        assert b"open-test-password" not in raw and "password" not in context
        assert path.read_bytes() == original
    finally:
        document.close()


def test_extraction_restrictions_are_honored(pdf_path, tmp_path):
    path = tmp_path / "restricted.pdf"
    with fitz.open(pdf_path) as source:
        source.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner-test-password",
                    permissions=fitz.PDF_PERM_PRINT)
    document = PdfDocument()
    document.open(path)
    try:
        with pytest.raises(ValueError, match="限制了内容提取"):
            build_document_context(document, "restricted.pdf", "session")
    finally:
        document.close()
