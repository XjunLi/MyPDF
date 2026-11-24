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
    window.mutate(lambda: first.document.add_comment(0, (62, 68), "unsaved across launches"))
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
        assert window.close_document() and window.close_document()
    finally:
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(5000)
        instance.close()
        QApplication.instance().removeEventFilter(router)
        router.deleteLater()
