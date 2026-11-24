import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from pdfreader.engine import PasswordRequired, PdfDocument
from pdfreader.pdf_files import FileConflict, PdfError, export_destination


def test_distinct_virtual_drive_paths_with_no_file_ids_stay_independent(monkeypatch):
    from pdfreader import paths
    monkeypatch.setattr(paths, "canonical_document_path", str)
    monkeypatch.setattr(Path, "stat", lambda self: SimpleNamespace(st_dev=0, st_ino=0))
    assert paths.same_document_path("paper.pdf", "paper.pdf")
    assert not paths.same_document_path("paper.pdf", "different.pdf")


def test_same_length_external_edit_with_restored_mtime_preserves_both_versions(pdf_path, tmp_path):
    original = pdf_path.read_bytes() + b"\n% version A\n"
    pdf_path.write_bytes(original)
    stamp = pdf_path.stat()
    document = PdfDocument()
    document.open(pdf_path)
    try:
        document.add_comment(0, (60, 70), "local draft")
        state = document.state
        changed = original.replace(b"% version A", b"% version B")
        pdf_path.write_bytes(changed)
        os.utime(pdf_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert pdf_path.stat().st_mtime_ns == stamp.st_mtime_ns
        with pytest.raises(FileConflict):
            document.save()
        assert pdf_path.read_bytes() == changed
        assert document.path == pdf_path and document.state == state and document.dirty
        assert document.undo() and document.redo()
        copy = tmp_path / "my edits.pdf"
        document.save(copy)
        assert not document.dirty and document.path == copy
        document.save()
        with fitz.open(copy) as verified:
            page = verified[0]
            assert next(page.annots()).info["content"] == "local draft"
        assert pdf_path.read_bytes() == changed
        assert not list(tmp_path.glob(".pdfreader-*"))
    finally:
        document.close()


def test_source_removed_or_replaced_while_open(document, pdf_path, tmp_path):
    before = pdf_path.read_bytes()
    document.add_comment(0, (60, 60), "pending")
    pdf_path.unlink()
    with pytest.raises(FileConflict):
        document.save()
    assert not pdf_path.exists()
    with fitz.open() as replacement:
        replacement.new_page()
        replacement.save(pdf_path)
    other = PdfDocument()
    with pytest.raises(FileConflict):
        other.open(pdf_path)
    with pytest.raises(FileConflict):
        document.save()
    # An identical restoration has no conflicting content.
    pdf_path.write_bytes(before)
    document.save()
    with pytest.raises(FileConflict):
        other.open(pdf_path)
    copy = tmp_path / "pending.pdf"
    document.save(copy)
    other.open(pdf_path)
    other.close()
    with pytest.raises(FileConflict):
        other.open(copy)
    document.new()
    other.open(copy)
    other.close()


def test_password_failure_never_grabs_or_drops_an_existing_lease(document, pdf_path, tmp_path):
    locked = tmp_path / "encrypted.pdf"
    with fitz.open(pdf_path) as source:
        source.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="reader", owner_pw="owner")
    with pytest.raises(PasswordRequired):
        document.open(locked, "wrong")
    other = PdfDocument()
    try:
        with pytest.raises(FileConflict):
            other.open(pdf_path)
        other.open(locked, "reader")
        other.save()
        with pytest.raises(PasswordRequired):
            other.open(locked, "wrong")
        with pytest.raises(FileConflict):
            document.open(locked, "reader")
    finally:
        other.close()
    document.open(locked, "reader")


def test_new_filename_stays_owned_after_an_external_atomic_replacement(document, pdf_path, tmp_path):
    path = tmp_path / "New Paper.PDF"
    document.save(path)
    alternate = path.with_name(path.name.swapcase())
    same_name = alternate.exists() and alternate.samefile(path)
    replacement = tmp_path / "incoming.pdf"
    replacement.write_bytes(pdf_path.read_bytes() + b"\n% another version\n")
    os.replace(replacement, path)
    second = PdfDocument()
    for name in (path, alternate) if same_name else (path,):
        with pytest.raises(FileConflict):
            second.open(name)
    document.close()
    second.open(path)
    second.close()


def test_save_as_target_created_while_pdf_is_being_encoded(document, tmp_path, monkeypatch):
    path = tmp_path / "new.pdf"
    external = b"independent file created during Save As"
    document.add_comment(0, (44, 68), "local changes")
    save = document.doc.save
    def concurrent_save(*args, **kwargs):
        save(*args, **kwargs)
        path.write_bytes(external)
    monkeypatch.setattr(document.doc, "save", concurrent_save)
    with pytest.raises(FileConflict):
        document.save(path)
    assert path.read_bytes() == external and document.dirty and document.path != path
    assert not list(tmp_path.glob(".pdfreader-*"))


def test_non_pdf_exports_cannot_write_through_an_alias_to_an_open_pdf(document, pdf_path, tmp_path):
    before = pdf_path.read_bytes()
    with pytest.raises(PdfError), export_destination(pdf_path):
        pytest.fail("PDF selected as text export")
    alias = tmp_path / "notes.txt"
    alias.hardlink_to(pdf_path)
    with pytest.raises(FileConflict), export_destination(alias) as temp:
        temp.write_bytes(b"exported text")
    assert pdf_path.read_bytes() == alias.read_bytes() == before
    document.close()
    with export_destination(alias) as temp:
        temp.write_bytes(b"exported text")
    assert alias.read_bytes() == b"exported text" and pdf_path.read_bytes() == before


def test_another_process_cannot_open_save_or_export_over_a_live_pdf(pdf_path, tmp_path):
    ready = tmp_path / "ready"
    code = """from pathlib import Path
import sys
from pdfreader.engine import PdfDocument
d = PdfDocument()
d.open(sys.argv[1])
d.add_comment(0, (50, 60), "first writer")
d.save()
Path(sys.argv[2]).touch()
sys.stdin.read()
"""
    holder = subprocess.Popen([sys.executable, "-c", code, str(pdf_path), str(ready)],
        cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
        env=dict(os.environ, PDFREADER_DATA_DIR=str(tmp_path / "other-profile")))
    second = PdfDocument()
    try:
        deadline = time.monotonic() + 12
        while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists(), f"PDF owner failed to start: {holder.poll()}"
        alias = tmp_path / "other name.pdf"
        os.link(pdf_path, alias)
        before = pdf_path.read_bytes()
        for path in (pdf_path, alias):
            with pytest.raises(FileConflict):
                second.open(path)
        second.new()
        second.add_comment(0, (60, 60), "second writer")
        for path in (pdf_path, alias):
            with pytest.raises(FileConflict):
                second.save(path)
            with pytest.raises(FileConflict):
                second.export_pages([0], path)
        assert second.dirty and second.path is None and pdf_path.read_bytes() == before
        second.save(tmp_path / "elsewhere.pdf")
        # A crashed reader cannot remove its own locks. The next open must recover.
        holder.kill()
        holder.wait(timeout=5)
        second.open(alias)
        assert second.annotations()[0]["text"] == "first writer"
    finally:
        second.close()
        if holder.poll() is None:
            holder.kill()
        holder.communicate(timeout=5)
