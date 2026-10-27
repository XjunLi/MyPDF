import pymupdf as fitz
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QToolButton
from pdfreader.app import MainWindow
from pdfreader.dialogs import CommentDialog, TextDialog
from test_selection import drag, endpoints, visible_point


def reader(qapp, pdf_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "settings"))
    window = MainWindow()
    window.auto_ocr.setChecked(False)
    window.show()
    window.open_file(str(pdf_path))
    window.view.set_zoom(1)
    window.view.goto(0)
    qapp.processEvents()
    return window


def test_highlight_is_available_only_for_text_selection(qapp, pdf_path, tmp_path, monkeypatch):
    window = reader(qapp, pdf_path, tmp_path, monkeypatch)
    for kind in ("highlight", "underline", "strike"):
        assert not window.mode_actions[kind].isEnabled()
    window.mode_actions["highlight"].trigger()
    assert window.view.mode == "select"
    assert not window.document.annotations()
    drag(window.view, *endpoints(window.view, 0, "reading"))
    assert window.mode_actions["highlight"].isEnabled()
    window.mode_actions["highlight"].trigger()
    assert window.view.mode == "select"
    assert len(window.document.annotations()) == 1
    assert not window.mode_actions["highlight"].isEnabled()
    window.undo()
    window.close()


def test_selected_text_comment_roundtrips_with_quote_and_undo(qapp, pdf_path, tmp_path, monkeypatch):
    window = reader(qapp, pdf_path, tmp_path, monkeypatch)
    drag(window.view, *endpoints(window.view, 0, "PDF reading"))
    def accept_note(dialog):
        assert dialog.windowTitle() == "文字注释"
        dialog.text.setPlainText("Check this phrase")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CommentDialog, "exec", accept_note)
    window.mode_actions["comment"].trigger()
    entries = window.document.annotations()
    assert len(entries) == 1
    assert entries[0]["type"] == "Highlight"
    assert entries[0]["quote"] == "PDF reading"
    assert entries[0]["text"] == "Check this phrase"
    assert "PDF reading" in window.comment_list.item(0).text()
    assert window.view.mode == "select"
    path = tmp_path / "text-comment.pdf"
    window.document.save(path)
    with fitz.open(path) as doc:
        page = doc[0]
        annotation = next(page.annots())
        assert annotation.info["content"] == "Check this phrase"
        assert len(annotation.vertices) == 4
    window.undo()
    assert not window.document.annotations()
    window.redo()
    assert window.document.annotations()[0]["quote"] == "PDF reading"
    window.document.open(path)
    assert window.document.annotations()[0]["quote"] == "PDF reading"
    window.close()


def test_point_comment_click_places_note_but_drag_does_not(qapp, pdf_path, tmp_path, monkeypatch):
    window = reader(qapp, pdf_path, tmp_path, monkeypatch)
    calls = []
    def accept_note(dialog):
        calls.append(dialog.windowTitle())
        dialog.text.setPlainText("Point note")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CommentDialog, "exec", accept_note)
    window.mode_actions["comment"].trigger()
    point = visible_point(window.view, 0, (200, 200))
    QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(window.view.viewport(), point + QPoint(60, 40))
    assert window.view.selection is None
    QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=point + QPoint(60, 40))
    assert not calls and not window.document.annotations()
    QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert calls == ["添加便笺"]
    entry = window.document.annotations()[0]
    assert entry["type"] == "Text" and entry["text"] == "Point note"
    assert window.view.mode == "select" and window.view.selection is None
    window.toggle_panel(None)
    marker = fitz.Rect(entry["rect"])
    center = visible_point(window.view, 0, (marker.tl + marker.br) / 2)
    QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton, pos=center)
    assert window.right.isVisible()
    assert window.comment_list.currentItem().data(Qt.ItemDataRole.UserRole)["xref"] == entry["xref"]
    window.undo()
    window.close()


def test_automatic_ocr_has_no_toolbar_button_and_can_cancel_pending_start(qapp, pdf_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREADER_DATA_DIR", str(tmp_path / "settings"))
    window = MainWindow()
    assert window.auto_ocr.isChecked()
    assert all(button.defaultAction() is not window.ocr_action for button in window.top.findChildren(QToolButton))
    calls = []
    monkeypatch.setattr(window, "start_ocr", lambda *args, **kwargs: calls.append(kwargs))
    window.open_file(str(pdf_path))
    QTest.qWait(490)
    assert calls == [{"automatic": True}]
    calls.clear()
    window.open_file(str(pdf_path))
    window.auto_ocr.setChecked(False)
    QTest.qWait(490)
    assert not calls
    window.close()


def test_edit_dialog_uses_original_style_by_default(qapp, pdf_path, tmp_path, monkeypatch):
    window = reader(qapp, pdf_path, tmp_path, monkeypatch)
    window.document.doc[0].insert_text((40, 210), "Colored title", fontname="hebo", fontsize=18.5, color=(.8,.1,.2))
    window.after_change()
    def accept_edit(dialog):
        assert dialog.preserve.isChecked()
        assert dialog.size.value() == 18.5
        assert dialog.color.name() == "#cc1a33"
        assert not dialog.custom_style.isVisible()
        dialog.text.setPlainText("Colored note")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(TextDialog, "exec", accept_edit)
    errors = []
    monkeypatch.setattr(window, "error", lambda exc: errors.append(str(exc)))
    box = window.document.doc[0].search_for("Colored title")[0]
    window.edit_at(0, tuple((box.tl + box.br) / 2))
    assert not errors
    span = next(s for b in window.document.doc[0].get_text("dict")["blocks"] for l in b.get("lines",[]) for s in l["spans"] if "Colored note" in s["text"])
    assert span["size"] == 18.5 and span["color"] == 0xcc1a33 and span["font"] == "Helvetica-Bold"
    window.undo()
    window.close()


def test_edits_resume_interrupted_ocr_but_respect_explicit_cancel(qapp, pdf_path, tmp_path, monkeypatch):
    window = reader(qapp, pdf_path, tmp_path, monkeypatch)
    # A process whose completion event is delayed reproduces successive edits
    # while the cancelled worker is still shutting down.
    class PendingProcess:
        def kill(self):
            pass
    window.auto_ocr.blockSignals(True)
    window.auto_ocr.setChecked(True)
    window.auto_ocr.blockSignals(False)
    window.ocr_process = PendingProcess()
    window.ocr_cancelled = False
    for message in ("first edit", "second edit"):
        window.mutate(lambda: window.document.add_comment(0, (100,200), message))
        assert window.ocr_pending_open == window.document_epoch
    window.cancel_ocr()
    window.mutate(lambda: window.document.add_comment(0, (100,200), "after cancel"))
    assert window.ocr_pending_open is None
    window.ocr_process = None
    window.document.saved_state = window.document.state
    window.close()
