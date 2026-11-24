"""Exercise a second desktop launch and an external edit using disposable PDFs."""
import shutil
import sys
import time

from PySide6.QtCore import QProcess
from PySide6.QtTest import QTest
import pymupdf as fitz

from .application import FileOpenRouter
from .paths import data_root, resource_root
from .pdf_files import FileConflict
from .single_instance import ReaderInstance


def exercise_file_protection(window, root, output):
    from PySide6.QtWidgets import QApplication
    path = root / "多次打开 first.pdf"
    other = root / "second launch.pdf"
    for filename in (path, other):
        shutil.copyfile(root / "smoke-result.pdf", filename)
    assert window.open_file(path)
    first = window.active_session
    contents, stamp = path.read_bytes(), path.stat()
    assert window.save()
    assert path.read_bytes() == contents and path.stat().st_mtime_ns == stamp.st_mtime_ns
    output["checks"].append("save_without_edits_preserves_original_bytes_and_timestamp")
    window.mutate(lambda: first.document.add_comment(0, (50, 50), "temporary annotation"))
    removed = first.document.annotations()[-1]["xref"]
    window.mutate(lambda: first.document.add_comment(0, (62, 68), "unsaved across launches"))
    kept = first.document.annotations()[-1]["xref"]
    window.mutate(lambda: first.document.delete_annotation(0, removed))
    assert window.open_file(other)
    router = FileOpenRouter(QApplication.instance())
    router.set_window(window)
    instance = ReaderInstance(data_root())
    instance.openRequested.connect(router.request_open)
    process = QProcess()
    try:
        assert instance.start_or_forward([])
        args = [] if getattr(sys, "frozen", False) else [str(resource_root() / "main.py")]
        process.setWorkingDirectory(str(root))
        process.start(sys.executable, args + [path.name])
        assert process.waitForStarted(5000), process.errorString()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and (process.state() != QProcess.ProcessState.NotRunning
                                                or window.active_session is not first):
            QTest.qWait(10)
        assert process.state() == QProcess.ProcessState.NotRunning
        assert process.exitCode() == 0 and process.exitStatus() == QProcess.ExitStatus.NormalExit
        assert window.active_session is first and len(window.sessions) == 2
        assert first.document.dirty and first.document.annotations()[-1]["text"] == "unsaved across launches"
        output["checks"].append("second_desktop_launch_reuses_dirty_tab")
        renamed = root / "改名后的文件.pdf"
        path.replace(renamed)
        path = renamed
        assert window.open_file(renamed) and window.active_session is first and first.document.path == renamed
        assert first.document.dirty and len(window.sessions) == 2
        output["checks"].append("renamed_file_reuses_existing_draft")
        third = root / "third document.pdf"
        shutil.copyfile(other, third)
        window.view.begin_inline_text(0, (80, first.document.doc[0].rect.height - 30))
        editor = window.view.inline_editor
        editor.input.setPlainText("Overflowing draft\n" * 20)
        assert not window.open_file(third) and window.active_session is first and len(window.sessions) == 2
        assert window.view.inline_editor is editor and "Overflowing draft" in editor.input.toPlainText()
        window.view.finish_inline_text(cancel=True)
        output["checks"].append("invalid_inline_edit_keeps_draft_and_does_not_add_tab")
        external = path.read_bytes() + b"\n% external revision\n"
        path.write_bytes(external)
        try:
            first.document.save()
        except FileConflict:
            pass
        else:
            raise AssertionError("External revision was overwritten")
        copy = root / "recovered.pdf"
        assert first.document.dirty
        first.document.save(copy)
        assert path.read_bytes() == external
        with fitz.open(copy) as result:
            page = result[0]
            assert any(note.info["content"] == "unsaved across launches" for note in page.annots())
        output["checks"].append("external_save_conflict_recovers_draft_to_copy")
        assert window.mutate(lambda: first.document.edit_comment(0, kept, "edited after saving"))
        assert window.save()
        saved, stamp = copy.read_bytes(), copy.stat()
        assert window.save() and copy.read_bytes() == saved and copy.stat().st_mtime_ns == stamp.st_mtime_ns
        duplicate = root / "saved revision copy.pdf"
        first.document.save(duplicate)
        assert duplicate.read_bytes() == saved
        with fitz.open(copy) as result:
            page = result[0]
            assert any(note.info["content"] == "edited after saving" for note in page.annots())
        output["checks"].append("saved_annotation_reference_remains_editable")
        assert window.close_document() and window.close_document()
    finally:
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(5000)
        instance.close()
        window.file_open_router = None
        QApplication.instance().removeEventFilter(router)
        router.deleteLater()
